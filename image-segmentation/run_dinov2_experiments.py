"""Run the no-install image-processing experiments on cropped_animals with DINOv2 heatmaps.

Only the listed method changes from experiment 14: DINOv2 ViT-S/14 with registers at 784 pixels,
local peaks and watershed. Outline experiments start from the same local peaks. Each experiment
has its own folder in results/experiments/, and dino_v2_summary.csv compares them with YOLOE and
experiment 14.

Run segment_animals_eyes.py and crop_animals.py first. Usage, from anywhere:
    .venv/bin/python image-segmentation/run_dinov2_experiments.py
"""

import csv

import cv2
import numpy as np
import torch

import run_dinov2_watershed as dinov2
import run_eye_experiments as experiments
from segment_animals_eyes import load_config

# Canny thresholds, as shares of the window's median brightness. Lower than experiment 12,
# because partial edges are enough for barriers and its stricter values found too few.
BARRIER_LOW = 0.33
BARRIER_HIGH = 0.66
CLUSTERS = 6            # Pixel clusters in each eye window.


def refine_watershed_canny(window):
    """Watershed with Canny edges drawn in black, so regions tend to stop at them."""
    gray = cv2.GaussianBlur(window["gray"], (0, 0), 2)
    middle = np.median(gray)
    edges = cv2.Canny(gray, round(BARRIER_LOW * middle), round(BARRIER_HIGH * middle))
    image = window["image"].copy()
    image[edges > 0] = 0

    markers = np.zeros(gray.shape, np.int32)
    border = experiments.BORDER
    markers[:border], markers[-border:], markers[:, :border], markers[:, -border:] = 1, 1, 1, 1
    markers[experiments.seed_disc(gray.shape, window["seed"])] = 2
    cv2.watershed(image, markers)
    return experiments.region_at_seed(markers == 2, window["seed"])


def refine_pixel_clusters(window):
    """Cluster pixels by color and position, then merge those the heatmap rates as eye."""
    lab = cv2.cvtColor(window["image"], cv2.COLOR_BGR2LAB).astype(np.float32)
    height, width = lab.shape[:2]
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    samples = np.column_stack([lab.reshape(-1, 3), xs.ravel(), ys.ravel()])

    cv2.setRNGSeed(0)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.5)
    _, labels, _ = cv2.kmeans(samples, CLUSTERS, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    labels = labels.reshape(height, width)

    similarity = window["similarity"]
    limit = experiments.midpoint_threshold(similarity, window["seed"])
    keep = np.zeros(labels.shape, bool)
    for cluster in range(CLUSTERS):
        members = labels == cluster
        if members.any() and similarity[members].mean() >= limit:
            keep |= members
    keep |= labels == labels[window["seed"][1], window["seed"][0]]
    return experiments.region_at_seed(keep, window["seed"])


OUTLINES = {
    "16_dinov2_guided_filter": experiments.refine_guided,
    "17_dinov2_grabcut": experiments.refine_grabcut,
    "18_dinov2_superpixels": experiments.refine_superpixels,
    "19_dinov2_heatmap_level": experiments.refine_heatmap_level,
    "23_dinov2_watershed_canny": refine_watershed_canny,
    "24_dinov2_pixel_clustering": refine_pixel_clusters,
}
LOCATORS = {
    "20_dinov2_threshold_components": experiments.locate_threshold,
    "21_dinov2_difference_of_gaussians": experiments.locate_dog,
    "22_dinov2_blob_detector": experiments.locate_blob_detector,
}


def main():
    config = load_config()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Running on execution device: {device.upper()}")
    crops = experiments.load_crops(config, dinov2.load_model(config["model"].parent, device), device, dinov2.patch_features)
    experiments.OUTLINE_ONLY.update(OUTLINES)

    rows = [experiments.yoloe_baseline(config, crops)]
    base = [experiments.run_locator(crop, experiments.locate_peaks) for crop in crops]
    baseline = [experiments.run_refiner(c, experiments.refine_watershed, e) for c, e in zip(crops, base)]
    rows.append(experiments.save_experiment(dinov2.NAME, crops, baseline))

    for name, refiner in OUTLINES.items():
        rows.append(experiments.save_experiment(name, crops, [experiments.run_refiner(c, refiner, e) for c, e in zip(crops, base)]))
    for name, locator in LOCATORS.items():
        rows.append(experiments.save_experiment(name, crops, [experiments.run_locator(c, locator) for c in crops]))

    with open(experiments.OUTPUT / "dino_v2_summary.csv", "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
