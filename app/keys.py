"""The visitor's own vllm-sr API key (entered in the browser; docs/API.md, "API key").

check(): format first (sk-vsr- keys only; sk-pt- keys work for System Two only), then one tiny System One call and one
tiny System Two call on the hosted API, both of which must succeed. Failures map to a code and a plain message:
format, wrong_kind, invalid (401), disabled (403), quota (429 quota_exceeded), rate_limited (other 429), unreachable
(network, timeout, 5xx), error.

Store: the key per visitor, in process memory only, dropped on DELETE /api/key, when a run fails because of it, or when
the visitor is idle for idle_s (or removed). Never written to disk, the database, logs or events; redact() and
RedactingStream keep anything key-shaped out of the log.

Env: VLLM_SR_API_KEY (optional, off by default: a server key used for visitors who have not entered one),
KEY_REQUIRED (auto: a key is required unless the server has its own or ROUTER_URL is on this machine; 1 or 0 forces),
KEY_CHECKS_PER_MIN (10 key checks a minute per visitor and per IP).
"""
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

import check
import gate

ENV = gate.ENV
ENV_KEY = gate.API_KEY
LOCAL = (urlparse(check.ROUTER_URL).hostname or "") in ("localhost", "127.0.0.1", "::1")
REQUIRED = {"1": True, "0": False}.get(ENV("KEY_REQUIRED", ""), not (ENV_KEY or LOCAL))
CHECKS_PER_MIN = int(ENV("KEY_CHECKS_PER_MIN", "10"))
CHECK_TIMEOUT = (5, 20)
KEY_URL = "https://app.vllm-sr.ai/#key"
SHAPE = re.compile(r"^sk-vsr-[A-Za-z0-9_-]{8,200}$")
SECRET = re.compile(r"sk-(?:vsr|pt)-[A-Za-z0-9_-]+")

MESSAGES = {
    "format": "That does not look like a vllm-sr API key. Keys from app.vllm-sr.ai start with sk-vsr-.",
    "wrong_kind": "This demo needs a key from app.vllm-sr.ai: sk-pt keys only work for chat.",
    "invalid": "The vllm-sr API does not accept this key. Check it, or get a new key at app.vllm-sr.ai.",
    "disabled": "This key is disabled or not allowed to use the models this demo needs.",
    "quota": "This key has used its daily quota. The quota resets at 00:00 UTC.",
    "rate_limited": "The vllm-sr API is rate limiting this key. Try again in {s} s.",
    "unreachable": "Cannot reach the vllm-sr API to check the key. Try again in a minute.",
    "error": "The key could not be checked. Try again.",
    "no_key": "Enter your vllm-sr API key to run the demo.",
    "too_many": "Too many key checks. Wait a minute, then try again.",
}
STATUS = {"format": 400, "wrong_kind": 400, "invalid": 401, "disabled": 403, "quota": 429, "rate_limited": 429,
          "too_many": 429, "unreachable": 502, "error": 502}
KEY_CODES = {"invalid", "disabled", "quota"}  # the codes that mean the key itself failed (not the service)


def redact(text):
    """Anything key-shaped in `text` replaced with sk-***."""
    return SECRET.sub("sk-***", text)


class RedactingStream:
    """A text stream (sys.stdout, sys.stderr) that redacts keys from everything written to it."""

    def __init__(self, stream):
        self._s = stream

    def write(self, s):
        return self._s.write(redact(s) if isinstance(s, str) else s)

    def __getattr__(self, name):
        return getattr(self._s, name)


def install_redaction():
    import sys
    if not isinstance(sys.stdout, RedactingStream):
        sys.stdout = RedactingStream(sys.stdout)
    if not isinstance(sys.stderr, RedactingStream):
        sys.stderr = RedactingStream(sys.stderr)


def message(code, retry_after=None):
    return MESSAGES.get(code, MESSAGES["error"]).format(s=f"{retry_after or 1:g}")


def failure(code, retry_after=None):
    """(http status, body) of a failed check."""
    body = {"ok": False, "code": code, "message": message(code, retry_after)}
    if retry_after is not None:
        body["retry_after"] = retry_after
    return STATUS.get(code, 502), body


classify = gate.classify


def shape(key):
    """(key trimmed, None) when the key looks right, else (None, code)."""
    k = (key if isinstance(key, str) else "").strip()
    if k.startswith("sk-pt-"):
        return None, "wrong_kind"
    if not SHAPE.match(k):
        return None, "format"
    return k, None


