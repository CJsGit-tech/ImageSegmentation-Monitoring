# 6. Measurement method and validation

## Measurement method

YOLOE and both YOLOE + DINOv2 variants are measured the same way, in image pixels:

1. **Group the eyes by animal.** YOLOE keeps its two most confident eyes per animal, and YOLOE +
   DINOv2 outputs at most two.
2. **Find each eye's center** from its outline.
3. **Identify the right eye**, when needed, as the eye with the smaller $x$.
4. **Calculate the distance** between the two centers.

## Formulas

**Polygon area**, using the shoelace formula, for vertices $(x_i, y_i)$ with the last vertex joined
back to the first:

$$A = \frac{1}{2}\sum_i (x_i y_{i+1} - x_{i+1} y_i)$$

**Eye center**, the polygon's centroid:

$$C_x = \frac{1}{6A}\sum_i (x_i + x_{i+1})(x_i y_{i+1} - x_{i+1} y_i)$$

$$C_y = \frac{1}{6A}\sum_i (y_i + y_{i+1})(x_i y_{i+1} - x_{i+1} y_i)$$

**Distance**, the Euclidean distance between two centers:

$$d = \sqrt{(x_2 - x_1)^2 + (y_2 - y_1)^2}$$

## Validation

### 1. Known-answer test

Made-up eye centers with known distances gave the expected results, including a 30-40-50 triangle
and the rule that an animal with one eye isn't measured. This confirms the calculation, not the model.

### 2. Comparison with hand marks

On the sample images, each distance was compared with the distance between the centers of the
hand-marked eye boxes. All results are in
[`validation_against_marks.csv`](files/measurements/validation_against_marks.csv).

**Each animal's eye distance**

| Image | Animal | Marked | YOLOE | Error | GrabCut | Error | Heatmap level | Error |
|---|---|---|---|---|---|---|---|---|
| 001 | Dog | 44.53 px | Not found | None | 43.82 px | 0.70 px | 43.14 px | 1.39 px |
| 001 | Cat | 11.67 px | Not found | None | 10.52 px | 1.16 px | 11.87 px | 0.19 px |
| 002 | Cat | 76.03 px | 73.12 px | 2.91 px | 70.38 px | 5.65 px | 73.15 px | 2.88 px |
| 002 | Dog | 78.50 px | 73.29 px | 5.21 px | 74.92 px | 3.58 px | 73.25 px | 5.25 px |
| 003 | Dog | 49.33 px | 48.54 px | 0.79 px | 55.47 px | 6.14 px | 48.77 px | 0.56 px |
| 003 | Cat | 40.51 px | 39.35 px | 1.17 px | 38.65 px | 1.87 px | 38.53 px | 1.98 px |

**Right eyes of each animal pair**

| Image | Pair | Marked | YOLOE | Error | GrabCut | Error | Heatmap level | Error |
|---|---|---|---|---|---|---|---|---|
| 001 | Dog to cat | 177.10 px | Not found | None | 176.50 px | 0.61 px | 175.79 px | 1.31 px |
| 002 | Cat to dog | 212.59 px | 215.25 px | 2.66 px | 215.33 px | 2.74 px | 214.02 px | 1.43 px |
| 003 | Dog to cat | 269.86 px | 268.88 px | 0.98 px | 264.46 px | 5.40 px | 273.39 px | 3.53 px |

**Summary**

| Method | Eye distance MAE | MAPE | Animals | Right-eye MAE | MAPE | Pairs |
|---|---|---|---|---|---|---|
| YOLOE | 2.52 px | 3.7% | 4 | 1.82 px | 0.8% | 2 |
| YOLOE + DINOv2, GrabCut | 3.18 px | 6.8% | 6 | 2.92 px | 1.2% | 3 |
| YOLOE + DINOv2, heatmap level | 2.04 px | 3.6% | 6 | 2.09 px | 0.9% | 3 |

The DINOv2 averages include image 001, where YOLOE has no result, so the rows don't cover the same
animals. On the same 4 animals, the eye distance MAE is 2.52 px for YOLOE, 4.31 px for GrabCut and
2.67 px for heatmap level.

### 3. Comparison between methods

On the other samples, YOLOE + DINOv2 was compared with YOLOE on the 18 eyes YOLOE found. The
distances differ from YOLOE's by 2.50 px for GrabCut and 0.54 px for heatmap level, on 7 animals.
This shows agreement rather than accuracy, and 4 of those 7 animals are copies of the test samples.

### Checks still to run

| Check | How | What passing looks like |
|---|---|---|
| Reviewed marks | Have a person mark eyes on the test images | Hits, false eyes and errors can be scored |
| Between annotators | Have two people mark independently | Their disagreement shows how exact the reference can be |
| Scale | Resize images by 2× and run again | Distances roughly double |
| Real-world size | Photograph a target of known size | Pixel results convert to the known size |

## Limits

- **Pixels only:** COCO images have no known scale, so results can't be converted to centimeters.
- **Compare within one image:** distances from different photos aren't comparable.
- **Head angle:** a turned head makes eyes look closer together, so this isn't the true anatomical distance.
- **Depth:** two animals at different distances from the camera aren't measured in the same plane.
- **Right-eye rule:** it assumes a forward-facing, upright head, and it can be wrong in a side view.
- **Center depends on the outline:** a centroid is the middle of the outlined shape, not necessarily
  the pupil, and a false eye gives a false distance.
- **The 002 dog is uncertain:** its mark gives 78.50 px, while YOLOE and both variants give 73-75 px. The mark
  may be off, so a person should check it.
- **Marks aren't exact:** Claude Code made them from zoomed views with no second annotator, so very
  small errors shouldn't be read as exact.
