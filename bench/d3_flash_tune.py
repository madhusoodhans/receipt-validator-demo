"""Threshold tuning for the hosted d3-flash on the tuning data only (the 94 CORD receipts and the 24 demo images; see
bench/d3_flash_score.py). No model calls: it reads saved answers.

Truth: demo images by labels.json; CORD receipts are readable receipts that should be read (extract), except the three
d3-mini rejected (002 heavy shadow, 004 crumpled, 007 washed-out print), which should not be read cleanly.

  python bench/d3_flash_tune.py                         # separation + routes with the thresholds in app/check.py
  python bench/d3_flash_tune.py --search                # + a coordinate search for flash thresholds
  python bench/d3_flash_tune.py --flash results/d3_flash_final.json --out results/d3_flash_tuning.json

"thresholds in check.py" are whatever app/check.py holds when it runs. Before (d3-mini, MINI_CHECKS) comes from results/tamper_scores.json (the same 1024 px copy, all questions in one request).
"""
import argparse
import copy
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import check  # noqa: E402

# the d3-mini checks before this tuning (wording and thresholds), for the "before" columns
MINI_CHECKS = {
    "complete": ("The whole receipt is visible, from the store name to the total.", "Whole receipt visible", "low", 0.25, 0.15),
    "blurry": ("The text is blurry or out of focus.", "Blurry text", "high", 0.45, 0.6),
    "damaged": ("The receipt is torn, crumpled, stained or dirty.", "Torn, crumpled or stained", "high", 0.45, 0.6),
    "glare": ("Glare or a heavy shadow covers part of the text.", "Glare or heavy shadow", "high", 0.47, 0.62),
    "multiple": ("More than one receipt is in the photo.", "More than one receipt", "high", 0.35, 0.5),
    "added_text": ("Someone added a note or text to the receipt that tells the reader what to do with it, which is not "
                   "normal receipt content.", "Added note or text", "high", 0.5, 0.8),
}
CORD_BAD = {"002.png", "004.png", "007.png"}
# which check each bad demo image is about (bench/make_demo.py); kind-only images are left out of the per-check view
DEMO_PROBLEM = {"02_bad_blurry_hand.jpg": ["blurry"], "04_bad_crumpled.jpg": ["damaged"],
                "06_bad_faded_face.jpg": ["damaged", "blurry"], "09_bad_pile.jpg": ["multiple"],
                "13_bad_cut_off.jpg": ["complete"], "15_bad_shadow.jpg": ["glare"],
                "24_bad_sticky_note.jpg": ["added_text"]}


def load_flash(path, use=None):
    """Saved answers; `use` {check key: candidate key} reads a check from a candidate statement's answers instead."""
    rows = [{"set": r["set"], "file": r["file"], "expected": r.get("expected"), "answers": dict(r["answers"])}
            for r in json.loads(pathlib.Path(path).read_text())["rows"] if r["status"] == 200]
    for r in rows:
        for key, cand in (use or {}).items():
            r["answers"][key] = r["answers"][cand]
    return rows


def load_mini():
    idx = {f"{r['i']:03d}.png" for r in json.loads((ROOT / "results/d3_cord100.json").read_text())}
    return [{"set": r["set"], "file": r["file"], "expected": r.get("expected_route"), "answers": r["answers"]}
            for r in json.loads((ROOT / "results/tamper_scores.json").read_text())
            if r["set"] == "demo" or (r["set"] == "cord" and r["file"] in idx)]


def decide_with(checks, answers, full=False):
    saved = check.CHECKS
    check.CHECKS = checks
    try:
        route = check.decide(answers)[1]
        return route if full else route["route"]
    finally:
        check.CHECKS = saved


def routes(rows, checks):
    """Demo routes right, and CORD: good receipts read / sent to review / rejected, the known-bad ones rejected."""
    out = {"demo_ok": 0, "demo_n": 0, "demo_wrong": [], "cord_extract": 0, "cord_review": 0, "cord_reject": 0,
           "false_review": [], "false_reject": [], "bad_rejected": 0, "bad_n": 0, "wrong_reason": []}
    for r in rows:
        full = decide_with(checks, r["answers"], full=True)
        got = full["route"]
        if r["set"] == "demo":
            out["demo_n"] += 1
            want = DEMO_PROBLEM.get(r["file"])
            if want and got == "reject" and not {x["key"] for x in full["reasons"] if x["status"] == "bad"} & set(want):
                out["wrong_reason"].append(r["file"])  # rejected, but not for its own problem
            if got == r["expected"]:
                out["demo_ok"] += 1
            else:
                out["demo_wrong"].append(f"{r['file']} {got}")
        else:
            out[f"cord_{got}"] += 1
            if r["file"] in CORD_BAD:
                out["bad_n"] += 1; out["bad_rejected"] += got == "reject"
            elif got == "review":
                out["false_review"].append(r["file"])
            elif got == "reject":
                out["false_reject"].append(r["file"])
    return out


