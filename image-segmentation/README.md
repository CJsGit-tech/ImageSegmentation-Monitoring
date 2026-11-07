# Image segmentation

Segment animals and their eyes in COCO images, plus model notes.

## Python environment

Use the repository-root `.venv` (Python 3.13). It keeps these packages separate
from your system Python. To recreate it on a compatible Apple Silicon Mac, run
these commands from the repository root:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r image-segmentation/requirements.txt
```

`requirements.txt` records the installed package versions. To add packages, use
`python -m pip install PACKAGE` while the environment is active, then refresh
the record with `python -m pip freeze > image-segmentation/requirements.txt`. Run `deactivate` to
leave the environment. In an editor, select `.venv/bin/python` as the interpreter.

Reference: [Ultralytics installation guide](https://docs.ultralytics.com/quickstart/).

## Segment animals and eyes

```bash
.venv/bin/python image-segmentation/segment_animals_eyes.py
```

COCO models have no eye class, so this uses YOLOE, which segments whatever it is
told to look for in text. It is given the animal names in `config.yaml` and "eye". The same
model looks at each image twice: at size 640 for animals and 1280 for eyes, because
animals are missed or mislabelled at the larger size. An eye is kept only if it
lies on a detected animal.

Each image in `data/` produces an annotated copy (animals green, eyes red) and a
JSON file with confidence scores in `results/yoloe/`. All settings are in `config.yaml`.
The script uses Apple's GPU through MPS when available and otherwise uses the CPU.

The first run downloads the YOLOE weights and a text encoder of about 250 MB into
`model/` (ignored by Git).

## Folders

```text
image-segmentation/
├── config.yaml, requirements.txt, *.py
├── data/                       Sample images
│   └── coco_cats_dogs_38/      38 test images with at least 2 cats or dogs each
├── docs/
│   ├── answers/                Assignment answers
│   ├── models/                 YOLOE and YOLO11n-seg notes and diagrams
│   └── eye_experiment_history.md
├── model/                      Model weights (ignored by Git)
└── results/
    ├── yoloe/                  YOLOE results on the samples (ignored by Git)
    ├── cropped_animals/        Cat and dog crops
    ├── heatmaps/               DINO v1 and DINOv2 heatmaps
    ├── binary_heatmaps/        Black-and-white heatmaps
    ├── experiments/
    │   ├── selected/           GrabCut and heatmap level, both with DINOv2
    │   └── archive/            Other experiments, with reasons in its README
    ├── coco_38_test/
    │   ├── yoloe/              YOLOE results on the 38 images
    │   ├── dinov2/             DINOv2 heatmaps, GrabCut and heatmap level results
    │   └── summary.csv         Comparison of the three methods
    └── archive/                Duplicate DINO v1 heatmaps
```

[The experiment history](docs/eye_experiment_history.md) explains how the current approach was
reached and lists what each script does.
