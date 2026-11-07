"""Measure speed and accuracy of YOLOE and YOLOE + DINOv2 on data/002-other_samples/.

Speed: each image runs through both flows REPEATS times after a warm-up, and the median time per image
is kept. Flow 1 is YOLOE's animal and eye passes. Flow 2 is YOLOE's animal pass, then DINOv2 on each
cat or dog crop, then one outline variant. The DINOv2 reference eyes are built once before timing.

Accuracy:
    Animals   YOLOE's cat and dog outlines against COCO's masks, for the COCO images only.
    Eyes      Each method's saved eyes against the boxes in data/002-other_samples/eye_marks.json.

Results go to outputs/002-other_samples_performance/. Run run_yoloe_test_samples.py and
run_yoloe_and_dinov2_other_samples.py first. Usage, from anywhere:
    .venv/bin/python image-segmentation/submission/scripts/evaluate_yoloe_and_dinov2_other_samples.py
"""

import csv
import itertools
import json
import math
import os
import platform
import statistics
import subprocess
import time

import cv2
import numpy as np
import torch
from ultralytics import YOLOE

import dinov2_eye_pipeline as pipeline
import run_yoloe_and_dinov2_other_samples as other
import run_yoloe_test_samples as segment
from measure_yoloe_eyes import centroid

ROOT = segment.ROOT
CONFIG = segment.load_config()
IMAGES = CONFIG["other_source"]
YOLOE_RESULTS = CONFIG["other_output"]
DINOV2_RESULTS = ROOT.parent / "outputs" / "002-other_samples_yoloe_dinov2"
OUTPUT = ROOT.parent / "outputs" / "002-other_samples_performance"
EYE_MARKS = IMAGES / "eye_marks.json"
COCO_ANNOTATIONS = ROOT.parents[1] / "experiments" / "data" / "coco_annotations" / "instances_val2017.json"
ANIMAL_CLASSES = ("cat", "dog")
VARIANTS = {"grabcut": pipeline.refine_grabcut, "heatmap_level": pipeline.refine_heatmap_level}
METHODS = ("yoloe", "dinov2_grabcut", "dinov2_heatmap_level")
REPEATS = 3
IOU_THRESHOLDS = np.arange(0.50, 0.96, 0.05)


def sync(device):
    if device == "mps":
        torch.mps.synchronize()


def image_paths():
    return sorted(p for p in IMAGES.iterdir() if p.suffix.lower() in CONFIG["image_suffixes"])


# ----- Speed -----

def yoloe_model(device):
    CONFIG["model"].parent.mkdir(parents=True, exist_ok=True)
    os.chdir(CONFIG["model"].parent)  # The text encoder is cached beside the weights.
    model = YOLOE(CONFIG["model"].name)
    prompts = [*CONFIG["animals"], CONFIG["eye_prompt"]]
    model.set_classes(prompts, model.get_text_pe(prompts))
    return model


def animal_pass(model, image, device):
    found = segment.detections(model.predict(
        image, conf=CONFIG["animal_conf"], imgsz=CONFIG["animal_imgsz"], device=device, verbose=False)[0])
    return segment.remove_duplicates([d for d in found if d["class"] != CONFIG["eye_prompt"]],
                                     image.shape, CONFIG["duplicate_overlap"])


def eye_pass(model, image, animals, device):
    found = segment.detections(model.predict(
        image, conf=CONFIG["eye_conf"], imgsz=CONFIG["eye_imgsz"], device=device, verbose=False)[0])
    candidates = segment.remove_duplicates([d for d in found if d["class"] == CONFIG["eye_prompt"]],
                                           image.shape, CONFIG["duplicate_overlap"])
    return [e for e in candidates if segment.owner_of(e, animals, CONFIG["max_eye_distance"]) is not None]


def time_image(path, yoloe, dinov2, reference, device):
    """Return the seconds each step takes on one image."""
    image = cv2.imread(str(path))
    times = {"dinov2_features": 0.0, "locate": 0.0, "grabcut": 0.0, "heatmap_level": 0.0}

    start = time.perf_counter()
    animals = animal_pass(yoloe, image, device)
    sync(device)
    times["yoloe_animals"] = time.perf_counter() - start

    start = time.perf_counter()
    eye_pass(yoloe, image, animals, device)
    sync(device)
    times["yoloe_eyes"] = time.perf_counter() - start

    targets = [a for a in animals if a["class"] in ANIMAL_CLASSES]
    for animal in targets:
        start = time.perf_counter()
        piece = pipeline.crop(image, animal["box"])
        features, grid = pipeline.patch_features(dinov2, piece, device)
        entry = pipeline.animal_entry(piece, animal, pipeline.crop_offset(animal), features, grid, reference)
        times["dinov2_features"] += time.perf_counter() - start

        start = time.perf_counter()
        located = pipeline.locate(entry)
        times["locate"] += time.perf_counter() - start

        for name, refiner in VARIANTS.items():
            start = time.perf_counter()
            if located:
                pipeline.run_refiner(entry, refiner, located)
            times[name] += time.perf_counter() - start
    times["animals"] = len(targets)
    return times


