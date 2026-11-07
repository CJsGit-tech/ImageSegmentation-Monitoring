"""Download a random set of COCO val2017 images with at least 2 cats or dogs in total.

An image can hold 2 cats, 2 dogs, 1 of each or more, and crowd annotations don't count. EXCLUDED
lists images that fail a visual check. Images are chosen with a fixed random seed, skipping any
already in data/. Images and selection.json go to data/coco_cats_dogs_38/. COCO's annotation file is used for selection only and is not used for
scoring, because COCO has no eye annotations for animals.

Usage, from anywhere:
    .venv/bin/python image-segmentation/select_coco_images.py
"""

import hashlib
import io
import json
import random
import shutil
import urllib.request
import zipfile
from collections import Counter

from segment_animals_eyes import ROOT

OUTPUT = ROOT / "data" / "coco_cats_dogs_38"
ANNOTATIONS = OUTPUT / "instances_val2017.json"
ANNOTATIONS_URL = "http://images.cocodataset.org/annotations/annotations_trainval2017.zip"
IMAGE_URL = "http://images.cocodataset.org/val2017/{}"
CLASSES = ("cat", "dog")
MIN_ANIMALS = 2
IMAGE_COUNT = 38
SEED = 42

# COCO annotations count these as 2 or more, but a visual check found they don't show 2 real cats or dogs.
EXCLUDED = {
    169076: "1 animal, boxed twice as a cat and a dog",
    375278: "1 clear animal",
    366884: "No clear cat or dog",
    411665: "1 cat and its reflection",
    289702: "1 dog and its reflection",
    57760: "Dogs too small to see, about 9 px wide",
    63552: "1 cat and its reflection",
    554291: "1 animal, boxed twice",
    464522: "1 clear dog, with only part of another",
    49810: "1 cat and its reflection",
    262938: "Toys and pictures, not real animals",
    427034: "1 dog and a framed picture",
}


def download(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def load_annotations():
    if not ANNOTATIONS.exists():
        print("Downloading COCO annotations, about 240 MB")
        with zipfile.ZipFile(io.BytesIO(download(ANNOTATIONS_URL))) as archive:
            with archive.open("annotations/instances_val2017.json") as source, open(ANNOTATIONS, "wb") as target:
                shutil.copyfileobj(source, target)
    return json.loads(ANNOTATIONS.read_text())


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    coco = load_annotations()
    class_names = {c["id"]: c["name"] for c in coco["categories"] if c["name"] in CLASSES}
    images = {i["id"]: i for i in coco["images"]}
    counts = {}
    for annotation in coco["annotations"]:
        if annotation["category_id"] in class_names and not annotation["iscrowd"]:
            counts.setdefault(annotation["image_id"], Counter())[class_names[annotation["category_id"]]] += 1

    candidates = sorted(i for i, count in counts.items() if sum(count.values()) >= MIN_ANIMALS and i not in EXCLUDED)
    usable = len(candidates) - len(EXCLUDED)
    print(f"Images with at least {MIN_ANIMALS} cats or dogs: {len(candidates)}, usable after exclusions: {usable}")
    if usable < IMAGE_COUNT:
        raise SystemExit(f"Only {usable} val2017 images qualify, and {IMAGE_COUNT} are needed.")

    existing = {hashlib.md5(p.read_bytes()).hexdigest() for p in (ROOT / "data").glob("*.jpg")}
    random.Random(SEED).shuffle(candidates)  # Shuffled before exclusions, so the order stays the same.
    selected = []
    for image_id in candidates:
        if len(selected) == IMAGE_COUNT:
            break
        if image_id in EXCLUDED:
            continue
        info = images[image_id]
        content = download(IMAGE_URL.format(info["file_name"]))
        if hashlib.md5(content).hexdigest() in existing:
            print(f"{info['file_name']}: skipped, already in data/")
            continue
        (OUTPUT / info["file_name"]).write_bytes(content)
        selected.append({"file_name": info["file_name"], "coco_id": image_id, "animals": dict(counts[image_id]),
                         "license": info["license"], "coco_url": info["coco_url"], "flickr_url": info.get("flickr_url")})

    (OUTPUT / "selection.json").write_text(json.dumps(
        {"seed": SEED, "qualifying_images": usable, "excluded": EXCLUDED, "images": selected}, indent=2))
    print(f"Saved {len(selected)} images to: {OUTPUT}")


if __name__ == "__main__":
    main()
