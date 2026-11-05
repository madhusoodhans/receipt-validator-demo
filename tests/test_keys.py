"""The visitor's API key (app/keys.py, docs/API.md "API key"): format checks, the error codes of the hosted API mapped
to plain messages, key errors in a run (no fallback, the key dropped), no_key gating, CORS for POST /api/key/check,
and the key never reaching the log, the database or an event.

A mock of the hosted API (bench/stub_models.py behind a key check) runs in this process; the unit tests call app/keys.py
and app/gate.py directly, the rest start the real server as a subprocess with its log in a file, then grep that log
and the database for the keys. Only fake keys (see fake()) are used.

  .venv/bin/python tests/test_keys.py
"""
import glob
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import ThreadingHTTPServer

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TMP = tempfile.mkdtemp(prefix="receipt-keys-")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


MOCK_PORT = free_port()
MOCK = f"http://127.0.0.1:{MOCK_PORT}"
os.environ.update(ROUTER_URL=MOCK, KEY_REQUIRED="1")
os.environ.pop("VLLM_SR_API_KEY", None)
sys.path[:0] = [os.path.join(ROOT, "app"), os.path.join(ROOT, "bench")]
import stub_models  # noqa: E402
import gate  # noqa: E402
import keys  # noqa: E402

stub_models.CONF["speed"] = 0.05
PREFIX = "sk-vsr" + "-"  # written apart so no key-shaped literal is in the repo
FAKE = PREFIX + "test-"


def fake(word):
    """An obviously fake key; the word picks how the mock API answers."""
    return f"{FAKE}fake-{word}"


GOOD = fake("good0001")
SEEN = {"auth": [], "posts": 0, "gets_with_auth": 0}
REVOKE = {}  # key -> {endpoint name: (status, error code)}: answers that change after the key check passed


def err(status, code, msg="refused"):
    return status, {"error": {"type": "error", "code": code, "message": msg}}


class Hosted(stub_models.Handler):
    """The stub models behind the hosted API's key check. Keys are fake; the word after the fake prefix picks a failure."""

    def do_GET(self):
        if self.headers.get("Authorization"):
            SEEN["gets_with_auth"] += 1
        return super().do_GET()

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        SEEN["posts"] += 1
        key = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
        SEEN["auth"].append(key)
        name = "d3" if self.path == "/v1/systemone" else "chat"
        if name == "chat":
            content = (json.loads(raw or b"{}").get("messages") or [{}])[-1].get("content")
            name = "extract" if isinstance(content, list) else "retake"
        refusal = None
        if not key:
            refusal = err(401, "missing_api_key")
        elif "-bad" in key or not key.startswith(FAKE):
            refusal = err(401, "invalid_api_key", f"unknown or revoked API key {key}")
        elif "-disabled" in key:
            refusal = err(403, "key_disabled")
        elif "-nosystemone" in key and name == "d3":
            refusal = err(403, "access_denied")
        elif "-quota" in key:
            refusal = err(429, "quota_exceeded")
        elif "-rate" in key:
            refusal = err(429, "rate_limit_exceeded")
        elif "-down" in key:
            refusal = err(503, "unavailable")
        elif "-slow" in key:
            time.sleep(1.5)
        elif "-echo" in key and name == "extract":  # a body that repeats the key: the log must not
            refusal = err(400, "bad_request", f"bad image for key {key}")
        elif name in REVOKE.get(key, {}):
            refusal = err(*REVOKE[key][name])
        if refusal:
            return self.send(refusal[0], refusal[1], {"Retry-After": "7"} if refusal[0] == 429 else None)
        self.rfile = io.BytesIO(raw)
        self.headers.replace_header("Content-Length", str(len(raw)))
        return super().do_POST()


mock = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), Hosted)
threading.Thread(target=mock.serve_forever, daemon=True).start()


