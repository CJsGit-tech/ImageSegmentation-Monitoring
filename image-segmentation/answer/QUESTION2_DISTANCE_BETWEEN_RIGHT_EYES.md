# Question 2: Distance between two animals' right eyes

請量測任意兩隻動物, 右眼的距離

## Answer

| Image | Pair | Distance |
|---|---|---|
| `002-coco-sample.jpg` | Cat to dog | 215.25 px |
| `003-coco-sample.jpg` | Dog to cat | 268.88 px |

`001-coco-sample.jpg` has no measurable pair, because no animal there has both eyes detected.

Distances are in image pixels, not real-world units. Pairs are only measured within one image,
because pixel positions in different photos aren't related.

## Which eye is the right eye

This means the animal's own right eye. When an animal faces the camera, that is the eye on the
left of the image, so the eye with the smaller $x$ is taken as the right eye. An animal needs both
eyes detected to apply this rule.

## Calculation

| Pair | Right eye 1 | Right eye 2 | Calculation | Distance |
|---|---|---|---|---|
| 002 cat to dog | Cat (342.82, 194.13) | Dog (325.41, 408.68) | √(17.41² + 214.55²) | 215.25 px |
| 003 dog to cat | Dog (346.60, 119.81) | Cat (162.52, 315.80) | √(184.08² + 195.99²) | 268.88 px |

Eye centers are polygon centroids. See
[Question 4](QUESTION4_MEASUREMENT_AND_VALIDATION.md) for the formulas and validation.

## Reproduce

Set the mode in `image-segmentation/config.yaml`:

```yaml
both_eyes: right-eye-pair
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
