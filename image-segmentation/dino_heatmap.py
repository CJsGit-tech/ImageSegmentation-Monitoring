"""Export a DINO eye heatmap for every image in results/cropped_animals/.

DINO ViT-S/8 turns each 8x8 patch of a crop into a feature. Eye features are averaged from
the YOLOE eye outlines in results/yoloe/*.json, and a patch is bright when it is similar to that
average. No model is trained. Each crop's reference eyes come only from other source images,
so a crop is never compared with its own eyes.

Run segment_animals_eyes.py and crop_animals.py first. Usage, from anywhere:
    .venv/bin/python image-segmentation/dino_heatmap.py
"""

import json
import math

import cv2
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from crop_animals import CROP_CLASSES, OUTPUT as CROPS
from segment_animals_eyes import ROOT, load_config

OUTPUT = ROOT / "results" / "heatmaps" / "dino_v1"
WEIGHTS_URL = "https://dl.fbaipublicfiles.com/dino/dino_deitsmall8_pretrain/dino_deitsmall8_pretrain.pth"
PATCH_SIZE = 8
LONG_SIDE = 448            # Crops are resized to this before DINO, so small eyes span more patches.
MIN_EYE_COVER = 0.5        # Share of a patch that must be eye for it to count as a reference.
COLOR_RANGE = (0.1, 0.7)   # Similarity shown from darkest to brightest, the same for every crop.
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406])
IMAGENET_STD = np.array([0.229, 0.224, 0.225])


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


class Block(nn.Module):
    def __init__(self, dim, heads):
        super().__init__()
        self.norm1 = nn.LayerNorm(dim, eps=1e-6)
        self.attn = Attention(dim, heads)
        self.norm2 = nn.LayerNorm(dim, eps=1e-6)
        self.mlp = Mlp(dim)

    def forward(self, x):
        x = x + self.attn(self.norm1(x))
        return x + self.mlp(self.norm2(x))


class PatchEmbed(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.proj = nn.Conv2d(3, dim, PATCH_SIZE, PATCH_SIZE)


class DinoViTSmall8(nn.Module):
    """DINO's ViT-S/8. Loading the weights with strict=True confirms the layers match."""

    def __init__(self, dim=384, depth=12, heads=6):
        super().__init__()
        self.patch_embed = PatchEmbed(dim)
        self.cls_token = nn.Parameter(torch.zeros(1, 1, dim))
        self.pos_embed = nn.Parameter(torch.zeros(1, 785, dim))
        self.blocks = nn.ModuleList(Block(dim, heads) for _ in range(depth))
        self.norm = nn.LayerNorm(dim, eps=1e-6)

    def forward(self, image):
        """Return one feature per patch, shaped (patches, dim)."""
        height, width = image.shape[2] // PATCH_SIZE, image.shape[3] // PATCH_SIZE
        x = self.patch_embed.proj(image).flatten(2).transpose(1, 2)
        side = int((self.pos_embed.shape[1] - 1) ** 0.5)
        # Positions were learned on a square grid, so stretch them to fit this crop.
        grid = self.pos_embed[:, 1:].reshape(1, side, side, -1).permute(0, 3, 1, 2).cpu()
        grid = F.interpolate(grid, size=(height, width), mode="bicubic", align_corners=False)
        positions = torch.cat([self.pos_embed[:, :1], grid.flatten(2).transpose(1, 2).to(x.device)], 1)
        x = torch.cat([self.cls_token.expand(len(x), -1, -1), x], 1) + positions
        for block in self.blocks:
            x = block(x)
        return self.norm(x)[0, 1:]


def load_model(weights_folder, device):
    state = torch.hub.load_state_dict_from_url(
        WEIGHTS_URL, model_dir=str(weights_folder), map_location="cpu", weights_only=True)
    model = DinoViTSmall8()
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


def eye_mask(path, image_shape, json_folder):
    """Return a crop-sized mask of the reference eyes that fall inside this crop.

    A crop is named <image>_<class>_<number>, where number counts the kept animals in its JSON.
    """
    stem, _, number = path.stem.rsplit("_", 2)
    prediction = json.loads((json_folder / f"{stem}.json").read_text())
    animals = [a for a in prediction["animals"] if a["class"] in CROP_CLASSES]
    x1, y1 = animals[int(number) - 1]["box"][:2]
    mask = np.zeros(image_shape[:2], np.uint8)
    for eye in prediction["eyes"]:
        polygon = np.array(eye["polygon"], np.int32) - [max(math.floor(x1), 0), max(math.floor(y1), 0)]
        cv2.fillPoly(mask, [polygon], 1)
    return stem, mask


def colorize(similarity):
    low, high = COLOR_RANGE
    levels = (np.clip((similarity - low) / (high - low), 0, 1) * 255).astype(np.uint8)
    # Each patch stays a visible square, the size DINO saw it.
    levels = cv2.resize(levels, None, fx=PATCH_SIZE, fy=PATCH_SIZE, interpolation=cv2.INTER_NEAREST)
    return cv2.applyColorMap(levels, cv2.COLORMAP_INFERNO)


def main():
    config = load_config()
    paths = sorted(p for p in CROPS.iterdir() if p.suffix.lower() in config["image_suffixes"])
    if not paths:
        raise SystemExit(f"No crops found in {CROPS}. Run crop_animals.py first.")

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Running on execution device: {device.upper()}")
    model = load_model(config["model"].parent, device)

    crops = []
    for path in paths:
        image = cv2.imread(str(path))
        if image is None:
            print(f"{path.name}: skipped, not a readable image")
            continue
        stem, mask = eye_mask(path, image.shape, config["output"])
        features, grid = patch_features(model, image, device)
        cover = cv2.resize(mask.astype(np.float32), grid[::-1], interpolation=cv2.INTER_AREA).ravel()
        crops.append({"path": path, "source": stem, "features": features, "grid": grid,
                      "eye_patches": features[cover > MIN_EYE_COVER]})

    OUTPUT.mkdir(exist_ok=True)
    for crop in crops:
        references = [c["eye_patches"] for c in crops if c["source"] != crop["source"]]
        references = np.vstack(references) if references else np.empty((0, 384))
        if not len(references):
            print(f"{crop['path'].name}: skipped, no reference eyes in other images")
            continue
        eye_feature = references.mean(0)
        eye_feature /= np.linalg.norm(eye_feature)
        similarity = (crop["features"] @ eye_feature).reshape(crop["grid"])
        cv2.imwrite(str(OUTPUT / f"{crop['path'].stem}.png"), colorize(similarity))
        print(f"{crop['path'].stem}.png: {len(references)} reference patches, "
              f"similarity {similarity.min():.2f} to {similarity.max():.2f}")

    print(f"Heatmaps saved to: {OUTPUT}")


if __name__ == "__main__":
    main()
