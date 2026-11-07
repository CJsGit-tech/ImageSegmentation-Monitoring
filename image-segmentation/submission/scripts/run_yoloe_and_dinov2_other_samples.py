"""Run YOLOE and YOLOE + DINOv2 on the other COCO images in data/002-other_samples/.

YOLOE alone runs the same way as run_yoloe_test_samples.py. For YOLOE + DINOv2, YOLOE first finds each
cat or dog, DINOv2 heatmaps its crop, local peaks locate the eyes, and GrabCut or the heatmap level
traces each outline. The reference eyes are YOLOE's outlines in the sample images.

There are no reviewed eye marks for these images, so summary.csv counts outputs and measures
agreement with YOLOE, not accuracy. overview.jpg in each folder is for checking by eye.

Results go to:
    outputs/002-other_samples_yoloe/          YOLOE's annotated images and JSON files.
    outputs/002-other_samples_yoloe_dinov2/
        heatmaps/        One heatmap per cat or dog, named <image>_<class>_<number>.png.
        grabcut/         Annotated images (animals green, eyes red) and JSON files for GrabCut.
        heatmap_level/   The same for heatmap level.
        summary.csv      Comparison with YOLOE.

Run run_yoloe_test_samples.py first. Usage, from anywhere:
    .venv/bin/python image-segmentation/submission/scripts/run_yoloe_and_dinov2_other_samples.py
"""

import csv
import json
import math

import cv2
import numpy as np
import torch

import dinov2_eye_pipeline as pipeline
import run_yoloe_test_samples as segment
from measure_yoloe_eyes import centroid

ROOT = segment.ROOT
DINOV2_OUTPUT = ROOT.parent / "outputs" / "002-other_samples_yoloe_dinov2"
HEATMAPS = DINOV2_OUTPUT / "heatmaps"
METHODS = {"grabcut": pipeline.refine_grabcut, "heatmap_level": pipeline.refine_heatmap_level}
MATCH_DISTANCE = 30     # Pixels. Nearest eyes further apart than this aren't the same eye.
TILE_HEIGHT = 240
TILES_PER_ROW = 5
HEATMAP_TILE_HEIGHT = 180
HEATMAP_PAIRS_PER_ROW = 4


def eye_reference(config, model, device):
    """Average DINOv2's features over YOLOE's eye outlines in the sample images."""
    patches = []
    for path in sorted(p for p in config["source"].iterdir() if p.suffix.lower() in config["image_suffixes"]):
        prediction = json.loads((config["output"] / f"{path.stem}.json").read_text())
        image = cv2.imread(str(path))
        for animal in [a for a in prediction["animals"] if a["class"] in pipeline.CROP_CLASSES]:
            piece = pipeline.crop(image, animal["box"])
            features, grid = pipeline.patch_features(model, piece, device)
            patches.append(pipeline.eye_patches(piece, pipeline.crop_offset(animal), prediction["eyes"], features, grid))
    return pipeline.normalized_mean(patches), sum(len(p) for p in patches)


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


def draw(path, outlines, animals, folder, config):
    """Draw YOLOE's cat and dog outlines with this variant's eye outlines, in the YOLOE output's style."""
    eyes = [e for animal in animals for e in animal["eyes"] if e["polygon"] is not None]
    canvas = segment.annotate(cv2.imread(str(path)), outlines, eyes, config["animal_color"], config["eye_color"])
    cv2.imwrite(str(folder / path.name), canvas)
    return canvas


def save_heatmap(entry, name):
    """Save an animal's heatmap, and return it beside its crop for the overview."""
    heatmap = pipeline.colorize(entry["similarity"])
    cv2.imwrite(str(HEATMAPS / f"{name}.png"), heatmap)
    width = max(round(heatmap.shape[1] * HEATMAP_TILE_HEIGHT / heatmap.shape[0]), 1)
    size = (width, HEATMAP_TILE_HEIGHT)
    return np.hstack([cv2.resize(entry["image"], size), cv2.resize(heatmap, size, interpolation=cv2.INTER_NEAREST)])


