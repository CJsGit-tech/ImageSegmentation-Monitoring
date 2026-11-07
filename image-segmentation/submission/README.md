# Animal and Eye Segmentation with Distance Measurement

This folder segments animals and their eyes in COCO images with at least 2 animals, measures each
animal's eye distance and the right-eye distance between animals, and explains the models and
measurements.

- **Reports:** the detailed write-ups in [`reports/`](reports), including
  [speed and accuracy on the other samples](reports/07_performance_on_other_samples.md)

## Final methods

| Method | Animal outlines | Eye outlines |
|---|---|---|
| **YOLOE** | YOLOE, prompts `bird, cat, dog` | YOLOE, prompt `eye` |
| **YOLOE + DINOv2** | YOLOE | DINOv2 heatmap peaks, outlined by GrabCut or the heatmap level |

YOLOE + DINOv2 has two outline variants:

- **GrabCut:** separates each eye from its surroundings using the colors in a small window. It
  matches YOLOE's eye outlines most closely.
- **Heatmap level:** traces the heatmap at half the height of each eye's peak. It matches YOLOE's eye
  centers and distances most closely.

No model was trained. [Report 5](reports/05_model_and_evaluation.md) explains why these models were
chosen, and [the experiment history](reports/supporting/experiment_history.md) covers the methods
that were tried and dropped.

## Results at a glance

**Test samples:** 3 images, with 12 hand-marked cat and dog eyes.

| Method | Eyes hit (of 12) | False eyes | Eye distance MAE, same 4 animals | Right-eye MAE |
|---|---|---|---|---|
| YOLOE | 9 | 0 | 2.52 px | 1.82 px (2 pairs) |
| YOLOE + DINOv2, GrabCut | 12 | 0 | 4.31 px | 2.92 px (3 pairs) |
| YOLOE + DINOv2, heatmap level | 12 | 0 | 2.67 px | 2.09 px (3 pairs) |

**Other samples:** 12 images, without eye marks.

| Method | Eyes output | Animals with two eyes (of 25) | Right-eye pairs |
|---|---|---|---|
| YOLOE | 18 | 7 | 2 |
| YOLOE + DINOv2, either variant | 48 | 23 | 15 |

**Speed and accuracy on the 9 new COCO images** in the other samples:

| Method | FPS | Eye precision | Eye recall | Eye F1 | Eye distance MAE |
|---|---|---|---|---|---|
| YOLOE | 5.56 | 77.8% | 22.6% | 0.35 | 1.95 px (2 animals) |
| YOLOE + DINOv2, GrabCut | 1.98 | 71.9% | 74.2% | 0.73 | 1.71 px (8 animals) |
| YOLOE + DINOv2, heatmap level | 3.95 | 71.9% | 74.2% | 0.73 | 2.17 px (9 animals) |

YOLOE's cat and dog outlines match COCO's masks well: 19 of 21 found, all correctly labelled, with
mask AP50 of 0.901 and AP50-95 of 0.597. Speeds are on an Apple M4 Pro using MPS.
[Report 7](reports/07_performance_on_other_samples.md) has the details.

- **Unreviewed marks:** Claude Code made all the eye marks, including drafts for the other samples,
  and no person has reviewed them.
- **Duplicates:** the other samples include copies of the 3 test samples.

## Limitations

### YOLOE

- **Misses many eyes:** it found 9 of 13 marked test sample eyes, and only 7 of 31 marked eyes on the
  9 new COCO images (recall 22.6%). Without both eyes, an animal can't be measured.
- **No right-eye pairs on new images:** its 2 pairs on the other samples both come from copies of the
  test samples.
- **Low eye confidence:** eye confidence on the test samples is only 0.12-0.35, so the threshold must
  stay at 0.1.
- **Can merge animals:** in `000000333772.jpg`, it drew one outline around 2 cats.
- **Settings tuned on the test samples:** the prompts, thresholds and input sizes were chosen on the 3
  test samples, so those scores are likely optimistic.
- **Licensing:** a maintainer comment in the YOLOE docs says production use requires AGPL-3.0
  compliance or an Enterprise License, so confirm this before commercial use.

### YOLOE + DINOv2

These apply to both outline variants.

- **Can't report "no eye":** at least one eye is always output for each animal, even when no eye is
  visible, so false eyes can appear, such as ears or body patches. No minimum score has been tested.
- **Depends on YOLOE:** YOLOE supplies the animal outlines and the reference eyes. An animal it misses
  gets no eyes, and animals it merges are treated as one, which gave an invalid 137.6 px eye distance
  across 2 cats in `000000333772.jpg`.
- **Coarse features:** each 14 px patch covers several original pixels, so small or blurry eyes can
  merge or lose detail. Some tiny results, such as 2.45 px, need checking.
- **False eyes on new images:** against the draft marks on the 9 new COCO images, 9 of its 32 scored
  eyes were false (precision 71.9%).
