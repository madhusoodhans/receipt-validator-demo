"""Qwen 27B reads a receipt image into JSON (schema enforced with response_format), through the Router."""
import base64
import io
import json
import re
import time
import uuid

import requests
from PIL import Image

import check
from check import ROUTER_URL, fit_for_router

ROUTER = f"{ROUTER_URL}/v1/chat/completions"
MODEL = "qwen27b"
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["store_name", "date", "currency", "items", "subtotal", "tax", "service_charge", "discount", "total"],
          "properties": {
              "store_name": {"type": ["string", "null"]},
              "date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
              "currency": {"type": ["string", "null"]},
              "items": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False, "required": ["name", "quantity", "amount"],
                  "properties": {"name": {"type": "string"}, "quantity": {"type": "number"},
                                 "unit_price": {"type": ["number", "null"]}, "amount": {"type": "number"}}}},
              "subtotal": {"type": ["number", "null"]}, "tax": {"type": ["number", "null"]},
              "service_charge": {"type": ["number", "null"]}, "discount": {"type": ["number", "null"]},
              "total": {"type": "number"}}}
PROMPT = ("Read this receipt into JSON. Copy only what is printed: never calculate, guess or fill in a value. "
          "Item names as printed. Amounts are plain numbers in the receipt's currency, keeping cents when printed "
          "(for example 'Rp 45.500' is 45500 and '$4.25' is 4.25). Discount is a positive number. Leave out zero-priced "
          "add-on lines. Subtotal, tax and service charge only if printed as their own lines. Currency only if a symbol "
          "or code is printed (Rp, RM, CHF, $, ...), as printed; otherwise null. Date only if printed and readable, as "
          "YYYY-MM-DD (European receipts write day.month.year); otherwise null. Store name null if blurred out.")
MIN_SIDE = 1000  # small images are upscaled so their text spans more vision patches
MAX_TOKENS = 4096  # a 60-item receipt is about 2,500 output tokens


class TooLong(Exception):
    """The 27B ran out of output tokens (or wrote JSON that does not parse); the message is shown to the user as is."""


def too_long(text, max_tokens):
    n = len(re.findall(r'"name"\s*:', text or ""))  # items written before the cut
    read = f" after reading about {n} item{'s' if n != 1 else ''}" if n else ""
    return TooLong(f"This receipt is too long for the demo: Qwen 27B hit its {max_tokens:,}-token output limit{read}. "
                   "Receipts up to about 60 items are supported.")


def prepare(image_bytes, mime):
    """Upscales images whose shorter side is under MIN_SIDE pixels (LANCZOS); others pass through unchanged."""
    im = Image.open(io.BytesIO(image_bytes))
    short = min(im.size)
    if short >= MIN_SIDE:
        return fit_for_router(image_bytes, mime, long_side=3200)
    f = min(MIN_SIDE / short, (check.MAX_IMAGE_PIXELS / (im.width * im.height)) ** 0.5)  # a long thin strip stays under the Router's limit
    im = im.convert("RGB").resize((round(im.width * f), round(im.height * f)), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=95)
    return buf.getvalue(), "image/jpeg"


def extract(image_bytes, mime="image/jpeg", timeout=(3, 120), upscale=True, cold=True, max_tokens=MAX_TOKENS):
    """Returns (receipt dict, ms, usage).

    cold: a fresh vLLM cache_salt per request, so a repeated image does not reuse cached prefix blocks. Every real
    upload is a new photo, so the cold time is the representative one, and repeats of a demo image time the same.
    """
    if upscale:
        image_bytes, mime = prepare(image_bytes, mime)
    url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
    body = {"model": MODEL, "temperature": 0, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                                      {"type": "text", "text": PROMPT}]}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "receipt", "schema": SCHEMA, "strict": True}}}
    if cold:
        body["cache_salt"] = uuid.uuid4().hex
    t = time.perf_counter()
    r = requests.post(ROUTER, json=body, timeout=timeout)
    r.raise_for_status()
    ms = round((time.perf_counter() - t) * 1000, 1)
    out = r.json()
    choice = out["choices"][0]
    text = choice["message"].get("content") or ""
    if choice.get("finish_reason") == "length":
        raise too_long(text, max_tokens)
    try:
        return json.loads(text), ms, out.get("usage", {})
    except json.JSONDecodeError:
        raise too_long(text, max_tokens) from None