def overview(canvases, path, per_row=TILES_PER_ROW, height=TILE_HEIGHT):
    tiles = [cv2.resize(c, (round(c.shape[1] * height / c.shape[0]), height)) for c in canvases]
    rows = [np.hstack(tiles[i:i + per_row]) for i in range(0, len(tiles), per_row)]
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
    images, yoloe_folder = config["other_source"], config["other_output"]
    paths = sorted(p for p in images.iterdir() if p.suffix.lower() in config["image_suffixes"])
    if not paths:
        raise SystemExit(f"No images found in {images}.")

    # YOLOE alone, with the same code and settings as run_yoloe_test_samples.py.
    original_load = segment.load_config
    segment.load_config = lambda: {**original_load(), "source": images, "output": yoloe_folder}
    segment.main()
    segment.load_config = original_load

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    config = segment.load_config()
    model = pipeline.load_model(config["model"].parent, device)
    reference, count = eye_reference(config, model, device)
    print(f"Reference: {count} eye patches from the sample images")

    yoloe, shapes, outlines, method_results = {}, {}, {}, {name: {} for name in METHODS}
    canvases = {name: [] for name in METHODS}
    heatmap_tiles = []
    HEATMAPS.mkdir(parents=True, exist_ok=True)
    for path in paths:
        prediction = json.loads((yoloe_folder / f"{path.stem}.json").read_text())
        image = cv2.imread(str(path))
        shapes[path.name] = image.shape[:2]
        indexed = [(i, a) for i, a in enumerate(prediction["animals"]) if a["class"] in pipeline.CROP_CLASSES]
        outlines[path.name] = [a for _, a in indexed]
        yoloe[path.name] = [{"class": a["class"], "eyes": sorted((e for e in prediction["eyes"] if e["animal_index"] == i),
                                                                 key=lambda e: -e["confidence"])[:2]} for i, a in indexed]
        for animals_name in METHODS:
            method_results[animals_name][path.name] = []
        for number, (_, animal) in enumerate(indexed, start=1):
            piece = pipeline.crop(image, animal["box"])
            features, grid = pipeline.patch_features(model, piece, device)
            entry = pipeline.animal_entry(piece, animal, pipeline.crop_offset(animal), features, grid, reference)
            heatmap_tiles.append(save_heatmap(entry, f"{path.stem}_{animal['class']}_{number}"))
            located = pipeline.locate(entry)
            for name, refiner in METHODS.items():
                eyes = pipeline.run_refiner(entry, refiner, located) if located else []
                method_results[name][path.name].append(
                    {"class": animal["class"], "box": animal["box"], "eyes": to_image(entry, eyes, located)})

    rows = []
    overview(heatmap_tiles, HEATMAPS / "overview.jpg", HEATMAP_PAIRS_PER_ROW, HEATMAP_TILE_HEIGHT)
    yoloe_as_results = {f: [{"eyes": [{"center": list(centroid([tuple(v) for v in e["polygon"]])), "polygon": e["polygon"]}
                                      for e in a["eyes"]]} for a in animals] for f, animals in yoloe.items()}
    rows.append(compare_with_yoloe("yoloe", yoloe, yoloe_as_results, shapes))
    for name in METHODS:
        folder = DINOV2_OUTPUT / name
        folder.mkdir(parents=True, exist_ok=True)
        for path in paths:
            animals = method_results[name][path.name]
            canvases[name].append(draw(path, outlines[path.name], animals, folder, config))
            (folder / f"{path.stem}.json").write_text(json.dumps({"image": path.name, "animals": animals}, indent=2))
        overview(canvases[name], folder / "overview.jpg")
        rows.append(compare_with_yoloe(f"dinov2_{name}", yoloe, method_results[name], shapes))

    animal_count = sum(len(a) for a in yoloe.values())
    print(f"Images: {len(paths)}, cats and dogs detected by YOLOE: {animal_count}, "
          f"images with no cat or dog: {sum(not a for a in yoloe.values())}")
    with open(DINOV2_OUTPUT / "summary.csv", "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(row)
    print(f"Results saved to: {yoloe_folder} and {DINOV2_OUTPUT}")


if __name__ == "__main__":
    main()
