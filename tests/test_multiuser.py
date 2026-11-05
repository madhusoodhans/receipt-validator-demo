"""Many visitors at once: isolation, scoped reset, cleanup, caps, rate limits, the model-call gate and 429 retry; and
models that are down, slow or failing: pre-flight refusal, the circuit breaker, the template fallback for retake
messages, a failed read mid-run, Retry-After, the client IP behind a proxy, the missing-key check, and no raw error text
in any event or response.
Runs the real server in-process against bench/stub_models.py (no GPU, no Router), on free ports, in a temp dir.

  .venv/bin/python tests/test_multiuser.py
"""
import io
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from http.server import ThreadingHTTPServer

import requests
from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TMP = tempfile.mkdtemp(prefix="receipt-test-")


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


# The stub must be up before app modules read ROUTER_URL; it listens on a free port, so pick one first.
_probe = ThreadingHTTPServer(("127.0.0.1", 0), None); STUB_PORT = _probe.server_address[1]; _probe.server_close()
os.environ.update(ROUTER_URL=f"http://127.0.0.1:{STUB_PORT}", DB=os.path.join(TMP, "t.db"),
                  UPLOAD_ROOT=os.path.join(TMP, "up"), UPLOADS_PER_MIN="1000", UPLOADS_PER_MIN_IP="1000",
                  PLAY_ALL="0", EXTRACT_MODEL="stub-extract", MESSAGE_MODEL="stub-message")
os.environ.pop("VLLM_SR_API_KEY", None)  # never sent to the stub
sys.path[:0] = [os.path.join(ROOT, "app"), os.path.join(ROOT, "bench")]
import stub_models  # noqa: E402
stub = ThreadingHTTPServer(("127.0.0.1", STUB_PORT), stub_models.Handler)
threading.Thread(target=stub.serve_forever, daemon=True).start()
stub_models.CONF["speed"] = 0.05
import server  # noqa: E402  (chdirs to the repo root and opens the temp database)
import db  # noqa: E402
import gate  # noqa: E402
import health  # noqa: E402
import message  # noqa: E402
import pipeline  # noqa: E402
import visitors  # noqa: E402

URL = serve(server.Handler)
DEMO = os.path.join(ROOT, "data/demo")


def vid():
    return str(uuid.uuid4())


def h(v, **extra):
    return {"X-Visitor-Id": v, **extra}


def upload(v, file="01_good_berghotel.jpg", raw=None, headers=None):
    """POST /api/check with a file upload; returns (status, {event: data})."""
    raw = raw or open(os.path.join(DEMO, file), "rb").read()
    r = requests.post(f"{URL}/api/check", files={"image": (file, raw, "image/jpeg")}, headers={**h(v), **(headers or {})})
    if r.status_code != 200:
        return r.status_code, r.json()
    ev, name = {}, None
    for line in r.text.splitlines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            ev[name] = json.loads(line[5:])
    return 200, ev


def reencode(file):
    """A demo photo as a new upload (other bytes, so no shared 27B baseline)."""
    buf = io.BytesIO(); Image.open(os.path.join(DEMO, file)).save(buf, "JPEG", quality=88)
    return buf.getvalue()


def get(path, v=None):
    return requests.get(f"{URL}{path}", headers=h(v) if v else {})


def test_isolation():
    a, b = vid(), vid()
    code, ev = upload(a)
    assert code == 200 and "stored" in ev, ev
    url = ev["received"]["image_url"]
    assert get(url, a).status_code == 200
    assert get(url, b).status_code == 404, "B must not fetch A's upload"
    assert get(url).status_code == 404, "no visitor id, no upload"
    code, ev2 = upload(a, "02_bad_blurry_hand.jpg", reencode("02_bad_blurry_hand.jpg"))
    assert ev2["route"]["route"] == "reject"
    assert len(get("/api/receipts", a).json()) == 1 and len(get("/api/rejections", a).json()) == 1
    assert get("/api/receipts", b).json() == [] and get("/api/rejections", b).json() == []
    sa, sb = get("/api/stats", a).json(), get("/api/stats", b).json()
    assert sa["uploads"] == 2 and sa["rejected"] == 1 and sb["uploads"] == 0, (sa, sb)
    assert "comparison" not in sa and "comparison" not in ev2["done"] and "gpu_price" not in sa, "no all-27B comparison"
    # unknown or malformed visitor ids see nothing, and cannot upload
    assert get("/api/receipts", vid()).json() == []
    assert get("/api/receipts", "not-a-uuid").json() == [] and get("/api/stats", "x").json()["uploads"] == 0
    r = requests.post(f"{URL}/api/check", files={"image": ("a.jpg", b"x")}, headers={"X-Visitor-Id": "../etc"})
    assert r.status_code == 400
    # ids that point outside the visitor's folder are not found
    assert get("/api/uploads/..%2F..%2Fetc%2Fpasswd", a).status_code == 404
    print("isolation ok")
    return a, b


