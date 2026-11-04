"""Model calls under load: at most LIMIT requests in flight per API kind (D3, chat completions), a short wait queue,
and one retry on 429/503. A call that cannot get a slot, or is refused twice, fails with Busy.

Env: MODEL_CONCURRENCY (12 per kind), MODEL_QUEUE (24 waiting per kind), MODEL_WAIT_S (15).
"""
import os
import threading
import time

import requests

LIMIT = int(os.environ.get("MODEL_CONCURRENCY", "12"))
QUEUE = int(os.environ.get("MODEL_QUEUE", "24"))
WAIT_S = float(os.environ.get("MODEL_WAIT_S", "15"))
RETRY_CAP_S = 5.0
BUSY = "The service is busy, please try again."


class Busy(Exception):
    def __init__(self):
        super().__init__(BUSY)


class Gate:
    def __init__(self, limit=LIMIT, queue=QUEUE, wait_s=WAIT_S):
        self.slots = threading.BoundedSemaphore(limit)
        self.limit, self.queue, self.wait_s = limit, queue, wait_s
        self.lock = threading.Lock()
        self.waiting = self.running = 0

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
STATS = {"retries_429": 0, "busy": 0}  # since start, for GET /api/metrics
_stats_lock = threading.Lock()
local = threading.local()  # local.refused: 429/503 answers seen by this thread's current run (the upload log line)


def _count(key):
    with _stats_lock:
        STATS[key] += 1


def retry_after(r):
    """Seconds to wait before the one retry: the Retry-After header (seconds) capped at RETRY_CAP_S, else 1."""
    try:
        return max(0.0, min(float(r.headers.get("Retry-After", "1")), RETRY_CAP_S))
    except ValueError:
        return 1.0


def post(kind, url, json, timeout, sleep=time.sleep):
    """requests.post through the gate of `kind`. Returns (response, ms of the answered attempt, queue wait left out).
    Raises Busy when no slot frees within WAIT_S, the queue is full, or the model refuses twice with 429/503."""
    for attempt in (0, 1):
        try:
            with GATES[kind]:
                t = time.perf_counter()
                r = requests.post(url, json=json, timeout=timeout)
                ms = round((time.perf_counter() - t) * 1000, 1)
        except Busy:
            _count("busy")
            raise
        if r.status_code not in (429, 503):
            return r, ms
        _count("retries_429")
        local.refused = getattr(local, "refused", 0) + 1
        if attempt == 0:
            sleep(retry_after(r))
    _count("busy")
    raise Busy


def queued():
    """{kind: {"running": n, "waiting": n}} for GET /api/metrics."""
    return {k: {"running": g.running, "waiting": g.waiting} for k, g in GATES.items()}