# -- unit tests ------------------------------------------------------------------------------------------------------
def test_format():
    ok = lambda k: keys.shape(k)  # noqa: E731
    assert ok(f"  {GOOD}\n") == (GOOD, None), "trimmed"
    assert ok("sk-pt-test-fake-0001")[1] == "wrong_kind"
    for bad in ["", None, 42, PREFIX, PREFIX + "short", FAKE + "fake 0001", "Bearer " + GOOD, PREFIX + "a" * 300,
                FAKE + "fake/0001", "vsr-test-fake-0001"]:
        assert ok(bad)[1] == "format", bad
    calls = SEEN["posts"]
    s, b = keys.check_key("sk-pt-test-fake-0001")
    assert (s, b["code"]) == (400, "wrong_kind") and "sk-pt keys only work for chat" in b["message"]
    s, b = keys.check_key("not a key")
    assert (s, b["code"]) == (400, "format")
    assert SEEN["posts"] == calls, "a bad format never reaches the API"
    print("format checks ok")


def test_check_codes():
    old = keys.CHECK_TIMEOUT
    keys.CHECK_TIMEOUT = (1, 0.5)
    try:
        cases = {GOOD: (200, None), fake("bad00001"): (401, "invalid"),
                 fake("disabled1"): (403, "disabled"), fake("nosystemone"): (403, "disabled"),
                 fake("quota0001"): (429, "quota"), fake("rate00001"): (429, "rate_limited"),
                 fake("slow00001"): (502, "unreachable"), fake("down00001"): (502, "unreachable")}
        for k, (status, code) in cases.items():
            n = SEEN["posts"]
            s, b = keys.check_key(k)
            assert s == status and b.get("code") == code, (k, s, b)
            assert SEEN["posts"] - n == 2, "one System One and one System Two call per check"
            assert k not in json.dumps(b), "the key is never echoed"
            if code:
                assert b["message"] and not b["ok"]
        assert "00:00 UTC" in keys.check_key(fake("quota0001"))[1]["message"]
        b = keys.check_key(fake("rate00001"))[1]
        assert b["retry_after"] == 7 and "7 s" in b["message"]
    finally:
        keys.CHECK_TIMEOUT = old
    # unreachable API: nothing listens on the port
    old_url = keys.check.ROUTER_URL
    keys.check.ROUTER_URL = f"http://127.0.0.1:{free_port()}"
    try:
        assert keys.check_key(GOOD)[1]["code"] == "unreachable"
    finally:
        keys.check.ROUTER_URL = old_url
    print("check error codes ok")


def test_gate_mapping():
    """Model calls in a run: key errors raise KeyFailed at once (no retry, breaker untouched)."""
    url = f"{MOCK}/v1/chat/completions"
    body = {"model": "vllm-sr/auto", "messages": [{"role": "user", "content": "hi"}]}
    for k, code in [(fake("bad00001"), "invalid"), (fake("disabled1"), "disabled"),
                    (fake("quota0001"), "quota")]:
        gate.local.key = k
        n = SEEN["posts"]
        try:
            gate.post("chat", url, body, (2, 5), model="retake")
            raise AssertionError("no error")
        except gate.KeyFailed as e:
            assert e.code == code and e.reason == "key"
        assert SEEN["posts"] - n == 1, "a key error is not retried"
        assert gate.TRACKER.available("retake"), "a key error does not mark the model down"
    gate.local.key = fake("rate00001")  # an ordinary 429: busy, as before
    try:
        gate.post("chat", url, body, (2, 5), model="retake", sleep=lambda s: None)
        raise AssertionError("no error")
    except gate.KeyFailed:
        raise AssertionError("a rate limit is not a key error")
    except gate.Busy:
        pass
    gate.GATES["chat"].hold_until = 0.0
    gate.local.key = GOOD
    r, _ms = gate.post("chat", url, body, (2, 5), model="retake")
    assert r.status_code == 200 and SEEN["auth"][-1] == GOOD, "the visitor's key is sent"
    gate.local.key = None
    print("gate key errors ok")


