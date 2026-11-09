"""Export the measurements and figures used by the submission answers.

For YOLOE and both YOLOE + DINOv2 variants, on the sample images and the other images,
this measures each animal's eye distance and the distance between the right eyes of every animal
pair. Right means the eye with the smaller x, which is the animal's own right eye when it faces the
camera. On the sample images, it also compares the distances with the reviewed eye marks.

Measurements go to outputs/measurements/ and figures to data/figures/. Run run_yoloe_test_samples.py, run_yoloe_dinov2_test_samples.py and
run_yoloe_and_dinov2_other_samples.py first. Usage, from anywhere:
    .venv/bin/python image-segmentation/submission/scripts/export_submission_files.py
"""

import csv
import itertools
import json
import math
import shutil

import cv2
import numpy as np

from measure_yoloe_eyes import centroid
from run_yoloe_test_samples import ROOT, load_config

MEASUREMENTS = ROOT.parent / "outputs" / "measurements"
FIGURES = ROOT.parent / "data" / "figures"
CONFIG = load_config()
SAMPLE_IMAGES = CONFIG["source"]
SAMPLE_YOLOE = CONFIG["output"]
SAMPLE_DINOV2 = ROOT.parent / "outputs" / "001-test_samples_yoloe_dinov2"
OTHER_IMAGES = CONFIG["other_source"]
OTHER_YOLOE = CONFIG["other_output"]
OTHER_DINOV2 = ROOT.parent / "outputs" / "002-other_samples_yoloe_dinov2"
ANIMALS_MEASURED = ("cat", "dog")
METHODS = ("yoloe", "dinov2_grabcut", "dinov2_heatmap_level")
TEST_FOLDERS = {"dinov2_grabcut": "grabcut", "dinov2_heatmap_level": "heatmap_level"}
ANIMAL_COLOR = (0, 200, 0)
EYE_COLOR = (0, 0, 255)
TILE_HEIGHT = 240
TILES_PER_ROW = 5

# Eye boxes (x1, y1, x2, y2), estimated by Claude Code from zoomed views, then reviewed by a person.
HAND_MARKS = {
    ("001-coco-sample", "dog 1"): [(205, 208, 224, 226), (246, 210, 272, 227)],
    ("001-coco-sample", "cat 2"): [(205, 37, 212, 43), (217, 35, 223, 41)],
    ("002-coco-sample", "cat 1"): [(326, 181, 358, 206), (401, 178, 435, 205)],
    ("002-coco-sample", "dog 2"): [(301, 389, 340, 421), (387, 386, 411, 425)],
    ("003-coco-sample", "dog 1"): [(338, 109, 357, 133), (381, 117, 411, 143)],
    ("003-coco-sample", "cat 2"): [(151, 308, 173, 326), (192, 310, 213, 326)],
}


def as_eye(center, polygon):
    return {"center": center, "polygon": polygon}


def yoloe_image(path):
    """Read a YOLOE JSON, keeping the two most confident eyes on each animal."""
    prediction = json.loads(path.read_text())
    animals, number = [], 0
    for index, animal in enumerate(prediction["animals"]):
        if animal["class"] in ANIMALS_MEASURED:
            number += 1
            label = f"{animal['class']} {number}"
        else:
            label = animal["class"]
        owned = sorted((e for e in prediction["eyes"] if e["animal_index"] == index), key=lambda e: -e["confidence"])[:2]
        eyes = [as_eye(list(centroid([tuple(v) for v in e["polygon"]])), e["polygon"]) for e in owned]
        animals.append({"label": label, "class": animal["class"], "polygon": animal["polygon"], "eyes": eyes})
    return animals


def outlines_from(animals, reference):
    """Add YOLOE's animal outlines, which the DINOv2 methods start from, by matching order."""
    targets = [a for a in reference if a["class"] in ANIMALS_MEASURED]
    return [{**animal, "label": target["label"], "class": target["class"], "polygon": target["polygon"]}
            for animal, target in zip(animals, targets)]


