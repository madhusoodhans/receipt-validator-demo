"""Load test: many simulated visitors uploading at once against the stub models (bench/stub_models.py), no GPU.

Starts the stub on :8931 and the demo server on :8631 (env STUB_PORT, LOAD_PORT; temp database and upload folder, the
Space's settings: PLAY_ALL=0), runs VISITORS visitors in parallel, each from its own IP (X-Forwarded-For) doing
UPLOADS checks (a mix of demo images, re-encoded demo photos as file uploads, and one large phone-sized photo), then:
  - routes match data/demo/labels.json; no run failed;
  - isolation: each visitor's /api/receipts, /api/rejections and /api/stats hold exactly their own runs, their upload
    images load for them and 404 for another visitor;
  - the database is consistent after the parallel writes (integrity check, no orphan line items, row counts add up);
  - memory (server RSS) and upload disk before, at peak and after the idle cleanup has removed every visitor.
Prints p50/p95 of the time to a finished run (client side, queue waits included).

  .venv/bin/python bench/load_test.py [--visitors 30] [--uploads 6] [--p429 0.0] [--speed 1.0]
"""
import argparse
import io
import json
import os
import random
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PY = sys.executable
DEMO = os.path.join(ROOT, "data/demo")
LABELS = json.load(open(os.path.join(DEMO, "labels.json")))
EXPECTED = {l["file"]: l["expected_route"] for l in LABELS}
PORT, STUB_PORT = os.environ.get("LOAD_PORT", "8631"), os.environ.get("STUB_PORT", "8931")
URL, STUB = f"http://127.0.0.1:{PORT}", f"http://127.0.0.1:{STUB_PORT}"


def wait_up(url, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if requests.get(url, timeout=1).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{url} did not come up")


def rss_mb(pid):
    for line in open(f"/proc/{pid}/status"):
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return 0.0


def variant(file, n, big=False):
    """A demo photo with different bytes (one pixel and the JPEG quality changed), optionally upscaled to 6000 px."""
    im = Image.open(os.path.join(DEMO, file)).convert("RGB")
    if big:
        f = 6000 / max(im.size)
        im = im.resize((round(im.width * f), round(im.height * f)), Image.BICUBIC)
    im.putpixel((0, 0), (n % 256, (n * 7) % 256, 0))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=85 + n % 10)
    return buf.getvalue()


def parse(text):
    ev, name = {}, None
    for line in text.splitlines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            ev[name] = json.loads(line[5:])
    return ev


def visitor(n, uploads):
    v, ip = str(uuid.uuid4()), f"198.51.{n // 250}.{n % 250 + 1}"
    hdr = {"X-Visitor-Id": v, "X-Forwarded-For": ip}
    rnd = random.Random(n)
    out = {"id": v, "runs": [], "errors": []}
    time.sleep(rnd.random() * 2)  # staggered arrivals
    for k in range(uploads):
        file = rnd.choice(LABELS)["file"]
        kind = "big" if k == 2 and n % 3 == 0 else "demo" if k % 2 == 0 else "file"
        t = time.perf_counter()
        if kind == "demo":
            r = requests.post(f"{URL}/api/check", json={"demo": file}, headers=hdr, timeout=300)
        else:
            raw = variant(file, n * 100 + k, big=kind == "big")
            r = requests.post(f"{URL}/api/check", files={"image": (f"photo{k}.jpg", raw, "image/jpeg")}, headers=hdr,
                              timeout=300)
        ms = (time.perf_counter() - t) * 1000
        if r.status_code != 200:
            out["errors"].append(f"HTTP {r.status_code}: {r.text[:120]}"); continue
        ev = parse(r.text)
        if "error" in ev or "stored" not in ev:
            out["errors"].append(f"{file}: {ev.get('error')}"); continue
        out["runs"].append({"file": file, "kind": kind, "ms": ms, "route": ev["route"]["route"],
                            "table": ev["stored"]["table"], "row": ev["stored"]["id"], "image_url": ev["received"]["image_url"]})
        time.sleep(rnd.uniform(0.5, 2))  # reading the result
    return out