def test_demo_runs_share_images():
    a = vid()
    r = requests.post(f"{URL}/api/check", json={"demo": "04_bad_crumpled.jpg"}, headers=h(a))
    assert r.status_code == 200 and "event: stored" in r.text
    row = get("/api/rejections", a).json()[0]
    assert row["image_url"] == "/api/demo/04_bad_crumpled.jpg", row
    assert not os.path.isdir(server.VIS.folder(a)), "demo runs store no file"
    assert "event: done" in r.text
    print("demo runs ok")


def test_reset_scoped(a, b):
    upload(b, "08_good_cord001.jpg")
    files_a = os.listdir(server.VIS.folder(a))
    assert requests.post(f"{URL}/api/reset", headers=h(b)).status_code == 200
    assert get("/api/receipts", b).json() == [] and not os.path.isdir(server.VIS.folder(b))
    assert len(get("/api/receipts", a).json()) == 1 and os.listdir(server.VIS.folder(a)) == files_a, "B's reset left A alone"
    assert requests.post(f"{URL}/api/reset").status_code == 400, "reset needs a visitor id"
    requests.post(f"{URL}/api/reset", headers=h(a))
    assert get("/api/stats", a).json()["uploads"] == 0 and not os.path.isdir(server.VIS.folder(a))
    print("reset ok")


def test_limits_http():
    """Refused before any model call."""
    server.VIS.per_visitor_min = 2
    a = vid()
    try:
        upload(a); upload(a)
        calls = sum(s["calls"] for s in stub_models.STATS.values())
        code, body = upload(a)
        assert code == 429 and "a minute" in body["error"], body
        assert sum(s["calls"] for s in stub_models.STATS.values()) == calls, "no model call on a refused upload"
    finally:
        server.VIS.per_visitor_min = 1000
    server.VIS.per_ip_min, server.TRUST_PROXY = 3, True  # behind a proxy (TRUST_PROXY=1): the client IP from the header
    try:
        ip = "203.0.113.9"
        for _ in range(3):
            assert upload(vid(), headers={"X-Forwarded-For": f"{ip}, 10.0.0.1"})[0] == 200
        code, body = upload(vid(), headers={"X-Forwarded-For": ip})
        assert code == 429 and "network" in body["error"], body
        assert upload(vid(), headers={"X-Forwarded-For": "198.51.100.1"})[0] == 200, "another IP is fine"
    finally:
        server.VIS.per_ip_min, server.TRUST_PROXY = 1000, False
    # 15 MB checked before the body is read: a huge Content-Length is refused at once
    import socket
    s = socket.create_connection(("127.0.0.1", int(URL.rsplit(":", 1)[1])))
    s.sendall(f"POST /api/check HTTP/1.1\r\nHost: x\r\nX-Visitor-Id: {vid()}\r\nContent-Type: multipart/form-data; "
              f"boundary=b\r\nContent-Length: {40 * 1024 * 1024}\r\n\r\n".encode())
    s.settimeout(3)
    assert b" 413 " in s.recv(4096).split(b"\r\n")[0]
    s.close()
    print("limits (http) ok")


def test_one_run_per_visitor():
    a = vid()
    stub_models.CONF["speed"] = 0.5  # the 27B takes ~1.7 s
    try:
        t = threading.Thread(target=upload, args=(a,)); t.start(); time.sleep(0.4)
        code, body = upload(a, "02_bad_blurry_hand.jpg")
        assert code == 409, (code, body)
        assert upload(vid(), "02_bad_blurry_hand.jpg")[0] == 200, "another visitor runs meanwhile"
        t.join()
    finally:
        stub_models.CONF["speed"] = 0.05
    print("one run per visitor ok")