def dinov2_method(folder, method, stem, reference):
    result = json.loads((folder / TEST_FOLDERS[method] / f"{stem}.json").read_text())
    animals = [{"eyes": [as_eye(e["center"], e["polygon"]) for e in a["eyes"] if e["center"] is not None]}
               for a in result["animals"]]
    return outlines_from(animals, reference)


def load_dataset():
    """Return {dataset: {method: {image stem: animals}}}, with each image's file path."""
    data = {"samples": {m: {} for m in METHODS}, "other_samples": {m: {} for m in METHODS}}
    images = {"samples": {}, "other_samples": {}}
    for path in sorted(SAMPLE_IMAGES.glob("*.jpg")):
        reference = yoloe_image(SAMPLE_YOLOE / f"{path.stem}.json")
        images["samples"][path.stem] = path
        data["samples"]["yoloe"][path.stem] = reference
        for method in TEST_FOLDERS:
            data["samples"][method][path.stem] = dinov2_method(SAMPLE_DINOV2, method, path.stem, reference)
    for path in sorted(OTHER_IMAGES.glob("*.jpg")):
        reference = yoloe_image(OTHER_YOLOE / f"{path.stem}.json")
        images["other_samples"][path.stem] = path
        data["other_samples"]["yoloe"][path.stem] = reference
        for method in TEST_FOLDERS:
            data["other_samples"][method][path.stem] = dinov2_method(OTHER_DINOV2, method, path.stem, reference)
    return data, images


def eye_rows(dataset, method, stem, animals):
    rows = []
    for animal in animals:
        centers = sorted(e["center"] for e in animal["eyes"])
        measured = len(centers) == 2
        rows.append({
            "dataset": dataset, "image": stem, "animal": animal["label"], "method": method, "eyes_found": len(centers),
            "right_eye_x": round(centers[0][0], 2) if measured else "", "right_eye_y": round(centers[0][1], 2) if measured else "",
            "left_eye_x": round(centers[1][0], 2) if measured else "", "left_eye_y": round(centers[1][1], 2) if measured else "",
            "distance_px": round(math.dist(*centers), 2) if measured else "",
        })
    return rows


def right_eye_rows(dataset, method, stem, animals):
    measurable = [(a["label"], sorted(e["center"] for e in a["eyes"])[0]) for a in animals if len(a["eyes"]) == 2]
    return [{
        "dataset": dataset, "image": stem, "animal_a": a, "animal_b": b, "method": method,
        "a_right_eye_x": round(p[0], 2), "a_right_eye_y": round(p[1], 2),
        "b_right_eye_x": round(q[0], 2), "b_right_eye_y": round(q[1], 2),
        "distance_px": round(math.dist(p, q), 2),
    } for (a, p), (b, q) in itertools.combinations(measurable, 2)]


def box_center(box):
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def validation_measurements(samples):
    """Pair each measured distance with the distance between the reviewed eye marks."""
    pairs = []
    for method in METHODS:
        for stem, animals in samples[method].items():
            right_eyes, marked_right = {}, {}
            for animal in animals:
                marks = HAND_MARKS.get((stem, animal["label"]))
                if marks is None:
                    continue
                centers = sorted(box_center(m) for m in marks)
                marked_right[animal["label"]] = centers[0]
                if len(animal["eyes"]) == 2:
                    measured = sorted(e["center"] for e in animal["eyes"])
                    right_eyes[animal["label"]] = measured[0]
                    predicted, manual = math.dist(*measured), math.dist(*centers)
                    pairs.append((method, "both_eyes", stem, animal["label"], predicted, manual))
            for a, b in itertools.combinations(sorted(right_eyes), 2):
                predicted, manual = math.dist(right_eyes[a], right_eyes[b]), math.dist(marked_right[a], marked_right[b])
                pairs.append((method, "right_eyes", stem, f"{a} to {b}", predicted, manual))
    return pairs