def measure_speed(device):
    yoloe = yoloe_model(device)
    dinov2 = pipeline.load_model(CONFIG["model"].parent, device)
    reference, _ = other.eye_reference(CONFIG, dinov2, device)
    paths = image_paths()
    time_image(paths[0], yoloe, dinov2, reference, device)  # Warm-up, not counted.

    runs = {path.name: [time_image(path, yoloe, dinov2, reference, device) for _ in range(REPEATS)] for path in paths}
    rows = []
    for name, repeats in runs.items():
        row = {"image": name, "animals": repeats[0]["animals"]}
        for step in ("yoloe_animals", "yoloe_eyes", "dinov2_features", "locate", "grabcut", "heatmap_level"):
            row[f"{step}_ms"] = round(1000 * statistics.median(r[step] for r in repeats), 1)
        row["flow1_yoloe_ms"] = round(row["yoloe_animals_ms"] + row["yoloe_eyes_ms"], 1)
        base = row["yoloe_animals_ms"] + row["dinov2_features_ms"] + row["locate_ms"]
        row["flow2_grabcut_ms"] = round(base + row["grabcut_ms"], 1)
        row["flow2_heatmap_level_ms"] = round(base + row["heatmap_level_ms"], 1)
        rows.append(row)
    return rows


def speed_summary(rows):
    total_animals = sum(r["animals"] for r in rows)
    summary = []
    for flow, key in (("YOLOE", "flow1_yoloe_ms"), ("YOLOE + DINOv2, GrabCut", "flow2_grabcut_ms"),
                      ("YOLOE + DINOv2, heatmap level", "flow2_heatmap_level_ms")):
        values = [r[key] for r in rows]
        mean = statistics.mean(values)
        summary.append({"flow": flow, "images": len(rows), "mean_ms_per_image": round(mean, 1),
                        "median_ms_per_image": round(statistics.median(values), 1),
                        "slowest_ms": round(max(values), 1), "fps": round(1000 / mean, 2)})
    dinov2_ms = sum(r["dinov2_features_ms"] for r in rows) / max(total_animals, 1)
    summary.append({"flow": "DINOv2 features per animal", "images": len(rows), "mean_ms_per_image": round(dinov2_ms, 1),
                    "median_ms_per_image": "", "slowest_ms": "", "fps": ""})
    return summary


# ----- Animal accuracy -----

def filled(polygons, shape):
    mask = np.zeros(shape, np.uint8)
    for polygon in polygons:
        points = np.array(polygon, np.float32).reshape(-1, 2).round().astype(np.int32)
        cv2.fillPoly(mask, [points], 1)
    return mask.astype(bool)


def iou(a, b):
    union = (a | b).sum()
    return float((a & b).sum() / union) if union else 0.0


def average_precision(predictions, truths, threshold):
    """COCO-style 101-point AP for one class at one IoU threshold."""
    if not truths:
        return None
    used, flags = set(), []
    for prediction in sorted(predictions, key=lambda p: -p["confidence"]):
        candidates = [(iou(prediction["mask"], t["mask"]), i) for i, t in enumerate(truths)
                      if t["image"] == prediction["image"] and i not in used]
        best = max(candidates, default=(0, None))
        if best[1] is not None and best[0] >= threshold:
            used.add(best[1])
            flags.append(1)
        else:
            flags.append(0)
    tp = np.cumsum(flags)
    precision = tp / np.arange(1, len(flags) + 1) if flags else np.array([])
    recall = tp / len(truths) if flags else np.array([])
    points = []
    for level in np.linspace(0, 1, 101):
        above = precision[recall >= level] if len(recall) else []
        points.append(max(above) if len(above) else 0)
    return float(np.mean(points))


