# 5. AI models and evaluation

## Models used

| Model | Used for | Training |
|---|---|---|
| **YOLOE** (`yoloe-26l-seg.pt`) with text prompts | Animal outlines in both methods, and eye outlines in the YOLOE method | None |
| **DINOv2** ViT-S/14 with registers | Patch features for the eye heatmaps in YOLOE + DINOv2 | None |

GrabCut and heatmap-level tracing are the two image-processing variants of YOLOE + DINOv2, not
trained models.

## Why these models

**YOLOE**

1. **It can look for eyes:** COCO has animal classes but no eye class, so COCO-trained models can't
   outline eyes. YOLOE segments what it is told to look for in text.
2. **It needs no training:** it can run without labeled eye data.
3. **It's reliable on animals:** it found all 7 sample animals, with animal confidence of 0.52-0.90.

**DINOv2**

1. **It finds eyes YOLOE misses:** with local peaks, it found all 12 marked cat and dog eyes on the
   sample images, while YOLOE found 9.
2. **It needs no training:** its features are compared with a few reference eyes.
3. **It beat the other option tested:** the original DINO gave noisier heatmaps. With the same steps,
   distance error against the marks fell from 5.47 to 1.71 px on the same 4 animals.

**GrabCut and heatmap level** were selected from 25 experiment runs, using the 9 sample eyes YOLOE
detected. GrabCut overlapped YOLOE's polygons most, and heatmap level matched its centers and
distances most closely. [The experiment history](supporting/experiment_history.md) covers the full
path, and [the YOLOE notes](supporting/yoloe_model.md) give more detail and citations.

## Evaluation formulas

**Intersection over Union**, the overlap between a prediction $A$ and the reference $B$:

$$IoU = \frac{|A \cap B|}{|A \cup B|}$$

**Precision, recall and F1**, where a true positive (TP) matches a true object, a false positive
(FP) has no match, and a false negative (FN) is a missed object:

$$Precision = \frac{TP}{TP + FP} \qquad Recall = \frac{TP}{TP + FN} \qquad F1 = \frac{2 \cdot Precision \cdot Recall}{Precision + Recall}$$

**Average Precision and mAP**, the standard COCO-style summary:

$$AP = \int_0^1 P(R)\,dR \qquad mAP_{50\text{-}95} = \frac{1}{10}\sum_{t \in \{0.50,\,0.55,\,\ldots,\,0.95\}} AP_t$$

AP and mAP weren't calculated here, because the eye references are approximate boxes at one
matching rule, and the DINOv2 methods have no confidence-ranked eye list.

**Distance error**, for measured distances $d_{pred}$ and reference distances $d_{true}$:

$$MAE = \frac{1}{n}\sum_i |d_{pred,i} - d_{true,i}| \qquad MAPE = \frac{100\%}{n}\sum_i \frac{|d_{pred,i} - d_{true,i}|}{d_{true,i}}$$

## Results on the test samples

Against 12 hand-marked cat and dog eyes. An eye is a hit when its center falls inside a mark.
Center error and box IoU cover the 9 eyes YOLOE found, and both YOLOE + DINOv2 variants hit all 9.

| Method | Hits | False | Precision | Recall | F1 | Center error | Box IoU |
|---|---|---|---|---|---|---|---|
| YOLOE | 9 | 0 | 100% | 75% | 0.86 | 1.98 px | 0.65 |
| YOLOE + DINOv2, GrabCut | 12 | 0 | 100% | 100% | 1.00 | 3.20 px | 0.69 |
| YOLOE + DINOv2, heatmap level | 12 | 0 | 100% | 100% | 1.00 | 2.50 px | 0.60 |

Including the bird's marked eye, YOLOE found 9 of 13 (recall 69.2%, F1 0.82).

## Agreement with YOLOE

These scores use only eyes YOLOE detected and no hand marks. They measure agreement with YOLOE, not
accuracy.

| Method | Images | Eyes compared | Polygon IoU | Center offset | Distance difference |
|---|---|---|---|---|---|
| YOLOE + DINOv2, GrabCut | Test samples | 9 | 0.66 | 2.57 px | 3.00 px (4 animals) |
| YOLOE + DINOv2, heatmap level | Test samples | 9 | 0.51 | 2.35 px | 0.28 px (4 animals) |
| YOLOE + DINOv2, GrabCut | Other samples | 18 | 0.69 | 2.41 px | 2.50 px (7 animals) |
| YOLOE + DINOv2, heatmap level | Other samples | 18 | 0.54 | 1.70 px | 0.54 px (7 animals) |

The other samples include copies of the 3 test samples, which supply 9 of the 18 eyes and 4 of the
7 animals compared. The results are also in
[`other_samples_method_comparison.csv`](files/measurements/other_samples_method_comparison.csv).

## Limits of this evaluation

- **Unreviewed marks:** Claude Code made the sample eye marks from zoomed views, and no person has
  checked them.
- **No accuracy score on the other samples:** COCO has no animal eye annotations, and these images
  have no reviewed marks. YOLOE + DINOv2 output 30 eyes with no YOLOE match, and some may be false.
- **Small sample:** 3 images, with models and settings chosen on the same images, so scores are
  likely optimistic.
- **Lenient hit rule:** a center inside a mark counts as a hit, even with a poor outline.
- **Animal masks not scored:** animals were counted, but mask IoU against COCO's masks wasn't calculated.
