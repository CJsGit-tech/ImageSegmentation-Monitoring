"""Run DINOv2 + GrabCut and DINOv2 + heatmap level on the sample images.

Each cat or dog is cut out using YOLOE's box. A crop's reference eyes are YOLOE's outlines in the other
sample images, so a crop is never compared with its own eyes. Results go to
outputs/001-test_samples_yoloe_dinov2/:
    cropped_animals/   The animal crops.
    heatmaps/          One heatmap per animal.
    grabcut/           For each image, an annotated copy (animals green, eyes red) and a JSON file
                       with each cat or dog's eye centers and outlines in pixels.
    heatmap_level/     The same for the heatmap level method.

The JSON files use the same format as run_yoloe_and_dinov2_other_samples.py, so
measure_yoloe_dinov2_eyes.py can measure either.

Run run_yoloe_test_samples.py first. Usage, from anywhere:
    .venv/bin/python image-segmentation/submission/scripts/run_yoloe_dinov2_test_samples.py
"""

import json

import cv2
import numpy as np
import torch

import dinov2_eye_pipeline as pipeline
from run_yoloe_test_samples import ROOT, annotate, load_config

OUTPUT = ROOT.parent / "outputs" / "001-test_samples_yoloe_dinov2"
CROPS = OUTPUT / "cropped_animals"
METHODS = {"grabcut": pipeline.refine_grabcut, "heatmap_level": pipeline.refine_heatmap_level}


def load_animals(config, model, device):
    animals = []
    for path in sorted(p for p in config["source"].iterdir() if p.suffix.lower() in config["image_suffixes"]):
        image = cv2.imread(str(path))
        prediction = json.loads((config["output"] / f"{path.stem}.json").read_text())
        targets = [a for a in prediction["animals"] if a["class"] in pipeline.CROP_CLASSES]
        for number, animal in enumerate(targets, start=1):
            name = f"{path.stem}_{animal['class']}_{number}"
            crop_path = CROPS / f"{name}.jpg"
            cv2.imwrite(str(crop_path), pipeline.crop(image, animal["box"]))
            piece = cv2.imread(str(crop_path))
            offset = pipeline.crop_offset(animal)
            features, grid = pipeline.patch_features(model, piece, device)
            animals.append({"name": name, "source": path.stem, "animal": animal, "piece": piece, "offset": offset,
                            "features": features, "grid": grid,
                            "eye_patches": pipeline.eye_patches(piece, offset, prediction["eyes"], features, grid)})
    return animals


def save_image(config, name, source):
    """Draw YOLOE's cat and dog outlines with this variant's eye outlines, in the YOLOE output's style."""
    image = cv2.imread(str(config["source"] / f"{source}.jpg"))
    prediction = json.loads((config["output"] / f"{source}.json").read_text())
    animals = [a for a in prediction["animals"] if a["class"] in pipeline.CROP_CLASSES]
    result = json.loads((OUTPUT / name / f"{source}.json").read_text())
    eyes = [e for animal in result["animals"] for e in animal["eyes"] if e["polygon"] is not None]
    cv2.imwrite(str(OUTPUT / name / f"{source}.jpg"),
                annotate(image, animals, eyes, config["animal_color"], config["eye_color"]))


def main():
    config = load_config()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Running on execution device: {device.upper()}")
    model = pipeline.load_model(config["model"].parent, device)
    CROPS.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "heatmaps").mkdir(parents=True, exist_ok=True)
    for name in METHODS:
        (OUTPUT / name).mkdir(parents=True, exist_ok=True)

    animals = load_animals(config, model, device)
    results = {name: {} for name in METHODS}
    for item in animals:
        reference = pipeline.normalized_mean([a["eye_patches"] for a in animals if a["source"] != item["source"]])
        entry = pipeline.animal_entry(item["piece"], item["animal"], item["offset"], item["features"], item["grid"], reference)
        cv2.imwrite(str(OUTPUT / "heatmaps" / f"{item['name']}.png"), pipeline.colorize(entry["similarity"]))
        located = pipeline.locate(entry)
        for name, refiner in METHODS.items():
            eyes = pipeline.run_refiner(entry, refiner, located)
            saved = [{"center": None if e["center"] is None else [round(float(v + o), 2) for v, o in zip(e["center"], entry["offset"])],
                      "polygon": None if e["polygon"] is None else (np.array(e["polygon"]) + entry["offset"]).round(1).tolist(),
                      "peak_score": round(float(start["score"]), 3)}
                     for e, start in zip(eyes, located)]
            results[name].setdefault(item["source"], []).append(
                {"class": item["animal"]["class"], "box": item["animal"]["box"], "eyes": saved})
    for name, images in results.items():
        for source, image_animals in images.items():
            (OUTPUT / name / f"{source}.json").write_text(
                json.dumps({"image": f"{source}.jpg", "animals": image_animals}, indent=2))
            save_image(config, name, source)
    print(f"Results saved to: {OUTPUT}")


if __name__ == "__main__":
    main()
