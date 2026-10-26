"""Qwen 4B writes the re-upload message from D3's answers (text only; it never sees the image), through the Router."""
import time

import requests

from check import ROUTER_URL

ROUTER = f"{ROUTER_URL}/v1/chat/completions"
MODEL = "qwen4b"
SYSTEM = ("You write the message a receipt scanning app shows when an upload cannot be used. Write one or two short, "
          "friendly sentences: say what is wrong, then exactly how to retake the photo. Name every problem listed. "
          "If the image is not a receipt, ask for a photo of the paper receipt. "
          "No greeting, no apology, no technical terms, no probabilities.")
ADVICE = {
    "kind": "the image is not a receipt",
    "complete": "part of the receipt is cut off (often the total at the bottom)",
    "blurry": "the text is blurry or out of focus",
    "damaged": "the receipt is crumpled, torn, faded or stained",
    "glare": "glare or a shadow covers part of the text",
    "multiple": "there is more than one receipt in the photo",
}


def describe(d3, route):
    lines = [f"What the image shows: {d3['kind']['choice'].replace('_', ' ')} "
             f"(confidence {d3['kind']['probabilities'][d3['kind']['choice']]:.2f})."]
    lines.append("Problems found:")
    bad = [r for r in route["reasons"] if r["status"] == "bad"]
    if bad and bad[0]["key"] == "kind":
        bad = bad[:1]  # not a receipt: the receipt checks (cut off, blurry, ...) do not apply
    for r in bad:
        what = ADVICE[r["key"]] if r["key"] != "kind" else f"{ADVICE['kind']} ({r['label'].split(',')[0].lower()})"
        lines.append(f"- {what} (confidence {r['prob']:.2f})")
    return "\n".join(lines)


def write_message(d3, route, timeout=(3, 30)):
    """Returns (text, ms, tokens)."""
    body = {"model": MODEL, "temperature": 0, "max_tokens": 120,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": describe(d3, route)}]}
    t = time.perf_counter()
    r = requests.post(ROUTER, json=body, timeout=timeout)
    r.raise_for_status()
    out = r.json()
    return (out["choices"][0]["message"]["content"].strip(), round((time.perf_counter() - t) * 1000, 1),
            (out.get("usage") or {}).get("total_tokens"))
