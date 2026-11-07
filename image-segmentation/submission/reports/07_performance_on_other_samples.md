# 7. Speed and accuracy on the other samples

This adds speed and accuracy checks on `data/002-other_samples/`, supporting the evaluation formulas in
[report 5](05_model_and_evaluation.md) and the validation in [report 6](06_measurement_and_validation.md).

## Summary

| Method | FPS | Eye precision | Eye recall | Eye F1 | Eye distance MAE |
|---|---|---|---|---|---|
| YOLOE | 5.56 | 77.8% | 22.6% | 0.35 | 1.95 px (2 animals) |
| YOLOE + DINOv2, GrabCut | 1.98 | 71.9% | 74.2% | 0.73 | 1.71 px (8 animals) |
| YOLOE + DINOv2, heatmap level | 3.95 | 71.9% | 74.2% | 0.73 | 2.17 px (9 animals) |

The eye scores are for the 9 new COCO images. They use draft eye marks that no person has reviewed,
so treat them as estimates.

- **YOLOE is the fastest,** but it found less than a quarter of the marked eyes.
- **YOLOE + DINOv2 finds about three times as many eyes,** with slightly lower precision.
- **Heatmap level is twice as fast as GrabCut,** with the same eyes found.
- **YOLOE's animal outlines are accurate:** all 19 it found match a COCO cat or dog with the right
  label, and it found 19 of 21.

## Speed

**Setup:** Apple M4 Pro, using its GPU through MPS, with PyTorch 2.15.0 and Ultralytics 8.4.203.
Each of the 12 images ran 3 times after a warm-up, and the median time per image was kept. Model
loading and building the DINOv2 reference eyes happen once, so they aren't counted.

| Method | Mean per image | Median | Slowest | FPS |
|---|---|---|---|---|
| YOLOE | 179.9 ms | 178.7 ms | 255.2 ms | 5.56 |
| YOLOE + DINOv2, GrabCut | 506.1 ms | 544.3 ms | 737.0 ms | 1.98 |
| YOLOE + DINOv2, heatmap level | 253.3 ms | 266.2 ms | 324.1 ms | 3.95 |

$$FPS = \frac{1000}{\text{mean milliseconds per image}}$$

**Where the time goes**, as the mean per image:

| Step | Time | Used by |
|---|---|---|
| YOLOE animal pass, 640 px | 49.6 ms | Both methods |
| YOLOE eye pass, 1280 px | 130.3 ms | YOLOE |
| DINOv2 features, 784 px | 96.8 ms per animal | YOLOE + DINOv2 |
| Locating eyes in the heatmap | 0.6 ms | YOLOE + DINOv2 |
| GrabCut outlines | 122.0 ms per animal | GrabCut variant |
| Heatmap level outlines | 0.6 ms per animal | Heatmap level variant |

- **YOLOE:** the large eye pass takes most of its time.
- **YOLOE + DINOv2:** time grows with the number of animals, because each is processed separately.
  The image with 4 dogs took 737 ms with GrabCut.
- **GrabCut** runs on the CPU and is the slowest step, while heatmap level is almost free.

These times are for one Mac. Speeds on other hardware, especially edge devices, need their own test.

## Animal accuracy

YOLOE's cat and dog outlines were compared with COCO's masks on the 9 new COCO images. The test
sample copies have no COCO IDs, so they're excluded. Both methods use the same YOLOE animals.

| Measure | Result |
|---|---|
| COCO cats and dogs | 21 |
| YOLOE animals | 19 |
| Matched at mask IoU ≥ 0.5 | 19, all with the right label |
| Precision | 100% |
| Recall | 90.5% |
| F1 | 0.95 |
| Mean mask IoU of matches | 0.82 |
| Mask AP50 | 0.901 |
| Mask AP75 | 0.718 |
| Mask AP50-95 | 0.597 |

