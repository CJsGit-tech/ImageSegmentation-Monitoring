"""Crop the cats and dogs detected in the images of the data folder.

Each animal is cut out by its bounding box and saved to outputs/cropped_animals/ as
<image name>_<class>_<number>.jpg. All the animals in config.yaml are prompted, so
that a bird is labelled a bird rather than a cat or dog, and only CROP_CLASSES are saved.

The source folder, model and detection settings come from config.yaml. Usage, from anywhere:
    .venv/bin/python image-segmentation/experiments/scripts/crop_animals.py
"""

import sys
from pathlib import Path

EXPERIMENTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXPERIMENTS.parent / "submission" / "scripts"))  # Shared YOLOE and measurement code.

import math
import os

import cv2
import torch
from ultralytics import YOLOE

from run_yoloe_test_samples import detections, load_config, remove_duplicates

CROP_CLASSES = ("cat", "dog")
OUTPUT = EXPERIMENTS / "outputs" / "cropped_animals"


def crop(image, box):
    """Return the part of an image inside a box, with the box limited to the image."""
    height, width = image.shape[:2]
    x1, y1, x2, y2 = box
    return image[max(math.floor(y1), 0):min(math.ceil(y2), height),
                 max(math.floor(x1), 0):min(math.ceil(x2), width)]


def main():
    config = load_config()
    # YOLOE saves its text encoder in the working directory, so stay in the weights folder.
    config["model"].parent.mkdir(parents=True, exist_ok=True)
    os.chdir(config["model"].parent)

    paths = sorted(p for p in config["source"].iterdir() if p.suffix.lower() in config["image_suffixes"])
    if not paths:
        raise SystemExit(f"No images found in {config['source']}")
    OUTPUT.mkdir(exist_ok=True)

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = YOLOE(config["model"].name)
    prompts = config["animals"]
    model.set_classes(prompts, model.get_text_pe(prompts))

    for path in paths:
        image = cv2.imread(str(path))
        if image is None:
            print(f"{path.name}: skipped, not a readable image")
            continue

        found = detections(model.predict(
            image, conf=config["animal_conf"], imgsz=config["animal_imgsz"], device=device, verbose=False)[0])
        animals = remove_duplicates(found, image.shape, config["duplicate_overlap"])
        targets = [a for a in animals if a["class"] in CROP_CLASSES]

        for number, animal in enumerate(targets, start=1):
            name = f"{path.stem}_{animal['class']}_{number}.jpg"
            cv2.imwrite(str(OUTPUT / name), crop(image, animal["box"]))
            print(f"{name}: confidence {animal['confidence']}, box {animal['box']}")
        if not targets:
            print(f"{path.name}: no cats or dogs found")

    print(f"Crops saved to: {OUTPUT}")


if __name__ == "__main__":
    main()
