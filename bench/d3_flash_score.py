"""Scores the tuning data on the hosted D3 (vllm-sr/d3-flash on https://app.vllm-sr.ai/v1/systemone) exactly as the
demo asks: check.for_d3 (the 1024 px copy), check.STATE and check.questions() in one request per image.

Tuning data only: the 94 CORD test receipts used for d3-mini (indices from results/d3_cord100.json, images in
data/cord_test, bench/download_cord.py) and the 24 demo images (data/demo, labels.json). Raw answers go to
results/d3_flash_<tag>.json; bench/d3_flash_tune.py reads them. 4 requests at a time.

The key comes from $VLLM_SR_API_KEY or ~/.vllm-sr-key (bench scripts only); it is never printed.

  .venv/bin/python bench/d3_flash_score.py [--tag raw] [--repeat 1]
  .venv/bin/python bench/d3_flash_score.py --tag wordings --extra bench/d3_flash_wordings.json  # + candidate statements
"""
import argparse
import base64
import json
import os
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import check  # noqa: E402

BASE = os.environ.get("VLLM_SR_BASE", "https://app.vllm-sr.ai").rstrip("/")
MODEL = os.environ.get("D3_MODEL", "vllm-sr/d3-flash")
CORD = pathlib.Path(os.environ.get("CORD_DIR", ROOT / "data/cord_test"))


def api_key():
    k = os.environ.get("VLLM_SR_API_KEY") or (pathlib.Path.home() / ".vllm-sr-key").read_text().strip()
    if not k:
        sys.exit("no API key: set VLLM_SR_API_KEY")
    return k


def items():
    out = []
    for lab in json.loads((ROOT / "data/demo/labels.json").read_text()):
        out.append({"set": "demo", "file": lab["file"], "group": lab["group"], "expected": lab["expected_route"],
                    "path": str(ROOT / "data/demo" / lab["file"])})
    for r in json.loads((ROOT / "results/d3_cord100.json").read_text()):
        i = r["i"]
        out.append({"set": "cord", "file": f"{i:03d}.png", "i": i, "mini_route": r["route"],
                    "path": str(CORD / f"{i:03d}.png")})
    return out


def score(item, headers, extra=None):
    raw = pathlib.Path(item["path"]).read_bytes()
    img, mime = check.for_d3(raw, "image/png" if item["path"].endswith(".png") else "image/jpeg")
    body = {"model": MODEL, "state": check.STATE, "images": [f"data:{mime};base64,{base64.b64encode(img).decode()}"],
            "questions": {**check.questions(), **(extra or {})}}
    row = {k: v for k, v in item.items() if k != "path"}
    for attempt in range(4):
        t = time.perf_counter()
        r = requests.post(f"{BASE}/v1/systemone", json=body, headers=headers, timeout=40)
        row["ms"] = round((time.perf_counter() - t) * 1000)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(float(r.headers.get("retry-after") or 2 * (attempt + 1)))
            continue
        break
    row["status"] = r.status_code
    if r.status_code == 200:
        j = r.json()
        row["answers"], row["usage"] = j["answers"], j.get("usage")
    else:
        try:
            row["error"] = r.json().get("error")
        except Exception:
            row["error"] = r.text[:300]
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="raw")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--extra", help="JSON {key: statement}: extra yes/no statements asked in the same request")
    a = ap.parse_args()
    extra = {k: {"type": "noul", "instructions": v} for k, v in json.loads(pathlib.Path(a.extra).read_text()).items()} \
        if a.extra else None
    headers = {"Authorization": f"Bearer {api_key()}", "content-type": "application/json"}
    todo = items() * a.repeat
    t = time.time()
    with ThreadPoolExecutor(4) as ex:
        rows = list(ex.map(lambda it: score(it, headers, extra), todo))
    bad = [r for r in rows if r["status"] != 200]
    out = ROOT / f"results/d3_flash_{a.tag}.json"
    out.write_text(json.dumps({"model": MODEL, "base": BASE, "state": check.STATE, "questions": {**check.questions(), **(extra or {})},
                               "n": len(rows), "errors": len(bad), "seconds": round(time.time() - t),
                               "rows": rows}, indent=1))
    ms = sorted(r["ms"] for r in rows if r["status"] == 200)
    print(f"{len(rows)} calls, {len(bad)} errors, median {ms[len(ms) // 2] if ms else '-'} ms -> {out}")


if __name__ == "__main__":
    main()