def validation_rows(pairs):
    return [{"method": method, "measurement": measurement, "image": stem, "item": item,
             "predicted_px": round(predicted, 2), "marked_px": round(manual, 2),
             "error_px": round(abs(predicted - manual), 2),
             "error_percent": round(100 * abs(predicted - manual) / manual, 1)}
            for method, measurement, stem, item, predicted, manual in pairs]


def validation_summary(pairs):
    """Return the MAE and MAPE of each method on each measurement."""
    rows = []
    for measurement, method in itertools.product(("both_eyes", "right_eyes"), METHODS):
        errors = [(abs(p[4] - p[5]), abs(p[4] - p[5]) / p[5]) for p in pairs if p[0] == method and p[1] == measurement]
        rows.append({"measurement": measurement, "method": method, "count": len(errors),
                     "mae_px": round(sum(e[0] for e in errors) / len(errors), 2),
                     "mape_percent": round(100 * sum(e[1] for e in errors) / len(errors), 1)})
    return rows


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)


def draw(path, animals):
    canvas = cv2.imread(str(path))
    for animal in animals:
        polygon = np.array(animal["polygon"], np.int32)
        cv2.polylines(canvas, [polygon], True, ANIMAL_COLOR, 2, cv2.LINE_AA)
        x, y = polygon.min(0)
        cv2.putText(canvas, animal["label"], (int(x) + 3, max(int(y) + 16, 16)), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    ANIMAL_COLOR, 1, cv2.LINE_AA)
    for animal in animals:
        for eye in animal["eyes"]:
            cv2.polylines(canvas, [np.array(eye["polygon"]).round().astype(np.int32)], True, EYE_COLOR, 1, cv2.LINE_AA)
    return canvas


def overview(canvases, path):
    tiles = [cv2.resize(c, (round(c.shape[1] * TILE_HEIGHT / c.shape[0]), TILE_HEIGHT)) for c in canvases]
    rows = [np.hstack(tiles[i:i + TILES_PER_ROW]) for i in range(0, len(tiles), TILES_PER_ROW)]
    width = max(r.shape[1] for r in rows)
    cv2.imwrite(str(path), np.vstack([cv2.copyMakeBorder(r, 0, 4, 0, width - r.shape[1], cv2.BORDER_CONSTANT) for r in rows]))


def main():
    data, images = load_dataset()
    eyes, rights = [], []
    for dataset, methods in data.items():
        for method, results in methods.items():
            for stem, animals in results.items():
                eyes.extend(eye_rows(dataset, method, stem, animals))
                rights.extend(right_eye_rows(dataset, method, stem, animals))
    write_csv(MEASUREMENTS / "eye_distances.csv", eyes)
    write_csv(MEASUREMENTS / "right_eye_distances.csv", rights)
    pairs = validation_measurements(data["samples"])
    write_csv(MEASUREMENTS / "validation_against_marks.csv", validation_rows(pairs))
    write_csv(MEASUREMENTS / "validation_summary.csv", validation_summary(pairs))

    for method in METHODS:
        folder = FIGURES / "samples" / method
        folder.mkdir(parents=True, exist_ok=True)
        for stem, path in images["samples"].items():
            cv2.imwrite(str(folder / f"{stem}.jpg"), draw(path, data["samples"][method][stem]))
        overview_folder = FIGURES / "other_samples"
        overview_folder.mkdir(parents=True, exist_ok=True)
        overview([draw(path, data["other_samples"][method][stem]) for stem, path in images["other_samples"].items()],
                 overview_folder / f"{method}.jpg")

    shutil.copy(OTHER_DINOV2 / "summary.csv", MEASUREMENTS / "other_samples_method_comparison.csv")
    shutil.copy(OTHER_DINOV2 / "heatmaps" / "overview.jpg", FIGURES / "other_samples" / "dinov2_heatmaps.jpg")
    print(f"Saved {len(eyes)} eye rows and {len(rights)} right-eye rows to: {MEASUREMENTS}")


if __name__ == "__main__":
    main()
