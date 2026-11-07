"""Compare YOLOE with two DINOv2 eye methods on the COCO images in data/coco_cats_dogs_38/.

YOLOE alone runs the same way as segment_animals_eyes.py. For the DINOv2 methods, YOLOE first finds
each cat or dog, DINOv2 heatmaps its crop, local peaks locate the eyes, and GrabCut or the heatmap
level traces each outline. Their reference eyes are YOLOE's outlines in data/, so no test image
influences the references or settings.

There are no reviewed eye marks for these images, so summary.csv counts outputs and measures
agreement with YOLOE, not accuracy. overview.jpg in each folder is for checking by eye.

Results go to results/coco_38_test/. Run segment_animals_eyes.py and select_coco_images.py first.
Usage, from anywhere:
    .venv/bin/python image-segmentation/run_coco_38_test.py
"""

import csv
import json
import math

import cv2
import numpy as np
import torch

import run_dinov2_watershed as dinov2
import run_eye_experiments as experiments
import segment_animals_eyes as segment
from crop_animals import CROP_CLASSES, crop as cut_box
from dino_heatmap import MIN_EYE_COVER
from measure_eyes import centroid

ROOT = segment.ROOT
IMAGES = ROOT / "data" / "coco_cats_dogs_38"
OUTPUT = ROOT / "results" / "coco_38_test"
METHODS = {"dinov2_grabcut": experiments.refine_grabcut, "dinov2_heatmap_level": experiments.refine_heatmap_level}
MATCH_DISTANCE = 30     # Pixels. Nearest eyes further apart than this aren't the same eye.
TILE_HEIGHT = 240
TILES_PER_ROW = 5


def eye_reference(config, model, device):
    """Average DINOv2's features over YOLOE's eye outlines in the sample images in data/."""
    patches = []
    for path in sorted(p for p in config["source"].iterdir() if p.suffix.lower() in config["image_suffixes"]):
        prediction = json.loads((config["output"] / f"{path.stem}.json").read_text())
        image = cv2.imread(str(path))
        for animal in [a for a in prediction["animals"] if a["class"] in CROP_CLASSES]:
            x0, y0 = [max(math.floor(v), 0) for v in animal["box"][:2]]
            piece = cut_box(image, animal["box"])
            mask = np.zeros(piece.shape[:2], np.uint8)
            for eye in prediction["eyes"]:
                cv2.fillPoly(mask, [np.array(eye["polygon"], np.int32) - [x0, y0]], 1)
            features, grid = dinov2.patch_features(model, piece, device)
            cover = cv2.resize(mask.astype(np.float32), grid[::-1], interpolation=cv2.INTER_AREA).ravel()
            patches.append(features[cover > MIN_EYE_COVER])
    reference = np.vstack(patches).mean(0)
    return reference / np.linalg.norm(reference), sum(len(p) for p in patches)


def animal_entry(image, animal, model, device, reference):
    """Build the crop entry the experiment functions expect, with no marks."""
    x0, y0 = [max(math.floor(v), 0) for v in animal["box"][:2]]
    piece = cut_box(image, animal["box"])
    mask = np.zeros(piece.shape[:2], np.uint8)
    cv2.fillPoly(mask, [np.array(animal["polygon"], np.int32) - [x0, y0]], 1)
    features, grid = dinov2.patch_features(model, piece, device)
    similarity = (features @ reference).reshape(grid).astype(np.float32)
    return {
        "image": piece, "offset": np.array([x0, y0]), "mask": mask, "grid": grid, "similarity": similarity,
        "full_similarity": cv2.resize(similarity, piece.shape[1::-1], interpolation=cv2.INTER_CUBIC),
        "grid_mask": cv2.resize(mask.astype(np.float32), grid[::-1], interpolation=cv2.INTER_AREA) > 0.5,
        "long_side": max(piece.shape[:2]), "marks": [],
    }


def locate(entry):
    if not entry["grid_mask"].any():
        return []
    return [{"center": list(e["center"]), "polygon": None, "score": e["score"]}
            for e in experiments.select_eyes(entry, experiments.locate_peaks(entry))]


def to_image(entry, eyes, located):
    """Move crop results to image coordinates, keeping each eye's peak score."""
    output = []
    for eye, start in zip(eyes, located):
        output.append({
            "center": None if eye["center"] is None else [round(float(v + o), 2) for v, o in zip(eye["center"], entry["offset"])],
            "polygon": None if eye["polygon"] is None else (np.array(eye["polygon"]) + entry["offset"]).round(1).tolist(),
            "peak_score": round(float(start["score"]), 3),
        })
    return output


