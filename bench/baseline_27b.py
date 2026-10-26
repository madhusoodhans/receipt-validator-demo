"""Measures the all-27B baseline: Qwen 27B reads every demo image (good and bad), one request at a time, with the same
call the pipeline makes (app/extract.py, cold cache). Writes data/demo/baseline.json, which the server uses for the
"everything on Qwen 27B" side of the comparison, with the GPU it was measured on. Run from the repo root:

  .venv/bin/python -I bench/baseline_27b.py [--out data/demo/baseline.json]
"""
import argparse
import hashlib
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "app"))
import gpuinfo  # noqa: E402
from extract import extract  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=os.path.join(ROOT, "data/demo/baseline.json"))
a = ap.parse_args()
demo = os.path.join(ROOT, "data/demo")
labels = json.load(open(os.path.join(demo, "labels.json")))

# One throwaway request (a re-encoded copy, so the real bytes stay uncached) so the first timed image does not pay warm-up.
im = Image.open(os.path.join(demo, labels[0]["file"])).convert("RGB"); im.putpixel((0, 0), (1, 2, 3))
buf = io.BytesIO(); im.save(buf, "JPEG", quality=90)
extract(buf.getvalue())

out = []
for l in labels:
    raw = open(os.path.join(demo, l["file"]), "rb").read()
    _rc, ms, usage = extract(raw)
    out.append({"file": l["file"], "group": l["group"], "sha256": hashlib.sha256(raw).hexdigest(), "ms": ms,
                "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")})
    print(f"{l['file']:28} {l['group']:4} {ms:7.0f} ms  {usage.get('completion_tokens')} tokens", flush=True)
    time.sleep(0.2)

doc = {"model": "qwen27b", "gpu": gpuinfo.gpu_name(),
       "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
       "how": "app/extract.py extract(), cold cache (fresh cache_salt), sequential, one run per image",
       "total_ms": round(sum(r["ms"] for r in out), 1), "images": out}
json.dump(doc, open(a.out, "w"), indent=1)
print(f"total {doc['total_ms'] / 1000:.1f} s for {len(out)} images -> {a.out}")