The two misses are a white dog at the edge of `000000107226.jpg`, and a second cat in
`000000333772.jpg`, which YOLOE merged with the first into one outline (IoU 0.58). AP is
calculated per class with COCO's 101-point method, then averaged over cats and dogs. With only 21
animals, one more hit or miss changes these scores noticeably.

## Eye accuracy

Each method's saved eyes were compared with eye boxes in
[`data/002-other_samples/eye_marks.json`](../data/002-other_samples/eye_marks.json). An eye is a hit
when its center falls inside a box, and each box counts once.

**9 new COCO images, 31 marked eyes**

| Method | Hits | False | Missed | Precision | Recall | F1 | Center error | Box IoU | Eye distance MAE | Right-eye MAE |
|---|---|---|---|---|---|---|---|---|---|---|
| YOLOE | 7 | 2 | 24 | 77.8% | 22.6% | 0.35 | 2.25 px | 0.51 | 1.95 px (2 animals) | None (0 pairs) |
| YOLOE + DINOv2, GrabCut | 23 | 9 | 8 | 71.9% | 74.2% | 0.73 | 1.99 px | 0.46 | 1.71 px (8 animals) | 1.25 px (2 pairs) |
| YOLOE + DINOv2, heatmap level | 23 | 9 | 8 | 71.9% | 74.2% | 0.73 | 1.75 px | 0.31 | 2.17 px (9 animals) | 1.73 px (2 pairs) |

**All 12 images, including the test sample copies, 43 marked eyes**

| Method | Hits | False | Missed | Precision | Recall | F1 | Eye distance MAE | Right-eye MAE |
|---|---|---|---|---|---|---|---|---|
| YOLOE | 16 | 2 | 27 | 88.9% | 37.2% | 0.53 | 2.33 px (6 animals) | 1.82 px (2 pairs) |
| YOLOE + DINOv2, GrabCut | 35 | 9 | 8 | 79.5% | 81.4% | 0.81 | 2.36 px (14 animals) | 2.20 px (5 pairs) |
| YOLOE + DINOv2, heatmap level | 35 | 9 | 8 | 79.5% | 81.4% | 0.81 | 2.19 px (15 animals) | 2.25 px (5 pairs) |

- **Distance errors** count only animals whose two eyes both hit the same marked animal, so the
  rows cover different animals.
- **Unscored eyes:** YOLOE + DINOv2 had 4 eyes inside ignore boxes, where the faces were too dark to
  mark.
- **Same eyes in both variants:** they start from the same eye locations, so hits and false eyes are
  identical. Only the outlines, and so the centers and distances, differ.

## How to trust these numbers

- **Draft marks:** Claude Code marked the eyes from zoomed views, and no person has reviewed them.
  Some eyes are only 3-5 px wide, closed or half closed, so the boxes are approximate.
  [`eye_marks_review.jpg`](../outputs/002-other_samples_performance/eye_marks_review.jpg) shows every
  mark for checking.
- **Ignore boxes:** in 2 images, faces were too dark to mark, so eyes there weren't scored. That
  favours YOLOE + DINOv2 slightly, because it output eyes there and YOLOE didn't.
- **Small set:** 9 new images, 21 animals and 31 marked eyes.
- **Duplicates:** the "all 12 images" rows include the test samples, whose settings were tuned on
  them, so the 9-image rows are the fairer estimate.

## Files

All results are in [`outputs/002-other_samples_performance/`](../outputs/002-other_samples_performance):

| File | Contents |
|---|---|
| `speed_summary.csv`, `speed_per_image.csv` | Speed of each method and each step |
| `animal_accuracy.csv`, `animal_matches.csv` | YOLOE animal outlines against COCO masks |
| `eye_accuracy.csv` | Eye scores for both image sets |
| `eye_marks_review.jpg` | The draft eye marks, for review |
| `environment.json` | Hardware, versions and settings |

To rerun, from the repository root:

```bash
.venv/bin/python image-segmentation/submission/scripts/evaluate_yoloe_and_dinov2_other_samples.py
```

COCO's annotation file is kept outside the submission, in
`image-segmentation/experiments/data/coco_annotations/`.