def check_isolation(results):
    """Each visitor sees exactly their own rows and upload files; another visitor sees none of them."""
    problems = 0
    for i, res in enumerate(results):
        hdr = {"X-Visitor-Id": res["id"]}
        other = {"X-Visitor-Id": results[(i + 1) % len(results)]["id"]}
        rec = {r["id"] for r in requests.get(f"{URL}/api/receipts", headers=hdr).json()}
        rej = {r["id"] for r in requests.get(f"{URL}/api/rejections", headers=hdr).json()}
        mine_rec = {r["row"] for r in res["runs"] if r["table"] == "receipts"}
        mine_rej = {r["row"] for r in res["runs"] if r["table"] == "rejections"}
        stats = requests.get(f"{URL}/api/stats", headers=hdr).json()
        if rec != mine_rec or rej != mine_rej or stats["uploads"] != len(res["runs"]):
            problems += 1; print("  mismatch for visitor", i, rec ^ mine_rec, rej ^ mine_rej, stats["uploads"], len(res["runs"]))
        for r in res["runs"]:
            if "/api/uploads/" in r["image_url"]:
                if requests.get(URL + r["image_url"], headers=hdr).status_code != 200:
                    problems += 1; print("  own upload missing", r["image_url"])
                if requests.get(URL + r["image_url"], headers=other).status_code != 404:
                    problems += 1; print("  LEAK: another visitor fetched", r["image_url"])
    return problems