def test_redaction_and_store():
    buf = io.StringIO()
    s = keys.RedactingStream(buf)
    s.write(f"x {GOOD} y sk-pt-abc_DEF-123 z\n")
    assert buf.getvalue() == "x sk-*** y sk-*** z\n"
    t = [1000.0]
    st = keys.Store(idle_s=60, per_min=3, clock=lambda: t[0])
    assert all(st.allow_check("v1", "1.2.3.4") for _ in range(3)) and not st.allow_check("v1", "1.2.3.4")
    assert not st.allow_check("v2", "1.2.3.4"), "per IP too"
    t[0] += 61
    assert st.allow_check("v1", "1.2.3.4")
    st.put("v1", GOOD)
    assert st.state("v1") == {"set": True, "required": True, "failed": None}
    st.fail("v1", "quota")
    assert st.get("v1") is None and st.state("v1")["failed"]["code"] == "quota"
    st.put("v1", GOOD); t[0] += 120; st.cleanup()
    assert st.get("v1") is None, "idle keys are dropped"
    print("redaction and store ok")


# -- the real server -------------------------------------------------------------------------------------------------
PORT = free_port()
URL = f"http://127.0.0.1:{PORT}"
LOG = os.path.join(TMP, "server.log")
DB = os.path.join(TMP, "t.db")
EVENTS = []  # every SSE text and JSON body seen, for the secrecy check


