"""Model calls under load and when a model is down.

Slots: at most LIMIT requests in flight per API kind (D3, chat completions), a short wait queue, and one retry on
429/503. A call that cannot get a slot, or is refused twice, fails with Busy.

Availability: TRACKER keeps one state per model role (d3; extract: the LLM that reads receipts; retake: the LLM that
writes retake messages), fed by the background probes (app/health.py) and
by every real call. BREAKER_FAILS consecutive failures (no connection, timeout, 5xx) mark a model down for BREAKER_S
seconds, and calls to it then fail at once without a request; any success, or a successful probe, marks it up again.
Every failure is a ModelError with a reason (unavailable, timeout, busy, error); the full detail goes to the log only.

429 and Retry-After: a refused call waits out Retry-After (up to RETRY_CAP_S) and tries once more; a longer
Retry-After is not retried. Either way the kind is held for Retry-After: calls of that kind wait it out when it is
short, or fail as busy at once without a request when it is long, so the shared API key is not hammered.

Env: MODEL_CONCURRENCY (12 per kind), MODEL_QUEUE (24 waiting per kind), MODEL_WAIT_S (15); BREAKER_FAILS (3),
BREAKER_S (30); read timeouts D3_TIMEOUT_S (15), MESSAGE_TIMEOUT_S (20), EXTRACT_TIMEOUT_S (30), all under the hosted
API's 35 s cap.

Keys: each call carries the key of the visitor whose run it is (local.key, set by the run's worker thread; see
app/keys.py), else the optional server key VLLM_SR_API_KEY, else none (a local Router). A call refused because of the
key (401, 403, 429 quota_exceeded) raises KeyFailed at once: no retry, and it does not feed the breaker (the model is
fine, the key is not).
"""
import os
import threading
import time

import requests

ENV = os.environ.get
LIMIT = int(ENV("MODEL_CONCURRENCY", "12"))
QUEUE = int(ENV("MODEL_QUEUE", "24"))
WAIT_S = float(ENV("MODEL_WAIT_S", "15"))
RETRY_CAP_S = 5.0
CONNECT_S = 5.0
TIMEOUT_S = {"d3": float(ENV("D3_TIMEOUT_S", "15")), "retake": float(ENV("MESSAGE_TIMEOUT_S", "20")),
             "extract": float(ENV("EXTRACT_TIMEOUT_S", "30"))}
API_KEY = (ENV("VLLM_SR_API_KEY") or "").strip()
HEADERS = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
NAMES = {"d3": "D3", "extract": "The LLM", "retake": "The LLM"}
BUSY = "The service is busy, please try again."


def auth():
    """The Authorization header for this thread's call: the visitor's key (local.key), else the server key, else none."""
    key = getattr(local, "key", None)
    return {"Authorization": f"Bearer {key}"} if key else HEADERS


