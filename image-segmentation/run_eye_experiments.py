"""Run 13 image-processing experiments that find eyes from DINO heatmaps.

Experiments 01-04 locate eyes in the heatmap. Experiments 05-13 start from the eyes found by
BASE_LOCATOR and refine each one in a small window of the crop. No model is trained.

Each experiment writes to its own folder in results/experiments/:
    <crop>.jpg      Result: green = hit, orange = wrong, white boxes = hand marks.
                    OUTLINE_ONLY experiments show only thin red eye outlines.
    <crop>.json     Eye centers and outlines, in original image pixels.
    overview.jpg    All crops together.
    metrics.json    Scores against HAND_MARKS.
results/experiments/dino_v1_summary.csv compares them, with YOLOE's eyes scored the same way.

Run segment_animals_eyes.py and crop_animals.py first. Usage, from anywhere:
    .venv/bin/python image-segmentation/run_eye_experiments.py
"""

import csv
import json
import math

import cv2
import numpy as np
import torch

from crop_animals import CROP_CLASSES, OUTPUT as CROPS
from dino_heatmap import LONG_SIDE, MIN_EYE_COVER, eye_mask, load_model, patch_features
from measure_eyes import centroid
from segment_animals_eyes import ROOT, load_config

OUTPUT = ROOT / "results" / "experiments"
BASE_LOCATOR = "02_local_peaks"
OUTLINE_ONLY = {"05_guided_filter", "06_grabcut", "07_watershed", "08_superpixels",
                "12_canny_contours", "13_heatmap_level",
                "14_dinov2_peaks_watershed"}  # Images show only thin red outlines.

# Eye boxes (x1, y1, x2, y2) in original image pixels. Marked by Claude Code from zoomed views
# and not reviewed by a person, so they are approximate.
HAND_MARKS = {
    ("001-coco-sample", "dog"): [(205, 208, 224, 226), (246, 210, 272, 227)],
    ("001-coco-sample", "cat"): [(205, 37, 212, 43), (217, 35, 223, 41)],
    ("002-coco-sample", "cat"): [(326, 181, 358, 206), (401, 178, 435, 205)],
    ("002-coco-sample", "dog"): [(301, 389, 340, 421), (387, 386, 411, 425)],
    ("003-coco-sample", "dog"): [(338, 109, 357, 133), (381, 117, 411, 143)],
    ("003-coco-sample", "cat"): [(151, 308, 173, 326), (192, 310, 213, 326)],
}

# Eye selection, shared by every experiment.
MIN_SPACING = 0.05       # Closest two eyes may be, as a share of the crop's long side.
MAX_SPACING = 0.40       # Farthest two eyes may be.
MIN_SCORE_RATIO = 0.7    # A second eye must score at least this share of the first.

# Heatmap locators, in patches.
SMOOTH_SIGMA = 1
MIN_PEAK_DISTANCE = 4
CENTER_RADIUS = 2
THRESHOLD_PERCENTILE = 98.5
PEAK_PERCENTILE = 95
DOG_SIGMAS = (1, 4)
DOG_PERCENTILE = 97
MIN_BLOB_PATCHES = 2
MAX_BLOB_SHARE = 0.03    # Largest eye blob, as a share of the animal's patches.
MAX_BLOB_ASPECT = 4
BLOB_UPSCALE = 4

# Window refiners. Every window is resized to about WINDOW_SIZE so settings fit all crops.
WINDOW_SHARE = 0.08      # Half the window's width, as a share of the crop's long side.
MIN_WINDOW_HALF = 12     # In crop pixels.
WINDOW_SIZE = 160
SEED_RADIUS = 8
BORDER = 12
MAX_WINDOW_SHARE = 0.6   # A larger shape has leaked past the eye, so it counts as a failure.
GUIDED_RADIUS = 8
GUIDED_EPS = 0.01
SLIC_SPACING = 16
SLIC_COMPACTNESS = 10
SLIC_ITERATIONS = 5
CANNY_LOW = 0.66          # Edge thresholds, as shares of the window's median brightness.
CANNY_HIGH = 1.33
MIN_CONTOUR_SHARE = 0.01  # Smallest eye outline, as a share of the window.