- **Limited animals:** it runs only on cats and dogs, using reference eyes from cats and dogs.
- **Slower, per animal:** DINOv2 takes about 97 ms per animal on an Apple M4 Pro, so images with many
  animals take longer.

**GrabCut variant**

- **Larger distance error on the test samples:** 4.31 px on the same 4 animals, against 2.67 px for
  heatmap level and 2.52 px for YOLOE.
- **Outline can shift the center:** on the 003 dog, it gave 55.47 px against a marked 49.33 px.
- **Needs visible contrast:** dark, blurry or low-contrast eyes give it little color to separate.
- **Slowest step:** GrabCut takes about 122 ms per animal on the CPU, giving 1.98 FPS overall.

**Heatmap level variant**

- **Loose outlines:** they cover nearly twice YOLOE's eye area on the test samples, with polygon IoU of
  0.51-0.54 against YOLOE, compared with 0.66-0.69 for GrabCut.
- **No image edges:** it traces only the heatmap, so it can't follow the eye's real edge.
- **Level is a fixed choice:** it uses a 50% level, and lower levels lost an eye in an earlier test.

### Both methods

- **Small validation:** only 3 test samples have eye marks, and settings were chosen on those images.
- **The 002 dog is uncertain:** its mark gives 78.50 px, while both methods give 73-75 px.
- **Pixels only:** distances aren't real-world sizes, and head angle and depth affect them.
- **Right-eye rule:** it assumes a forward-facing, upright head.

## Folder contents

```text
submission/
├── README.md
├── requirements.txt                Installed Python package versions
├── data/
│   ├── 001-test_samples/           3 test samples with hand-marked eyes
│   └── 002-other_samples/          9 other COCO images, copies of the test samples, and draft eye marks
├── outputs/
│   ├── 001-test_samples_yoloe/            YOLOE results
│   ├── 001-test_samples_yoloe_dinov2/     Crops, heatmaps and both outline variants
│   ├── 002-other_samples_yoloe/           YOLOE results
│   ├── 002-other_samples_yoloe_dinov2/    Heatmaps, both outline variants and summary.csv
│   └── 002-other_samples_performance/     Speed and accuracy results
├── reports/                        Reports, figures, measurements and supporting notes
└── scripts/
    ├── config.yaml                                  Data, output, YOLOE and measurement settings
    ├── run_yoloe_test_samples.py                    YOLOE on the test samples
    ├── run_yoloe_dinov2_test_samples.py             YOLOE + DINOv2 on the test samples
    ├── run_yoloe_and_dinov2_other_samples.py        YOLOE and YOLOE + DINOv2 on the other samples
    ├── evaluate_yoloe_and_dinov2_other_samples.py   Speed and accuracy on the other samples
    ├── dinov2_eye_pipeline.py                       Shared YOLOE + DINOv2 steps
    ├── measure_yoloe_eyes.py                        R2 and R3 for one YOLOE result
    ├── measure_yoloe_dinov2_eyes.py                 R2 and R3 for one YOLOE + DINOv2 result
    └── export_submission_files.py                   R2 and R3 for every result, plus figures, in reports/files/
```

Model weights are in `../model/` and aren't committed. Earlier experiments are in
[`../experiments/`](../experiments/README.md).

## Measure the eye distances

Two scripts print the calculation for one image, one for each method. The mode is set by `both_eyes`
in `scripts/config.yaml`: `both-eyes` measures each animal's two eyes (R2), and `right-eye-pair`
measures the right eyes of every pair of animals (R3).

YOLOE:

```bash
.venv/bin/python image-segmentation/submission/scripts/measure_yoloe_eyes.py --json image-segmentation/submission/outputs/001-test_samples_yoloe/002-coco-sample.json
```

YOLOE + DINOv2, using a JSON file from either the `grabcut/` or `heatmap_level/` folder:

```bash
.venv/bin/python image-segmentation/submission/scripts/measure_yoloe_dinov2_eyes.py --json image-segmentation/submission/outputs/001-test_samples_yoloe_dinov2/grabcut/002-coco-sample.json
```

Both work on either image set. To measure every result at once and save the CSV files used by
reports 3 and 4, run `export_submission_files.py`, as shown below.

## Reproduce the results

Run these from the repository root, in order:

```bash
.venv/bin/python image-segmentation/submission/scripts/run_yoloe_test_samples.py
```

```bash
.venv/bin/python image-segmentation/submission/scripts/run_yoloe_dinov2_test_samples.py
```

```bash
.venv/bin/python image-segmentation/submission/scripts/run_yoloe_and_dinov2_other_samples.py
```

```bash
.venv/bin/python image-segmentation/submission/scripts/export_submission_files.py
```

```bash
.venv/bin/python image-segmentation/submission/scripts/evaluate_yoloe_and_dinov2_other_samples.py
```

The last command needs COCO's annotation file in `../experiments/data/coco_annotations/`.

To install the packages in a new environment:

```bash
.venv/bin/python -m pip install -r image-segmentation/submission/requirements.txt
```