def animal_accuracy():
    coco = json.loads(COCO_ANNOTATIONS.read_text())
    names = {c["id"]: c["name"] for c in coco["categories"]}
    predictions, truths, matches = [], [], []
    for path in image_paths():
        if not path.stem.isdigit():
            continue  # Only COCO-named images have COCO masks.
        image_id = int(path.stem)
        shape = cv2.imread(str(path)).shape[:2]
        image_truths = [{"image": image_id, "class": names[a["category_id"]],
                         "mask": filled(a["segmentation"], shape)} for a in coco["annotations"]
                        if a["image_id"] == image_id and names[a["category_id"]] in ANIMAL_CLASSES and not a["iscrowd"]]
        prediction = json.loads((YOLOE_RESULTS / f"{path.stem}.json").read_text())
        image_predictions = [{"image": image_id, "class": a["class"], "confidence": a["confidence"],
                              "mask": filled([a["polygon"]], shape)} for a in prediction["animals"]]
        truths += image_truths
        predictions += image_predictions

        # Greedy matching at IoU 0.5, most confident first, labels ignored.
        used = set()
        for p in sorted(image_predictions, key=lambda p: -p["confidence"]):
            best = max(((iou(p["mask"], t["mask"]), i) for i, t in enumerate(image_truths) if i not in used), default=(0, None))
            if best[1] is not None and best[0] >= 0.5:
                used.add(best[1])
                matches.append({"image": path.stem, "predicted": p["class"], "truth": image_truths[best[1]]["class"],
                                "iou": round(best[0], 3)})
            else:
                matches.append({"image": path.stem, "predicted": p["class"], "truth": "", "iou": round(best[0], 3)})
        for i, t in enumerate(image_truths):
            if i not in used:
                matches.append({"image": path.stem, "predicted": "", "truth": t["class"], "iou": ""})

    tp = sum(1 for m in matches if m["predicted"] and m["truth"])
    fp = sum(1 for m in matches if m["predicted"] and not m["truth"])
    fn = sum(1 for m in matches if m["truth"] and not m["predicted"])
    labelled = sum(1 for m in matches if m["predicted"] and m["predicted"] == m["truth"])
    precision, recall = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    ap = {}
    for threshold in IOU_THRESHOLDS:
        per_class = [average_precision([p for p in predictions if p["class"] == c], [t for t in truths if t["class"] == c],
                                       threshold) for c in ANIMAL_CLASSES]
        per_class = [v for v in per_class if v is not None]
        ap[round(float(threshold), 2)] = float(np.mean(per_class))
    summary = {"coco_animals": len(truths), "predicted_animals": len(predictions), "tp_iou50": tp, "fp_iou50": fp,
               "fn_iou50": fn, "correct_label": labelled, "precision": round(precision, 3), "recall": round(recall, 3),
               "f1": round(2 * precision * recall / max(precision + recall, 1e-9), 3),
               "mean_iou_of_matches": round(statistics.mean(m["iou"] for m in matches if m["predicted"] and m["truth"]), 3),
               "mask_ap50": round(ap[0.5], 3), "mask_ap75": round(ap[0.75], 3),
               "mask_ap50_95": round(float(np.mean(list(ap.values()))), 3)}
    return summary, matches


# ----- Eye accuracy -----

def method_animals(method, stem):
    if method == "yoloe":
        prediction = json.loads((YOLOE_RESULTS / f"{stem}.json").read_text())
        animals = []
        for index, animal in enumerate(prediction["animals"]):
            if animal["class"] not in ANIMAL_CLASSES:
                continue
            owned = sorted((e for e in prediction["eyes"] if e["animal_index"] == index), key=lambda e: -e["confidence"])[:2]
            animals.append([{"center": centroid([tuple(v) for v in e["polygon"]]), "polygon": e["polygon"]} for e in owned])
        return animals
    variant = method.removeprefix("dinov2_")
    result = json.loads((DINOV2_RESULTS / variant / f"{stem}.json").read_text())
    return [[e for e in a["eyes"] if e["center"] is not None] for a in result["animals"]]


def box_center(box):
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def box_iou(a, b):
    width, height = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    overlap = width * height
    return overlap / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap)