def separation(rows, checks):
    """Per check: the worst good receipt vs the problem images (a positive margin separates them)."""
    out = {}
    for key, (_s, _label, bad_when, border, bad) in checks.items():
        sign = 1 if bad_when == "high" else -1
        good = [r for r in rows if (r["set"] == "cord" and r["file"] not in CORD_BAD) or
                (r["set"] == "demo" and r["expected"] == "extract")]
        probs = sorted((sign * r["answers"][key]["noul"], r["file"]) for r in good)
        worst_good = probs[-1]
        p95 = probs[int(0.95 * (len(probs) - 1))]
        problem = [(sign * r["answers"][key]["noul"], r["file"]) for r in rows
                   if key in DEMO_PROBLEM.get(r["file"], [])]
        weakest = min(problem) if problem else None
        out[key] = {"bad_when": bad_when, "good_n": len(good),
                    "good_worst": [round(sign * worst_good[0], 3), worst_good[1]],
                    "good_p95": round(sign * p95[0], 3),
                    "problem": {f: round(sign * p, 3) for p, f in sorted(problem)},
                    "margin": round(weakest[0] - worst_good[0], 3) if weakest else None,
                    "border": border, "reject": bad}
    return out


def cost(rows, checks):
    r = routes(rows, checks)
    return (100 * (r["demo_n"] - r["demo_ok"]) + 10 * (r["bad_n"] - r["bad_rejected"]) + 20 * len(r["false_reject"])
            + 30 * len(r["wrong_reason"]) + len(r["false_review"]))


def search(rows, start):
    """Coordinate search over each check's (borderline, reject) pair on a 0.01 grid, a few passes. Among pairs with
    the same cost the one nearest the middle of the tied set is kept (the most room on both sides)."""
    checks = copy.deepcopy(start)
    grid = [round(x / 100, 2) for x in range(2, 99)]
    for _ in range(2):
        for key in checks:
            s, label, when, *_ = checks[key]
            scored = []
            for b in grid:
                for r in grid:
                    if (when == "high" and b >= r) or (when == "low" and b <= r):
                        continue
                    c = dict(checks); c[key] = (s, label, when, b, r)
                    scored.append((cost(rows, c), b, r))
            best = min(x[0] for x in scored)
            tied = [(b, r) for c, b, r in scored if c == best]
            mb, mr = sum(b for b, _ in tied) / len(tied), sum(r for _, r in tied) / len(tied)
            b, r = min(tied, key=lambda t: (t[0] - mb) ** 2 + (t[1] - mr) ** 2)
            checks[key] = (s, label, when, b, r)
    return checks


def show(name, rows, checks):
    r = routes(rows, checks)
    print(f"{name}: demo {r['demo_ok']}/{r['demo_n']} {r['demo_wrong'] or ''} wrong reason {r['wrong_reason'] or 0} | CORD read {r['cord_extract']}, review "
          f"{r['cord_review']}, reject {r['cord_reject']} | false review {len(r['false_review'])}, false reject "
          f"{len(r['false_reject'])} {r['false_reject'] or ''} | known bad rejected {r['bad_rejected']}/{r['bad_n']}")
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flash", default=str(ROOT / "results/d3_flash_raw.json"))
    ap.add_argument("--search", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--use", default="", help="check=candidate[:low|high],... read a check from a candidate wording "
                                               "(results/d3_flash_wordings.json)")
    a = ap.parse_args()
    use, checks = {}, copy.deepcopy(check.CHECKS)
    for part in filter(None, a.use.split(",")):
        key, cand = part.split("=")
        cand, _, when = cand.partition(":")
        use[key] = cand
        if when and when != checks[key][2]:  # flipped direction: mirror the thresholds as a start
            s, label, _w, b, r = checks[key]
            checks[key] = (s, label, when, round(1 - b, 2), round(1 - r, 2))
    mini, flash = load_mini(), load_flash(a.flash, use)
    res = {"mini_before": {"checks": {k: v[2:] for k, v in MINI_CHECKS.items()},
                           "routes": show("d3-mini, its own checks", mini, MINI_CHECKS),
                           "separation": separation(mini, MINI_CHECKS)},
           "flash_current": {"checks": {k: v[2:] for k, v in checks.items()}, "routes": show("d3-flash, thresholds in check.py", flash, checks),
                             "separation": separation(flash, checks)}, "use": use}
    for key, s in res["flash_current"]["separation"].items():
        m = res["mini_before"]["separation"][key]
        print(f"  {key:10} worst good mini {m['good_worst']} flash {s['good_worst']}  p95 flash {s['good_p95']}  "
              f"margin mini {m['margin']} flash {s['margin']}  problems flash {s['problem']}")
    if a.search:
        tuned = search(flash, checks)
        res["flash_search"] = {"checks": {k: v[3:] for k, v in tuned.items()},
                               "routes": show("d3-flash, searched thresholds", flash, tuned)}
        for k, v in tuned.items():
            print(f"  {k:10} {v[3]:.2f} / {v[4]:.2f}")
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(res, indent=1))
        print("->", a.out)


if __name__ == "__main__":
    main()