def fresh(**kw):
    d = tempfile.mkdtemp(dir=TMP)
    con = db.connect(os.path.join(d, "x.db"))
    clock = [1_000_000.0]
    vs = visitors.Visitors(con, os.path.join(d, "up"), clock=lambda: clock[0], **kw)
    return vs, con, clock


def store(vs, con, v, n=1, size=1000):
    for _ in range(n):
        assert vs.admit(v, "1.2.3.4") is None
        uid = vs.save(v, os.urandom(size), ".jpg")
        db.add_rejection(con, uid, "x.jpg", [], "msg", {}, 1.0, "sha", v)
        vs.done(v)
        time.sleep(0.01)  # distinct file mtimes


def test_cleanup_and_caps():
    vs, con, clock = fresh(max_uploads=3, per_visitor_min=100)
    a, b = vid(), vid()
    store(vs, con, a, 5)
    assert len(os.listdir(vs.folder(a))) == 3 and len(db.recent_rejections(con, a)) == 3, "20-per-visitor cap (3 here)"
    paths = {r["image_url"].rsplit("/", 1)[1] for r in db.recent_rejections(con, a)}
    assert all(vs.path(a, p) for p in paths), "kept rows keep their files"
    store(vs, con, b, 1)
    clock[0] += 30 * 60; vs.touch(b)
    clock[0] += 31 * 60
    assert vs.cleanup() == 1
    assert db.recent_rejections(con, a) == [] and not os.path.isdir(vs.folder(a)), "idle visitor removed"
    assert len(db.recent_rejections(con, b)) == 1, "active visitor kept"
    # demo-run rows count toward max_rows only, not max_uploads
    vs, con, clock = fresh(max_uploads=2, max_rows=4, per_visitor_min=100)
    for _ in range(6):
        vs.admit(a, "ip"); db.add_rejection(con, f"{db.DEMO_PREFIX}1", "d.jpg", [], "m", {}, 1.0, "s", a); vs.done(a)
    assert len(db.recent_rejections(con, a)) == 4
    # disk cap: the least recently active visitor goes first
    vs, con, clock = fresh(max_bytes=2500, per_visitor_min=100)
    c = vid()
    store(vs, con, a, 1); clock[0] += 1; store(vs, con, b, 1); clock[0] += 1; store(vs, con, c, 1)
    assert vs.disk_bytes() <= 2500 and not os.path.isdir(vs.folder(a)) and db.recent_rejections(con, a) == []
    assert len(db.recent_rejections(con, c)) == 1
    # visitor cap
    vs, con, clock = fresh(max_visitors=2, per_visitor_min=100)
    store(vs, con, a); clock[0] += 1; store(vs, con, b); clock[0] += 1; store(vs, con, c)
    assert set(vs.seen) == {b, c} and db.recent_rejections(con, a) == []
    print("cleanup and caps ok")


def test_rate_limits_unit():
    vs, con, clock = fresh(per_visitor_min=6, per_ip_min=20)
    a = vid()
    for _ in range(6):
        assert vs.admit(a, "ip1") is None; vs.done(a)
    assert vs.admit(a, "ip1")[0] == 429
    clock[0] += 61
    assert vs.admit(a, "ip1") is None; vs.done(a)
    for _ in range(20):
        v = vid(); assert vs.admit(v, "ip2") is None; vs.done(v)
    assert vs.admit(vid(), "ip2")[0] == 429
    assert vs.admit(a, "ip1") is None and vs.admit(a, "ip1")[0] == 409, "one run at a time"
    print("rate limits ok")


class Fake:
    def __init__(self, code, retry_after=None):
        self.status_code, self.headers = code, ({"Retry-After": retry_after} if retry_after else {})


