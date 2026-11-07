"""Export eye heatmaps from DINO and DINOv2 to results/heatmaps/dino_v1/ and results/heatmaps/dino_v2/.

Both use the same crops, reference eyes and color scale, and a 56-patch grid on each crop's
long side. Images are named after their crops.

Run segment_animals_eyes.py and crop_animals.py first. Usage, from anywhere:
    .venv/bin/python image-segmentation/export_heatmaps.py
"""

import cv2
import torch

import dino_heatmap as dino_v1
import run_dinov2_watershed as dino_v2
import run_eye_experiments as experiments
from segment_animals_eyes import ROOT, load_config

OUTPUT = ROOT / "results" / "heatmaps"
MODELS = {"dino_v1": dino_v1, "dino_v2": dino_v2}


def main():
    config = load_config()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Running on execution device: {device.upper()}")
    for folder, source in MODELS.items():
        model = source.load_model(config["model"].parent, device)
        crops = experiments.load_crops(config, model, device, source.patch_features)
        (OUTPUT / folder).mkdir(parents=True, exist_ok=True)
        for crop in crops:
            similarity = crop["similarity"]
            cv2.imwrite(str(OUTPUT / folder / f"{crop['name']}.png"), dino_v1.colorize(similarity))
            print(f"{folder}/{crop['name']}.png: similarity {similarity.min():.2f} to {similarity.max():.2f}")
    print(f"Heatmaps saved to: {OUTPUT}")


if __name__ == "__main__":
    main()
