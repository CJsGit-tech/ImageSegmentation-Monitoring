# How we reached the current eye segmentation approach

The goal is to outline each animal's eyes accurately enough to measure the distance between
them. This records what was tried, what each step showed, and why the work moved on.

**Where it stands:** DINOv2 heatmaps with image processing on animal crops found all 12 cat and
dog eyes in the 3 sample images, with no false eyes and no model training. YOLOE found 9 of them.
On 38 new COCO images with at least 2 cats or dogs each, the two selected methods output far more
eyes than YOLOE, but also false ones, such as ears or body patches. Those images have no reviewed eye marks, so accuracy is still unmeasured. See
[Limits](#limits).

## The path

| Step | What we tried | What we learned | Next move |
|---|---|---|---|
| 1 | YOLOE text prompts | Animals were found well, but small eyes were often missed | Look for better eye detection |
| 2 | Crops, clustering, SAM 2.1, prompt-free YOLOE | None beat YOLOE's 9 eyes with 0 false | Study a different kind of method |
| 3 | Reading the STEGO code | STEGO builds on DINO features, so test those before training anything | Try DINO on the crops |
| 4 | DINO with k-means | Clusters didn't isolate eyes, which cover about 1% of a crop | Compare patches with known eyes instead |
| 5 | DINO similarity heatmaps | Eyes lit up in every crop, including ones YOLOE missed | Turn heatmaps into eye outlines |
| 6 | Binary images and histogram smoothing | Thresholds alone left specks, and histograms gave no reliable cutoff | Use spatial image processing |
| 7 | 13 DINO v1 experiments | Finding eyes was largely solved, but edges and distances weren't | Try a better feature model |
| 8 | DINOv2 + local peaks + watershed | Distance error fell from 5.47 to 1.71 px, with all 12 eyes still found | Check full images and other methods |
| 9 | DINOv2 on full images | Small animals suffered, so crops stay | Keep using `cropped_animals` |
| 10 | 9 no-install DINOv2 experiments | GrabCut gave the best outlines, and watershed the best distances against the marks | Compare with YOLOE without using marks |
| 11 | Ranking on the 9 eyes YOLOE found | GrabCut overlapped YOLOE most, and heatmap level matched its centers and distances most closely | Keep only these two |
| 12 | 38 new COCO images with 2+ cats or dogs | Both methods output far more eyes than YOLOE, including false ones | Prepare the submission |
| 13 | Final submission | YOLOE and YOLOE + DINOv2 kept, with both outline variants | Current position |

## Step details

### 1. YOLOE baseline

One YOLOE-26 model searched for the animal names at 640 px and "eye" at 1280 px. It found all 7
animals and 9 of 13 eyes with no false detections. Counting only cats and dogs, it found 9 of
12 eyes. All 4 misses were in image 001: one dog eye, both cat eyes and the bird's eye.

### 2. Other eye detection attempts

Each option was scored as correct eyes and false eyes, or as noted.

| Attempt | Correct | False | Result |
|---|---|---|---|
| YOLOE on animal crops | 7 | 0 | Fewer eyes than the whole image |
| Scikit-image style clustering | 5 | 9 | Too many false eyes |
| SAM 2.1 | 4 | 4 | Too many false eyes |
| YOLOE prompt-free | 5 | 0 | Also found only 5 of 7 animals |

### 3. STEGO

STEGO groups pixels into classes without labels by training a small network on frozen DINO
features. It needs a large image set, and nothing forces it to create an "eye" class. It also
needed porting to run on a Mac, so we tested its foundation, DINO, without any training.

### 4. DINO with k-means

K-means on DINO ViT-S/8 features didn't produce an eye cluster. These scores are optimistic,
because the best cluster was chosen using the eye locations.

| Input size | IoU | Precision | Recall | Eyes hit | False blobs |
|---|---|---|---|---|---|
| 224 px | 0.13 | 17% | 36% | 2 of 10 | 5 |
| 672 px | 0.12 | 13% | 67% | 6 of 10 | 87 |

### 5. Similarity heatmaps

Averaging the features of known eyes from one image, then comparing every patch of another
image with that average, worked far better. Eye patches outscored background patches about
99.9% of the time at 224 px and 96% at 672 px. This is the discovery the current approach
builds on. It needs reference eyes but no training.

The reference eyes come from YOLOE's outlines in other images, so a crop is never compared with
its own eyes.

### 6. Binary images and histogram smoothing

Keeping each heatmap's brightest 1.5% of pixels showed eye blobs, but also left specks. A check
of the histograms found that valleys appeared or vanished with the smoothing level, and that
Otsu left 34-65% of each heatmap white. Eyes are too small a share of the pixels to form their
own hump, and a histogram can't tell where a pixel is. So the work moved to spatial methods.

### 7. DINO v1 experiments

Four locators found eyes in the heatmap. Then 9 outline methods started from the best locator,
local peaks. Scores count 12 cat and dog eyes.

| Experiment | Eyes hit | False eyes | No outline | Box IoU | Status |
|---|---|---|---|---|---|
| `01_threshold_components` | 11 | 1 | 0 | 0.51 | Archived |
| `02_local_peaks` | 12 | 0 | 0 | None | Archived |
| `03_difference_of_gaussians` | 8 | 4 | 0 | None | Archived |
| `04_simple_blob_detector` | 10 | 2 | 0 | None | Archived |
| `05_guided_filter` | 12 | 0 | 0 | 0.54 | Archived |
| `06_grabcut` | 11 | 1 | 0 | 0.55 | Archived |
| `07_watershed` | 12 | 0 | 0 | 0.50 | Archived |
| `08_superpixels` | 11 | 1 | 0 | 0.60 | Archived |
| `09_gradient_voting` | 11 | 1 | 0 | 0.10 | Archived |
| `10_hough_ellipse` | 5 | 5 | 2 | 0.50 | Archived |
| `11_mser` | 7 | 1 | 4 | 0.35 | Archived |
| `12_canny_contours` | 3 | 0 | 9 | 0.50 | Archived |
| `13_heatmap_level` | 12 | 0 | 0 | 0.50 | Archived |

Local peaks beat YOLOE at finding eyes, but every outline method had about twice YOLOE's
distance error. Canny failed because its edges were too few or didn't close around the eye,
partly because of its thresholds. Tracing the heatmap always gave a closed outline, but a looser
one.

### 8. DINOv2

Only the model changed: DINOv2 ViT-S/14 with registers, at 784 px to keep the same 56-patch
grid. The heatmaps became much cleaner, with a mostly dark background and clear eye glows.

| Local peaks + watershed | Eyes hit | False eyes | Distance MAE (4 animals) | Centre error (9 eyes) | Box IoU (9 eyes) |
|---|---|---|---|---|---|
| YOLOE | 9 | 0 | 2.52 px | 1.98 px | 0.65 |
| DINO v1 | 12 | 0 | 5.47 px | 4.13 px | 0.53 |
| DINOv2 | 12 | 0 | 1.71 px | 2.15 px | 0.53 |

YOLOE's row uses its own polygons rather than local peaks and watershed.

### 9. Full images

Skipping the crop step scored lower at both sizes. The fixed peak spacing likely caused the
misses at 784 px, because it left no room for a second peak on closely spaced eyes, so this
wasn't a fully fair test. Small animals are also weaker on full images: the 001 cat's eyes are
only about 7 px wide, while each patch covers about 11 px of the original.

| Input | Eyes hit | False eyes | Animals with both eyes |
|---|---|---|---|
| Crops, 784 px | 12 | 0 | 6 |
| Full image, 784 px | 10 | 0 | 4 |
| Full image, 1568 px | 10 | 1 | 5 |

### 10. DINOv2 experiments

The test list was limited to image processing and smoothing, excluding new AI models and
anything needing an install. DenseCRF was left out because `pydensecrf` may be hard to install.

| Experiment | Eyes hit | False eyes | Distance MAE (4 animals) | Centre error | Box IoU | Status |
|---|---|---|---|---|---|---|
| `14_dinov2_peaks_watershed` | 12 | 0 | 1.71 px | 2.15 px | 0.53 | Archived |
| `16_dinov2_guided_filter` | 12 | 0 | 3.04 px | 2.64 px | 0.63 | Archived |
| `17_dinov2_grabcut` | 12 | 0 | 4.31 px | 3.20 px | 0.69 | Selected |
| `18_dinov2_superpixels` | 12 | 0 | 2.68 px | 2.44 px | 0.55 | Archived |
| `19_dinov2_heatmap_level` | 12 | 0 | 2.67 px | 2.50 px | 0.60 | Selected |
| `20_dinov2_threshold_components` | 11 | 0 | 3.40 px (3 animals) | 2.85 px | 0.62 | Archived |
| `21_dinov2_difference_of_gaussians` | 12 | 0 | 2.78 px | 4.27 px | None | Archived |
| `22_dinov2_blob_detector` | 12 | 0 | 3.44 px | 3.27 px | None | Archived |
| `23_dinov2_watershed_canny` | 12 | 0 | 1.48 px | 2.77 px | 0.53 | Archived |
| `24_dinov2_pixel_clustering` | 11 | 1 | 3.72 px | 4.99 px | 0.53 | Archived |

Centre error and box IoU use the 9 eyes YOLOE found, and experiments 20 and 24 hit only 8 of them.

### 11. Ranking on YOLOE's eyes

This used only the 9 eyes YOLOE detected, with no hand marks, so it measures agreement with YOLOE
rather than accuracy. `17_dinov2_grabcut` had the highest mean polygon IoU (0.66).
`19_dinov2_heatmap_level` had the closest centers (2.35 px) and distances (0.28 px). These two were
selected, and the rest were moved to the archive with the reasons in its README.

### 12. New COCO test images

Images had to show at least 2 real cats or dogs. COCO val2017 has 55 images annotated that way, and
50 were chosen at random. A visual check by Claude Code then removed 12 that don't really show 2:
reflections, toys, framed pictures, one animal boxed twice, or animals too small to see. The reasons
are in `select_coco_images.py`.

That left 38 images: 12 with only cats, 18 with only dogs and 8 with both. COCO lists 88 cats and
dogs across them, and YOLOE found 76, with no cat or dog in 2 images. Both DINOv2 methods used
YOLOE's animals. Their reference eyes came from the 3 sample images, and no settings were changed.

| Method | Eyes output | Animals with two eyes | Matched YOLOE's 14 eyes | Other eyes | Polygon IoU with YOLOE | Center offset | Distance difference (4 animals) |
|---|---|---|---|---|---|---|---|
| YOLOE | 14 | 4 | Reference | Reference | Reference | Reference | Reference |
| DINOv2 + GrabCut | 128 | 52 | 14 | 114 | 0.66 | 2.41 px | 1.76 px |
| DINOv2 + heatmap level | 128 | 52 | 14 | 114 | 0.49 | 1.58 px | 1.56 px |

Both DINOv2 methods start from the same peaks, so they output the same number of eyes. They always
output at least one eye per animal, even if no eye is visible, so some of the other eyes are false,
such as ears or body patches. These images have no reviewed eye marks, so accuracy is unmeasured.

## Current best options

| Goal | Experiment | Why |
|---|---|---|
| Closest outlines to YOLOE | `17_dinov2_grabcut` | Highest polygon overlap on both the samples and the 38 images |
| Closest measurements to YOLOE | `19_dinov2_heatmap_level` | Closest centers and distances on both the samples and the 38 images |

## Limits

- **Small test:** 6 crops from 3 images, with settings first chosen on these same images.
- **No marks on the 38 images:** their results show agreement with YOLOE and output counts, not accuracy.
- **No way to report "no eye":** the DINOv2 methods always output an eye, so false eyes appear on
  animals facing away or with hidden eyes.
- **Depends on YOLOE's animals:** an animal YOLOE misses gets no eyes from the DINOv2 methods.
- **Unreviewed marks:** Claude Code made the eye marks from zoomed views, and no person has
  checked them. Hits, false eyes, distance error, centre error and box IoU all depend on them.
- **The 002 dog is uncertain:** the mark gives 78.5 px, YOLOE and several other methods give
  about 73 px, and watershed gives about 83 px. The distance ranking could change after a review.
- **Lenient hit rule:** an eye counts as hit if its centre falls inside the mark, so a poor outline
  can still count.
- **Not independent of YOLOE:** it supplies the animal outlines and the reference eyes.
- **Cats and dogs only:** the bird hasn't been tested with DINOv2 heatmaps.
- **Pixels only:** distances aren't in real-world units.

## Next steps

1. Have a person mark or review the eyes on the 38 images, so accuracy can be measured.
2. Have a person review or replace the marks on the 3 sample images.

### 13. Final submission

The submission keeps only YOLOE and YOLOE + DINOv2, with both the GrabCut and heatmap level outline
variants. Its other samples are 9 of the 38 test images plus copies of the 3 test samples, rerun
with the final scripts.

## Folders and scripts

| Location | Contents |
|---|---|
| `submission/scripts/` | The final pipeline for YOLOE and YOLOE + DINOv2 |
| `submission/outputs/` | Final results on the test samples and other samples |
| `experiments/scripts/` | The scripts for the experiments, heatmap comparisons, binary images and image selection |
| `experiments/outputs/experiments/selected/` | The original runs of the two selected outline variants |
| `experiments/outputs/experiments/archive/` | All other experiments, with the reasons in its README |
| `experiments/outputs/experiments/*.csv` | Experiment summaries, with distance error averaged over animals with both eyes correct |

The experiment scripts write new results directly to `experiments/outputs/experiments/`, so a rerun
doesn't update the `selected/` or `archive/` folders.