def _call(url, body, key, session=requests):
    """One tiny call: None when it succeeded, else (code, retry seconds)."""
    try:
        r = session.post(url, json=body, timeout=CHECK_TIMEOUT, headers={"Authorization": f"Bearer {key}"})
    except requests.RequestException as e:
        print(f"key check: {urlparse(url).path} {type(e).__name__}", flush=True)
        return "unreachable", None
    if r.status_code < 400:
        return None
    try:
        parsed = r.json()
    except ValueError:
        parsed = None
    code, retry = classify(r.status_code, parsed, gate.retry_after(r))
    err = parsed.get("error") if isinstance(parsed, dict) else None
    print(f"key check: {urlparse(url).path} HTTP {r.status_code} "
          f"{(err or {}).get('code') if isinstance(err, dict) else ''} -> {code}", flush=True)
    return code, retry


PROBES = (
    (lambda: f"{check.ROUTER_URL}/v1/systemone",
     lambda: {"model": check.MODEL, "state": "ok", "questions": {"ok": {"type": "noul", "instructions": "ok"}}}),
    (lambda: f"{check.ROUTER_URL}/v1/chat/completions",
     lambda: {"model": "vllm-sr/auto", "max_tokens": 1, "messages": [{"role": "user", "content": "Say OK"}]}),
)
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="keycheck")  # at most 4 check calls in flight


def check_key(key):
    """(http status, body) for POST /api/key/check: 200 {"ok": true} when the key works for both systems."""
    k, code = shape(key)
    if code:
        return failure(code)
    results = list(_pool.map(lambda p: _call(p[0](), p[1](), k), PROBES))
    bad = [r for r in results if r]
    if not bad:
        return 200, {"ok": True}
    order = ["invalid", "disabled", "quota", "rate_limited", "unreachable", "error"]
    code, retry = min(bad, key=lambda r: order.index(r[0]) if r[0] in order else len(order))
    return failure(code, retry)


class Store:
    """The visitors' keys, in memory only, with per-minute check limits."""

    def __init__(self, idle_s=3600, max_keys=1000, per_min=CHECKS_PER_MIN, clock=time.time):
        self.idle_s, self.max_keys, self.per_min, self.clock = idle_s, max_keys, per_min, clock
        self.lock = threading.Lock()
        self.keys = {}  # visitor id -> [key, last used]
        self.failed = {}  # visitor id -> {"code", "message"} of the run error that dropped the key
        self.hits = {}  # ("v", id) or ("ip", address) -> deque of check times

    def allow_check(self, visitor_id, ip):
        """True when one more key check is allowed for this visitor and IP (and counts it)."""
        now = self.clock()
        with self.lock:
            qs = [self.hits.setdefault(k, deque()) for k in (("ip", ip), ("v", visitor_id)) if k[1]]
            for q in qs:
                while q and q[0] <= now - 60:
                    q.popleft()
            if any(len(q) >= self.per_min for q in qs):
                return False
            for q in qs:
                q.append(now)
            return True

    def put(self, visitor_id, key):
        with self.lock:
            self.keys[visitor_id] = [key, self.clock()]
            self.failed.pop(visitor_id, None)
            if len(self.keys) > self.max_keys:
                oldest = min(self.keys, key=lambda v: self.keys[v][1])
                del self.keys[oldest]

    def get(self, visitor_id):
        """The visitor's key, or None."""
        with self.lock:
            e = self.keys.get(visitor_id)
            if not e:
                return None
            e[1] = self.clock()
            return e[0]

    def has(self, visitor_id):
        with self.lock:
            return visitor_id in self.keys

    def forget(self, visitor_id):
        with self.lock:
            self.keys.pop(visitor_id, None)
            self.failed.pop(visitor_id, None)
            self.hits.pop(("v", visitor_id), None)

    def fail(self, visitor_id, code):
        """A run failed because of the key: drop it, and remember why for GET /api/key."""
        with self.lock:
            self.keys.pop(visitor_id, None)
            self.failed[visitor_id] = {"code": code, "message": run_message(code)}

    def state(self, visitor_id):
        """GET /api/key."""
        with self.lock:
            return {"set": visitor_id in self.keys, "required": REQUIRED, "failed": self.failed.get(visitor_id)}

    def cleanup(self):
        now = self.clock()
        with self.lock:
            for v in [v for v, (_k, t) in self.keys.items() if t < now - self.idle_s]:
                del self.keys[v]
                self.failed.pop(v, None)
            for k in [k for k, q in self.hits.items() if not q or q[-1] <= now - 60]:
                del self.hits[k]
            if len(self.failed) > self.max_keys:
                self.failed.clear()

    def loop(self, every_s=300):
        while True:
            time.sleep(every_s)
            try:
                self.cleanup()
            except Exception as e:  # noqa: BLE001
                print("key cleanup failed:", repr(e), flush=True)


RUN_MESSAGES = {
    "invalid": "The vllm-sr API refused your API key (not valid or revoked). Enter a working key to go on.",
    "disabled": "Your API key is disabled or not allowed to use the models this demo needs. Enter another key to go on.",
    "quota": "Your API key has used its daily quota, so the run stopped. The quota resets at 00:00 UTC; or enter another key.",
}


def run_message(code):
    return RUN_MESSAGES.get(code, MESSAGES["error"])