def classify(status, body, retry_after=1.0):
    """(code, retry seconds or None) of a refused vllm-sr API call with HTTP `status` and parsed JSON `body`; (None,
    None) when not refused. Codes: invalid (401), disabled (403), quota (429 quota_exceeded), rate_limited (other 429),
    unreachable (5xx), error."""
    if status < 400:
        return None, None
    err = body.get("error") if isinstance(body, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    if status == 401:
        return "invalid", None
    if status == 403:
        return "disabled", None
    if status == 429:
        return ("quota", None) if code == "quota_exceeded" else ("rate_limited", retry_after)
    if status >= 500:
        return "unreachable", None
    return "error", None


def key_code(r):
    """invalid, disabled or quota when the response `r` refused the call because of the key, else None."""
    if r.status_code not in (401, 403, 429):
        return None
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = None
    code, _retry = classify(r.status_code, body)
    return code if code in ("invalid", "disabled", "quota") else None


def timeout(model):
    """(connect, read) seconds for a call to `model`."""
    return CONNECT_S, TIMEOUT_S[model]


class ModelError(Exception):
    """A model call that failed. reason: unavailable, timeout, busy or error; str() is one plain line for the UI."""

    def __init__(self, model, reason, seconds=None):
        self.model, self.reason, self.seconds = model, reason, seconds
        super().__init__(self.line())

    def line(self, model=None):
        """The failure in one plain line, naming `model` when the error itself does not know it (a full queue)."""
        who = NAMES.get(self.model or model, "The model")
        if self.reason == "timeout":
            return f"{who} did not answer within {self.seconds or TIMEOUT_S.get(self.model or model, 0):g} s"
        return {"unavailable": f"{who} is unavailable", "busy": f"{who} is busy"}.get(
            self.reason, f"{who} could not handle the request")


class KeyFailed(ModelError):
    """The API refused the call because of the key. code: invalid, disabled or quota."""

    def __init__(self, model, code):
        self.code = code
        super().__init__(model, "key")

    def line(self, model=None):
        who = NAMES.get(self.model or model, "The model")
        why = {"invalid": "the key is not valid", "disabled": "the key is disabled or not allowed",
               "quota": "the key's daily quota is used up"}.get(self.code, "the key was refused")
        return f"{who} call was refused: {why}"


class Busy(ModelError):
    def __init__(self, model=None):
        super().__init__(model, "busy")

    def __str__(self):
        return BUSY


class Tracker:
    """Per-model availability: the last probe, plus a circuit breaker fed by real calls."""

    def __init__(self, models=tuple(NAMES), fails=int(ENV("BREAKER_FAILS", "3")), open_s=float(ENV("BREAKER_S", "30")),
                 clock=time.monotonic):
        self.fails, self.open_s, self.clock = fails, open_s, clock
        self.lock = threading.Lock()
        self.s = {m: {"probe_ok": None, "fails": 0, "open_until": 0.0, "why": None} for m in models}

    def __contains__(self, model):
        return model in self.s

    def ok(self, model):
        with self.lock:
            self.s[model].update(probe_ok=True, fails=0, open_until=0.0, why=None)

    def fail(self, model, why):
        with self.lock:
            s = self.s[model]
            s["fails"] += 1; s["why"] = why
            if s["fails"] >= self.fails:
                if s["open_until"] <= self.clock():
                    print(f"breaker: {model} marked down for {self.open_s:g} s after {s['fails']} failures ({why})",
                          flush=True)
                s["open_until"] = self.clock() + self.open_s

    def probed(self, model, ok, why=None):
        if ok:
            return self.ok(model)
        with self.lock:
            self.s[model].update(probe_ok=False, why=why)

    def available(self, model):
        """False while the last probe failed or the breaker is open. Unknown (no probe yet) counts as available."""
        with self.lock:
            s = self.s[model]
            return s["probe_ok"] is not False and s["open_until"] <= self.clock()

    def why(self, model):
        with self.lock:
            return self.s[model]["why"]

    def reset(self):
        with self.lock:
            for s in self.s.values():
                s.update(probe_ok=None, fails=0, open_until=0.0, why=None)


TRACKER = Tracker()


class Gate:
    def __init__(self, limit=LIMIT, queue=QUEUE, wait_s=WAIT_S):
        self.slots = threading.BoundedSemaphore(limit)
        self.limit, self.queue, self.wait_s = limit, queue, wait_s
        self.lock = threading.Lock()
        self.waiting = self.running = 0
        self.hold_until = 0.0  # time.monotonic() before which the API asked us (Retry-After) not to call

    def hold(self, seconds):
        with self.lock:
            self.hold_until = max(self.hold_until, time.monotonic() + seconds)

    def held_for(self):
        with self.lock:
            return max(0.0, self.hold_until - time.monotonic())

    def __enter__(self):
        with self.lock:
            if self.waiting >= self.queue:
                raise Busy
            self.waiting += 1
        try:
            if not self.slots.acquire(timeout=self.wait_s):
                raise Busy
        finally:
            with self.lock:
                self.waiting -= 1
        with self.lock:
            self.running += 1
        return self

    def __exit__(self, *exc):
        with self.lock:
            self.running -= 1
        self.slots.release()


GATES = {"d3": Gate(), "chat": Gate()}
STATS = {"retries_429": 0, "busy": 0, "failed": 0, "skipped_down": 0}  # since start, for GET /api/metrics
_stats_lock = threading.Lock()
local = threading.local()  # local.refused: 429/503 answers seen by this thread's current run (the upload log line)


def _count(key):
    with _stats_lock:
        STATS[key] += 1


def retry_after(r):
    """The Retry-After header in seconds (1 when missing or not a number)."""
    try:
        return max(0.0, float(r.headers.get("Retry-After", "1")))
    except ValueError:
        return 1.0


def _failed(model, reason, detail, breaker=True, seconds=None):
    """Logs the full detail, feeds the breaker, and returns the plain ModelError to raise."""
    _count("failed")
    print(f"model call failed: {model or '?'} {reason}: {detail}", flush=True)
    if breaker and model in TRACKER:
        TRACKER.fail(model, reason)
    return ModelError(model, reason, seconds)


def post(kind, url, json, timeout, sleep=time.sleep, model=None, headers=None):
    """requests.post through the gate of `kind`, for `model` (d3, extract, retake; None: not tracked), with `headers`
    added to the key. Returns (2xx response, ms of the answered attempt, queue wait left out). Raises ModelError: at
    once when the model is marked down; Busy when no slot frees within WAIT_S, the queue is full, the API asked for a
    pause longer than RETRY_CAP_S (Retry-After), or the model refuses twice with 429/503."""
    if model in TRACKER and not TRACKER.available(model):
        _count("skipped_down")
        raise ModelError(model, "unavailable")
    gate = GATES[kind]
    for attempt in (0, 1):
        held = gate.held_for()
        if held > RETRY_CAP_S:  # a long Retry-After from an earlier call: no request until it has passed
            _count("busy")
            raise Busy(model)
        if held:
            sleep(held)
        try:
            with gate:
                t = time.perf_counter()
                try:
                    r = requests.post(url, json=json, timeout=timeout, headers={**auth(), **(headers or {})})
                except requests.Timeout as e:
                    raise _failed(model, "timeout", repr(e), seconds=timeout[1] if isinstance(timeout, tuple) else timeout) from None
                except requests.RequestException as e:
                    raise _failed(model, "unavailable", repr(e)) from None
                ms = round((time.perf_counter() - t) * 1000, 1)
        except Busy:
            _count("busy")
            raise
        code = key_code(r)
        if code:
            _count("failed")
            print(f"model call refused: {model or '?'} HTTP {r.status_code}: key {code}", flush=True)
            raise KeyFailed(model, code)
        if r.status_code not in (429, 503):
            break
        _count("retries_429")
        local.refused = getattr(local, "refused", 0) + 1
        wait = retry_after(r)
        gate.hold(wait)
        if attempt == 0 and wait <= RETRY_CAP_S:
            continue  # the hold above makes the retry wait out Retry-After
        break
    if r.status_code in (429, 503):
        _count("busy")
        if r.status_code == 503 and attempt == 1 and model in TRACKER:  # a 503 twice is as likely down as busy: it feeds the breaker
            TRACKER.fail(model, "busy")
        print(f"model call refused: {model or '?'} HTTP {r.status_code} (Retry-After {retry_after(r):g} s)", flush=True)
        raise Busy(model)
    if r.status_code >= 500:
        raise _failed(model, "unavailable", f"HTTP {r.status_code}: {getattr(r, 'text', '')[:500]}")
    if r.status_code >= 400:  # our request was refused (a bad image, an unknown model name, a bad key): not an outage
        raise _failed(model, "error", f"HTTP {r.status_code}: {getattr(r, 'text', '')[:500]}", breaker=False)
    if model in TRACKER:
        TRACKER.ok(model)
    return r, ms


def queued():
    """{kind: {"running": n, "waiting": n}} for GET /api/metrics."""
    return {k: {"running": g.running, "waiting": g.waiting} for k, g in GATES.items()}