def load_crops(config, model, device, patch_features=patch_features):
    """Load each crop with its animal mask, marks and leave-one-image-out similarity map."""
    crops = []
    for path in sorted(p for p in CROPS.iterdir() if p.suffix.lower() in config["image_suffixes"]):
        image = cv2.imread(str(path))
        stem, animal_class, number = path.stem.rsplit("_", 2)
        prediction = json.loads((config["output"] / f"{stem}.json").read_text())
        animal = [a for a in prediction["animals"] if a["class"] in CROP_CLASSES][int(number) - 1]
        offset = np.array([max(math.floor(animal["box"][0]), 0), max(math.floor(animal["box"][1]), 0)])

        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.fillPoly(mask, [np.array(animal["polygon"], np.int32) - offset], 1)
        _, eyes = eye_mask(path, image.shape, config["output"])
        features, grid = patch_features(model, image, device)
        cover = cv2.resize(eyes.astype(np.float32), grid[::-1], interpolation=cv2.INTER_AREA).ravel()
        crops.append({
            "name": path.stem, "source": stem, "class": animal_class, "image": image, "offset": offset,
            "mask": mask, "grid": grid, "features": features, "eye_patches": features[cover > MIN_EYE_COVER],
            "grid_mask": cv2.resize(mask.astype(np.float32), grid[::-1], interpolation=cv2.INTER_AREA) > 0.5,
            "long_side": max(image.shape[:2]), "marks": HAND_MARKS[(stem, animal_class)],
        })
    for crop in crops:
        references = np.vstack([c["eye_patches"] for c in crops if c["source"] != crop["source"]])
        eye_feature = references.mean(0)
        eye_feature /= np.linalg.norm(eye_feature)
        crop["similarity"] = (crop["features"] @ eye_feature).reshape(crop["grid"]).astype(np.float32)
        crop["full_similarity"] = cv2.resize(crop["similarity"], crop["image"].shape[1::-1],
                                             interpolation=cv2.INTER_CUBIC)
    return crops


def to_crop(crop, column, row):
    height, width = crop["image"].shape[:2]
    return (column + 0.5) * width / crop["grid"][1], (row + 0.5) * height / crop["grid"][0]


def blur(values, sigma):
    return cv2.GaussianBlur(values, (0, 0), sigma)


def find_peaks(response, allowed, minimum):
    """Return (score, column, row) of each local maximum, strongest first and spaced apart."""
    dilated = cv2.dilate(response, np.ones((5, 5), np.uint8))
    rows, columns = np.where((response == dilated) & allowed & (response >= minimum))
    peaks = sorted(zip(response[rows, columns].tolist(), columns.tolist(), rows.tolist()), reverse=True)
    kept = []
    for peak in peaks:
        if all(math.hypot(peak[1] - k[1], peak[2] - k[2]) >= MIN_PEAK_DISTANCE for k in kept):
            kept.append(peak)
    return kept


def peak_candidates(crop, response, peaks):
    """Turn peaks into crop-pixel candidates, with sub-patch centers from nearby response."""
    weights = np.clip(response - np.percentile(response[crop["grid_mask"]], 50), 0, None)
    candidates = []
    for score, column, row in peaks:
        top, left = max(row - CENTER_RADIUS, 0), max(column - CENTER_RADIUS, 0)
        patch = weights[top:row + CENTER_RADIUS + 1, left:column + CENTER_RADIUS + 1]
        ys, xs = np.mgrid[top:top + patch.shape[0], left:left + patch.shape[1]]
        total = patch.sum()
        x, y = ((xs * patch).sum() / total, (ys * patch).sum() / total) if total else (column, row)
        candidates.append({"score": score, "center": to_crop(crop, x, y)})
    return candidates


