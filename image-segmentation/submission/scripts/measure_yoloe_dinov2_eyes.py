"""Measure eye distances from a YOLOE + DINOv2 result JSON.

Use a JSON file from the grabcut/ or heatmap_level/ folder in outputs/001-test_samples_yoloe_dinov2/
or outputs/002-other_samples_yoloe_dinov2/. The modes and output match measure_yoloe_eyes.py:
    both-eyes       Distance between the two eyes of each animal.
    right-eye-pair  Distance between the right eyes of every pair of animals.

Distances are in image pixels. Right means the animal's own right eye, which is the one on the left
of the image when the animal faces the camera.

The mode is set by both_eyes in config.yaml. Usage:
    .venv/bin/python image-segmentation/submission/scripts/measure_yoloe_dinov2_eyes.py \
        --json image-segmentation/submission/outputs/001-test_samples_yoloe_dinov2/grabcut/002-coco-sample.json
"""

import argparse
import json
from pathlib import Path

import yaml

from measure_yoloe_eyes import CONFIG_PATH, MODES


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, required=True, help="YOLOE + DINOv2 result JSON path")
    return parser.parse_args()


def animals_with_eyes(result):
    """Return every cat or dog with a label and its eye centers, ordered by x in the image.

    The eye centers were already calculated from the outlines when the result was saved.
    """
    animals = []
    for index, animal in enumerate(result["animals"]):
        eyes = [e for e in animal["eyes"] if e["center"] is not None][:2]
        centres = sorted(tuple(e["center"]) for e in eyes)
        animals.append({"label": f"{animal['class']} #{index}", "eyes": centres, "found": len(eyes)})
    return animals


def main():
    args = parse_args()
    mode = yaml.safe_load(CONFIG_PATH.read_text())["both_eyes"]
    if mode not in MODES:
        raise SystemExit(f"Unknown mode {mode!r} in {CONFIG_PATH.name}. Choose from: {', '.join(MODES)}")
    result = json.loads(args.json.read_text())
    print(f"Image: {result['image']}  Variant: {args.json.parent.name}  Mode: {mode}")
    MODES[mode](animals_with_eyes(result))


if __name__ == "__main__":
    main()
