# 3. Distance between each animal's eyes

## Test samples

Distances are in image pixels, and "Not found" means fewer than 2 eyes were output for that animal.

| Image | Animal | YOLOE | YOLOE + DINOv2, GrabCut | YOLOE + DINOv2, heatmap level |
|---|---|---|---|---|
| 001 | Dog | Not found (1 eye) | 43.82 px | 43.14 px |
| 001 | Cat | Not found (0 eyes) | 10.52 px | 11.87 px |
| 001 | Bird | Not found (0 eyes) | Not run | Not run |
| 002 | Cat | 73.12 px | 70.38 px | 73.15 px |
| 002 | Dog | 73.29 px | 74.92 px | 73.25 px |
| 003 | Dog | 48.54 px | 55.47 px | 48.77 px |
| 003 | Cat | 39.35 px | 38.65 px | 38.53 px |

## Calculation example

The 002 cat with YOLOE, using the center of each eye outline:

$$d = \sqrt{(415.91 - 342.82)^2 + (192.42 - 194.13)^2} = 73.12 \text{ px}$$

[The measurement report](06_measurement_and_validation.md) gives the formulas.

## Other samples

| Image | Animal | YOLOE | YOLOE + DINOv2, GrabCut | YOLOE + DINOv2, heatmap level |
|---|---|---|---|---|
| 000000107226 | Dog 1 | Not found (0 eyes) | 14.63 px | 14.75 px |
| 000000107226 | Dog 2 | Not found (0 eyes) | 20.43 px | 20.68 px |
| 000000125405 | Dog 1 | Not found (0 eyes) | 27.02 px | 27.79 px |
| 000000125405 | Dog 2 | Not found (0 eyes) | 22.43 px | 23.70 px |
| 000000333772 | Cat 1 | Not found (1 eye) | 137.60 px* | 137.35 px* |
| 000000372819 | Dog 1 | Not found (0 eyes) | 9.54 px | 9.42 px |
| 000000372819 | Dog 2 | Not found (0 eyes) | 13.97 px | 13.44 px |
| 000000372819 | Dog 3 | Not found (0 eyes) | 8.48 px | 9.34 px |
| 000000372819 | Dog 4 | Not found (0 eyes) | 6.56 px | 2.45 px |
| 000000402473 | Cat 1 | Not found (0 eyes) | 50.24 px | 50.90 px |
| 000000402473 | Cat 2 | 47.12 px | 42.28 px | 47.60 px |
| 000000413395 | Cat 1 | Not found (0 eyes) | 24.16 px | 22.69 px |
| 000000413395 | Cat 2 | Not found (1 eye) | 29.31 px | 28.97 px |
| 000000543581 | Dog 1 | Not found (0 eyes) | Not found (1 eye) | Not found (1 eye) |
| 000000543581 | Cat 2 | Not found (0 eyes) | Not found (1 eye) | Not found (1 eye) |
| 000000551815 | Cat 1 | 63.39 px | 63.15 px | 63.36 px |
| 000000551815 | Cat 2 | Not found (1 eye) | 73.53 px | 72.43 px |
| 000000555705 | Cat 1 | 51.07 px | 50.34 px | 49.59 px |
| 000000555705 | Cat 2 | Not found (0 eyes) | 44.85 px | 44.70 px |
| 001-coco-sample | Dog 1 | Not found (1 eye) | 43.70 px | 43.07 px |
| 001-coco-sample | Cat 2 | Not found (0 eyes) | 10.49 px | 11.84 px |
| 001-coco-sample | Bird | Not found (0 eyes) | Not run | Not run |
| 002-coco-sample | Cat 1 | 73.12 px | 70.20 px | 72.08 px |
| 002-coco-sample | Dog 2 | 73.29 px | 74.70 px | 72.85 px |
| 003-coco-sample | Dog 1 | 48.54 px | 55.15 px | 48.30 px |
| 003-coco-sample | Cat 2 | 39.35 px | 38.59 px | 39.44 px |

*YOLOE outlined 2 cats as one animal in this image, so this distance is between eyes on 2 different
cats and isn't a valid eye distance.

The other samples have no reviewed eye marks, so these distances are outputs rather than verified
measurements. Some DINOv2 eyes may be false, and very small results, such as 2.45 px, need checking.
The copies of the test samples give slightly different DINOv2 results here, because their reference
eyes include all 3 test samples instead of only the other two.

Every distance and eye center is in [`eye_distances.csv`](files/measurements/eye_distances.csv).