def locate_threshold(crop):
    smooth = blur(crop["similarity"], SMOOTH_SIGMA)
    limit = np.percentile(smooth[crop["grid_mask"]], THRESHOLD_PERCENTILE)
    binary = ((smooth >= limit) & crop["grid_mask"]).astype(np.uint8)
    count, labels, stats, centers = cv2.connectedComponentsWithStats(binary)
    largest = MAX_BLOB_SHARE * crop["grid_mask"].sum()
    candidates = []
    for label in range(1, count):
        width, height, area = stats[label, cv2.CC_STAT_WIDTH], stats[label, cv2.CC_STAT_HEIGHT], stats[label, cv2.CC_STAT_AREA]
        if MIN_BLOB_PATCHES <= area <= largest and max(width, height) <= MAX_BLOB_ASPECT * min(width, height):
            blob = cv2.resize((labels == label).astype(np.uint8), crop["image"].shape[1::-1],
                              interpolation=cv2.INTER_NEAREST)
            contours, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            candidates.append({"score": float(smooth[labels == label].max()),
                               "center": to_crop(crop, *(centers[label] - 0)),
                               "polygon": max(contours, key=cv2.contourArea).reshape(-1, 2).tolist()})
    return candidates


def locate_peaks(crop):
    smooth = blur(crop["similarity"], SMOOTH_SIGMA)
    minimum = np.percentile(smooth[crop["grid_mask"]], PEAK_PERCENTILE)
    return peak_candidates(crop, smooth, find_peaks(smooth, crop["grid_mask"], minimum))


def locate_dog(crop):
    response = blur(crop["similarity"], DOG_SIGMAS[0]) - blur(crop["similarity"], DOG_SIGMAS[1])
    minimum = max(np.percentile(response[crop["grid_mask"]], DOG_PERCENTILE), 1e-6)
    return peak_candidates(crop, response, find_peaks(response, crop["grid_mask"], minimum))


def locate_blob_detector(crop):
    values = crop["similarity"][crop["grid_mask"]]
    low, high = np.percentile(values, 5), np.percentile(values, 99.9)
    levels = (np.clip((crop["similarity"] - low) / (high - low), 0, 1) * 255).astype(np.uint8)
    levels[~crop["grid_mask"]] = 0
    large = cv2.resize(levels, None, fx=BLOB_UPSCALE, fy=BLOB_UPSCALE, interpolation=cv2.INTER_CUBIC)

    params = cv2.SimpleBlobDetector_Params()
    params.minThreshold, params.maxThreshold, params.thresholdStep = 120, 250, 10
    params.filterByColor, params.blobColor = True, 255
    params.filterByArea = True
    params.minArea = MIN_BLOB_PATCHES * BLOB_UPSCALE ** 2
    params.maxArea = MAX_BLOB_SHARE * crop["grid_mask"].sum() * BLOB_UPSCALE ** 2
    params.filterByCircularity, params.minCircularity = True, 0.2
    params.filterByConvexity, params.minConvexity = True, 0.5
    params.filterByInertia, params.minInertiaRatio = True, 0.1

    candidates = []
    for point in cv2.SimpleBlobDetector_create(params).detect(large):
        column, row = point.pt[0] / BLOB_UPSCALE - 0.5, point.pt[1] / BLOB_UPSCALE - 0.5
        grid_row = min(max(round(row), 0), crop["grid"][0] - 1)
        grid_column = min(max(round(column), 0), crop["grid"][1] - 1)
        if crop["grid_mask"][grid_row, grid_column]:
            candidates.append({"score": float(crop["similarity"][grid_row, grid_column]),
                               "center": to_crop(crop, column, row)})
    return candidates


def select_eyes(crop, candidates):
    """Keep the best-scoring plausible pair, or one eye if no second candidate qualifies."""
    ranked = sorted(candidates, key=lambda c: -c["score"])[:8]
    best = None
    for i, first in enumerate(ranked):
        for second in ranked[i + 1:]:
            gap = math.dist(first["center"], second["center"]) / crop["long_side"]
            if MIN_SPACING <= gap <= MAX_SPACING and second["score"] >= MIN_SCORE_RATIO * first["score"]:
                if best is None or first["score"] + second["score"] > sum(c["score"] for c in best):
                    best = [first, second]
    return best or ranked[:1]


