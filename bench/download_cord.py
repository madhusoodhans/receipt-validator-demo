"""Downloads the CORD v2 test split (naver-clova-ix/cord-v2 on the Hugging Face hub, CC BY 4.0) into data/cord_test/:
000.png ... 099.png (the image bytes as published) and gt.json ([{"i", "file", "size", "gt"}], gt = the gt_parse labels).

Only the bench scripts need it (bench/make_standins.py, bench/make_demo.py, the 100-receipt D3 check); the demo itself
runs from data/demo/. Needs requirements-bench.txt. Run from anywhere:

  HF_HUB_DISABLE_XET=1 .venv/bin/python -I bench/download_cord.py [--out data/cord_test]
"""
import argparse
import io
import json
import os

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # plain HTTP downloads; the Xet transfer path stalled on our hosts

import pyarrow.parquet as pq  # noqa: E402
from huggingface_hub import hf_hub_download, list_repo_files  # noqa: E402
from PIL import Image  # noqa: E402

REPO = "naver-clova-ix/cord-v2"
REVISION = "7f0115a4b758a71d6473b8d085751692da2fef98"  # the revision the demo set and results were built from
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=os.path.join(ROOT, "data/cord_test"))
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)

files = [f for f in list_repo_files(REPO, repo_type="dataset", revision=REVISION)
         if f.startswith("data/test-") and f.endswith(".parquet")]
rows = []
for f in sorted(files):
    path = hf_hub_download(REPO, f, repo_type="dataset", revision=REVISION)
    rows += pq.read_table(path).to_pylist()

gt = []
for i, r in enumerate(rows):
    data = r["image"]["bytes"]
    name = f"{i:03d}.png"
    with open(os.path.join(a.out, name), "wb") as fh:
        fh.write(data)
    size = list(Image.open(io.BytesIO(data)).size)
    gt.append({"i": i, "file": name, "size": size, "gt": json.loads(r["ground_truth"])["gt_parse"]})
with open(os.path.join(a.out, "gt.json"), "w") as fh:
    json.dump(gt, fh, indent=1, ensure_ascii=False)
print(f"{len(gt)} CORD test receipts -> {a.out}")
