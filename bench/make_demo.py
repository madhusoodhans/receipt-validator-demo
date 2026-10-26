"""Builds the demo set (data/demo/): the user's images from data/raw/{good,bad}, stand-ins for the problems they do not
cover, and CORD test receipts. Play order alternates good and bad. Labels in data/demo/labels.json."""
import json, os, shutil
from PIL import Image

OUT = "data/demo"
os.makedirs(OUT, exist_ok=True)
P0 = {l["file"]: l for l in json.load(open("data/phase0/labels.json"))}

GOOD = [
    ("berghotel", "data/raw/good/images.jpeg", "Swiss hotel bar receipt", "user",
     {"store_name": "Berghotel Grosse Scheidegg", "date": "2007-07-30", "currency": "CHF", "total": 54.50, "tax": 3.85,
      "items": [9.00, 5.00, 22.00, 18.50]}),
    ("matcha_invoice", "data/raw/good/images (1).jpeg", "Malaysian cafe receipt", "user",
     {"currency": "RM", "total": 12.00, "subtotal": 12.00, "discount": 3.00, "items": [15.00]}),
    ("cash_template", "data/raw/good/images (2).jpeg", "Receipt template (lorem ipsum)", "user",
     {"currency": "$", "total": 63.38, "tax": 5.55, "items": [4.25, 1.10, 4.00, 21.55, 6.99, 15.50, 9.99]}),
] + [(f"cord{P0[f]['cord_index']:03d}", f"data/phase0/{f}", "Indonesian receipt (CORD)", "cord", None)
     for f in ["01_good_cord001.jpg", "02_good_cord006.jpg", "03_good_cord010.jpg", "04_good_cord015.jpg",
               "05_good_cord022.jpg", "06_good_cord025.jpg", "07_good_cord027.jpg", "08_good_cord029.jpg",
               "12_good_cord021.jpg"]]
BAD = [
    ("blurry_hand", "data/raw/bad/images.jpeg", "Blurry, held in a hand", "user", ["blurry"]),
    ("crumpled", "data/raw/bad/Receipt_that_was_badly_scanned.jpg", "Crumpled receipt", "user", ["damaged"]),
    ("faded_face", "data/raw/bad/Face-on-receipt.jpg", "Faded thermal print", "user", ["damaged", "blurry"]),
    ("pile", "data/raw/bad/images (2).jpeg", "A pile of receipts", "user", ["multiple"]),
    ("stock_register", "data/raw/bad/images (1).jpeg", "Blank stock register form", "user", ["kind"]),
    ("cut_off", "data/phase0/14_cutoff_synth.jpg", "Total cut off (synthetic)", "stand-in", ["complete"]),
    ("shadow", "data/cord_test/002.png", "Hand shadow over the items (CORD)", "cord", ["glare"]),
    ("screenshot", "data/phase0/19_screenshot_chat.jpg", "Chat app screenshot", "stand-in", ["kind"]),
    ("dog_photo", "data/phase0/18_photo_dog.jpg", "Photo of a dog", "stand-in", ["kind"]),
]
# Borderline: real CORD photos D3 is unsure about; the 27B reads them and they are stored with needs_review.
BORDER = [
    ("faded_creased", 19, "Faded and creased (CORD)"),
    ("soft_focus", 91, "Soft focus and some glare (CORD)"),
]
for f in os.listdir(OUT):  # images and labels only: baseline.json (measured 27B times) is kept
    if f.endswith(".jpg") or f == "labels.json":
        os.remove(os.path.join(OUT, f))
labels, order = [], []
g, b = list(GOOD), list(BAD)
while g or b:  # good, bad, good, bad ... then the remaining good ones
    if g: order.append(("good", g.pop(0)))
    if b: order.append(("bad", b.pop(0)))
for pos, (k, i, label) in zip((6, 15), BORDER):  # one borderline image in each half of the walk-through
    order.insert(pos, ("borderline", (k, f"data/cord_test/{i:03d}.png", label, "cord", i)))
for n, (group, item) in enumerate(order, 1):
    key, src, label, source, extra = item
    name = f"{n:02d}_{group}_{key}.jpg"
    Image.open(src).convert("RGB").save(f"{OUT}/{name}", quality=95)
    row = {"file": name, "group": group, "label": label, "source": source,
           "expected_route": {"good": "extract", "bad": "reject", "borderline": "review"}[group]}
    if group == "borderline":
        cord = {r["i"]: r["gt"] for r in json.load(open("data/cord_test/gt.json"))}
        row["cord_index"] = extra; row["gt"] = cord[extra]
    elif group == "good":
        if source == "cord":
            p = P0[os.path.basename(src)]; row["cord_index"] = p["cord_index"]; row["gt"] = p["gt"]
        else:
            row["truth"] = extra
    else:
        row["problems"] = extra
    if source == "cord" and group == "bad":
        row["cord_index"] = 2
    labels.append(row)
json.dump(labels, open(f"{OUT}/labels.json", "w"), indent=1, ensure_ascii=False)
from collections import Counter
print(len(labels), "images:", dict(Counter(l["group"] for l in labels)))