def make_window(crop, center):
    """Cut an enlarged window around an eye, with the similarity map at the same size."""
    half = max(round(WINDOW_SHARE * crop["long_side"]), MIN_WINDOW_HALF)
    height, width = crop["image"].shape[:2]
    x1, y1 = max(round(center[0]) - half, 0), max(round(center[1]) - half, 0)
    x2, y2 = min(round(center[0]) + half, width), min(round(center[1]) + half, height)
    scale = WINDOW_SIZE / (2 * half)
    size = (max(round((x2 - x1) * scale), 8), max(round((y2 - y1) * scale), 8))
    image = cv2.resize(crop["image"][y1:y2, x1:x2], size, interpolation=cv2.INTER_CUBIC)
    similarity = cv2.resize(crop["full_similarity"][y1:y2, x1:x2], size, interpolation=cv2.INTER_CUBIC)
    seed = (min(round((center[0] - x1) * size[0] / (x2 - x1)), size[0] - 1),
            min(round((center[1] - y1) * size[1] / (y2 - y1)), size[1] - 1))
    return {"image": image, "gray": cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), "similarity": similarity,
            "seed": seed, "origin": (x1, y1), "scale": (size[0] / (x2 - x1), size[1] / (y2 - y1))}


def midpoint_threshold(values, seed):
    x, y = seed
    near = values[max(y - 4, 0):y + 5, max(x - 4, 0):x + 5]
    return (np.median(values) + near.max()) / 2


def region_at_seed(binary, seed):
    """Return the outline of the region at the seed, or of the region nearest to it."""
    count, labels = cv2.connectedComponents(binary.astype(np.uint8))
    if count < 2:
        return None
    label = labels[seed[1], seed[0]]
    if label == 0:
        ys, xs = np.nonzero(labels)
        nearest = np.argmin((xs - seed[0]) ** 2 + (ys - seed[1]) ** 2)
        label = labels[ys[nearest], xs[nearest]]
    region = (labels == label).astype(np.uint8)
    if region.sum() > MAX_WINDOW_SHARE * region.size:
        return None
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max(contours, key=cv2.contourArea).reshape(-1, 2)


def seed_disc(shape, seed):
    mask = np.zeros(shape, np.uint8)
    cv2.circle(mask, seed, SEED_RADIUS, 1, -1)
    return mask.astype(bool)


def refine_guided(window):
    guide = window["gray"].astype(np.float32) / 255
    source = window["similarity"]
    size = (2 * GUIDED_RADIUS + 1, 2 * GUIDED_RADIUS + 1)
    mean_i, mean_p = cv2.blur(guide, size), cv2.blur(source, size)
    a = (cv2.blur(guide * source, size) - mean_i * mean_p) / (cv2.blur(guide * guide, size) - mean_i ** 2 + GUIDED_EPS)
    b = mean_p - a * mean_i
    filtered = cv2.blur(a, size) * guide + cv2.blur(b, size)
    return region_at_seed(filtered >= midpoint_threshold(filtered, window["seed"]), window["seed"])


def refine_grabcut(window):
    similarity = window["similarity"]
    mask = np.full(similarity.shape, cv2.GC_PR_BGD, np.uint8)
    mask[similarity >= midpoint_threshold(similarity, window["seed"])] = cv2.GC_PR_FGD
    mask[seed_disc(similarity.shape, window["seed"])] = cv2.GC_FGD
    mask[:BORDER], mask[-BORDER:], mask[:, :BORDER], mask[:, -BORDER:] = [cv2.GC_BGD] * 4
    cv2.setRNGSeed(0)
    cv2.grabCut(window["image"], mask, None, np.zeros((1, 65)), np.zeros((1, 65)), 5, cv2.GC_INIT_WITH_MASK)
    return region_at_seed((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), window["seed"])


def refine_watershed(window):
    markers = np.zeros(window["gray"].shape, np.int32)
    markers[:BORDER], markers[-BORDER:], markers[:, :BORDER], markers[:, -BORDER:] = 1, 1, 1, 1
    markers[seed_disc(window["gray"].shape, window["seed"])] = 2
    cv2.watershed(window["image"], markers)
    return region_at_seed(markers == 2, window["seed"])


