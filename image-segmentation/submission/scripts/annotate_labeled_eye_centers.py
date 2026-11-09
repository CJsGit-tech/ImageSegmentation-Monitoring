"""Draw the labeled eye boxes, their centers and the resulting distances on all 12 images.

Labels come from data/002-other_samples/eye_marks.json. Each eye's center is the middle of its box, and
these centers are the reference that measured distances are compared against. Annotated images go to
outputs/004-labeled_eye_centers/ at twice the original size, but distances stay in original image pixels.

Right eyes use the smaller x, which assumes the animal faces the camera. Right-eye lines are only drawn
when an image has at most two measurable animals, because more would overlap.

Usage:
    .venv/bin/python image-segmentation/submission/scripts/annotate_labeled_eye_centers.py
"""

import itertools
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "data/002-other_samples"
OUT = ROOT / "outputs/004-labeled_eye_centers"

SCALE = 2
MAX_ANIMALS_FOR_PAIRS = 2
LABEL_SIZE = 20
FOOTER_SIZE = 17
YELLOW = (255, 214, 0)
BLUE = (0, 229, 255)
PINK = (255, 80, 200)
DARK = (18, 18, 18)
LIGHT = (235, 235, 235)


def center(box):
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def scaled(point):
    return point[0] * SCALE, point[1] * SCALE


def measurable(animals):
    """Return (name, centers ordered by x) for each animal with two labeled eyes."""
    return [(a["animal"], sorted((center(box) for box in a["eyes"]), key=lambda c: c[0]))
            for a in animals if len(a["eyes"]) == 2]


def draw_label(draw, text, anchor, font, color, size, taken):
    """Draw text on a dark tag near anchor, moving it to avoid eye boxes and other labels."""
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    width, height = right - left + 10, bottom - top + 8
    x = min(max(anchor[0] - width / 2, 2), size[0] - width - 2)
    start = min(max(anchor[1] - height - 12, 2), size[1] - height - 2)

    def is_free(y):
        return 2 <= y <= size[1] - height - 2 and not any(
            x < t[2] + 4 and x + width > t[0] - 4 and y < t[3] + 4 and y + height > t[1] - 4 for t in taken)

    options = (start + direction * (height + 6) * step for step in range(12) for direction in (1, -1))
    y = next((option for option in options if is_free(option)), start)
    taken.append((x, y, x + width, y + height))
    draw.rectangle((x, y, x + width, y + height), fill=DARK)
    draw.text((x + 5 - left, y + 4 - top), text, font=font, fill=color)


def draw_dashed_box(draw, box, color):
    x1, y1, x2, y2 = (value * SCALE for value in box)
    for x in range(int(x1), int(x2), 12):
        draw.line((x, y1, min(x + 7, x2), y1), fill=color, width=3)
        draw.line((x, y2, min(x + 7, x2), y2), fill=color, width=3)
    for y in range(int(y1), int(y2), 12):
        draw.line((x1, y, x1, min(y + 7, y2)), fill=color, width=3)
        draw.line((x2, y, x2, min(y + 7, y2)), fill=color, width=3)


def footer_lines(ignored, measured, pairs_drawn):
    lines = ["Boxes: eye labels estimated by Claude Code, reviewed by a person. Dots: box centers. Distances: original image pixels.",
             "Blue dot: right eye, assuming the animal faces the camera (smaller x)."]
    if ignored:
        lines.append("Dashed pink: eyes couldn't be placed, so eyes there aren't scored.")
    if not pairs_drawn:
        lines.append(f"Right-eye lines not drawn: {len(measured)} measurable animals would overlap.")
    if not measured:
        lines.append("No animal in this image has two labeled eyes.")
    return lines


def annotate(stem, animals, ignored):
    """Save one annotated image and return its distances in original image pixels."""
    image = Image.open(IMAGES / f"{stem}.jpg").convert("RGB")
    image = image.resize((image.width * SCALE, image.height * SCALE), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(LABEL_SIZE)

    for box in ignored:
        draw_dashed_box(draw, box, PINK)
    for animal in animals:
        for box in animal["eyes"]:
            draw.rectangle([value * SCALE for value in box], outline=YELLOW, width=1)

    measured = measurable(animals)
    pairs_drawn = len(measured) <= MAX_ANIMALS_FOR_PAIRS
    distances, eye_labels, pair_labels = [], [], []
    for name, (right, left) in measured:
        a, b = scaled(right), scaled(left)
        distance = math.dist(right, left)
        draw.line((*a, *b), fill=YELLOW, width=2)
        eye_labels.append((f"{name.split(',')[0]}: {distance:.2f} px", ((a[0] + b[0]) / 2, min(a[1], b[1]) - 16), YELLOW))
        distances.append({"image": stem, "measurement": "both_eyes", "item": name, "marked_px": round(distance, 2)})
    if pairs_drawn:
        for (name_a, eyes_a), (name_b, eyes_b) in itertools.combinations(measured, 2):
            a, b = scaled(eyes_a[0]), scaled(eyes_b[0])
            distance = math.dist(eyes_a[0], eyes_b[0])
            draw.line((*a, *b), fill=BLUE, width=2)
            pair_labels.append((f"right eyes: {distance:.2f} px", ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + 12), BLUE))
            distances.append({"image": stem, "measurement": "right_eyes", "item": f"{name_a} to {name_b}", "marked_px": round(distance, 2)})

    for animal in animals:
        boxes = sorted(animal["eyes"], key=lambda box: center(box)[0])
        for index, box in enumerate(boxes):
            x, y = scaled(center(box))
            radius = max(2.5, min(5, min(box[2] - box[0], box[3] - box[1]) * SCALE / 5))
            color = BLUE if index == 0 and len(boxes) == 2 else YELLOW
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color, outline=(0, 0, 0))

    # Eye boxes are reserved so labels never cover what they describe. Eye labels go first to stay close.
    taken = [tuple(value * SCALE for value in box) for animal in animals for box in animal["eyes"]]
    for text, anchor, color in eye_labels + pair_labels:
        draw_label(draw, text, anchor, font, color, image.size, taken)

    lines = footer_lines(ignored, measured, pairs_drawn)
    canvas = Image.new("RGB", (image.width, image.height + 12 + 25 * len(lines)), DARK)
    canvas.paste(image, (0, 0))
    footer_draw = ImageDraw.Draw(canvas)
    footer_font = ImageFont.load_default(FOOTER_SIZE)
    for row, text in enumerate(lines):
        footer_draw.text((8, image.height + 6 + row * 25), text, font=footer_font, fill=LIGHT)
    canvas.save(OUT / f"{stem}_labeled_eye_centers.jpg", quality=92)
    return distances


def main():
    marks = json.loads((IMAGES / "eye_marks.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    for stem, animals in marks["images"].items():
        for row in annotate(stem, animals, marks["ignore"].get(stem, [])):
            print(f"{row['image']},{row['measurement']},{row['item']},{row['marked_px']}")
    print(f"Saved {len(marks['images'])} images to: {OUT}")


if __name__ == "__main__":
    main()