def start_server():
    env = {k: v for k, v in os.environ.items() if k != "VLLM_SR_API_KEY"}
    env.update(PORT=str(PORT), HOST="127.0.0.1", ROUTER_URL=MOCK, KEY_REQUIRED="1", DB=DB, PLAY_ALL="0",
               UPLOAD_ROOT=os.path.join(TMP, "up"), LANDING_URL="http://landing.test:9000", KEY_CHECKS_PER_MIN="40",
               UPLOADS_PER_MIN="100", UPLOADS_PER_MIN_IP="100")
    proc = subprocess.Popen([sys.executable, "-I", os.path.join(ROOT, "app/server.py")], env=env,
                            stdout=open(LOG, "w"), stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            if requests.get(f"{URL}/healthz", timeout=1).ok:
                return proc
        except requests.RequestException:
            time.sleep(0.1)
    raise RuntimeError("server did not start: " + open(LOG).read())


def h(v, **extra):
    return {"X-Visitor-Id": v, **extra}


def upload(v, file):
    r = requests.post(f"{URL}/api/check", files={"image": (file, open(os.path.join(ROOT, "data/demo", file), "rb").read(),
                                                           "image/jpeg")}, headers=h(v))
    EVENTS.append(r.text)
    if r.status_code != 200:
        return r.status_code, r.json()
    ev, name = {}, None
    for line in r.text.splitlines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            ev[name] = json.loads(line[5:])
    return 200, ev


def set_key(v, key):
    r = requests.post(f"{URL}/api/key", json={"key": key}, headers=h(v))
    EVENTS.append(r.text)
    return r.status_code, r.json()


GOOD_IMG, BAD_IMG = "01_good_berghotel.jpg", None


def test_server():
    global BAD_IMG
    labels = json.load(open(os.path.join(ROOT, "data/demo/labels.json")))
    BAD_IMG = next(l["file"] for l in labels if l["expected_route"] == "reject")
    cfg = requests.get(f"{URL}/api/config").json()
    assert cfg["key_required"] is True and cfg["key_url"] == "https://app.vllm-sr.ai/#key"

    # health needs no key, and says the caller has none
    v = str(uuid.uuid4())
    hl = requests.get(f"{URL}/health", headers=h(v)).json()
    assert hl["status"] == "ok" and hl["key"] == "missing", hl
    assert SEEN["gets_with_auth"] == 0, "availability checks send no key"

    # no_key: nothing runs
    n = SEEN["posts"]
    s, b = upload(v, GOOD_IMG)
    assert s == 401 and b["code"] == "no_key" and b["message"], (s, b)
    assert SEEN["posts"] == n, "no model call without a key"
    assert requests.get(f"{URL}/api/key", headers=h(v)).json() == {"set": False, "required": True, "failed": None}
    print("no_key gating ok")

    # CORS for /api/key/check only
    for origin, allowed in [("http://localhost:8630", True), ("http://127.0.0.1:8630", True),
                            ("http://landing.test:9000", True), ("http://evil.test", False)]:
        r = requests.options(f"{URL}/api/key/check", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                                               "Access-Control-Request-Headers": "content-type"})
        assert r.status_code == 204
        assert (r.headers.get("Access-Control-Allow-Origin") == origin) == allowed, (origin, r.headers)
        if allowed:
            assert "POST" in r.headers["Access-Control-Allow-Methods"]
            assert "content-type" in r.headers["Access-Control-Allow-Headers"].lower()
    r = requests.post(f"{URL}/api/key/check", json={"key": GOOD}, headers={"Origin": "http://localhost:8630"})
    EVENTS.append(r.text)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert r.headers.get("Access-Control-Allow-Origin") == "http://localhost:8630"
    r = requests.post(f"{URL}/api/key/check", json={"key": GOOD}, headers={"Origin": "http://evil.test"})
    assert "Access-Control-Allow-Origin" not in r.headers
    assert requests.options(f"{URL}/api/key", headers={"Origin": "http://localhost:8630"}).status_code == 404
    assert requests.post(f"{URL}/api/check", headers={"Origin": "http://localhost:8630", **h(v)},
                         json={"demo": GOOD_IMG}).headers.get("Access-Control-Allow-Origin") is None
    print("CORS ok")

    # validation codes through the endpoints; nothing stored on failure
    for key, status, code in [("nope", 400, "format"), ("sk-pt-test-fake-0001", 400, "wrong_kind"),
                              (fake("bad00001"), 401, "invalid"), (fake("quota0001"), 429, "quota")]:
        s, b = set_key(v, key)
        assert (s, b["code"]) == (status, code), (key, s, b)
        assert requests.get(f"{URL}/api/key", headers=h(v)).json()["set"] is False
        r = requests.post(f"{URL}/api/key/check", json={"key": key})
        EVENTS.append(r.text)
        assert (r.status_code, r.json()["code"]) == (status, code)
    assert requests.post(f"{URL}/api/key", json={"key": GOOD}).status_code == 400, "POST /api/key needs a visitor"

    # a good key: stored for this visitor only, never returned
    s, b = set_key(v, GOOD)
    assert s == 200 and b == {"ok": True, "set": True}
    r = requests.get(f"{URL}/api/key", headers=h(v)); EVENTS.append(r.text)
    assert r.json() == {"set": True, "required": True, "failed": None}
    assert requests.get(f"{URL}/health", headers=h(v)).json()["key"] == "set"
    assert requests.get(f"{URL}/api/key", headers=h(str(uuid.uuid4()))).json()["set"] is False, "per visitor"

    s, ev = upload(v, GOOD_IMG)
    assert s == 200 and ev["stored"]["table"] == "receipts" and "error" not in ev, ev
    s, ev = upload(v, BAD_IMG)
    assert s == 200 and ev["message"]["model"] != "template" and ev["stored"]["table"] == "rejections", ev
    assert SEEN["auth"][-1] == GOOD, "the run used the visitor's key"
    print("runs with the visitor's key ok")

    # the key revoked mid-run (reading): a key error, the key dropped, the next upload refused
    REVOKE[GOOD] = {"extract": (401, "invalid_api_key")}
    s, ev = upload(v, GOOD_IMG)
    e = ev["error"]
    assert e["step"] == "extract" and e["reason"] == "key" and e["code"] == "invalid" and "stored" not in ev, ev
    assert "API key" in e["message"]
    st = requests.get(f"{URL}/api/key", headers=h(v)).json()
    assert st["set"] is False and st["failed"]["code"] == "invalid", st
    assert upload(v, GOOD_IMG)[1]["code"] == "no_key"
    print("key failure mid-run (read) ok")

    # quota used up during the retake message: no standard message in its place
    v2, k2 = str(uuid.uuid4()), fake("good0002")
    assert set_key(v2, k2)[0] == 200
    REVOKE[k2] = {"retake": (429, "quota_exceeded")}
    s, ev = upload(v2, BAD_IMG)
    e = ev.get("error") or {}
    assert e.get("step") == "message" and e.get("code") == "quota" and "message" not in ev and "stored" not in ev, ev
    assert "00:00 UTC" in e["message"]
    # and a 403 at D3
    v3, k3 = str(uuid.uuid4()), fake("good0003")
    assert set_key(v3, k3)[0] == 200
    REVOKE[k3] = {"d3": (403, "key_disabled")}
    s, ev = upload(v3, GOOD_IMG)
    assert ev["error"]["step"] == "d3" and ev["error"]["code"] == "disabled", ev
    hl = requests.get(f"{URL}/health").json()
    assert hl["d3"] and hl["extract"] and hl["retake"], "key errors do not mark models down"
    print("key failure mid-run (message, d3) ok")

    # an API answer that repeats the key goes to the log redacted
    v4, k4 = str(uuid.uuid4()), fake("echo0004")
    assert set_key(v4, k4)[0] == 200
    s, ev = upload(v4, GOOD_IMG)
    assert ev["error"]["reason"] == "error", ev

    # DELETE forgets it
    v5 = str(uuid.uuid4())
    assert set_key(v5, fake("good0005"))[0] == 200
    assert requests.delete(f"{URL}/api/key", headers=h(v5)).json()["set"] is False
    assert requests.get(f"{URL}/api/key", headers=h(v5)).json()["set"] is False
    print("delete ok")

    # the check limit per visitor / IP
    hits = [requests.post(f"{URL}/api/key/check", json={"key": "nope"}).status_code for _ in range(45)]
    assert 429 in hits and hits.index(429) <= 40
    r = requests.post(f"{URL}/api/key/check", json={"key": "nope"})
    assert r.json()["code"] == "too_many"
    print("check rate limit ok")


