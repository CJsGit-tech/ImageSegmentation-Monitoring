"""Draw eye distances on the test samples, in image pixels and rough centimetres.

Run the YOLOE + DINOv2 pipeline first, because this reads its results from
outputs/001-test_samples_yoloe_dinov2/. Annotated images go to outputs/003-eye_distance_annotations/,
and the measurements are printed as CSV.

Distances come from the heatmap level variant. Each animal's scale comes from the mean length of its
GrabCut eye outlines, taken as the width of the cornea in CORNEA_MM. The centimetres are rough and
were not checked against real measurements. Right-eye pairs stay in pixels, because a single scale
doesn't apply to animals at different depths.

Usage:
    .venv/bin/python image-segmentation/submission/scripts/annotate_eye_distances.py
"""

import itertools
import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data/001-test_samples"
RESULTS = ROOT / "outputs/001-test_samples_yoloe_dinov2"
OUT = ROOT / "outputs/003-eye_distance_annotations"

# Cat: Carrington 1986, https://onlinelibrary.wiley.com/doi/10.1111/j.1475-1313.1986.tb01157.x
# Dog: middle of the 16-18 mm typical of medium dogs, https://www.nature.com/articles/s41598-022-24438-5
CORNEA_MM = {"cat": 16.5, "dog": 17.0}

ZOOM = 2
FONT = cv2.FONT_HERSHEY_DUPLEX
LINE = (0, 255, 255)
PAIR = (255, 220, 120)
OUTLINE_KEYS = ("polygon", "outline", "contour")


def outline(eye):
    for key in OUTLINE_KEYS:
        if eye.get(key):
            return np.array(eye[key], dtype=np.float32)
    raise KeyError(f"No outline in {list(eye)}")


def label(canvas, text, anchor, color, above=True):
    """Draw a boxed label centred on anchor, kept inside the image."""
    (w, h), base = cv2.getTextSize(text, FONT, 0.6, 1)
    x = int(np.clip(anchor[0] - w / 2, 4, canvas.shape[1] - w - 4))
    y = int(anchor[1] - 26 if above else anchor[1] + 26 + h)
    y = int(np.clip(y, h + 6, canvas.shape[0] - base - 6))
    cv2.line(canvas, (int(anchor[0]), int(anchor[1])), (x + w // 2, y), color, 1, cv2.LINE_AA)
    cv2.rectangle(canvas, (x - 4, y - h - 5), (x + w + 4, y + base + 3), (20, 20, 20), -1)
    cv2.putText(canvas, text, (x, y), FONT, 0.6, color, 1, cv2.LINE_AA)


def main():
    OUT.mkdir(exist_ok=True)
    print("image,animal,right_eye_x,right_eye_y,left_eye_x,left_eye_y,distance_px,eye_px,mm_per_px,distance_mm")
    for path in sorted(DATA.glob("*.jpg")):
        level = json.loads((RESULTS / "heatmap_level" / f"{path.stem}.json").read_text())
        grabcut = json.loads((RESULTS / "grabcut" / f"{path.stem}.json").read_text())
        image = cv2.imread(str(path))
        height, width = image.shape[:2]
        canvas = cv2.resize(image, (width * ZOOM, height * ZOOM), interpolation=cv2.INTER_CUBIC)

        right_eyes = []
        labels = []
        for animal, cut in zip(level["animals"], grabcut["animals"]):
            if animal["class"] not in CORNEA_MM or len(animal["eyes"]) < 2:
                continue
            for eye in animal["eyes"]:
                points = (outline(eye) * ZOOM).astype(np.int32)
                cv2.polylines(canvas, [points], True, (60, 60, 255), 1, cv2.LINE_AA)
            right, left = sorted(tuple(e["center"]) for e in animal["eyes"])
            distance = float(np.hypot(left[0] - right[0], left[1] - right[1]))
            lengths = [max(cv2.minAreaRect(outline(e))[1]) for e in cut["eyes"]]
            eye_px = float(np.mean(lengths))
            mm_per_px = CORNEA_MM[animal["class"]] / eye_px
            mm = distance * mm_per_px
            print(f"{path.stem},{animal['class']},{right[0]},{right[1]},{left[0]},{left[1]},"
                  f"{distance:.2f},{eye_px:.1f},{mm_per_px:.3f},{mm:.1f}")

            a, b = (np.array(right) * ZOOM).astype(int), (np.array(left) * ZOOM).astype(int)
            cv2.line(canvas, tuple(a), tuple(b), LINE, 2, cv2.LINE_AA)
            for point in (a, b):
                cv2.circle(canvas, tuple(point), 4, LINE, -1, cv2.LINE_AA)
            middle = (a + b) / 2
            text = f"{animal['class']}: {distance:.1f} px ~ {mm / 10:.1f} cm"
            labels.append((text, middle, LINE, middle[1] > 60))
            right_eyes.append(np.array(right))

        for a, b in itertools.combinations(right_eyes, 2):
            start, end = (a * ZOOM).astype(int), (b * ZOOM).astype(int)
            cv2.line(canvas, tuple(start), tuple(end), PAIR, 1, cv2.LINE_AA)
            distance = float(np.hypot(*(a - b)))
            print(f"{path.stem},right-eye pair,,,,,{distance:.2f},,,")
            labels.append((f"right eyes: {distance:.1f} px", (start + end) / 2, PAIR, False))

        for text, anchor, color, above in labels:
            label(canvas, text, anchor, color, above)

        footer = np.full((34, canvas.shape[1], 3), 20, dtype=np.uint8)
        cv2.putText(footer, "cm values are rough estimates from eye size; px values are measured.",
                    (8, 23), FONT, 0.5, (235, 235, 235), 1, cv2.LINE_AA)
        cv2.imwrite(str(OUT / f"{path.stem}_eye_distances.jpg"), np.vstack([canvas, footer]),
                    [cv2.IMWRITE_JPEG_QUALITY, 92])


if __name__ == "__main__":
    main()