def test_gate_and_retry():
    orig = gate.requests.post
    slept = []
    try:
        answers = [Fake(429, "2"), Fake(200)]
        gate.requests.post = lambda *a, **k: answers.pop(0)
        r, _ms = gate.post("chat", "u", {}, 1, sleep=slept.append)
        assert r.status_code == 200 and len(slept) == 1 and 1.9 < slept[0] <= 2.0, slept
        gate.GATES["chat"].hold_until = 0.0  # the fake sleep did not wait it out
        answers = [Fake(429, "60"), Fake(200)]
        n = len(slept)
        try:
            gate.post("chat", "u", {}, 1, sleep=slept.append); raise AssertionError("expected Busy")
        except gate.Busy as e:
            assert str(e) == "The service is busy, please try again."
        assert len(slept) == n and len(answers) == 1, "Retry-After over 5 s: no wait, no retry"
        try:
            gate.post("chat", "u", {}, 1, sleep=slept.append); raise AssertionError("expected Busy")
        except gate.Busy:
            pass
        assert len(answers) == 1, "held for Retry-After: no request at all"
        gate.GATES["chat"].hold_until = 0.0
        answers = [Fake(503, "1"), Fake(503, "1")]
        try:
            gate.post("chat", "u", {}, 1, sleep=slept.append); raise AssertionError("expected Busy")
        except gate.Busy:
            pass
        assert not answers, "503 retried once"
        gate.GATES["chat"].hold_until = 0.0
    finally:
        gate.requests.post = orig
        gate.GATES["chat"].hold_until = 0.0
    g = gate.Gate(limit=1, queue=1, wait_s=0.2)
    with g:
        t = time.perf_counter()
        try:
            with g:
                raise AssertionError("expected Busy")
        except gate.Busy:
            assert 0.15 < time.perf_counter() - t < 1
    # the pipeline turns Busy into a failed step with the busy message
    stub_models.CONF["p429"] = 1.0
    try:
        code, ev = upload(vid())
        assert ev["error"]["step"] == "d3" and ev["error"]["message"] == gate.BUSY, ev
    finally:
        stub_models.CONF["p429"] = 0.0
    print("gate and 429 retry ok")


def test_client_ip():
    f = server.client_ip_from  # public example addresses (Python counts the documentation ranges as private)
    assert f([], "10.0.0.9") == "10.0.0.9", "no header: the peer"
    assert f(["9.9.9.9"], "10.0.0.9") == "9.9.9.9"
    assert f(["1.2.3.4, 9.9.9.9, 10.16.0.3"], "10.0.0.9") == "9.9.9.9", "rightmost public, past proxy hops"
    assert f(["1.2.3.4", "9.9.9.9"], "10.0.0.9") == "9.9.9.9", "a spoofed first hop is skipped"
    assert f(["garbage, 10.1.1.1"], "10.0.0.9") == "10.1.1.1", "only private hops: the first valid one"
    # the HTTP path: without TRUST_PROXY the header is ignored; with it, two clients behind one proxy count apart
    assert not server.TRUST_PROXY
    old = server.VIS.per_ip_min
    server.TRUST_PROXY, server.VIS.per_ip_min = True, 1
    try:
        assert upload(vid(), headers={"X-Forwarded-For": "8.8.4.4"})[0] == 200
        assert upload(vid(), headers={"X-Forwarded-For": "8.8.8.8"})[0] == 200, "another client IP"
        assert upload(vid(), headers={"X-Forwarded-For": "8.8.4.4"})[0] == 429, "same client IP"
    finally:
        server.TRUST_PROXY, server.VIS.per_ip_min = False, old
    print("client ip ok")


def test_key_check():
    """A local Router (the stub) needs no visitor key; the hosted API does unless the server has its own."""
    import keys
    assert keys.LOCAL and not keys.REQUIRED, "a local Router needs no key"
    assert server.config_body()["key_required"] is False
    print("key check ok")


def test_routed():
    """The LLM events say what the Router picked (x-vsr-selected-* headers) and how many tokens went to reasoning."""
    code, ev = upload(vid(), "08_good_cord001.jpg", reencode("08_good_cord001.jpg"))
    x = ev["extract"]
    assert x["model"] == "stub-extract" and x["routed"] == {"model": "stub/vision-model", "decision": "vision",
                                                              "reasoning": "on"} and x["reasoning_tokens"] == 400, x
    code, ev = upload(vid(), "20_bad_dog_photo.jpg", reencode("20_bad_dog_photo.jpg"))
    m = ev["message"]
    assert m["routed"] == {"model": "stub/text-model", "decision": "fast", "reasoning": "off"}, m
    print("routed ok")


def test_decode():
    im = Image.new("RGB", (6000, 4000), (250, 250, 250))
    exif = Image.Exif(); exif[0x0112] = 6  # stored sideways: rotate 90 degrees to view
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=80, exif=exif)
    raw = buf.getvalue()
    big, size = server.open_image(raw, upload=True)
    assert size == (6000, 4000) and max(big.size) < 6000 and max(big.size) >= server.UPLOAD_LONG_SIDE, big.size
    out, mime, dims = server.read_image("multipart/form-data", raw, "big.jpg")
    assert mime == "image/jpeg" and dims == (1067, 1600), dims  # upright (portrait) and 1600 px long side
    small = open(os.path.join(DEMO, "01_good_berghotel.jpg"), "rb").read()
    assert server.read_image("multipart/form-data", small, "s.jpg")[0] is small, "small JPEGs pass through"
    try:
        server.read_image("multipart/form-data", b"not an image", "x.txt"); raise AssertionError
    except server.Refused:
        pass
    print("decode ok")


