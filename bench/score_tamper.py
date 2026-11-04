"""Calibrates the added-text check: every image through D3 exactly as the demo asks (check.ask_d3: the 1024 px copy,
all questions in one request), then the added_text probability per set and what the thresholds in app/check.py do.

Sets: the demo images (data/demo), the CORD test receipts (data/cord_test, bench/download_cord.py) and the synthetic
tampered images (data/tampered, bench/make_tampered.py). Env CORD_DIR reads the CORD receipts elsewhere. Writes results/tamper_scores.json. 4 requests at a time.

  .venv/bin/python bench/score_tamper.py            # score, then the summary
  .venv/bin/python bench/score_tamper.py --summary  # the summary of the saved scores only
"""
import glob
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "app"))
import check  # noqa: E402

OUT = os.path.join(ROOT, "results/tamper_scores.json")
CORD = os.environ.get("CORD_DIR", os.path.join(ROOT, "data/cord_test"))
KEY = "added_text"


def sets():
    labels = {l["file"]: l for l in json.load(open(os.path.join(ROOT, "data/demo/labels.json")))}
    out = []
    for p in sorted(glob.glob(os.path.join(ROOT, "data/demo/*.jpg"))):
        l = labels.get(os.path.basename(p), {})
        out.append(("demo", p, l.get("group"), l.get("expected_route")))
    out += [("cord", p, "good", None) for p in sorted(glob.glob(os.path.join(CORD, "*.png")))]
    out += [("tampered", p, "tampered", "reject") for p in sorted(glob.glob(os.path.join(ROOT, "data/tampered/*.jpg")))]
    return out


def score(item):
    s, path, group, expected = item
    raw = open(path, "rb").read()
    answers, ms = check.ask_d3(raw, "image/png" if path.endswith(".png") else "image/jpeg")
    return {"set": s, "file": os.path.basename(path), "group": group, "expected_route": expected, "ms": ms,
            "answers": answers}


def summary(rows):
    _statement, _label, _when, border, bad = check.CHECKS[KEY]
    print(f"thresholds: borderline from {border}, bad from {bad}")
    for name, pick in [("tampered", lambda r: r["set"] == "tampered"),
                       ("demo good/borderline", lambda r: r["set"] == "demo" and r["group"] in ("good", "borderline")),
                       ("demo bad", lambda r: r["set"] == "demo" and r["group"] == "bad"),
                       ("CORD test", lambda r: r["set"] == "cord")]:
        xs = [r for r in rows if pick(r)]
        if not xs:
            continue
        p = sorted(float(r["answers"][KEY]["noul"]) for r in xs)
        top = sorted(xs, key=lambda r: -float(r["answers"][KEY]["noul"]))[:3]
        print(f"{name:22} n={len(xs):3}  min {p[0]:.3f}  median {p[len(p) // 2]:.3f}  max {p[-1]:.3f}  "
              f">= borderline {sum(v >= border for v in p)}  >= bad {sum(v >= bad for v in p)}  top: "
              + ", ".join(f"{r['file']} {float(r['answers'][KEY]['noul']):.2f}" for r in top))
    print("routes (decide() with every check):")
    for r in rows:
        d3, route = check.decide(r["answers"])
        flagged = [x["status"] for x in route["reasons"] if x["key"] == KEY]
        r["route"], r["added_text"] = route["route"], (flagged or ["ok"])[0]
        want = r["expected_route"]
        if r["set"] == "tampered" or (want and route["route"] != want) or flagged:
            print(f"  {r['set']:8} {r['file']:34} {KEY} {float(r['answers'][KEY]['noul']):.3f} ({r['added_text']}), "
                  f"route {route['route']}" + (f" (expected {want})" if want else "") + f": {route['summary']}")
    cord = [r for r in rows if r["set"] == "cord"]
    print(f"CORD: {sum(r['route'] == 'reject' for r in cord)} rejected in all, "
          f"{sum(r['added_text'] == 'bad' for r in cord)} rejected for added text, "
          f"{sum(r['added_text'] == 'borderline' for r in cord)} borderline for added text, of {len(cord)}")


if __name__ == "__main__":
    if "--summary" in sys.argv:
        rows = json.load(open(OUT))
    else:
        with ThreadPoolExecutor(4) as ex:
            rows = list(ex.map(score, sets()))
        json.dump(rows, open(OUT, "w"), indent=1)
    summary(rows)