def check_db(path, results):
    con = sqlite3.connect(path)
    ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    orphans = con.execute("SELECT COUNT(*) FROM line_items WHERE receipt_id NOT IN (SELECT id FROM receipts)").fetchone()[0]
    rows = con.execute("SELECT (SELECT COUNT(*) FROM receipts) + (SELECT COUNT(*) FROM rejections)").fetchone()[0]
    per = dict(con.execute("SELECT visitor_id, COUNT(*) FROM (SELECT visitor_id FROM receipts UNION ALL "
                           "SELECT visitor_id FROM rejections) GROUP BY visitor_id").fetchall())
    con.close()
    expected = sum(len(r["runs"]) for r in results)
    per_ok = all(per.get(r["id"], 0) == len(r["runs"]) for r in results)
    return ok, orphans, rows, expected, per_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--visitors", type=int, default=30)
    ap.add_argument("--uploads", type=int, default=6)
    ap.add_argument("--p429", type=float, default=0.0)
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="receipt-load-")
    db_path, up = os.path.join(tmp, "load.db"), os.path.join(tmp, "uploads")
    env = {**os.environ, "STUB_SPEED": str(a.speed), "STUB_429": str(a.p429), "STUB_PORT": STUB_PORT}
    stub = subprocess.Popen([PY, "-I", os.path.join(ROOT, "bench/stub_models.py")], env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    wait_up(f"{STUB}/v1/models")  # before the server starts: its first health probe must find the models up
    log = open(os.path.join(tmp, "server.log"), "w")
    srv = subprocess.Popen([PY, "-I", os.path.join(ROOT, "app/server.py")], stdout=log, stderr=subprocess.STDOUT, env={
        **os.environ, "PORT": PORT, "ROUTER_URL": STUB, "DB": db_path, "UPLOAD_ROOT": up, "PLAY_ALL": "0",
        "IDLE_MIN": "0.5", "CLEANUP_S": "5", "MALLOC_ARENA_MAX": "2"})  # as in the Dockerfile
    try:
        wait_up(f"{URL}/healthz")
        if requests.get(f"{URL}/health", timeout=10).json().get("status") != "ok":
            raise RuntimeError("the stub models are not all up")
        time.sleep(3)  # warm-up calls (one D3 per demo image) finish
        rss0, peak = rss_mb(srv.pid), [0.0]
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                peak[0] = max(peak[0], rss_mb(srv.pid)); time.sleep(0.1)
        threading.Thread(target=sample, daemon=True).start()
        t = time.time()
        with ThreadPoolExecutor(a.visitors) as ex:
            results = list(ex.map(lambda n: visitor(n, a.uploads), range(a.visitors)))
        wall = time.time() - t
        m = requests.get(f"{URL}/api/metrics").json()
        stop.set()
        runs = [r for res in results for r in res["runs"]]
        errors = [e for res in results for e in res["errors"]]
        busy = [e for e in errors if "busy" in e]  # expected with --p429: refused twice, the step fails politely
        errors = [e for e in errors if e not in busy]
        wrong = [r for r in runs if r["route"] != EXPECTED[r["file"]] and not (r["route"] == "review" and EXPECTED[r["file"]] == "extract")]
        ms = sorted(r["ms"] for r in runs)
        p = lambda q: ms[min(len(ms) - 1, int(q * len(ms)))]  # noqa: E731
        print(f"{a.visitors} visitors x {a.uploads} checks in {wall:.1f} s: {len(runs)} stored, {len(errors)} failed, "
              f"{len(wrong)} unexpected routes, {len(busy)} ended 'busy'")
        for e in errors[:5]:
            print("  failed:", e)
        by = {k: sorted(r["ms"] for r in runs if r["kind"] == k) for k in ("demo", "file", "big")}
        print(f"time to a finished run: p50 {statistics.median(ms):.0f} ms, p95 {p(0.95):.0f} ms, max {ms[-1]:.0f} ms; "
              + ", ".join(f"{k} p50 {statistics.median(v):.0f} ms (n={len(v)})" for k, v in by.items() if v))
        stub_stats = requests.get(f"{STUB}/stub/stats").json()["endpoints"]
        print("stub: " + ", ".join(f"{k} {s['calls']} calls, {s['refused']} 429s, max {s['max_in_flight']} in flight"
                                   for k, s in stub_stats.items()))
        print(f"server metrics after the run: {json.dumps(m)}")
        leaks = check_isolation(results)
        print(f"isolation: {leaks} problems over {len(results)} visitors and {len(runs)} runs")
        ok, orphans, rows, expected, per_ok = check_db(db_path, results)
        print(f"database: integrity {'ok' if ok else 'BAD'}, {orphans} orphan line items, {rows} rows for {expected} "
              f"stored runs, per-visitor counts {'match' if per_ok else 'DO NOT match'}")
        print(f"memory (server RSS): {rss0:.0f} MB before, {peak[0]:.0f} MB peak; upload disk {m['upload_disk_bytes'] / 1e6:.1f} MB")
        end = time.time() + 90
        while time.time() < end:  # idle 30 s, cleanup every 5 s
            m2 = requests.get(f"{URL}/api/metrics").json()
            if m2["visitors"] == 0 and m2["upload_disk_bytes"] == 0:
                break
            time.sleep(2)
        rows_after = check_db(db_path, [])[2]
        left = sum(len(fs) for _d, _s, fs in os.walk(up))
        print(f"after cleanup: {m2['visitors']} visitors, {rows_after} rows, {left} upload files, "
              f"disk {m2['upload_disk_bytes']} bytes, RSS {rss_mb(srv.pid):.0f} MB")
        lines = [l for l in open(os.path.join(tmp, "server.log")) if l.startswith("upload v=")]
        print(f"server log: {len(lines)} upload lines, e.g. {lines[0].strip() if lines else '-'}")
        good = not errors and not wrong and leaks == 0 and ok and orphans == 0 and rows == expected and per_ok \
            and m2["visitors"] == 0 and rows_after == 0 and left == 0
        print("PASS" if good else "FAIL")
        return 0 if good else 1
    finally:
        srv.terminate(); stub.terminate(); srv.wait(); stub.wait()


if __name__ == "__main__":
    sys.exit(main())
