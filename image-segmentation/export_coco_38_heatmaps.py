"""Export the DINOv2 eye heatmaps behind the 38-image test to results/coco_38_test/heatmaps/.

There is one heatmap per cat or dog YOLOE found, named <image>_<class>_<number>.png, with the same
crop, reference eyes and color scale as run_coco_38_test.py. overview.jpg shows each crop with its
heatmap. An image with no detected cat or dog has no heatmap.

Run run_coco_38_test.py first. Usage, from anywhere:
    .venv/bin/python image-segmentation/export_coco_38_heatmaps.py
"""

import json

import cv2
import numpy as np
import torch

import dino_heatmap
import run_coco_38_test as test
import run_dinov2_watershed as dinov2
import segment_animals_eyes as segment
from crop_animals import CROP_CLASSES

OUTPUT = test.OUTPUT / "heatmaps"
TILE_HEIGHT = 180
PAIRS_PER_ROW = 4


def main():
    config = segment.load_config()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Running on execution device: {device.upper()}")
    model = dinov2.load_model(config["model"].parent, device)
    reference, _ = test.eye_reference(config, model, device)
    OUTPUT.mkdir(parents=True, exist_ok=True)

    tiles, empty = [], []
    for path in sorted(test.IMAGES.glob("*.jpg")):
        prediction = json.loads((test.OUTPUT / "yoloe" / f"{path.stem}.json").read_text())
        animals = [a for a in prediction["animals"] if a["class"] in CROP_CLASSES]
        if not animals:
            empty.append(path.name)
        image = cv2.imread(str(path))
        for number, animal in enumerate(animals, start=1):
            entry = test.animal_entry(image, animal, model, device, reference)
            heatmap = dino_heatmap.colorize(entry["similarity"])
            cv2.imwrite(str(OUTPUT / f"{path.stem}_{animal['class']}_{number}.png"), heatmap)

            width = max(round(heatmap.shape[1] * TILE_HEIGHT / heatmap.shape[0]), 1)
            tiles.append(np.hstack([cv2.resize(entry["image"], (width, TILE_HEIGHT)),
                                    cv2.resize(heatmap, (width, TILE_HEIGHT), interpolation=cv2.INTER_NEAREST)]))

    rows = [np.hstack(tiles[i:i + PAIRS_PER_ROW]) for i in range(0, len(tiles), PAIRS_PER_ROW)]
    row_width = max(row.shape[1] for row in rows)
    cv2.imwrite(str(OUTPUT / "overview.jpg"),
                np.vstack([cv2.copyMakeBorder(r, 0, 4, 0, row_width - r.shape[1], cv2.BORDER_CONSTANT) for r in rows]))
    print(f"Saved {len(tiles)} heatmaps to: {OUTPUT}")
    print(f"Images with no cat or dog: {empty}")


if __name__ == "__main__":
    main()
