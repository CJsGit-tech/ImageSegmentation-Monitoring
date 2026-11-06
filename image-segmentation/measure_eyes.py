"""Measure eye distances from a prediction JSON written by segment_animals_eyes.py.

Modes:
    both-eyes       Distance between the two eyes of each animal.
    right-eye-pair  Distance between the right eyes of every pair of animals.

Distances are in image pixels. Right means the animal's own right eye, which is the one
on the left of the image when the animal faces the camera.

The mode is set by both_eyes in config.yaml. Usage:
    .venv/bin/python image-segmentation/measure_eyes.py \
        --json image-segmentation/output/002-coco-sample.json
"""

import argparse
import itertools
import json
import math
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, required=True, help="prediction output JSON path")
    return parser.parse_args()


def centroid(polygon):
    """Return a polygon's area centroid, using the shoelace formula."""
    doubled_area = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        cross = x1 * y2 - x2 * y1
        doubled_area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if doubled_area == 0:  # All points on one line, so the polygon has no area.
        return (sum(p[0] for p in polygon) / len(polygon), sum(p[1] for p in polygon) / len(polygon))
    return cx / (3 * doubled_area), cy / (3 * doubled_area)


def distance(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def animals_with_eyes(prediction):
    """Return every animal with a label and its eyes, ordered by x in the image.

    An animal with more than two detected eyes keeps its two most confident.
    """
    animals = []
    for index, animal in enumerate(prediction["animals"]):
        eyes = [e for e in prediction["eyes"] if e["animal_index"] == index]
        eyes = sorted(eyes, key=lambda e: -e["confidence"])[:2]
        centres = sorted(centroid(e["polygon"]) for e in eyes)
        animals.append({"label": f"{animal['class']} #{index}", "eyes": centres, "found": len(eyes)})
    return animals


def show_calculation(name_a, a, name_b, b):
    """Print one distance with its working, and return the distance."""
    result = distance(a, b)
    print(f"  {name_a} = ({a[0]:.2f}, {a[1]:.2f})")
    print(f"  {name_b} = ({b[0]:.2f}, {b[1]:.2f})")
    print(f"  d = sqrt(({b[0]:.2f} - {a[0]:.2f})^2 + ({b[1]:.2f} - {a[1]:.2f})^2) = {result:.2f} px")
    return result


def measure_both_eyes(animals):
    for animal in animals:
        print(animal["label"])
        if len(animal["eyes"]) < 2:
            print(f"  cannot measure: {animal['found']} eye(s) detected, 2 needed")
            continue
        right, left = animal["eyes"]  # Image order: the animal's right eye comes first.
        show_calculation("right eye", right, "left eye", left)


def measure_right_eye_pairs(animals):
    measurable = [a for a in animals if len(a["eyes"]) == 2]
    for animal in animals:
        if len(animal["eyes"]) < 2:
            print(f"{animal['label']}: skipped, {animal['found']} eye(s) detected and 2 are needed to tell right from left")
    if len(measurable) < 2:
        print("cannot measure: fewer than 2 animals have both eyes detected")
        return
    for first, second in itertools.combinations(measurable, 2):
        print(f"{first['label']} to {second['label']}")
        show_calculation(f"{first['label']} right eye", first["eyes"][0],
                         f"{second['label']} right eye", second["eyes"][0])


MODES = {"both-eyes": measure_both_eyes, "right-eye-pair": measure_right_eye_pairs}


def main():
    args = parse_args()
    mode = yaml.safe_load(CONFIG_PATH.read_text())["both_eyes"]
    if mode not in MODES:
        raise SystemExit(f"Unknown mode {mode!r} in {CONFIG_PATH.name}. Choose from: {', '.join(MODES)}")
    prediction = json.loads(args.json.read_text())
    print(f"Image: {prediction['image']}  Mode: {mode}")
    MODES[mode](animals_with_eyes(prediction))


if __name__ == "__main__":
    main()
