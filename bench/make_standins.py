"""Builds the Phase 0 stand-in set (data/phase0/) from CORD test images, a public-domain photo and a real screenshot.
Stand-ins until the user's own photos arrive; labels go to data/phase0/labels.json."""
import json, os, random
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

random.seed(0)
SRC, OUT = "data/cord_test", "data/phase0"
os.makedirs(OUT, exist_ok=True)
gt = {r["i"]: r["gt"] for r in json.load(open(f"{SRC}/gt.json"))}
cord = lambda i: Image.open(f"{SRC}/{i:03d}.png").convert("RGB")
labels = []

def add(name, im, route, problems, source, cord_i=None):
    im.save(f"{OUT}/{name}.jpg", quality=92)
    labels.append({"file": f"{name}.jpg", "route": route, "problems": problems, "source": source,
                   **({"cord_index": cord_i, "gt": gt[cord_i]} if cord_i is not None else {})})

# 1-8 good receipts (CORD test, clean)
for k, i in enumerate([1, 6, 10, 15, 22, 25, 27, 29], 1):
    add(f"{k:02d}_good_cord{i:03d}", cord(i), "extract", [], "cord", i)
# 9-12: more CORD candidates for good, later replaced by the user's photos
for k, i in enumerate([44, 47, 36, 21], 9):
    add(f"{k:02d}_good_cord{i:03d}", cord(i), "extract", [], "cord", i)
# 13 blurry: Gaussian blur on a clean receipt
add("13_blurry_synth", cord(35).filter(ImageFilter.GaussianBlur(6)), "reject", ["blurry"], "synthetic from cord 35")
# 14 cut off: keep top 55 % (total gone)
im = cord(37); add("14_cutoff_synth", im.crop((0, 0, im.width, int(im.height * 0.55))), "reject", ["incomplete"], "synthetic from cord 37")
# 15 crumpled: real CORD photo
add("15_crumpled_cord004", cord(4), "reject", ["damaged"], "cord 4 (real)")
# 16 glare: bright elliptical blob over the totals of a clean receipt
im = cord(15); a = np.asarray(im).astype(np.float32); h, w = a.shape[:2]
yy, xx = np.mgrid[0:h, 0:w]; cy, cx = h * 0.72, w * 0.55
m = np.exp(-(((yy - cy) / (h * 0.16)) ** 2 + ((xx - cx) / (w * 0.35)) ** 2))[..., None]
add("16_glare_synth", Image.fromarray(np.clip(a * (1 - m) + 255 * m, 0, 255).astype(np.uint8)), "reject", ["glare"], "synthetic from cord 15")
# 17 two receipts on one table
bg = Image.new("RGB", (1400, 1200), (92, 64, 44)); r1, r2 = cord(22), cord(6)
for r, x in ((r1, 40), (r2, 720)):
    r.thumbnail((640, 1120)); bg.paste(r.rotate(random.uniform(-4, 4), expand=True, fillcolor=(92, 64, 44)), (x, 40))
add("17_two_receipts_synth", bg, "reject", ["multiple"], "composite of cord 22 and 6")
# 18 random photo
add("18_photo_dog", Image.open("data/raw/dog_public_domain.jpg").convert("RGB"), "reject", ["not_document"], "Wikimedia Commons, public domain")
# 19 screenshot of a chat app
add("19_screenshot_chat", Image.open("data/raw/chat_screenshot.jpg").convert("RGB"), "reject", ["screenshot"], "Telegram BotFather screenshot")
# 20 wrong document: a rendered restaurant menu
def font(sz):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p): return ImageFont.truetype(p, sz)
    return ImageFont.load_default()
menu = Image.new("RGB", (900, 1250), (250, 244, 230)); d = ImageDraw.Draw(menu)
d.text((450, 70), "Trattoria Bella", font=font(64), fill=(90, 30, 20), anchor="mm")
d.text((450, 140), "Dinner menu", font=font(34), fill=(90, 30, 20), anchor="mm")
y = 220
for sec, items in [("Starters", [("Bruschetta al pomodoro", "6.50", "Grilled bread, tomato, basil"), ("Minestrone", "7.00", "Seasonal vegetable soup")]),
                   ("Pasta", [("Spaghetti carbonara", "13.50", "Egg, pecorino, guanciale"), ("Penne all'arrabbiata", "11.00", "Spicy tomato sauce"), ("Lasagne della casa", "14.00", "Beef ragu, bechamel")]),
                   ("Desserts", [("Tiramisu", "6.50", "Mascarpone, espresso"), ("Panna cotta", "6.00", "Vanilla, berry coulis")])]:
    d.text((80, y), sec, font=font(40), fill=(90, 30, 20)); y += 65
    for n, p, desc in items:
        d.text((100, y), n, font=font(30), fill=(30, 30, 30)); d.text((800, y), p, font=font(30), fill=(30, 30, 30), anchor="ra")
        d.text((100, y + 38), desc, font=font(22), fill=(110, 110, 110)); y += 90
    y += 20
menu = menu.rotate(2.5, expand=True, fillcolor=(70, 70, 75)).filter(ImageFilter.GaussianBlur(0.6))
add("20_menu_rendered", menu, "reject", ["wrong_document"], "rendered with PIL")

json.dump(labels, open(f"{OUT}/labels.json", "w"), indent=1, ensure_ascii=False)
print(len(labels), "images in", OUT)
