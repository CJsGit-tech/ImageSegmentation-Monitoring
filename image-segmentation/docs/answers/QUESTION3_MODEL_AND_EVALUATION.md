# Question 3: AI model and evaluation

解釋使用甚麼AI模型, 為什麼, 評估標準公式是甚麼

## Model used

**YOLOE (`yoloe-26l-seg.pt`) with text prompts**, run through Ultralytics. It is an
open-vocabulary instance segmentation model: instead of being limited to fixed classes,
it segments whatever it is told to look for in text.

Settings used in `segment_animals_eyes.py` and `config.yaml`:

| Step | Text prompts | Detections kept | Input size | Confidence |
|---|---|---|---|---|
| Find animals | `bird, cat, dog, eye` | Everything except `eye` | 640 | 0.5 |
| Find eyes | `bird, cat, dog, eye` | Only `eye` | 1280 | 0.1 |

After both steps:

- Detections whose masks mostly cover the same pixels are reduced to the most confident one.
- An eye is kept only if its center is on an animal's outline, or within 10 pixels of it.
- Each kept eye is linked to its animal.

## Why this model

1. **It can look for eyes.** COCO has animal classes but no eye class, so COCO-trained
   models such as `yolo11n-seg` can outline animals but not eyes. A public pretrained model
   with an animal eye label was not found.
2. **No training is needed.** It can be tested straight away, without labeled eye data.
3. **One model covers both tasks.** The same model segments animals and eyes.
4. **It did best in our comparison** on the three sample images.

### Comparison on the sample images

Eyes are scored out of 13. All rows use conf 0.1 unless stated.

| Method | Eyes found | False detections | Note |
|---|---|---|---|
| **YOLOE, full image, size 1280 (used)** | **9** | **0** | All 7 animals found |
| YOLOE, full image, size 640, conf 0.05 | 9 | 0 | Lower threshold, not tested further |
| YOLOE, full image, size 640 | 5 | 0 | |
| YOLOE on animal crops, original scale | 7 | 0 | |
| YOLOE prompt-free, size 640 | 5 | 0 | Only 5 of 7 animals kept, because of different labels |
| K-means clustering on animal crops | 5 | 9 | Has no concept of an eye |
| SAM 2.1 on animal crops + shape rules | 4 | 4 | SAM 2.1 takes no text prompts |

Size 1280 is used only for eyes. At that size the animal search lost both animals in
image 003 and labelled the dog in image 002 as a cat.

SAM 3, which supports text prompts, was not tested because its weights need separate access.

## Evaluation formulas

**Intersection over Union**, the overlap between a prediction $A$ and the truth $B$:

$$IoU = \frac{|A \cap B|}{|A \cup B|}$$

**Precision, recall and F1**, where a true positive (TP) is a prediction matching a true
object, a false positive (FP) has no match, and a false negative (FN) is a missed object:

$$Precision = \frac{TP}{TP + FP} \qquad Recall = \frac{TP}{TP + FN} \qquad F1 = \frac{2 \cdot Precision \cdot Recall}{Precision + Recall}$$

**Average Precision and mAP**, the standard COCO-style summary:

$$AP = \int_0^1 P(R)\,dR \qquad mAP_{50\text{-}95} = \frac{1}{10}\sum_{t \in \{0.50,\,0.55,\,\ldots,\,0.95\}} AP_t$$

For segmentation, $A$ and $B$ are masks, so this is called mask mAP.

## Results

### Eyes

Matched against 13 manually marked eye boxes, at box IoU ≥ 0.2:

| Metric | Calculation | Result |
|---|---|---|
| Precision | 9 / (9 + 0) | 100% |
| Recall | 9 / (9 + 4) | 69.2% |
| F1 | 2 × 1.00 × 0.692 / 1.692 | 0.82 |

| Image | Found | Missed |
|---|---|---|
| 001 | 1 of 5 | Dog's other eye, both eyes of the cat behind the glass, the bird's eye |
| 002 | 4 of 4 | None |
| 003 | 4 of 4 | None |

### Animals

All 7 animals were found with correct labels. This is a count only, and mask IoU was not calculated.

### Published model accuracy

The [Ultralytics YOLOE docs](https://docs.ultralytics.com/models/yoloe) list `yoloe-26l` at
37.8 mAP50-95 with text prompts on LVIS minival. That covers all LVIS classes, so it says
nothing specific about animal eyes.

## Limits of this evaluation

- **Very small test set:** 3 images and 13 eyes. One eye changes recall by about 8%.
- **Tuned on the test images:** model, thresholds and input sizes were chosen on these same
  images, so these scores are likely optimistic.
- **Lenient matching:** IoU ≥ 0.2 was used because the marks are approximate. A hit shows the
  location is right, not that the outline is accurate.
- **Not mask mAP:** the eye marks are boxes rather than masks, and only hits at a single
  threshold were counted, so AP and mAP were not calculated.
- **Marks not independently checked:** the eye boxes were marked by Claude Code from zoomed
  views of the images, with no second annotator. They should be reviewed by a person.
- **Low eye confidence:** eye scores are about 0.12–0.35, compared with 0.52–0.90 for animals.
  Raising `eye_conf` to 0.25 would lose most eyes.

## What a stronger evaluation would need

- **Animals:** compare against COCO's official animal masks to get real mask IoU and mAP.
- **Eyes:** hand-label eye masks on a larger, separate test set, since COCO has none.
