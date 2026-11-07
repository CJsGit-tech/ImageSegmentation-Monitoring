# Archived eye experiments

These experiments are discarded from further investigation. Only two methods remain, in
`../selected/`:

- `17_dinov2_grabcut`: highest polygon overlap with YOLOE (mean IoU 0.66).
- `19_dinov2_heatmap_level`: closest to YOLOE's centers and distances (2.35 px and 0.28 px).

Eye counts use 12 cat and dog eyes on 3 images, against hand marks made by Claude Code and not
reviewed by a person. "Compared with YOLOE" scores use only the 9 eyes YOLOE detected, with no
hand marks, and measure agreement with YOLOE rather than accuracy. Settings were chosen on these
same images, so these results aren't final proof.

YOLOE baseline: 9 eyes hit, 0 false eyes.

## Missed or false eyes

| Experiment | Eyes hit | False eyes | Why archived |
|---|---|---|---|
| `01_threshold_components` | 11 | 1 | Missed an eye and added a false one, while local peaks hit all 12 |
| `03_difference_of_gaussians` | 8 | 4 | Fewer eyes hit than YOLOE |
| `04_simple_blob_detector` | 10 | 2 | Missed eyes and added false ones, while local peaks hit all 12 |
| `06_grabcut` | 11 | 1 | Missed an eye and added a false one, drifting to the 003 dog's nose |
| `08_superpixels` | 11 | 1 | Missed an eye and added a false one |
| `09_gradient_voting` | 11 | 1 | Missed an eye and added a false one, and its circles fit poorly (box IoU 0.10) |
| `10_hough_ellipse` | 5 | 5 | Fewer eyes hit than YOLOE, and no shape for 2 eyes |
| `11_mser` | 7 | 1 | Fewer eyes hit than YOLOE, and no shape for 4 eyes |
| `12_canny_contours` | 3 | 0 | Fewer eyes hit than YOLOE, and no outline for 9 eyes |
| `15_dinov2_original_784` | 10 | 0 | Used full images, missed 2 eyes, and crops scored better |
| `15_dinov2_original_1568` | 10 | 1 | Used full images, missed 2 eyes and added a false one, and crops scored better |
| `20_dinov2_threshold_components` | 11 | 0 | Missed an eye |
| `24_dinov2_pixel_clustering` | 11 | 1 | Missed an eye and added a false one, with outlines spreading into fur |

## DINO v1 replaced by DINOv2

These hit all 12 eyes with no false eyes, but DINOv2 versions of the same methods were available.

| Experiment | Why archived |
|---|---|
| `02_local_peaks` | DINO v1 locator with no outline, and 7.83 px distance error against the hand marks |
| `05_guided_filter` | DINO v1, replaced by `16_dinov2_guided_filter` |
| `07_watershed` | DINO v1, replaced by `14_dinov2_peaks_watershed` |
| `13_heatmap_level` | DINO v1, replaced by `19_dinov2_heatmap_level` |

## DINOv2 methods not selected

These also hit all 12 eyes with no false eyes. They led on neither polygon overlap nor closeness to
YOLOE's centers and distances.

| Experiment | Polygon IoU | Center offset | Distance difference | Why archived |
|---|---|---|---|---|
| `14_dinov2_peaks_watershed` | 0.58 | 2.76 px | 4.18 px | Overlap below GrabCut, and centers and distances far from YOLOE's |
| `16_dinov2_guided_filter` | 0.53 | 2.36 px | 0.52 px | Close to heatmap level, but behind it on all three measures |
| `18_dinov2_superpixels` | 0.54 | 2.41 px | 0.91 px | Overlap below GrabCut, and centers and distances behind heatmap level |
| `21_dinov2_difference_of_gaussians` | None | 4.99 px | 5.29 px | No outline, and the furthest from YOLOE |
| `22_dinov2_blob_detector` | None | 2.97 px | 1.68 px | No outline |
| `23_dinov2_watershed_canny` | 0.56 | 3.34 px | 3.89 px | Overlap below GrabCut, and centers and distances far from YOLOE's |

Against the hand marks, `14_dinov2_peaks_watershed` and `23_dinov2_watershed_canny` had the lowest
distance errors (1.71 and 1.48 px). They were archived because this selection compared with YOLOE,
and those marks haven't been reviewed.
