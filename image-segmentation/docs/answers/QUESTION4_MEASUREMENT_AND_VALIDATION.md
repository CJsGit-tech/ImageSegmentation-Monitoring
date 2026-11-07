# Question 4: Measurement method and validation

解釋量測的方式與公式, 以及怎麼驗證

## Measurement method

`measure_eyes.py` reads a prediction JSON and works in image pixels:

1. **Group the eyes** by `animal_index`. If an animal has more than two, keep the two most confident.
2. **Find each eye's center** from its polygon.
3. **Identify the right eye** when needed.
4. **Calculate the distance** between the centers.

```bash
.venv/bin/python image-segmentation/measure_eyes.py --json image-segmentation/results/yoloe/002-coco-sample.json
```

The mode is set by `both_eyes` in `image-segmentation/config.yaml`:

| Mode | Measures |
|---|---|
| `both-eyes` | Distance between the two eyes of each animal |
| `right-eye-pair` | Distance between the right eyes of every pair of animals in the image |

## Formulas

**Polygon area**, using the shoelace formula, for vertices $(x_i, y_i)$ with the last vertex
joined back to the first:

$$A = \frac{1}{2}\sum_i (x_i y_{i+1} - x_{i+1} y_i)$$

**Eye center**, the polygon's centroid:

$$C_x = \frac{1}{6A}\sum_i (x_i + x_{i+1})(x_i y_{i+1} - x_{i+1} y_i)$$

$$C_y = \frac{1}{6A}\sum_i (y_i + y_{i+1})(x_i y_{i+1} - x_{i+1} y_i)$$

**Distance**, the Euclidean distance between two centers:

$$d = \sqrt{(x_2 - x_1)^2 + (y_2 - y_1)^2}$$

**Right eye:** this means the animal's own right eye. When an animal faces the camera, that
is the eye on the left of the image, so the eye with the smaller $x$ is taken as the right eye.

## Results

### Distance between each animal's eyes

| Image | Animal | Distance |
|---|---|---|
| 002 | Cat | 73.12 px |
| 002 | Dog | 73.29 px |
| 003 | Dog | 48.54 px |
| 003 | Cat | 39.35 px |
| 001 | Dog | Not measurable, 1 eye detected |
| 001 | Bird | Not measurable, 0 eyes detected |
| 001 | Cat | Not measurable, 0 eyes detected |

### Distance between two animals' right eyes

| Image | Pair | Distance |
|---|---|---|
| 002 | Cat to dog | 215.25 px |
| 003 | Dog to cat | 268.88 px |

Image 001 has no measurable pair. An animal needs both eyes detected before the script can
tell which is the right one.

## Validation

### Checks already run

**Known-answer test: passed.** Made-up eye shapes with known centers gave the exact expected
results. This confirms the calculation, not the model.

| Case | Expected | Result |
|---|---|---|
| Two eyes, 30 and 40 px apart along each axis | 50.00 px | 50.00 px |
| Triangle eye whose centroid differs from its first vertex | 220.93 px | 220.93 px |
| Right eyes of two animals | 285.67 px | 285.67 px |
| A third, less confident eye | Ignored | Ignored |
| Animal with one eye | Reported as not measurable | Reported as not measurable |

**Comparison with manual marks: done on the 4 measurable animals.** The manual distance uses
the centers of the marked eye boxes.

| Measurement | Manual | Predicted | Error | Error % |
|---|---|---|---|---|
| 002 cat, both eyes | 76.03 px | 73.12 px | 2.91 px | 3.8% |
| 002 dog, both eyes | 78.50 px | 73.29 px | 5.21 px | 6.6% |
| 003 dog, both eyes | 49.33 px | 48.54 px | 0.79 px | 1.6% |
| 003 cat, both eyes | 40.51 px | 39.35 px | 1.17 px | 2.9% |
| 002 cat to dog, right eyes | 212.59 px | 215.25 px | 2.66 px | 1.2% |
| 003 dog to cat, right eyes | 269.86 px | 268.88 px | 0.98 px | 0.4% |

$$MAE = \frac{1}{n}\sum_i |d_{pred,i} - d_{true,i}| \qquad MAPE = \frac{100\%}{n}\sum_i \frac{|d_{pred,i} - d_{true,i}|}{d_{true,i}}$$

| Mode | MAE | MAPE |
|---|---|---|
| `both-eyes` (4 measurements) | 2.52 px | 3.7% |
| `right-eye-pair` (2 measurements) | 1.82 px | 0.8% |

Predicted distances between an animal's eyes were shorter than manual in all 4 cases. With so
few measurements and approximate marks, this may or may not be a real bias.

**Repeatability on one device: passed.** Repeated runs of `segment_animals_eyes.py` on MPS
produced byte-identical JSON.

### Checks still to run

| Check | How | What passing looks like |
|---|---|---|
| Scale | Resize images by 2×, run again and compare | Distances roughly double |
| Device | Compare CPU and MPS results | Differences of about a pixel or less |
| Between annotators | Have two people mark the eyes independently | Their disagreement shows how exact the "truth" can be |
| Larger test set | Measure more images with hand-marked eyes | MAE stays low, and more animals are measurable |
| Real-world size | Photograph a target of known size | Pixel results convert to the known size |

## Limits

- **Pixels only:** COCO images have no known scale, so results can't be converted to centimeters.
- **Compare within one image:** distances from different photos aren't comparable, and the
  script reads one JSON at a time.
- **Head angle:** a turned head makes eyes look closer together, as the dog in image 003 shows,
  so this isn't the true anatomical distance.
- **Depth:** two animals at different distances from the camera aren't measured in the same plane.
- **Right-eye rule assumes a forward-facing, upright head:** a side view or an unusual head
  angle can make the rule wrong.
- **Center depends on the outline:** a centroid is the middle of the outlined shape, not
  necessarily the pupil. The loose outline on the dog's eye in image 002 likely explains its
  larger error.
- **Missing eyes:** 3 of the 7 animals can't be measured, all in image 001.
- **Manual marks aren't exact:** they were marked by Claude Code from zoomed views, with no
  second annotator, so they should be reviewed by a person before relying on these errors.
