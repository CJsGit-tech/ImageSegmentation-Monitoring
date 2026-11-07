"""Export a black-and-white version of every heatmap in results/heatmaps/dino_v1/ to results/binary_heatmaps/.

In each heatmap, the brightest TOP_PERCENT of pixels become white and the rest black.

Run dino_heatmap.py first. Usage, from anywhere:
    .venv/bin/python image-segmentation/binary_heatmap.py
"""

from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
HEATMAPS = ROOT / "results" / "heatmaps" / "dino_v1"
OUTPUT = ROOT / "results" / "binary_heatmaps"
TOP_PERCENT = 1.5


def heat_levels(heatmap):
    """Undo the INFERNO colors, returning each pixel's original 0-255 level."""
    colors = cv2.applyColorMap(np.arange(256, dtype=np.uint8), cv2.COLORMAP_INFERNO).reshape(256, 3)
    codes = np.zeros(1 << 24, np.uint8)
    codes[(colors[:, 0].astype(int) << 16) | (colors[:, 1].astype(int) << 8) | colors[:, 2]] = np.arange(256)
    pixels = heatmap.astype(int)
    return codes[(pixels[:, :, 0] << 16) | (pixels[:, :, 1] << 8) | pixels[:, :, 2]]


def main():
    paths = sorted(HEATMAPS.glob("*.png"))
    if not paths:
        raise SystemExit(f"No heatmaps found in {HEATMAPS}. Run dino_heatmap.py first.")
    OUTPUT.mkdir(exist_ok=True)
    for path in paths:
        levels = heat_levels(cv2.imread(str(path)))
        limit = np.percentile(levels, 100 - TOP_PERCENT)
        binary = (levels >= limit).astype(np.uint8) * 255
        cv2.imwrite(str(OUTPUT / path.name), binary)
        print(f"{path.name}: level {limit:.0f} and above, {100 * (binary > 0).mean():.2f}% white")
    print(f"Binary images saved to: {OUTPUT}")


if __name__ == "__main__":
    main()