def draw(path, animals, folder):
    canvas = cv2.imread(str(path))
    for animal in animals:
        for eye in animal["eyes"]:
            if eye["polygon"] is not None:
                cv2.polylines(canvas, [np.array(eye["polygon"]).round().astype(np.int32)], True, (0, 0, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(folder / path.name), canvas)
    return canvas


def overview(canvases, path):
    tiles = [cv2.resize(c, (round(c.shape[1] * TILE_HEIGHT / c.shape[0]), TILE_HEIGHT)) for c in canvases]
    rows = [np.hstack(tiles[i:i + TILES_PER_ROW]) for i in range(0, len(tiles), TILES_PER_ROW)]
    width = max(row.shape[1] for row in rows)
    cv2.imwrite(str(path), np.vstack([cv2.copyMakeBorder(r, 0, 4, 0, width - r.shape[1], cv2.BORDER_CONSTANT) for r in rows]))


def filled(polygon, shape):
    mask = np.zeros(shape, np.uint8)
    cv2.fillPoly(mask, [np.array(polygon).round().astype(np.int32)], 1)
    return mask


def compare_with_yoloe(name, yoloe, results, shapes):
    """Count a method's eyes, and compare them with YOLOE's eyes on the same animals."""
    ious, offsets, distances = [], [], []
    matched = extra = total = two_eyes = failed = 0
    for file_name, animals in results.items():
        for number, animal in enumerate(animals):
            usable = [e for e in animal["eyes"] if e["center"] is not None]
            failed += len(animal["eyes"]) - len(usable)
            total += len(usable)
            two_eyes += len(usable) == 2
            references = yoloe[file_name][number]["eyes"]
            used = set()
            for reference in references:
                if not usable:
                    continue
                center = centroid([tuple(v) for v in reference["polygon"]])
                index = min(range(len(usable)), key=lambda i: math.dist(usable[i]["center"], center))
                offset = math.dist(usable[index]["center"], center)
                if offset <= MATCH_DISTANCE and index not in used:
                    used.add(index)
                    matched += 1
                    offsets.append(offset)
                    a, b = filled(usable[index]["polygon"], shapes[file_name]), filled(reference["polygon"], shapes[file_name])
                    ious.append((a & b).sum() / max((a | b).sum(), 1))
            extra += len(usable) - len(used)
            if len(references) == 2 and len(usable) == 2:
                first, second = [centroid([tuple(v) for v in r["polygon"]]) for r in references]
                distances.append(abs(math.dist(usable[0]["center"], usable[1]["center"]) - math.dist(first, second)))
    return {"method": name, "animals_with_two_eyes": two_eyes, "eyes_output": total, "no_outline": failed,
            "matched_yoloe_eyes": matched, "eyes_without_yoloe_match": extra,
            "mean_polygon_iou": round(float(np.mean(ious)), 3) if ious else None,
            "median_polygon_iou": round(float(np.median(ious)), 3) if ious else None,
            "mean_center_offset_px": round(float(np.mean(offsets)), 2) if offsets else None,
            "mean_distance_difference_px": round(float(np.mean(distances)), 2) if distances else None,
            "distance_animals": len(distances)}


def main():
    config = segment.load_config()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    paths = sorted(IMAGES.glob("*.jpg"))
    if not paths:
        raise SystemExit(f"No images found in {IMAGES}. Run select_coco_images.py first.")

    # YOLOE alone, with the same code and settings as segment_animals_eyes.py.
    yoloe_folder = OUTPUT / "yoloe"
    original_load = segment.load_config
    segment.load_config = lambda: {**original_load(), "source": IMAGES, "output": yoloe_folder}
    segment.main()
    segment.load_config = original_load

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    config = segment.load_config()
    model = dinov2.load_model(config["model"].parent, device)
    reference, count = eye_reference(config, model, device)
    print(f"Reference: {count} eye patches from the sample images")

    yoloe, shapes, method_results = {}, {}, {name: {} for name in METHODS}
    canvases = {name: [] for name in METHODS}
    yoloe_canvases = []
    for path in paths:
        prediction = json.loads((yoloe_folder / f"{path.stem}.json").read_text())
        image = cv2.imread(str(path))
        shapes[path.name] = image.shape[:2]
        indexed = [(i, a) for i, a in enumerate(prediction["animals"]) if a["class"] in CROP_CLASSES]
        yoloe[path.name] = [{"class": a["class"], "eyes": sorted((e for e in prediction["eyes"] if e["animal_index"] == i),
                                                                 key=lambda e: -e["confidence"])[:2]} for i, a in indexed]
        for animals_name in METHODS:
            method_results[animals_name][path.name] = []
        for _, animal in indexed:
            entry = animal_entry(image, animal, model, device, reference)
            located = locate(entry)
            for name, refiner in METHODS.items():
                eyes = experiments.run_refiner(entry, refiner, located) if located else []
                method_results[name][path.name].append(
                    {"class": animal["class"], "box": animal["box"], "eyes": to_image(entry, eyes, located)})
        yoloe_canvases.append(draw(path, [{"eyes": a["eyes"]} for a in yoloe[path.name]], yoloe_folder))

    rows = []
    overview(yoloe_canvases, yoloe_folder / "overview.jpg")
    yoloe_as_results = {f: [{"eyes": [{"center": list(centroid([tuple(v) for v in e["polygon"]])), "polygon": e["polygon"]}
                                      for e in a["eyes"]]} for a in animals] for f, animals in yoloe.items()}
    rows.append(compare_with_yoloe("yoloe", yoloe, yoloe_as_results, shapes))
    for name in METHODS:
        folder = OUTPUT / name
        folder.mkdir(exist_ok=True)
        for path in paths:
            animals = method_results[name][path.name]
            canvases[name].append(draw(path, animals, folder))
            (folder / f"{path.stem}.json").write_text(json.dumps({"image": path.name, "animals": animals}, indent=2))
        overview(canvases[name], folder / "overview.jpg")
        rows.append(compare_with_yoloe(name, yoloe, method_results[name], shapes))

    animal_count = sum(len(a) for a in yoloe.values())
    print(f"Images: {len(paths)}, cats and dogs detected by YOLOE: {animal_count}, "
          f"images with no cat or dog: {sum(not a for a in yoloe.values())}")
    with open(OUTPUT / "summary.csv", "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(row)
    print(f"Results saved to: {OUTPUT}")


if __name__ == "__main__":
    main()
