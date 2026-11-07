"""Run DINOv2 + local peaks + watershed on the original images in data/, without cropping.

Each full image goes through DINOv2 once. YOLOE's outlines then limit the eye search to each
cat or dog, using the same settings as experiment 14. Reference eyes come from other images.

Results go to outputs/experiments/15_dinov2_original_<size>/:
    <image>.jpg     The full image with thin red eye outlines.
    <animal>.json   Each animal's eye centers and outlines, in image pixels.
    metrics.json    Scores against HAND_MARKS.

Run run_yoloe_test_samples.py first. Usage, from anywhere:
    .venv/bin/python image-segmentation/experiments/scripts/run_dinov2_original_images.py
"""

import sys
from pathlib import Path

EXPERIMENTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXPERIMENTS.parent / "submission" / "scripts"))  # Shared YOLOE and measurement code.

import json

import cv2
import numpy as np
import torch

import run_dinov2_watershed as dinov2
import run_eye_experiments as experiments
from crop_animals import CROP_CLASSES
from dino_heatmap import MIN_EYE_COVER
from run_yoloe_test_samples import load_config

LONG_SIDES = (784, 1568)  # Each image's long side when it enters DINOv2.


def load_animals(config, model, device):
    """Return one experiment entry per cat or dog, all sharing their image's features."""
    animals = []
    for path in sorted(p for p in config["source"].iterdir() if p.suffix.lower() in config["image_suffixes"]):
        image = cv2.imread(str(path))
        prediction = json.loads((config["output"] / f"{path.stem}.json").read_text())
        features, grid = dinov2.patch_features(model, image, device)

        eyes = np.zeros(image.shape[:2], np.uint8)
        for eye in prediction["eyes"]:
            cv2.fillPoly(eyes, [np.array(eye["polygon"], np.int32)], 1)
        cover = cv2.resize(eyes.astype(np.float32), grid[::-1], interpolation=cv2.INTER_AREA).ravel()

        targets = [a for a in prediction["animals"] if a["class"] in CROP_CLASSES]
        for number, animal in enumerate(targets, start=1):
            mask = np.zeros(image.shape[:2], np.uint8)
            cv2.fillPoly(mask, [np.array(animal["polygon"], np.int32)], 1)
            x1, y1, x2, y2 = animal["box"]
            animals.append({
                "name": f"{path.stem}_{animal['class']}_{number}", "source": path.stem, "class": animal["class"],
                "image": image, "offset": np.array([0, 0]), "mask": mask, "grid": grid, "features": features,
                "eye_patches": features[cover > MIN_EYE_COVER],
                "grid_mask": cv2.resize(mask.astype(np.float32), grid[::-1], interpolation=cv2.INTER_AREA) > 0.5,
                "long_side": max(x2 - x1, y2 - y1),  # Spacing and window sizes still follow the animal.
                "marks": experiments.HAND_MARKS[(path.stem, animal["class"])],
            })

    sources = {a["source"]: a for a in animals}
    for animal in animals:
        references = np.vstack([a["eye_patches"] for a in sources.values() if a["source"] != animal["source"]])
        eye_feature = references.mean(0)
        eye_feature /= np.linalg.norm(eye_feature)
        animal["similarity"] = (animal["features"] @ eye_feature).reshape(animal["grid"]).astype(np.float32)
        animal["full_similarity"] = cv2.resize(animal["similarity"], animal["image"].shape[1::-1],
                                               interpolation=cv2.INTER_CUBIC)
    return animals


def save_results(folder, animals, eye_lists):
    folder.mkdir(parents=True, exist_ok=True)
    scores = [experiments.score_crop(a, eyes) for a, eyes in zip(animals, eye_lists)]
    canvases = {}
    for animal, eyes in zip(animals, eye_lists):
        canvas = canvases.setdefault(animal["source"], animal["image"].copy())
        for eye in eyes:
            if eye.get("polygon") is not None:
                cv2.polylines(canvas, [np.array(eye["polygon"]).round().astype(np.int32)], True, (0, 0, 255), 1, cv2.LINE_AA)
        saved = [{"hit": e["hit"],
                  "center": None if e["center"] is None else [round(float(v), 2) for v in e["center"]],
                  "polygon": None if e.get("polygon") is None else np.array(e["polygon"]).round(1).tolist()} for e in eyes]
        (folder / f"{animal['name']}.json").write_text(json.dumps({"crop": animal["name"], "eyes": saved}, indent=2))
    for source, canvas in canvases.items():
        cv2.imwrite(str(folder / f"{source}.jpg"), canvas)
    metrics = experiments.summarize(folder.name, animals, scores, eye_lists)
    (folder / "metrics.json").write_text(json.dumps(metrics, indent=2))
    return metrics


def main():
    config = load_config()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Running on execution device: {device.upper()}")
    model = dinov2.load_model(config["model"].parent, device)
    for size in LONG_SIDES:
        dinov2.LONG_SIDE = size
        animals = load_animals(config, model, device)
        located = [experiments.run_locator(a, experiments.locate_peaks) for a in animals]
        refined = [experiments.run_refiner(a, experiments.refine_watershed, eyes) for a, eyes in zip(animals, located)]
        print(save_results(experiments.OUTPUT / f"15_dinov2_original_{size}", animals, refined))
        print(f"  grid {animals[0]['grid']}, peaks alone:",
              experiments.summarize("peaks", animals, [experiments.score_crop(a, e) for a, e in zip(animals, located)], located))


if __name__ == "__main__":
    main()