def test_secrecy(proc):
    proc.terminate(); proc.wait(5)
    used = [GOOD] + [fake(w) for w in
                     ("bad00001", "quota0001", "good0002", "good0003", "echo0004", "good0005")] + ["sk-pt-test-fake-0001"]
    log = open(LOG).read()
    assert "bad image for key" in log and "sk-***" in log, "the echoing answer was logged, redacted"
    blobs = {"log": log, "events": "\n".join(EVENTS)}
    for p in glob.glob(DB + "*"):
        blobs[os.path.basename(p)] = open(p, "rb").read().decode("latin-1")
    for p in glob.glob(os.path.join(TMP, "up", "**", "*"), recursive=True):
        if os.path.isfile(p):
            blobs[p] = open(p, "rb").read().decode("latin-1")
    for name, text in blobs.items():
        for k in used:
            assert k not in text, f"a key in {name}"
            assert k[12:] not in text, f"part of a key in {name}"
        assert not re.search(r"sk-(vsr|pt)-[A-Za-z0-9]", text), f"something key-shaped in {name}"
    print(f"secrecy ok ({len(log.splitlines())} log lines, {len(blobs)} files and streams checked)")


if __name__ == "__main__":
    test_format()
    test_check_codes()
    test_gate_mapping()
    test_redaction_and_store()
    proc = start_server()
    try:
        test_server()
    finally:
        test_secrecy(proc)
    print("all key tests passed")
