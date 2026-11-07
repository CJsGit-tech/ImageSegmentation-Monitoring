# 2. Animal and eye segmentation

## Overview

Two methods outline the eyes. Both use YOLOE to outline the animals.

| Method | Animal outlines | Eye outlines |
|---|---|---|
| **YOLOE** | YOLOE, prompts `bird, cat, dog` | YOLOE, prompt `eye` |
| **YOLOE + DINOv2** | YOLOE | DINOv2 heatmap peaks, outlined by either GrabCut or the heatmap level |

YOLOE + DINOv2 has two outline variants, and both are reported:

- **GrabCut:** separates each eye from its surroundings using the colors in a small window.
- **Heatmap level:** traces the heatmap at half the height of each eye's peak.

In the figures, animals are green and eyes are red.

## How YOLOE + DINOv2 works

```text
YOLOE animal outline → crop the animal → DINOv2 patch features
→ similarity to reference eyes (heatmap) → keep peaks inside the animal outline
→ pick up to 2 peaks → GrabCut or heatmap level → eye outline
```

No model was trained. Reference eyes are the DINOv2 features under YOLOE's eye outlines in the test
samples. On the test samples, a crop's references come only from the other test samples. On the
other samples, they come from all 3 test samples.

## Test samples

| Image | YOLOE | YOLOE + DINOv2, GrabCut | YOLOE + DINOv2, heatmap level |
|---|---|---|---|
| 001 | ![YOLOE on image 001](files/figures/samples/yoloe/001-coco-sample.jpg) | ![GrabCut on image 001](files/figures/samples/dinov2_grabcut/001-coco-sample.jpg) | ![Heatmap level on image 001](files/figures/samples/dinov2_heatmap_level/001-coco-sample.jpg) |
| 002 | ![YOLOE on image 002](files/figures/samples/yoloe/002-coco-sample.jpg) | ![GrabCut on image 002](files/figures/samples/dinov2_grabcut/002-coco-sample.jpg) | ![Heatmap level on image 002](files/figures/samples/dinov2_heatmap_level/002-coco-sample.jpg) |
| 003 | ![YOLOE on image 003](files/figures/samples/yoloe/003-coco-sample.jpg) | ![GrabCut on image 003](files/figures/samples/dinov2_grabcut/003-coco-sample.jpg) | ![Heatmap level on image 003](files/figures/samples/dinov2_heatmap_level/003-coco-sample.jpg) |

YOLOE found all 7 animals, including the bird. YOLOE + DINOv2 runs only on cats and dogs, so the
bird has no eye result from it.

## Other samples

| Method | Cats and dogs outlined | Eyes output | Animals with two eyes |
|---|---|---|---|
| YOLOE | 25 | 18 | 7 |
| YOLOE + DINOv2, either variant | 25, from YOLOE | 48 | 23 |

Both variants start from the same eye locations, so they output the same number of eyes. YOLOE also
found the bird in image 001. In `000000333772.jpg`, YOLOE drew one outline around 2 cats, so YOLOE +
DINOv2 treated them as one animal.

Overviews of all 12 images:

- [YOLOE](files/figures/other_samples/yoloe.jpg)
- [YOLOE + DINOv2, GrabCut](files/figures/other_samples/dinov2_grabcut.jpg)
- [YOLOE + DINOv2, heatmap level](files/figures/other_samples/dinov2_heatmap_level.jpg)
- [DINOv2 heatmaps, each beside its crop](files/figures/other_samples/dinov2_heatmaps.jpg)

Full-size results and JSON outlines are in [`outputs/`](../outputs).
