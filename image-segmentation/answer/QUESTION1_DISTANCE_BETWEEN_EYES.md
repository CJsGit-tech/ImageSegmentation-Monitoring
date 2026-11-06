# Question 1: Distance between each animal's eyes

請量測每隻動物, 雙眼的距離

## Answer

| Image | Animal | Distance |
|---|---|---|
| `002-coco-sample.jpg` | Cat | 73.12 px |
| `002-coco-sample.jpg` | Dog | 73.29 px |
| `003-coco-sample.jpg` | Dog | 48.54 px |
| `003-coco-sample.jpg` | Cat | 39.35 px |
| `001-coco-sample.jpg` | Dog | Not measurable, 1 eye detected |
| `001-coco-sample.jpg` | Bird | Not measurable, 0 eyes detected |
| `001-coco-sample.jpg` | Cat | Not measurable, 0 eyes detected |

4 of the 7 animals could be measured. Distances are in image pixels, not real-world units.

## Calculation

| Animal | Eye 1 center | Eye 2 center | Calculation | Distance |
|---|---|---|---|---|
| 002 cat | (342.82, 194.13) | (415.91, 192.42) | √(73.09² + 1.71²) | 73.12 px |
| 002 dog | (325.41, 408.68) | (398.60, 404.92) | √(73.19² + 3.76²) | 73.29 px |
| 003 dog | (346.60, 119.81) | (394.16, 129.50) | √(47.56² + 9.69²) | 48.54 px |
| 003 cat | (162.52, 315.80) | (201.86, 316.38) | √(39.34² + 0.58²) | 39.35 px |

Eye centers are polygon centroids. See
[Question 4](QUESTION4_MEASUREMENT_AND_VALIDATION.md) for the formulas and validation.

## Reproduce

Set the mode in `image-segmentation/config.yaml`:

```yaml
both_eyes: both-eyes
```

Then run from the repository root:

```bash
.venv/bin/python image-segmentation/segment_animals_eyes.py
```

```bash
.venv/bin/python image-segmentation/measure_eyes.py --json image-segmentation/output/002-coco-sample.json
```

```bash
.venv/bin/python image-segmentation/measure_eyes.py --json image-segmentation/output/003-coco-sample.json
```
