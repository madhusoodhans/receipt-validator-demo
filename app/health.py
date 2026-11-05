"""Model availability for GET /health, the top-bar badge and the pre-flight check of POST /api/check.

A background thread checks the API every HEALTH_INTERVAL seconds (default 60) with free GETs only, never a model call,
so availability costs no quota however many people visit, and needs no API key (both GETs are public): GET /v1/models must list the D3 model, and GET /pt/api/info
(System Two) must list the chat models (a local Router without /pt/api/info: GET /v1/models). One round is at most two
requests. Each result goes to gate.TRACKER, which real calls feed too (its circuit breaker), so the state here is the
tracker's: a model that answers the GETs but fails real calls is marked down by the breaker.
"""
import os
import threading
import time

import requests

import check
import extract
import gate
import message

INTERVAL = float(os.environ.get("HEALTH_INTERVAL", "60"))
PROBE_TIMEOUT = (5, 10)
READY = threading.Event()
CHECKED_AT = [None]
STATUS = {
    "ok": "All systems working",
    "degraded": "Standard retake messages in use: LLM unavailable",
    "down": "Receipt checking unavailable",
}
REFUSED = "Receipt checking is unavailable right now. Please try again in a minute."


def listing(path, pick):
    """(set of model ids, None) from a GET, or (None, why) in a few plain words; the response is only logged."""
    try:
        r = requests.get(f"{check.ROUTER_URL}{path}", timeout=PROBE_TIMEOUT)  # public, no key
    except requests.Timeout:
        return None, "timed out"
    except requests.RequestException:
        return None, "unreachable"
    if r.status_code != 200:
        print(f"health probe {path}: HTTP {r.status_code} {r.text[:300]}", flush=True)
        return None, "not found" if r.status_code == 404 else "down" if r.status_code >= 500 else "error"
    try:
        return pick(r.json()), None
    except Exception:  # noqa: BLE001
        print(f"health probe {path}: unexpected answer {r.text[:300]}", flush=True)
        return None, "error"


def ids(body):
    return {m.get("id") for m in body.get("data") or []} | {m.get("id") for m in body.get("models") or []}


def probes():
    """{model role: (ok, why)} from at most two GETs."""
    models, why = listing("/v1/models", ids)
    out = {"d3": (True, None) if models and check.MODEL in models else (False, why or "not served")}
    chat, cwhy = listing("/pt/api/info", ids)
    if cwhy == "not found":  # a local Router: its /v1/models lists the chat models too
        chat, cwhy = models, why
    for role, model in (("extract", extract.MODEL), ("retake", message.MODEL)):
        out[role] = (True, None) if chat and model in chat else (False, cwhy or "not served")
    return out


def check_one(name, ok, why):
    """One model's probe result into the tracker."""
    was = gate.TRACKER.available(name)
    gate.TRACKER.probed(name, ok, why)
    if was != ok:
        print(f"health: {name} {'up' if ok else f'down ({why})'}", flush=True)


def round_once():
    for name, (ok, why) in probes().items():
        check_one(name, ok, why)
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
    status = "down" if not (up["d3"] and up["extract"]) else "degraded" if not up["retake"] else "ok"
    detail = {m: gate.TRACKER.why(m) or "down" for m, ok in up.items() if not ok}
    return {"ok": status == "ok", "status": status, "message": STATUS[status], **up, "detail": detail,
            "checked_at": CHECKED_AT[0]}


def wait_ready():
    READY.wait(timeout=8)  # only the first request after startup can wait, for the first round


def preflight():
    """None when an upload can start, else the plain refusal: D3 and the reading LLM must be up (messages have a
    fallback)."""
    return None if gate.TRACKER.available("d3") and gate.TRACKER.available("extract") else REFUSED
