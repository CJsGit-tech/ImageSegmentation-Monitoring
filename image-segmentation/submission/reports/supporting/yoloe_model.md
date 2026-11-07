# Model choice: YOLOE with Ultralytics

This project uses `yoloe-26l-seg.pt` through the Ultralytics Python SDK to segment animals
and their eyes.

## Why the Ultralytics SDK

Ultralytics is a Python SDK that makes object detection and instance segmentation models easy
to use. The same package covers the steps this project needs:

| Need | What the SDK provides | Source |
|---|---|---|
| Prediction | Load a model, set text prompts and predict in a few lines | [YOLOE docs][yoloe-docs] |
| Visualization | Display results directly with `results[0].show()` | [YOLOE docs][yoloe-docs] |
| Inference on exported models | Predict or validate directly on an exported file, such as `yolo26n.onnx` | [Detection docs][detect-docs] |
| Custom training | Prepare a labeled dataset, adjust a config file and train from Python | [Ultralytics training guide][train-guide] |
| Fine-tuning YOLOE | Dedicated trainers such as `YOLOEPESegTrainer` | [YOLOE docs][yoloe-docs] |
| Model export | ONNX, CoreML, NCNN, LiteRT and other formats | [Export docs][export-docs] |

YOLOE itself is documented as exporting to ONNX, OpenVINO, TensorRT, CoreML, LiteRT and RKNN
([YOLOE docs][yoloe-docs]).

## Why YOLOE

COCO has no eye class, so COCO-trained models can't segment eyes. YOLOE is an open-vocabulary
model: it segments what it is told to look for in text, so `eye` can be requested without any
training ([YOLOE docs][yoloe-docs]).

## Performance on edge devices

**Summary:** published results support real-time YOLOE on some edge hardware, mainly at smaller
model sizes. This project's current setup hasn't been tested on an edge device, and it uses a
larger model than the edge results below.

### Published results

The [YOLOE paper][yoloe-paper] measured speed on an Nvidia T4 GPU with TensorRT and an iPhone 12
with CoreML, using exported models on LVIS:

| Model | Nvidia T4 (FPS) | iPhone 12 (FPS) |
|---|---|---|
| YOLOE-11-S | 301.2 | 73.3 |
| YOLOE-11-M | 168.3 | 39.2 |
| YOLOE-11-L | 130.5 | 35.1 |
| YOLOE-v8-S | 305.8 | 64.3 |
| YOLOE-v8-M | 156.7 | 41.7 |
| YOLOE-v8-L | 102.5 | 27.2 |
| YOLO-Worldv2-S (for comparison) | 216.4 | 48.9 |
| YOLO-Worldv2-L (for comparison) | 80.0 | 22.1 |

The paper reports YOLOE-v8-S as 1.4× faster on the T4 and 1.3× faster on the iPhone 12 than
YOLO-Worldv2-S ([paper][yoloe-paper]).

The [YOLOE docs][yoloe-docs] say the `n` and `s` sizes run on edge GPUs such as Jetson or on CPU
at reduced resolution. They also state that inference generally needs an NVIDIA GPU with 4–8 GB
of VRAM.

### What the evidence doesn't show

- **YOLOE-26 on devices:** the paper predates YOLOE-26, so the figures above are for the older
  YOLOE-v8 and YOLOE-11 models. No on-device speeds for YOLOE-26 were found.
- **Jetson and Raspberry Pi:** no published YOLOE speed measurements were found for these.
- **Independence:** the speed figures come from the model's authors, not an independent benchmark.
- **Segmentation overhead:** the docs note that the loaded `yoloe-26l-seg.pt` is larger than its
  detection figures, at 35.4 M parameters and 142.0 B FLOPs ([YOLOE docs][yoloe-docs]).

### How this applies to this project

| Current setup | Effect on edge use |
|---|---|
| Large model (`26l`) | The docs point to `n` and `s` for edge GPUs and CPU |
| Eye search at size 1280 | Much slower than 640: about 0.8 vs 0.22 s per image on an Apple M4 Pro CPU in our rough test |
| Two passes per image | About double the model runs of a single pass |
| Text encoder of about 254 MB | Can be avoided after export, because prompts are saved into the exported model ([YOLOE docs][yoloe-docs]) |

Moving to a smaller model or input size would probably lower eye accuracy. On our images, at
conf 0.1, `26s` found 7 of 13 eyes against 9 for `26l`, and size 640 found 5 against 9 for 1280.
Edge deployment should be tested on the target device.

Exported YOLOE models are static: `set_classes()` and visual prompts stop working after export,
so prompts must be chosen first ([YOLOE docs][yoloe-docs]).

## Limitations of the current approach

### Only specific animals were tested

The config prompts `bird`, `cat` and `dog`, and testing used 3 images containing only those
animals. COCO's other animal classes (horse, sheep, cow, elephant, bear, zebra, giraffe) haven't
been tested, so results may not carry over to them.

### Eye confidence is low

| Detection | Confidence on the sample images |
|---|---|
| Animals | 0.52–0.90 |
| Eyes | 0.12–0.35 |

Because of this, `eye_conf` must stay low at 0.1. Raising it to 0.25 would lose most eyes. Four of
the 13 marked eyes are still missed, all in image 001.

### Suggested next step: custom training

