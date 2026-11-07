"""Export a DINOv2 eye heatmap for every full image in data/ to outputs/heatmaps/dino_v2_full_images/.

Each image's reference eyes come from YOLOE's eye outlines in the other images. The color scale
matches the crop heatmaps, and images are named after their source images.

Run run_yoloe_test_samples.py and crop_animals.py first. Usage, from anywhere:
    .venv/bin/python image-segmentation/experiments/scripts/export_full_image_heatmaps.py
"""

import sys
from pathlib import Path

EXPERIMENTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXPERIMENTS.parent / "submission" / "scripts"))  # Shared YOLOE and measurement code.

import cv2
import torch

import dino_heatmap
import run_dinov2_original_images as full_images
import run_dinov2_watershed as dinov2
from run_yoloe_test_samples import load_config

OUTPUT = EXPERIMENTS / "outputs" / "heatmaps" / "dino_v2_full_images"


def main():
    config = load_config()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Running on execution device: {device.upper()}")
    model = dinov2.load_model(config["model"].parent, device)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    sources = {animal["source"]: animal for animal in full_images.load_animals(config, model, device)}
    for name, animal in sources.items():
        similarity = animal["similarity"]
        cv2.imwrite(str(OUTPUT / f"{name}.png"), dino_heatmap.colorize(similarity))
        print(f"{name}.png: grid {animal['grid']}, similarity {similarity.min():.2f} to {similarity.max():.2f}")
    print(f"Heatmaps saved to: {OUTPUT}")


if __name__ == "__main__":
    main()
