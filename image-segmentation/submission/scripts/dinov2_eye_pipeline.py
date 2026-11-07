"""The final DINOv2 eye pipeline, shared by run_yoloe_dinov2_test_samples.py and run_coco_38_test.py.

YOLOE's animal outline limits the search. DINOv2 turns each animal crop into patch features, and
their similarity to reference eyes makes a heatmap. Local peaks locate up to two eyes, and GrabCut
or the heatmap level outlines each one. No model is trained.
"""

import math
import shutil
import ssl
import urllib.request
from pathlib import Path

import certifi
import cv2
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from measure_yoloe_eyes import centroid

WEIGHTS_URL = "https://dl.fbaipublicfiles.com/dinov2/dinov2_vits14/dinov2_vits14_reg4_pretrain.pth"
CROP_CLASSES = ("cat", "dog")
PATCH_SIZE = 14
LONG_SIDE = 784            # Crops are resized to this, giving a 56-patch grid on the long side.
REGISTERS = 4
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406])
IMAGENET_STD = np.array([0.229, 0.224, 0.225])
MIN_EYE_COVER = 0.5        # Share of a patch that must be eye for it to count as a reference.

# Heatmap images.
HEATMAP_BLOCK = 8          # Each patch is drawn as a square of this many pixels.
COLOR_RANGE = (0.1, 0.7)   # Similarity shown from darkest to brightest, the same for every crop.

# Eye selection.
MIN_SPACING = 0.05         # Closest two eyes may be, as a share of the crop's long side.
MAX_SPACING = 0.40         # Farthest two eyes may be.
MIN_SCORE_RATIO = 0.7      # A second eye must score at least this share of the first.

# Local peaks, in patches.
SMOOTH_SIGMA = 1
MIN_PEAK_DISTANCE = 4
CENTER_RADIUS = 2
PEAK_PERCENTILE = 95

# Outlines. Every window is resized to about WINDOW_SIZE so settings fit all crops.
WINDOW_SHARE = 0.08        # Half the window's width, as a share of the crop's long side.
MIN_WINDOW_HALF = 12       # In crop pixels.
WINDOW_SIZE = 160
SEED_RADIUS = 8
BORDER = 12
MAX_WINDOW_SHARE = 0.6     # A larger shape has leaked past the eye, so it counts as a failure.


class Attention(nn.Module):
    def __init__(self, dim, heads):
        super().__init__()
        self.heads = heads
        self.qkv = nn.Linear(dim, dim * 3)
        self.proj = nn.Linear(dim, dim)

    def forward(self, x):
        batch, tokens, dim = x.shape
        head_dim = dim // self.heads
        q, k, v = self.qkv(x).reshape(batch, tokens, 3, self.heads, head_dim).permute(2, 0, 3, 1, 4)
        weights = (q @ k.transpose(-2, -1) * head_dim ** -0.5).softmax(-1)
        return self.proj((weights @ v).transpose(1, 2).reshape(batch, tokens, dim))