Build our own training dataset and run custom training. This is a main reason the SDK is a good
fit, because the same package used for prediction also covers training, validation and export.

1. **Build the dataset.** [Pascal-Part][pascal-part] already includes eye part masks for cats,
   dogs, cows, horses and sheep, and the related [Pascal-Part-116][pascal-part-116] benchmark
   lists bird eyes. COCO images can be hand-labeled to match this project, since COCO has no eye masks.
2. **Train.** Either train a YOLO segmentation model with an `eye` class ([training
   guide][train-guide]) or fine-tune YOLOE ([YOLOE docs][yoloe-docs]).
3. **Evaluate.** Keep a separate test set and report mask mAP, precision and recall.
4. **Export.** Export to a format that suits the target device ([export docs][export-docs]).

Things to expect:

- **Small eyes stay hard:** [small objects can still be missed][roboflow] after fine-tuning, and
  training can't add detail to an eye only a few pixels wide.
- **Fixed classes after fine-tuning:** a checkpoint fine-tuned with `YOLOEPESegTrainer` raises an
  error if the classes change ([YOLOE docs][yoloe-docs]).
- **Licensing:** a maintainer comment on the [YOLOE docs][yoloe-docs] says production use requires
  either AGPL-3.0 compliance or an Enterprise License. Confirm this before any commercial use.

### Reducing the labeling work with vision-language models

Labeling eyes by hand is tedious, so vision-language models (VLMs) could create first-draft
labels for people to correct. We have used recent models such as GPT to help label document
layouts, and the same idea could apply here.

The approach has support in published work. One [2025 paper][auto-label-paper] used
vision-language models to generate labels, then trained lightweight detectors on them, and
reported competitive performance with large cuts in labeling time and cost. It used YOLO-World,
YOLOE and Grounding DINO rather than GPT or Gemini.

**Proposed workflow:**

1. **Draft boxes.** Ask a VLM for eye boxes on each animal crop. The [Gemini API][gemini-docs], for
   example, can return boxes as normalized coordinates, and they need converting to pixels.
2. **Create masks.** Pass each box to SAM to produce an eye mask. SAM accepts box prompts, and
   Ultralytics' [`auto_annotate`][auto-annotate] already pairs a detector with SAM and writes
   YOLO-format polygon labels, though its detector must be a YOLO model that has the class.
3. **Review.** Have a person correct or reject every label.
4. **Train.** Train on the reviewed labels with the Ultralytics SDK.

**Risks to manage:**

- **Eyes are harder than document layouts:** layout regions are large, while some eyes here are
  only a few pixels wide. No controlled comparison of GPT or Gemini box accuracy on small objects
  was found, so this should first be tested on a small hand-labeled sample.
- **Wrong labels teach the model mistakes:** auto-labels can [add wrong labels][auto-label-review],
  especially for hard objects or at low confidence, which is why step 3 matters.
- **Keep testing separate:** the final test set must be labeled and checked by people, not taken
  from VLM output. Otherwise the score only shows agreement with the VLM.
- **Segmentation support varies:** Gemini's [image understanding docs][gemini-docs] describe
  contour masks from Gemini 2.5, but its [Gemini 3 guide][gemini-3] says Gemini 3 Pro and Flash
  don't support them. Using SAM for the masks avoids depending on this.

## References

- [YOLOE: Real-Time Seeing Anything (paper)][yoloe-paper]
- [Ultralytics YOLOE documentation][yoloe-docs]
- [Ultralytics model export documentation][export-docs]
- [Ultralytics object detection documentation][detect-docs]
- [Ultralytics custom training guide for instance segmentation][train-guide]
- [Roboflow: training YOLO26 segmentation on custom data][roboflow]
- [Going Denser with Open-Vocabulary Part Segmentation (Pascal-Part)][pascal-part]
- [Understanding Multi-Granularity for Open-Vocabulary Part Segmentation (Pascal-Part-116)][pascal-part-116]
- [Auto-Labeling Data for Object Detection (paper)][auto-label-paper]
- [Literature review of Auto-Labeling Data for Object Detection][auto-label-review]
- [Gemini API image understanding documentation][gemini-docs]
- [Gemini 3 developer guide][gemini-3]
- [Ultralytics SAM documentation, including auto-annotation][auto-annotate]

[yoloe-paper]: https://arxiv.org/abs/2503.07465
[yoloe-docs]: https://docs.ultralytics.com/models/yoloe
[export-docs]: https://docs.ultralytics.com/modes/export
[detect-docs]: https://docs.ultralytics.com/tasks/detect
[train-guide]: https://www.ultralytics.com/blog/how-to-custom-train-ultralytics-yolo26-for-instance-segmentation
[roboflow]: https://blog.roboflow.com/train-yolo26-instance-segmentation-custom-data/
[pascal-part]: https://arxiv.org/pdf/2305.11173
[pascal-part-116]: https://arxiv.org/pdf/2406.11384
[auto-label-paper]: https://arxiv.org/pdf/2506.02359
[auto-label-review]: https://www.themoonlight.io/review/auto-labeling-data-for-object-detection
[gemini-docs]: https://ai.google.dev/gemini-api/docs/image-understanding
[gemini-3]: https://ai.google.dev/gemini-api/docs/gemini-3
[auto-annotate]: https://docs.ultralytics.com/models/sam