def test_endpoints():
    assert get("/healthz").json() == {"ok": True}
    c = get("/api/config").json()
    assert c["play_all"] is False and "measure_baseline" not in c
    assert get("/api/baseline/abc").status_code == 404, "no baseline endpoint"
    m = get("/api/metrics").json()
    assert {"visitors", "uploads_last_hour", "model_calls", "model_429_503", "upload_disk_bytes"} <= set(m), m
    print("endpoints ok", m)


# -- models down, slow or failing ----------------------------------------------------------------------------------
RAW = ("{", "}", "HTTP", "Error", "Traceback", "upstream", "http://", "Exception")


def stub(**conf):
    """Sets the stub's per-model failures (down, delay, status); everything else back to normal."""
    stub_models.CONF.update(down=[], delay={}, status={}); stub_models.CONF.update(conf)


def healthy():
    stub(); gate.TRACKER.reset(); health.round_once()
    assert get("/health").json()["status"] == "ok"


def calls():
    return sum(s["calls"] for s in stub_models.STATS.values())


def plain(ev):
    """Every string the UI shows from an error or message event, and every error in an API answer, is plain words."""
    for name in ("error", "message"):
        for v in (ev[name].values() if isinstance(ev.get(name), dict) else []):
            for x in (v if isinstance(v, list) else [v]):
                assert not isinstance(x, str) or not any(r in x for r in RAW), (name, x)
    if "error" in ev and isinstance(ev["error"], str):  # a JSON refusal
        assert not any(r in ev["error"] for r in RAW), ev


def test_preflight():
    healthy()
    for model in ("d3", "extract"):
        stub(down=[model]); health.round_once()
        hl = get("/health").json()
        assert hl["status"] == "down" and hl["message"] == "Receipt checking unavailable" and hl[model] is False, hl
        n = calls()
        code, body = upload(vid())
        assert code == 503 and body["error"] == "Receipt checking is unavailable right now. Please try again in a minute."
        r = requests.post(f"{URL}/api/check", json={"demo": "01_good_berghotel.jpg"}, headers=h(vid()))
        assert r.status_code == 503
        assert calls() == n, "no model call when refused"
        plain(body)
        healthy()
    stub(down=["retake"]); health.round_once()
    hl = get("/health").json()
    assert hl["status"] == "degraded" and hl["message"] == "Standard retake messages in use: LLM unavailable", hl
    n4 = stub_models.STATS.get("retake", {}).get("calls", 0)
    code, ev = upload(vid(), "02_bad_blurry_hand.jpg", reencode("02_bad_blurry_hand.jpg"))
    assert code == 200 and ev["message"]["model"] == "template", ev
    assert ev["message"]["fallback"] == "standard message (LLM unavailable)" and "stored" in ev
    assert stub_models.STATS.get("retake", {}).get("calls", 0) == n4, "message LLM marked down: not called"
    code, ev = upload(vid())
    assert code == 200 and "stored" in ev and ev["stored"]["table"] == "receipts", "good receipts still read"
    healthy()
    print("pre-flight ok")


def test_breaker_unit():
    clock = [0.0]
    t = gate.Tracker(fails=3, open_s=30, clock=lambda: clock[0])
    assert t.available("d3"), "unknown counts as up"
    t.fail("d3", "timeout"); t.fail("d3", "timeout")
    assert t.available("d3")
    t.fail("d3", "timeout")
    assert not t.available("d3") and t.why("d3") == "timeout"
    clock[0] += 29.9
    assert not t.available("d3")
    clock[0] += 0.2
    assert t.available("d3"), "open for 30 s only"
    t.fail("d3", "unavailable")
    assert not t.available("d3"), "still failing after the 30 s: open again at once"
    t.ok("d3")
    assert t.available("d3") and t.why("d3") is None, "a success closes it"
    t.probed("retake", False, "unreachable")
    assert not t.available("retake")
    t.probed("retake", True)
    assert t.available("retake")
    print("breaker (unit) ok")


