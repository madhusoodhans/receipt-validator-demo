"""D3 check of one upload (d3-mini via the Router's /v1/systemone) and the routing rule.

One request per image with every question; the answers decide the route:
  reject   wrong kind of image, or any check past its threshold      -> Qwen 4B writes a re-upload message
  review   a check in its borderline band                             -> Qwen 27B reads it, stored with needs_review
  extract  everything clearly fine                                    -> Qwen 27B reads it
"""
import base64
import io
import os
import time

import requests
from PIL import Image

ROUTER_URL = os.environ.get("ROUTER_URL", "http://localhost:8899").rstrip("/")  # the vllm-sr Router
ROUTER = f"{ROUTER_URL}/v1/systemone"
MODEL = "vllm-sr/d3-mini"
STATE = "A user uploaded this image to a receipt scanning app. Check whether it is a receipt that can be read."

KIND = {"type": "choice", "instructions": "What does the image show?",
        "criteria": {"receipt": "A shop or restaurant receipt",
                     "invoice": "An invoice or bill on full-size paper",
                     "other_document": "Another document, such as a menu, letter or form",
                     "screenshot": "A screenshot of a phone or computer screen",
                     "photo": "A photo that is not a document"}}
KIND_LABEL = {"invoice": "An invoice, not a receipt", "other_document": "Another document, not a receipt",
              "screenshot": "A screenshot, not a receipt", "photo": "A photo, not a receipt"}
ACCEPTED_KINDS = {"receipt", "invoice"}

# key: (statement, label, bad_when, borderline_from, bad_from). For bad_when "low" the probabilities count downwards.
CHECKS = {
    "complete": ("The whole receipt is visible, from the store name to the total.", "Whole receipt visible", "low", 0.25, 0.15),
    "blurry": ("The text is blurry or out of focus.", "Blurry text", "high", 0.45, 0.6),
    "damaged": ("The receipt is torn, crumpled, stained or dirty.", "Torn, crumpled or stained", "high", 0.45, 0.6),
    "glare": ("Glare or a heavy shadow covers part of the text.", "Glare or heavy shadow", "high", 0.47, 0.62),  # tuned on 94 CORD receipts
    "multiple": ("More than one receipt is in the photo.", "More than one receipt", "high", 0.35, 0.5),
}


def questions():
    q = {"kind": KIND}
    for key, (statement, *_rest) in CHECKS.items():
        q[key] = {"type": "noul", "instructions": statement}
    return q


MAX_IMAGE_BYTES = 6_000_000  # the Router refuses images over 8 MB; D3 reads at most 1.6 MP anyway
MAX_IMAGE_PIXELS = 12_000_000  # ... and over 16 MP (a 24 MP iPhone photo is only ~4 MB as JPEG)


def fit_for_router(image_bytes, mime, long_side=2400):
    """Large images (big phone photos, PNGs) are shrunk to `long_side` and re-encoded as JPEG; others pass through
    unchanged."""
    im = Image.open(io.BytesIO(image_bytes))
    if len(image_bytes) <= MAX_IMAGE_BYTES and im.width * im.height <= MAX_IMAGE_PIXELS:
        return image_bytes, mime
    im = im.convert("RGB")
    im.thumbnail((long_side, long_side), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=92)
    return buf.getvalue(), "image/jpeg"


# D3 gets its own smaller copy: its cost grows with the image's tokens. At 1024 px it rejected exactly the same
# images as at full size (23/23 demo routes right, same 3 rejects in 100 CORD receipts; only borderline extract/review
# swaps), with 1.3x its throughput and ~35% fewer tokens. 768 px shifted the glare scores across the thresholds.
D3_LONG_SIDE = 1024


def for_d3(image_bytes, mime):
    """A copy at most D3_LONG_SIDE on its long side (JPEG); smaller images pass through unchanged."""
    im = Image.open(io.BytesIO(image_bytes))
    if max(im.size) <= D3_LONG_SIDE:
        return fit_for_router(image_bytes, mime)
    im = im.convert("RGB")
    im.thumbnail((D3_LONG_SIDE, D3_LONG_SIDE), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


def ask_d3(image_bytes, mime="image/jpeg", timeout=(3, 20), with_tokens=False):
    """Returns (raw answers, ms), plus the tokens D3 read with `with_tokens`."""
    image_bytes, mime = for_d3(image_bytes, mime)
    url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
    t = time.perf_counter()
    r = requests.post(ROUTER, json={"model": MODEL, "state": STATE, "images": [url], "questions": questions()},
                      timeout=timeout)
    r.raise_for_status()
    out, ms = r.json(), round((time.perf_counter() - t) * 1000, 1)
    if with_tokens:
        u = out.get("usage") or {}
        return out["answers"], ms, (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
    return out["answers"], ms


def decide(answers):
    """Applies the routing rule. Returns the `d3` and `route` payloads of docs/API.md (without ms/model)."""
    kind = answers["kind"]
    checks, reasons = [], []
    for key, (_statement, label, bad_when, border, bad) in CHECKS.items():
        p = float(answers[key]["noul"])
        if bad_when == "high":
            status = "bad" if p >= bad else "borderline" if p >= border else "ok"
        else:
            status = "bad" if p <= bad else "borderline" if p <= border else "ok"
        checks.append({"key": key, "label": label, "prob": round(p, 4), "bad_when": bad_when,
                       "threshold": bad, "borderline_from": border, "status": status})
        if status != "ok":
            reasons.append({"key": key, "label": label, "prob": round(p, 4), "status": status})
    d3 = {"kind": {"choice": kind["choice"], "probabilities": {k: round(float(v), 4) for k, v in kind["probabilities"].items()}},
          "checks": checks}
    if kind["choice"] not in ACCEPTED_KINDS:
        # not a receipt: the receipt checks do not apply, so the kind is the only reason
        reasons = [{"key": "kind", "label": KIND_LABEL[kind["choice"]],
                    "prob": round(float(kind["probabilities"][kind["choice"]]), 4), "status": "bad"}]
        route = "reject"
    elif any(r["status"] == "bad" for r in reasons):
        route = "reject"
    elif reasons:
        route = "review"
    else:
        route = "extract"
    shown = [r for r in reasons if r["status"] == "bad"] or reasons
    if shown and shown[0]["key"] == "kind":
        shown = shown[:1]
    summary = ", ".join(r["label"] for r in shown) if shown else "Readable receipt"
    return d3, {"route": route, "reasons": reasons, "summary": summary}