def slic_labels(image):
    """SLIC superpixels. cv2.ximgproc needs opencv-contrib, which this project doesn't install."""
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    height, width = lab.shape[:2]
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    grid_x, grid_y = np.meshgrid(np.arange(SLIC_SPACING // 2, width, SLIC_SPACING),
                                 np.arange(SLIC_SPACING // 2, height, SLIC_SPACING))
    centers = np.column_stack([lab[grid_y.ravel(), grid_x.ravel()], grid_x.ravel(), grid_y.ravel()]).astype(np.float32)
    labels = np.zeros((height, width), np.int32)
    for _ in range(SLIC_ITERATIONS):
        nearest = np.full((height, width), np.inf, np.float32)
        for index, (l, a, b, cx, cy) in enumerate(centers):
            x1, x2 = max(int(cx) - SLIC_SPACING, 0), min(int(cx) + SLIC_SPACING + 1, width)
            y1, y2 = max(int(cy) - SLIC_SPACING, 0), min(int(cy) + SLIC_SPACING + 1, height)
            color = ((lab[y1:y2, x1:x2] - (l, a, b)) ** 2).sum(-1)
            place = (xs[y1:y2, x1:x2] - cx) ** 2 + (ys[y1:y2, x1:x2] - cy) ** 2
            cost = color + place * (SLIC_COMPACTNESS / SLIC_SPACING) ** 2
            closer = cost < nearest[y1:y2, x1:x2]
            nearest[y1:y2, x1:x2][closer] = cost[closer]
            labels[y1:y2, x1:x2][closer] = index
        for index in range(len(centers)):
            members = labels == index
            if members.any():
                centers[index] = [*lab[members].mean(0), xs[members].mean(), ys[members].mean()]
    return labels


def refine_superpixels(window):
    labels = slic_labels(window["image"])
    means = np.zeros(window["similarity"].shape, np.float32)
    for label in np.unique(labels):
        means[labels == label] = window["similarity"][labels == label].mean()
    return region_at_seed(means >= midpoint_threshold(means, window["seed"]), window["seed"])


def circle_outline(center, radius):
    angles = np.linspace(0, 2 * np.pi, 32, endpoint=False)
    return np.column_stack([center[0] + radius * np.cos(angles), center[1] + radius * np.sin(angles)])


def refine_gradient_voting(window):
    """Vote against each gradient, because an eye's dark center is darker than its surroundings."""
    gray = cv2.GaussianBlur(window["gray"], (0, 0), 2).astype(np.float32)
    gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    magnitude = np.hypot(gx, gy)
    ys, xs = np.nonzero(magnitude > max(np.percentile(magnitude, 70), 1e-3))
    if not len(xs):
        return None
    ux, uy = gx[ys, xs] / magnitude[ys, xs], gy[ys, xs] / magnitude[ys, xs]
    height, width = gray.shape
    radii = np.linspace(0.05, 0.35, 10) * WINDOW_SIZE
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    closeness = np.exp(-((xx - window["seed"][0]) ** 2 + (yy - window["seed"][1]) ** 2) / (2 * (0.35 * WINDOW_SIZE) ** 2))
    best = (0, None, None)
    for radius in radii:
        vote_x = np.round(xs - ux * radius).astype(int)
        vote_y = np.round(ys - uy * radius).astype(int)
        inside = (vote_x >= 0) & (vote_x < width) & (vote_y >= 0) & (vote_y < height)
        votes = np.zeros(gray.shape, np.float32)
        np.add.at(votes, (vote_y[inside], vote_x[inside]), 1)
        votes = cv2.GaussianBlur(votes, (0, 0), 3) * closeness / radius  # Larger circles get more votes.
        y, x = np.unravel_index(np.argmax(votes), votes.shape)
        if votes[y, x] > best[0]:
            best = (votes[y, x], (x, y), radius)
    return circle_outline(best[1], best[2]) if best[1] else None


def ellipse_outline(ellipse):
    (x, y), (a, b), angle = ellipse
    return cv2.ellipse2Poly((round(x), round(y)), (max(round(a / 2), 1), max(round(b / 2), 1)), round(angle), 0, 360, 10)


def refine_hough_ellipse(window):
    """Try Hough circles first, then fall back to fitting an ellipse on edge contours."""
    gray = cv2.medianBlur(window["gray"], 5)
    similarity = window["similarity"]
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=WINDOW_SIZE // 3, param1=100, param2=18,
                               minRadius=round(0.06 * WINDOW_SIZE), maxRadius=round(0.4 * WINDOW_SIZE))
    height, width = gray.shape
    best = (-np.inf, None)
    if circles is not None:
        for x, y, radius in circles[0]:
            if 0 <= x < width and 0 <= y < height and similarity[int(y), int(x)] > best[0]:
                best = (similarity[int(y), int(x)], circle_outline((x, y), radius))
        if best[1] is not None:
            return best[1]
    contours, _ = cv2.findContours(cv2.Canny(gray, 50, 150), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    for contour in contours:
        if len(contour) >= 5:
            (x, y), (a, b), angle = cv2.fitEllipse(contour)
            shape_fits = 0.1 * WINDOW_SIZE < max(a, b) < 0.8 * WINDOW_SIZE and max(a, b) <= 3 * max(min(a, b), 1e-3)
            if shape_fits and 0 <= x < width and 0 <= y < height and similarity[int(y), int(x)] > best[0]:
                best = (similarity[int(y), int(x)], ellipse_outline(((x, y), (a, b), angle)))
    return best[1]


def refine_mser(window):
    gray = window["gray"]
    area = gray.size
    mser = cv2.MSER_create(delta=5, min_area=round(0.005 * area), max_area=round(0.4 * area))
    regions = [r for image in (gray, 255 - gray) for r in mser.detectRegions(image)[0] if len(r) >= 5]
    best = (np.inf, None)
    for points in regions:
        (x, y), (a, b), angle = cv2.fitEllipse(points)
        distance = math.dist((x, y), window["seed"])
        if max(a, b) <= 3 * max(min(a, b), 1e-3) and distance <= 0.3 * WINDOW_SIZE and distance < best[0]:
            best = (distance, ellipse_outline(((x, y), (a, b), angle)))
    return best[1]


def refine_canny_contours(window):
    """Trace closed edges around the peak, and let the heatmap pick the one that fits the eye."""
    gray = cv2.GaussianBlur(window["gray"], (0, 0), 2)
    middle = np.median(gray)
    edges = cv2.Canny(gray, round(CANNY_LOW * middle), round(CANNY_HIGH * middle))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    area = window["gray"].size
    seed = (float(window["seed"][0]), float(window["seed"][1]))
    best = (-np.inf, None)
    for contour in contours:
        if (MIN_CONTOUR_SHARE * area <= cv2.contourArea(contour) <= MAX_WINDOW_SHARE * area
                and cv2.pointPolygonTest(contour, seed, False) >= 0):
            mask = np.zeros(window["gray"].shape, np.uint8)
            cv2.fillPoly(mask, [contour], 1)
            score = window["similarity"][mask > 0].mean()
            if score > best[0]:
                best = (score, contour.reshape(-1, 2))
    return best[1]


def refine_heatmap_level(window, share=0.5):
    """Trace the smoothed heatmap at a share of the height from its window median to this peak."""
    similarity = window["similarity"]
    x, y = window["seed"]
    peak = similarity[max(y - 4, 0):y + 5, max(x - 4, 0):x + 5].max()
    level = np.median(similarity) + share * (peak - np.median(similarity))
    return region_at_seed(similarity >= level, window["seed"])


LOCATORS = {
    "01_threshold_components": locate_threshold,
    "02_local_peaks": locate_peaks,
    "03_difference_of_gaussians": locate_dog,
    "04_simple_blob_detector": locate_blob_detector,
}
REFINERS = {
    "05_guided_filter": refine_guided,
    "06_grabcut": refine_grabcut,
    "07_watershed": refine_watershed,
    "08_superpixels": refine_superpixels,
    "09_gradient_voting": refine_gradient_voting,
    "10_hough_ellipse": refine_hough_ellipse,
    "11_mser": refine_mser,
    "12_canny_contours": refine_canny_contours,
    "13_heatmap_level": refine_heatmap_level,
}


def run_locator(crop, locator):
    eyes = select_eyes(crop, locator(crop))
    return [{"center": list(e["center"]), "polygon": e.get("polygon")} for e in eyes]


def run_refiner(crop, refiner, base_eyes):
    eyes = []
    for base in base_eyes:
        window = make_window(crop, base["center"])
        outline = refiner(window)
        if outline is None or len(outline) < 3:
            eyes.append({"center": None, "polygon": None, "start": base["center"]})
            continue
        points = np.asarray(outline, np.float32) / window["scale"] + window["origin"]
        polygon = points.tolist()
        eyes.append({"center": list(centroid(polygon)), "polygon": polygon, "start": base["center"]})
    return eyes


def box_iou(a, b):
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    overlap = width * height
    return overlap / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap)


def score_crop(crop, eyes):
    """Match eyes to the marks. A hit has its center inside a mark, and each mark counts once."""
    offset = crop["offset"]
    unmatched = list(range(len(crop["marks"])))
    result = {"hits": 0, "false": 0, "failed": 0, "matched": {}, "ious": []}
    for eye in eyes:
        eye["hit"] = False
        if eye["center"] is None:
            result["failed"] += 1
            continue
        x, y = eye["center"][0] + offset[0], eye["center"][1] + offset[1]
        mark = next((m for m in unmatched if crop["marks"][m][0] <= x <= crop["marks"][m][2]
                     and crop["marks"][m][1] <= y <= crop["marks"][m][3]), None)
        if mark is None:
            result["false"] += 1
            continue
        unmatched.remove(mark)
        eye["hit"] = True
        result["hits"] += 1
        result["matched"][mark] = (x, y)
        if eye.get("polygon") is not None:
            points = np.array(eye["polygon"]) + offset
            box = (*points.min(0), *points.max(0))
            result["ious"].append(box_iou(box, crop["marks"][mark]))
    return result


def mark_center(mark):
    return (mark[0] + mark[2]) / 2, (mark[1] + mark[3]) / 2


def summarize(name, crops, scores, eye_lists):
    errors, percents = [], []
    for crop, score in zip(crops, scores):
        if len(score["matched"]) == 2:
            predicted = math.dist(score["matched"][0], score["matched"][1])
            manual = math.dist(mark_center(crop["marks"][0]), mark_center(crop["marks"][1]))
            errors.append(abs(predicted - manual))
            percents.append(100 * errors[-1] / manual)
    ious = [iou for score in scores for iou in score["ious"]]
    return {
        "experiment": name,
        "eyes_hit": sum(s["hits"] for s in scores),
        "total_eyes": sum(len(c["marks"]) for c in crops),
        "false_eyes": sum(s["false"] for s in scores),
        "failed_outlines": sum(s["failed"] for s in scores),
        "animals_with_two_eyes_output": sum(sum(e["center"] is not None for e in eyes) == 2 for eyes in eye_lists),
        "animals_with_both_eyes_correct": len(errors),
        "distance_mae_px": round(float(np.mean(errors)), 2) if errors else None,
        "distance_mape_percent": round(float(np.mean(percents)), 1) if percents else None,
        "mean_box_iou": round(float(np.mean(ious)), 3) if ious else None,
    }


def draw_outlines(crop, eyes):
    """Draw only the eye outlines, as thin red lines."""
    scale = max(LONG_SIDE / crop["long_side"], 1)
    canvas = cv2.resize(crop["image"], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    for eye in eyes:
        if eye.get("polygon") is not None:
            polygon = (np.array(eye["polygon"]) * scale).round().astype(np.int32)
            cv2.polylines(canvas, [polygon], True, (0, 0, 255), 1, cv2.LINE_AA)
    return canvas


def draw(crop, eyes):
    scale = max(LONG_SIDE / crop["long_side"], 1)
    canvas = cv2.resize(crop["image"], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    for x1, y1, x2, y2 in crop["marks"]:
        start = (round((x1 - crop["offset"][0]) * scale), round((y1 - crop["offset"][1]) * scale))
        end = (round((x2 - crop["offset"][0]) * scale), round((y2 - crop["offset"][1]) * scale))
        cv2.rectangle(canvas, start, end, (255, 255, 255), 1)
    for eye in eyes:
        color = (0, 220, 0) if eye["hit"] else (0, 140, 255)
        if eye.get("polygon") is not None:
            polygon = (np.array(eye["polygon"]) * scale).round().astype(np.int32)
            cv2.polylines(canvas, [polygon], True, color, 2, cv2.LINE_AA)
        center = eye["center"] or eye.get("start")
        marker = cv2.MARKER_CROSS if eye["center"] else cv2.MARKER_TILTED_CROSS
        cv2.drawMarker(canvas, (round(center[0] * scale), round(center[1] * scale)),
                       color if eye["center"] else (0, 0, 255), marker, 12, 2)
    return canvas


def overview(images):
    height = 300
    tiles = [cv2.resize(i, (round(i.shape[1] * height / i.shape[0]), height)) for i in images]
    rows = [np.hstack(tiles[:3]), np.hstack(tiles[3:])]
    width = max(row.shape[1] for row in rows)
    return np.vstack([cv2.copyMakeBorder(r, 0, 4, 0, width - r.shape[1], cv2.BORDER_CONSTANT) for r in rows])


def save_experiment(name, crops, eye_lists):
    folder = OUTPUT / name
    folder.mkdir(parents=True, exist_ok=True)
    scores, images = [], []
    for crop, eyes in zip(crops, eye_lists):
        scores.append(score_crop(crop, eyes))
        images.append(draw_outlines(crop, eyes) if name in OUTLINE_ONLY else draw(crop, eyes))
        cv2.imwrite(str(folder / f"{crop['name']}.jpg"), images[-1])
        original = [{
            "hit": eye["hit"],
            "center": None if eye["center"] is None else [round(float(v + o), 2) for v, o in zip(eye["center"], crop["offset"])],
            "polygon": None if eye.get("polygon") is None else (np.array(eye["polygon"]) + crop["offset"]).round(1).tolist(),
        } for eye in eyes]
        (folder / f"{crop['name']}.json").write_text(json.dumps({"crop": crop["name"], "eyes": original}, indent=2))
    cv2.imwrite(str(folder / "overview.jpg"), overview(images))
    metrics = summarize(name, crops, scores, eye_lists)
    (folder / "metrics.json").write_text(json.dumps(metrics, indent=2))
    return metrics


def yoloe_baseline(config, crops):
    """Score YOLOE's detected eyes the same way as the experiments."""
    eye_lists, scores = [], []
    for crop in crops:
        prediction = json.loads((config["output"] / f"{crop['source']}.json").read_text())
        animals = [(i, a) for i, a in enumerate(prediction["animals"]) if a["class"] in CROP_CLASSES]
        index = animals[int(crop["name"].rsplit("_", 1)[1]) - 1][0]
        owned = sorted((e for e in prediction["eyes"] if e["animal_index"] == index), key=lambda e: -e["confidence"])[:2]
        eyes = [{"center": [v - o for v, o in zip(centroid(e["polygon"]), crop["offset"])],
                 "polygon": (np.array(e["polygon"]) - crop["offset"]).tolist()} for e in owned]
        eye_lists.append(eyes)
        scores.append(score_crop(crop, eyes))
    return summarize("yoloe_baseline", crops, scores, eye_lists)


def main():
    config = load_config()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Running on execution device: {device.upper()}")
    crops = load_crops(config, load_model(config["model"].parent, device), device)

    rows = [yoloe_baseline(config, crops)]
    located = {}
    for name, locator in LOCATORS.items():
        located[name] = [run_locator(crop, locator) for crop in crops]
        rows.append(save_experiment(name, crops, located[name]))
    for name, refiner in REFINERS.items():
        rows.append(save_experiment(name, crops, [run_refiner(c, refiner, e) for c, e in zip(crops, located[BASE_LOCATOR])]))

    with open(OUTPUT / "dino_v1_summary.csv", "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(row)
    print(f"Results saved to: {OUTPUT}")


if __name__ == "__main__":
    main()
