# Eye experiments

This folder holds earlier work that isn't part of the submission. It covers the DINO v1 and DINOv2
experiments, heatmap comparisons, binary heatmaps and background notes.
[The experiment history](../submission/reports/supporting/experiment_history.md) explains what was
tried and why.

| Location | Contents |
|---|---|
| `scripts/` | Experiment, heatmap and binary image scripts |
| `outputs/experiments/selected/` | The original runs of the two selected methods |
| `outputs/experiments/archive/` | Other experiments, with the reasons in its README |
| `outputs/heatmaps/` | DINO v1 and DINOv2 heatmap sets |
| `outputs/cropped_animals/` | Cat and dog crops from the sample images |
| `notes/` | YOLO11n-seg notes and diagrams |

These scripts use YOLOE results, datasets and shared code from `../submission/`. Run them from the
repository root, for example:

```bash
.venv/bin/python image-segmentation/experiments/scripts/run_dinov2_experiments.py
```