def test_breaker_http():
    """3 failed reads (5xx) open the breaker: the next upload is refused before any model call; a success closes it."""
    healthy()
    stub(status={"extract": 500})
    for _ in range(3):
        code, ev = upload(vid())
        e = ev["error"]
        assert e["step"] == "extract" and e["reason"] == "unavailable" and e["detail"] == "The LLM is unavailable", e
        assert e["message"] == pipeline.UNAVAILABLE["extract"] and e["skipped"] == ["sumcheck", "stored"]
        assert "stored" not in ev and "done" not in ev
        plain(ev)
    assert not gate.TRACKER.available("extract")
    n = calls()
    code, body = upload(vid())
    assert code == 503 and calls() == n, "breaker open: refused, no model call"
    assert get("/health").json()["detail"]["extract"] == "unavailable"
    stub()
    health.round_once()  # a successful probe marks it up
    assert gate.TRACKER.available("extract") and upload(vid())[1].get("stored")
    # D3 answering 502 with a JSON body: the step says unavailable, nothing of the body reaches the page
    stub(status={"d3": 502})
    code, ev = upload(vid())
    assert ev["error"]["step"] == "d3" and ev["error"]["reason"] == "unavailable", ev
    assert ev["error"]["message"] == pipeline.UNAVAILABLE["d3"] and ev["error"]["skipped"] == ["route", "model", "stored"]
    assert "route" not in ev and "stored" not in ev
    plain(ev)
    healthy()
    print("breaker (http) ok")


def test_timeouts_mid_run():
    """Models that are up at the start but fail during the run."""
    healthy()
    old = dict(gate.TIMEOUT_S)
    gate.TIMEOUT_S.update(extract=0.5, retake=0.5, d3=0.5)
    try:
        stub(delay={"extract": 2})
        code, ev = upload(vid())
        e = ev["error"]
        assert e["step"] == "extract" and e["reason"] == "timeout", e
        assert e["detail"] == "The LLM did not answer within 0.5 s"
        assert e["message"] == "Your receipt passed the checks, but reading is unavailable right now. Please try again shortly."
        assert "d3" in ev and "route" in ev and "stored" not in ev
        plain(ev)
        stub(delay={"retake": 2})
        code, ev = upload(vid(), "04_bad_crumpled.jpg", reencode("04_bad_crumpled.jpg"))
        m = ev["message"]
        assert m["model"] == "template" and m["fallback"] == "standard message (LLM timed out)", m
        assert m["text"] == message.template(ev["route"]) and "crumpled" in m["text"], m
        assert ev["stored"]["table"] == "rejections" and "done" in ev
        assert get("/api/rejections", vid()).json() == []
        stub(down=["retake"])
        code, ev = upload(vid(), "24_bad_sticky_note.jpg", reencode("24_bad_sticky_note.jpg"))
        m = ev["message"]
        assert m["fallback"] == "standard message (LLM unavailable)", m
        assert "original receipt only" in m["text"], m["text"]
        stub(delay={"d3": 2})
        code, ev = upload(vid())
        assert ev["error"]["step"] == "d3" and ev["error"]["reason"] == "timeout" and "received" in ev, ev
        plain(ev)
    finally:
        gate.TIMEOUT_S.update(old)
        healthy()
    print("timeouts mid-run ok")


def test_template():
    route = lambda *keys: {"reasons": [{"key": k, "label": k, "prob": 0.9, "status": "bad"} for k in keys]}  # noqa: E731
    assert message.template(route("kind")).startswith("This image does not look like a receipt")
    t = message.template(route("blurry", "glare"))
    assert t == ("This photo cannot be used: the text is blurry and glare or a shadow covers part of the text. "
                 "Please hold the phone steady and let it focus and move out of direct light, then try again."), t
    assert "original receipt only" in message.template(route("added_text"))
    print("template ok")


if __name__ == "__main__":
    a, b = test_isolation()
    test_demo_runs_share_images()
    test_reset_scoped(a, b)
    test_limits_http()
    test_one_run_per_visitor()
    test_cleanup_and_caps()
    test_rate_limits_unit()
    test_gate_and_retry()
    test_decode()
    test_client_ip()
    test_key_check()
    test_routed()
    test_endpoints()
    test_template()
    test_breaker_unit()
    test_preflight()
    test_breaker_http()
    test_timeouts_mid_run()
    print("all tests passed")
