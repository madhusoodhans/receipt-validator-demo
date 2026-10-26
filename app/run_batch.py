"""Runs the demo set through the pipeline headless and prints routes, messages and extracted rows.
Usage: python app/run_batch.py [--db results/batch.db] [--out results/batch.json] [files...]"""
import argparse
import json
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
import db  # noqa: E402
import pipeline  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--db", default="results/batch.db")
ap.add_argument("--out", default="results/batch.json")
ap.add_argument("files", nargs="*")
a = ap.parse_args()
if os.path.exists(a.db):
    os.remove(a.db)
con = db.connect(a.db)
labels = json.load(open("data/demo/labels.json"))
labels = [l for l in labels if not a.files or l["file"] in a.files]
runs = []
for n, l in enumerate(labels):
    path = f"data/demo/{l['file']}"
    ev = dict(pipeline.run(con, f"b{n}", l["file"], open(path, "rb").read(), "image/jpeg", Image.open(path).size))
    runs.append({"label": l, "events": ev})
    if "error" in ev:
        print(f"{l['file']:28} ERROR {ev['error']}"); continue
    r = ev["route"]["route"]; ok = (r == "reject") == (l["expected_route"] == "reject")
    s = ev["done"]["steps"]
    line = f"{l['file']:28} {r:8} {'ok ' if ok else 'XX '} d3 {s['d3']:5.0f} ms  "
    if r == "reject":
        line += f"4B {s['message']:5.0f} ms  [{ev['route']['summary']}] {ev['message']['text']}"
    else:
        rc = ev["extract"]["receipt"]
        line += (f"27B {s['extract']:5.0f} ms  {rc.get('store_name')!s:.24} | {rc.get('date')} | {rc.get('currency')} "
                 f"{rc.get('total')} | {len(rc.get('items') or [])} items | sum {'ok' if ev['sumcheck']['ok'] else 'FAIL'}"
                 f"{' REVIEW' if ev['stored']['needs_review'] else ''}")
    print(line, flush=True)
bl = "data/demo/baseline.json"
demo_baseline = {i["sha256"]: i["ms"] for i in json.load(open(bl))["images"]} if os.path.exists(bl) else {}
stats = pipeline.stats(con, demo_baseline)
json.dump({"runs": runs, "stats": stats}, open(a.out, "w"), indent=1, ensure_ascii=False)
print(json.dumps(stats, indent=1))