class Mlp(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.fc1 = nn.Linear(dim, dim * 4)
        self.fc2 = nn.Linear(dim * 4, dim)

    def forward(self, x):
        return self.fc2(F.gelu(self.fc1(x)))


class PatchEmbed(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.proj = nn.Conv2d(3, dim, PATCH_SIZE, PATCH_SIZE)


class LayerScale(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.gamma = nn.Parameter(torch.ones(dim))

    def forward(self, x):
        return x * self.gamma


class Block(nn.Module):
    def __init__(self, dim, heads):
        super().__init__()
        self.norm1 = nn.LayerNorm(dim, eps=1e-6)
        self.attn = Attention(dim, heads)
        self.ls1 = LayerScale(dim)
        self.norm2 = nn.LayerNorm(dim, eps=1e-6)
        self.mlp = Mlp(dim)
        self.ls2 = LayerScale(dim)

    def forward(self, x):
        x = x + self.ls1(self.attn(self.norm1(x)))
        return x + self.ls2(self.mlp(self.norm2(x)))


class DinoV2Small14(nn.Module):
    """DINOv2 ViT-S/14 with registers. Loading the weights with strict=True confirms the layers match."""

    def __init__(self, dim=384, depth=12, heads=6):
        super().__init__()
        self.patch_embed = PatchEmbed(dim)
        self.cls_token = nn.Parameter(torch.zeros(1, 1, dim))
        self.register_tokens = nn.Parameter(torch.zeros(1, REGISTERS, dim))
        self.pos_embed = nn.Parameter(torch.zeros(1, 1370, dim))
        self.mask_token = nn.Parameter(torch.zeros(1, dim))
        self.blocks = nn.ModuleList(Block(dim, heads) for _ in range(depth))
        self.norm = nn.LayerNorm(dim, eps=1e-6)

    def forward(self, image):
        """Return one feature per patch, shaped (patches, dim)."""
        height, width = image.shape[2] // PATCH_SIZE, image.shape[3] // PATCH_SIZE
        x = self.patch_embed.proj(image).flatten(2).transpose(1, 2)
        side = int((self.pos_embed.shape[1] - 1) ** 0.5)
        grid = self.pos_embed[:, 1:].reshape(1, side, side, -1).permute(0, 3, 1, 2).cpu()
        grid = F.interpolate(grid, size=(height, width), mode="bicubic", align_corners=False)
        positions = torch.cat([self.pos_embed[:, :1], grid.flatten(2).transpose(1, 2).to(x.device)], 1)
        x = torch.cat([self.cls_token.expand(len(x), -1, -1), x], 1) + positions
        x = torch.cat([x[:, :1], self.register_tokens.expand(len(x), -1, -1), x[:, 1:]], 1)
        for block in self.blocks:
            x = block(x)
        return self.norm(x)[0, 1 + REGISTERS:]


def download_weights(url, weights_folder):
    """Return the weights file in weights_folder, downloading it there first if it's missing.

    Uses certifi's certificates, because some Python installs on macOS have none configured.
    """
    path = Path(weights_folder) / url.rsplit("/", 1)[-1]
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {path.name} to {path.parent}")
        partial = path.with_name(path.name + ".part")
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(url, context=context, timeout=600) as response, open(partial, "wb") as file:
            shutil.copyfileobj(response, file)
        partial.replace(path)
    return path


def load_model(weights_folder, device):
    state = torch.load(download_weights(WEIGHTS_URL, weights_folder), map_location="cpu", weights_only=True)
    model = DinoV2Small14()
    model.load_state_dict(state, strict=True)
    return model.to(device).eval()


@torch.no_grad()
def patch_features(model, image, device):
    """Return unit-length patch features and the patch grid's (rows, columns)."""
    height, width = image.shape[:2]
    scale = LONG_SIDE / max(height, width)
    rows, columns = (max(round(side * scale / PATCH_SIZE), 1) for side in (height, width))
    resized = cv2.resize(image, (columns * PATCH_SIZE, rows * PATCH_SIZE), interpolation=cv2.INTER_CUBIC)
    rgb = resized[:, :, ::-1] / 255.0
    tensor = torch.tensor(((rgb - IMAGENET_MEAN) / IMAGENET_STD).transpose(2, 0, 1)[None],
                          dtype=torch.float32, device=device)
    return F.normalize(model(tensor), dim=1).cpu().numpy(), (rows, columns)


def colorize(similarity):
    low, high = COLOR_RANGE
    levels = (np.clip((similarity - low) / (high - low), 0, 1) * 255).astype(np.uint8)
    # Each patch stays a visible square, the size DINO saw it.
    levels = cv2.resize(levels, None, fx=HEATMAP_BLOCK, fy=HEATMAP_BLOCK, interpolation=cv2.INTER_NEAREST)
    return cv2.applyColorMap(levels, cv2.COLORMAP_INFERNO)


def crop(image, box):
    """Return the part of an image inside a box, with the box limited to the image."""
    height, width = image.shape[:2]
    x1, y1, x2, y2 = box
    return image[max(math.floor(y1), 0):min(math.ceil(y2), height),
                 max(math.floor(x1), 0):min(math.ceil(x2), width)]


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


def locate_peaks(crop):
    smooth = blur(crop["similarity"], SMOOTH_SIGMA)
    minimum = np.percentile(smooth[crop["grid_mask"]], PEAK_PERCENTILE)
    return peak_candidates(crop, smooth, find_peaks(smooth, crop["grid_mask"], minimum))


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


def refine_grabcut(window):
    similarity = window["similarity"]
    mask = np.full(similarity.shape, cv2.GC_PR_BGD, np.uint8)
    mask[similarity >= midpoint_threshold(similarity, window["seed"])] = cv2.GC_PR_FGD
    mask[seed_disc(similarity.shape, window["seed"])] = cv2.GC_FGD
    mask[:BORDER], mask[-BORDER:], mask[:, :BORDER], mask[:, -BORDER:] = [cv2.GC_BGD] * 4
    cv2.setRNGSeed(0)
    cv2.grabCut(window["image"], mask, None, np.zeros((1, 65)), np.zeros((1, 65)), 5, cv2.GC_INIT_WITH_MASK)
    return region_at_seed((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), window["seed"])


def refine_heatmap_level(window, share=0.5):
    """Trace the smoothed heatmap at a share of the height from its window median to this peak."""
    similarity = window["similarity"]
    x, y = window["seed"]
    peak = similarity[max(y - 4, 0):y + 5, max(x - 4, 0):x + 5].max()
    level = np.median(similarity) + share * (peak - np.median(similarity))
    return region_at_seed(similarity >= level, window["seed"])


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


def animal_entry(piece, animal, offset, features, grid, reference):
    """Build one animal's working data from its crop, YOLOE outline and DINOv2 features."""
    mask = np.zeros(piece.shape[:2], np.uint8)
    cv2.fillPoly(mask, [np.array(animal["polygon"], np.int32) - offset], 1)
    similarity = (features @ reference).reshape(grid).astype(np.float32)
    return {
        "image": piece, "offset": np.array(offset), "mask": mask, "grid": grid, "similarity": similarity,
        "full_similarity": cv2.resize(similarity, piece.shape[1::-1], interpolation=cv2.INTER_CUBIC),
        "grid_mask": cv2.resize(mask.astype(np.float32), grid[::-1], interpolation=cv2.INTER_AREA) > 0.5,
        "long_side": max(piece.shape[:2]),
    }


def locate(entry):
    """Return up to two located eyes, each with its peak score."""
    if not entry["grid_mask"].any():
        return []
    return [{"center": list(e["center"]), "polygon": None, "score": e["score"]}
            for e in select_eyes(entry, locate_peaks(entry))]


def eye_patches(piece, offset, eyes, features, grid):
    """Return the patch features under YOLOE's eye outlines in one crop."""
    mask = np.zeros(piece.shape[:2], np.uint8)
    for eye in eyes:
        cv2.fillPoly(mask, [np.array(eye["polygon"], np.int32) - offset], 1)
    cover = cv2.resize(mask.astype(np.float32), grid[::-1], interpolation=cv2.INTER_AREA).ravel()
    return features[cover > MIN_EYE_COVER]


def normalized_mean(patches):
    mean = np.vstack(patches).mean(0)
    return mean / np.linalg.norm(mean)


def crop_offset(animal):
    return [max(math.floor(v), 0) for v in animal["box"][:2]]
