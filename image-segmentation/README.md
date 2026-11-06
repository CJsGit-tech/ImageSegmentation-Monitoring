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
JSON file with confidence scores in `output/`. All settings are in `config.yaml`.
The script uses Apple's GPU through MPS when available and otherwise uses the CPU.

The first run downloads the YOLOE weights and a text encoder of about 250 MB into
`model/` (ignored by Git).

## Files

- `segment_animals_eyes.py`: outline animals and their eyes separately.
- `segment_eyes_on_crops.py`: same task, but looks for eyes inside each animal's crop
  and saves to `output_crops/`.
- `config.yaml`: settings for both scripts.
- `requirements.txt`: installed Python package versions.
- `data/`: sample COCO images.
- `output/`: annotated images and JSON results (ignored by Git).
- `model/`: weights, model documentation and animated diagrams.
