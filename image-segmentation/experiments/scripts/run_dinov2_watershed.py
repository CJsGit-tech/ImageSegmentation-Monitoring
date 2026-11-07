"""Run the local peaks + watershed experiment with DINOv2 in place of DINO.

Only the model changes: DINOv2 ViT-S/14 with registers. Crops are resized to LONG_SIDE so the
patch grid stays at 56 patches, the same as DINO ViT-S/8 at 448 pixels, and the settings in
run_eye_experiments.py keep the same meaning. Results go to outputs/experiments/14_dinov2_peaks_watershed/.

Run run_yoloe_test_samples.py and crop_animals.py first. Usage, from anywhere:
    .venv/bin/python image-segmentation/experiments/scripts/run_dinov2_watershed.py
"""

import sys
from pathlib import Path

EXPERIMENTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXPERIMENTS.parent / "submission" / "scripts"))  # Shared YOLOE and measurement code.

import cv2
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

import run_eye_experiments as experiments
from dino_heatmap import IMAGENET_MEAN, IMAGENET_STD, Attention, Mlp, PatchEmbed
from run_yoloe_test_samples import load_config

NAME = "14_dinov2_peaks_watershed"
WEIGHTS_URL = "https://dl.fbaipublicfiles.com/dinov2/dinov2_vits14/dinov2_vits14_reg4_pretrain.pth"
PATCH_SIZE = 14
LONG_SIDE = 784
REGISTERS = 4


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
        self.patch_embed.proj = nn.Conv2d(3, dim, PATCH_SIZE, PATCH_SIZE)
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


def load_model(weights_folder, device):
    from dinov2_eye_pipeline import download_weights  # Shared downloader in submission/scripts.

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


def main():
    config = load_config()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Running on execution device: {device.upper()}")
    crops = experiments.load_crops(config, load_model(config["model"].parent, device), device, patch_features)

    located = [experiments.run_locator(crop, experiments.locate_peaks) for crop in crops]
    refined = [experiments.run_refiner(crop, experiments.refine_watershed, eyes) for crop, eyes in zip(crops, located)]
    print(experiments.save_experiment(NAME, crops, refined))

    peak_scores = [experiments.score_crop(crop, eyes) for crop, eyes in zip(crops, located)]
    print("Peaks alone:", experiments.summarize("peaks", crops, peak_scores, located))
    print(f"Results saved to: {experiments.OUTPUT / NAME}")


if __name__ == "__main__":
    main()
