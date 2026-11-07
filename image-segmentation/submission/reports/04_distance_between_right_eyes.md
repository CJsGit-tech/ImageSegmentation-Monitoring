# 4. Distance between two animals' right eyes

## Test samples

| Image | Animal pair | YOLOE | YOLOE + DINOv2, GrabCut | YOLOE + DINOv2, heatmap level |
|---|---|---|---|---|
| 001 | Dog to cat | Not found | 176.50 px | 175.79 px |
| 002 | Cat to dog | 215.25 px | 215.33 px | 214.02 px |
| 003 | Dog to cat | 268.88 px | 264.46 px | 273.39 px |

YOLOE has no result for image 001, because neither of its animals there has both eyes. The bird has
no pair result from either method.

## Which eye is the right eye

This means the animal's own right eye. When an animal faces the camera, that is the eye on the left
of the image, so the eye with the smaller $x$ is taken as the right eye. An animal needs both eyes
found for this rule, so an animal with one eye isn't paired.

## Calculation example

The 002 cat and dog with YOLOE:

$$d = \sqrt{(342.82 - 325.41)^2 + (194.13 - 408.68)^2} = 215.25 \text{ px}$$

## Other samples

| Image | Animal pair | YOLOE | YOLOE + DINOv2, GrabCut | YOLOE + DINOv2, heatmap level |
|---|---|---|---|---|
| 000000107226 | Dog 1 to dog 2 | Not found | 170.36 px | 169.23 px |
| 000000125405 | Dog 1 to dog 2 | Not found | 270.10 px | 273.66 px |
| 000000372819 | Dog 1 to dog 2 | Not found | 81.31 px | 81.41 px |
| 000000372819 | Dog 1 to dog 3 | Not found | 207.64 px | 207.54 px |
| 000000372819 | Dog 1 to dog 4 | Not found | 68.81 px | 69.00 px |
| 000000372819 | Dog 2 to dog 3 | Not found | 203.38 px | 203.48 px |
| 000000372819 | Dog 2 to dog 4 | Not found | 119.46 px | 119.92 px |
| 000000372819 | Dog 3 to dog 4 | Not found | 151.57 px | 151.48 px |
| 000000402473 | Cat 1 to cat 2 | Not found | 458.31 px | 464.45 px |
| 000000413395 | Cat 1 to cat 2 | Not found | 221.09 px | 219.94 px |
| 000000551815 | Cat 1 to cat 2 | Not found | 150.98 px | 146.39 px |
| 000000555705 | Cat 1 to cat 2 | Not found | 111.86 px | 112.71 px |
| 001-coco-sample | Dog 1 to cat 2 | Not found | 176.57 px | 174.86 px |
| 002-coco-sample | Cat 1 to dog 2 | 215.25 px | 215.51 px | 213.78 px |
| 003-coco-sample | Dog 1 to cat 2 | 268.88 px | 264.79 px | 274.25 px |

YOLOE pairs only animals with both eyes found, so on the 9 new COCO images it has no pair. These
images have no reviewed marks, and the right-eye rule is less reliable for animals that aren't
facing the camera.

Every pair's distance and right-eye centers are in
[`right_eye_distances.csv`](files/measurements/right_eye_distances.csv).
