"""Segment animals and their eyes separately in every image of a folder.

COCO models have no eye class, so this uses YOLOE, which segments whatever it is
told to look for in text. It is given the animal names in config.yaml and "eye".
Weights and the text encoder are stored in model/.

An eye is kept only if it lies on a detected animal, and is linked to that animal.
Each image produces an annotated copy (animals green, eyes red) and a JSON file
with the classes, confidences, boxes and polygons.

Settings are in config.yaml. Usage, from anywhere:
    .venv/bin/python image-segmentation/submission/scripts/run_yoloe_test_samples.py
"""

import json
import os
from pathlib import Path

import cv2
import numpy as np
import torch
import yaml
from ultralytics import YOLOE

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"


def load_config():
    config = yaml.safe_load(CONFIG_PATH.read_text())
    for key in ("source", "output", "model", "other_source", "other_output"):
        config[key] = (ROOT / config[key]).resolve()
    config["image_suffixes"] = {suffix.lower() for suffix in config["image_suffixes"]}
    config["animal_color"] = tuple(config["animal_color"])
    config["eye_color"] = tuple(config["eye_color"])
    return config


def outline(mask, size):
    """Trace a mask's largest outer contour, ignoring holes and stray fragments.

    result.masks.xy joins every fragment into one polygon, which draws stray lines
    across the object.
    """
    height, width = size
    binary = cv2.resize((mask > 0.5).astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []
    return max(contours, key=cv2.contourArea).reshape(-1, 2).tolist()


def detections(result):
    """Return a result's detections as plain, JSON-ready dictionaries."""
    if result.masks is None:
        return []
    found = []
    for box, conf, cls, mask in zip(
        result.boxes.xyxy.tolist(), result.boxes.conf.tolist(),
        result.boxes.cls.tolist(), result.masks.data.cpu().numpy(),
    ):
        polygon = outline(mask, result.orig_shape)
        if len(polygon) >= 3:  # Very small masks can produce no usable outline.
            found.append({
                "class": result.names[int(cls)],
                "confidence": round(conf, 3),
                "box": [round(v, 1) for v in box],
                "polygon": polygon,
            })
    return found

def contour(det):
    return np.array(det["polygon"], dtype=np.int32)


def remove_duplicates(dets, shape, max_overlap):
    """Keep the most confident detection where masks mostly cover the same pixels.

    Comparing against the smaller mask also removes a partial outline lying inside a
    full one. Two animals that only overlap, such as one leaning on another, both stay.
    """
    kept, kept_masks = [], []
    for det in sorted(dets, key=lambda d: -d["confidence"]):
        mask = np.zeros(shape[:2], dtype=np.uint8)
        cv2.fillPoly(mask, [contour(det)], 1)
        area = int(mask.sum())
        is_duplicate = any(
            int((mask & other).sum()) > max_overlap * min(area, int(other.sum()))
            for other in kept_masks
        )
        if area and not is_duplicate:
            kept.append(det)
            kept_masks.append(mask)
    return kept


def owner_of(eye, animals, max_distance):
    """Return the index of the animal under this eye, or None if there is none."""
    x1, y1, x2, y2 = eye["box"]
    centre = (float((x1 + x2) / 2), float((y1 + y2) / 2))
    best, best_distance = None, -max_distance
    for index, animal in enumerate(animals):
        # Positive inside the outline, negative outside.
        distance = cv2.pointPolygonTest(contour(animal).astype(np.float32), centre, True)
        if distance >= best_distance:
            best, best_distance = index, distance
    return best


def annotate(image, animals, eyes, animal_color, eye_color):
    canvas = image.copy()
    for animal in animals:
        cv2.fillPoly(canvas, [contour(animal)], animal_color)
    canvas = cv2.addWeighted(canvas, 0.2, image, 0.8, 0)  # Light tint, so eyes stay visible.
    for animal in animals:
        cv2.polylines(canvas, [contour(animal)], True, animal_color, 2)
        x1, y1 = map(int, animal["box"][:2])
        cv2.putText(canvas, animal["class"], (x1 + 2, max(y1 + 14, 14)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, animal_color, 1, cv2.LINE_AA)
    for eye in eyes:
        cv2.polylines(canvas, [contour(eye)], True, eye_color, 1, cv2.LINE_AA)
    return canvas


def main():
    config = load_config()
    # YOLOE saves its text encoder in the working directory. Staying in the weights
    # folder keeps them together and reuses the encoder instead of downloading it again.
    config["model"].parent.mkdir(parents=True, exist_ok=True)
    os.chdir(config["model"].parent)

    paths = sorted(p for p in config["source"].iterdir() if p.suffix.lower() in config["image_suffixes"])
    if not paths:
        raise SystemExit(f"No images found in {config['source']}")
    config["output"].mkdir(parents=True, exist_ok=True)

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Running on execution device: {device.upper()}")
    model = YOLOE(config["model"].name)
    eye_prompt = config["eye_prompt"]
    prompts = [*config["animals"], eye_prompt]
    model.set_classes(prompts, model.get_text_pe(prompts))

    for path in paths:
        image = cv2.imread(str(path))
        if image is None:
            print(f"{path.name}: skipped, not a readable image")
            continue

        # The same model looks twice: eyes need the enlarged image, but at that size
        # animals fill the frame and are missed or mislabelled.
        animal_pass = detections(model.predict(
            image, conf=config["animal_conf"], imgsz=config["animal_imgsz"], device=device, verbose=False)[0])
        eye_pass = detections(model.predict(
            image, conf=config["eye_conf"], imgsz=config["eye_imgsz"], device=device, verbose=False)[0])
        animals = remove_duplicates(
            [d for d in animal_pass if d["class"] != eye_prompt], image.shape, config["duplicate_overlap"])
        candidates = remove_duplicates(
            [d for d in eye_pass if d["class"] == eye_prompt], image.shape, config["duplicate_overlap"])

        eyes = []
        for eye in candidates:
            owner = owner_of(eye, animals, config["max_eye_distance"])
            if owner is not None:
                eyes.append({**eye, "animal_index": owner})

        cv2.imwrite(str(config["output"] / f"{path.stem}.jpg"),
                    annotate(image, animals, eyes, config["animal_color"], config["eye_color"]))
        (config["output"] / f"{path.stem}.json").write_text(
            json.dumps({"image": path.name, "animals": animals, "eyes": eyes}, indent=2))
        print(f"{path.name}: {len(animals)} animals, {len(eyes)} eyes "
              f"({len(candidates) - len(eyes)} eye detections off any animal removed)")

    print(f"Results saved to: {config['output']}")


if __name__ == "__main__":
    main()