def inside(point, box):
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def eye_accuracy(marks, method, images):
    """Score one method's eyes against the marks on the listed images."""
    hits = false = total = ignored = 0
    center_errors, ious, distance_errors, distance_percent, right_errors = [], [], [], [], []
    for stem in images:
        marked = marks["images"][stem]  # [{"animal": name, "eyes": [box, ...]}]
        skip = marks.get("ignore", {}).get(stem, [])
        boxes = [(a_index, box) for a_index, a in enumerate(marked) for box in a["eyes"]]
        total += len(boxes)
        free = list(range(len(boxes)))
        measured_right = {}
        for animal in method_animals(method, stem):
            matched = []
            for eye in animal:
                x, y = eye["center"]
                if any(inside((x, y), box) for box in skip):
                    ignored += 1
                    continue
                index = next((i for i in free if boxes[i][1][0] <= x <= boxes[i][1][2] and boxes[i][1][1] <= y <= boxes[i][1][3]), None)
                if index is None:
                    false += 1
                    continue
                free.remove(index)
                hits += 1
                matched.append((boxes[index][0], eye["center"], box_center(boxes[index][1])))
                center_errors.append(math.dist(eye["center"], box_center(boxes[index][1])))
                if eye.get("polygon"):
                    points = np.array(eye["polygon"])
                    ious.append(box_iou((*points.min(0), *points.max(0)), boxes[index][1]))
            if len(matched) == 2 and matched[0][0] == matched[1][0]:
                predicted = math.dist(matched[0][1], matched[1][1])
                true = math.dist(matched[0][2], matched[1][2])
                distance_errors.append(abs(predicted - true))
                distance_percent.append(100 * abs(predicted - true) / true)
                right_predicted = min((m[1] for m in matched), key=lambda c: c[0])
                right_true = min((m[2] for m in matched), key=lambda c: c[0])
                measured_right[matched[0][0]] = (right_predicted, right_true)
        for a, b in itertools.combinations(sorted(measured_right), 2):
            right_errors.append(abs(math.dist(measured_right[a][0], measured_right[b][0])
                                    - math.dist(measured_right[a][1], measured_right[b][1])))
    precision, recall = hits / max(hits + false, 1), hits / max(total, 1)

    def mean(values):
        return round(statistics.mean(values), 2) if values else ""

    return {"method": method, "images": len(images), "marked_eyes": total, "hits": hits, "false_eyes": false,
            "missed": total - hits, "ignored_eyes": ignored, "precision": round(precision, 3), "recall": round(recall, 3),
            "f1": round(2 * precision * recall / max(precision + recall, 1e-9), 3),
            "center_error_px": mean(center_errors), "box_iou": mean(ious),
            "animals_measured": len(distance_errors), "distance_mae_px": mean(distance_errors),
            "distance_mape_percent": mean(distance_percent), "right_eye_pairs": len(right_errors),
            "right_eye_mae_px": mean(right_errors)}


def review_sheet(marks, path, height=320, per_row=4):
    """Draw the eye marks (yellow) and ignore boxes (grey) on each image, for a person to check."""
    tiles = []
    for image_path in image_paths():
        canvas = cv2.imread(str(image_path))
        for animal in marks["images"].get(image_path.stem, []):
            for x1, y1, x2, y2 in animal["eyes"]:
                cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 255, 255), 1)
        for x1, y1, x2, y2 in marks.get("ignore", {}).get(image_path.stem, []):
            cv2.rectangle(canvas, (x1, y1), (x2, y2), (160, 160, 160), 1)
        cv2.putText(canvas, image_path.stem, (5, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        tiles.append(cv2.resize(canvas, (round(canvas.shape[1] * height / canvas.shape[0]), height)))
    rows = [np.hstack(tiles[i:i + per_row]) for i in range(0, len(tiles), per_row)]
    width = max(r.shape[1] for r in rows)
    cv2.imwrite(str(path), np.vstack([cv2.copyMakeBorder(r, 0, 4, 0, width - r.shape[1], cv2.BORDER_CONSTANT) for r in rows]))


def write_csv(path, rows):
    with open(path, "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)


def hardware():
    chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    return {"chip": chip or platform.processor(), "torch": torch.__version__, "device": "mps" if torch.backends.mps.is_available() else "cpu"}


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    info = hardware()
    print(f"Running on execution device: {info['device'].upper()} ({info['chip']})")

    speed = measure_speed(info["device"])
    write_csv(OUTPUT / "speed_per_image.csv", speed)
    write_csv(OUTPUT / "speed_summary.csv", speed_summary(speed))

    summary, matches = animal_accuracy()
    write_csv(OUTPUT / "animal_accuracy.csv", [summary])
    write_csv(OUTPUT / "animal_matches.csv", matches)

    marks = json.loads(EYE_MARKS.read_text())
    stems = [p.stem for p in image_paths()]
    new = [s for s in stems if s.isdigit()]
    rows = [{"images_set": "9 new COCO images", **eye_accuracy(marks, m, new)} for m in METHODS]
    rows += [{"images_set": "all 12 images", **eye_accuracy(marks, m, stems)} for m in METHODS]
    review_sheet(marks, OUTPUT / "eye_marks_review.jpg")
    write_csv(OUTPUT / "eye_accuracy.csv", rows)
    (OUTPUT / "environment.json").write_text(json.dumps({**info, "repeats": REPEATS, "eye_marks_status": marks["status"]}, indent=2))

    for row in speed_summary(speed):
        print(row)
    print(summary)
    for row in rows:
        print(row)
    print(f"Results saved to: {OUTPUT}")


if __name__ == "__main__":
    main()
