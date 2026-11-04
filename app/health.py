"""Model availability for GET /health, the top-bar badge and the pre-flight check of POST /api/check.

A background thread probes each model every HEALTH_INTERVAL seconds (default 30), the way the pipeline calls it but
tiny: D3 one question on a 64x64 image (/v1/systemone), Qwen 27B the same image with max_tokens 1, Qwen 4B one word of
text with max_tokens 1 (chat completions; any OpenAI-compatible endpoint). Each result goes to gate.TRACKER, which real
calls feed too (its circuit breaker), so the state here is the tracker's.
"""
import base64
import io
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image

import check
import extract
import gate
import message

INTERVAL = float(os.environ.get("HEALTH_INTERVAL", "30"))
PROBE_TIMEOUT = (3, 10)
READY = threading.Event()
CHECKED_AT = [None]
STATUS = {
    "ok": "All systems working",
    "degraded": "Standard retake messages in use: Qwen 4B unavailable",
    "down": "Receipt checking unavailable",
}
REFUSED = "Receipt checking is unavailable right now. Please try again in a minute."


def tiny_image():
    """A 64x64 PNG with a dark stripe: enough for a model to accept as an image, a few tokens to read."""
    im = Image.new("RGB", (64, 64), (240, 240, 235))
    for x in range(8, 56):
        for y in range(28, 36):
            im.putpixel((x, y), (30, 30, 30))
    buf = io.BytesIO(); im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


IMG = tiny_image()


def probes():
    chat = f"{check.ROUTER_URL}/v1/chat/completions"
    return {
        "d3": (check.ROUTER, {"model": check.MODEL, "state": "Health check.", "images": [IMG],
                              "questions": {"dark": {"type": "noul", "instructions": "The image has a dark stripe."}}}),
        "qwen27b": (chat, {"model": extract.MODEL, "max_tokens": 1, "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": IMG}}, {"type": "text", "text": "Say ok."}]}]}),
        "qwen4b": (chat, {"model": message.MODEL, "max_tokens": 1, "messages": [{"role": "user", "content": "Say ok."}]}),
    }


def probe(name, url, body):
    """(ok, why): why is a few plain words. The response itself is only logged."""
    try:
        r = requests.post(url, json=body, timeout=PROBE_TIMEOUT, headers=gate.HEADERS)
    except requests.Timeout:
        return False, "timed out"
    except requests.RequestException:
        return False, "unreachable"
    if r.status_code == 200:
        return True, None
    text = r.text[:300].lower()
    print(f"health probe {name}: HTTP {r.status_code} {r.text[:300]}", flush=True)
    if name == "qwen27b" and r.status_code == 400 and "image" in text:
        return False, "image input off"  # vLLM started with --limit-mm-per-prompt image=0
    if "model_not_found" in text or r.status_code == 404:
        return False, "not served"
    if r.status_code == 429:
        return True, None  # answering, only busy
    return False, "down" if r.status_code >= 500 else "error"


def check_one(name, url, body):
    """One probe; its result goes to the tracker at once, so a slow probe does not hold back the others."""
    ok, why = probe(name, url, body)
    was = gate.TRACKER.available(name)
    gate.TRACKER.probed(name, ok, why)
    if was != ok:
        print(f"health: {name} {'up' if ok else f'down ({why})'}", flush=True)


def round_once():
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(lambda kv: check_one(kv[0], *kv[1]), probes().items()))
    CHECKED_AT[0] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    READY.set()


def loop():
    """Probes at startup and every INTERVAL seconds."""
    while True:
        try:
            round_once()
        except Exception as e:  # noqa: BLE001
            print("health round failed:", repr(e), flush=True)
            READY.set()
        time.sleep(INTERVAL)


def state():
    """GET /health: per-model state from the tracker, the overall status and its badge text."""
    up = {m: gate.TRACKER.available(m) for m in gate.NAMES}
    status = "down" if not (up["d3"] and up["qwen27b"]) else "degraded" if not up["qwen4b"] else "ok"
    detail = {m: gate.TRACKER.why(m) or "down" for m, ok in up.items() if not ok}
    return {"ok": status == "ok", "status": status, "message": STATUS[status], **up, "detail": detail,
            "checked_at": CHECKED_AT[0]}


def wait_ready():
    READY.wait(timeout=8)  # only the first request after startup can wait, for the first round


def preflight():
    """None when an upload can start, else the plain refusal: D3 and Qwen 27B must be up (Qwen 4B has a fallback)."""
    return None if gate.TRACKER.available("d3") and gate.TRACKER.available("qwen27b") else REFUSED
