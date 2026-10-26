"""HTTP server for the demo (docs/API.md): the pipeline as Server-Sent Events, the demo set, stats, and the built UI.

  python app/server.py            # 127.0.0.1:8601, serves web/ and /api/...
Env: ROUTER_URL (http://localhost:8899, the vllm-sr Router for D3 and both Qwen models), HOST (127.0.0.1), PORT (8601),
DB (data/receipts.db), GPU_USD_PER_HOUR (DigitalOcean's MI300X on-demand price, for the dollars box).
"""
import base64
import io
import itertools
import json
import queue
import mimetypes
import os
import sys
import threading
import time
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import requests
from PIL import Image, ImageOps

try:  # HEIC, the iPhone camera's own format (pip install pillow-heif); without it HEIC uploads are refused
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))
import check  # noqa: E402
import db  # noqa: E402
import pipeline  # noqa: E402
import gpuinfo  # noqa: E402
from extract import extract  # noqa: E402

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8601"))
WEB, DEMO, UPLOADS = "web", "data/demo", "data/uploads"
MAX_BYTES = 15 * 1024 * 1024
PING_SECONDS = 2  # keep-alive interval on /api/check; the page treats a long silence as a lost connection
os.makedirs(UPLOADS, exist_ok=True)
CON = db.connect(os.environ.get("DB", db.PATH))
SEQ = itertools.count(int(time.time()) % 100000)
PIPE_LOCK = threading.Lock()  # one upload at a time: the demo shows one pipeline run, and timings stay clean
LABELS = json.load(open(f"{DEMO}/labels.json"))
EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
BASELINE_FILE = f"{DEMO}/baseline.json"  # 27B time on every demo image (bench/baseline_27b.py)
DEMO_BASELINE = ({i["sha256"]: i["ms"] for i in json.load(open(BASELINE_FILE))["images"]}
                 if os.path.isfile(BASELINE_FILE) else {})
DEMO_TOKENS = ({i["sha256"]: i["prompt_tokens"] + i["completion_tokens"] for i in json.load(open(BASELINE_FILE))["images"]}
               if os.path.isfile(BASELINE_FILE) else {})
MEASURING = set()  # image hashes whose 27B baseline is being measured
# Price of one MI300X hour for the dollars box: DigitalOcean's list price (1x and 8x Droplets are priced per GPU),
# https://www.digitalocean.com/pricing/gpu-droplets. GPU_USD_PER_HOUR overrides it for another cloud.
GPU_PRICE = ({"usd_per_hour": float(os.environ["GPU_USD_PER_HOUR"]), "source": "GPU_USD_PER_HOUR set on the server",
              "checked": time.strftime("%Y-%m-%d")} if os.environ.get("GPU_USD_PER_HOUR") else
             {"usd_per_hour": 2.59, "source": "DigitalOcean GPU Droplet, MI300X, on-demand", "checked": "2026-10-26"})
GPU_NAME = gpuinfo.gpu_name()  # read once at startup, to check the measurements were made on this GPU


def read_json(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def measurement_warning():
    """A plain sentence when the 27B baseline was measured on a different GPU than this one (or does not say), else
    None. Quiet when this host's GPU cannot be read."""
    doc = read_json(BASELINE_FILE)  # read per request, so a fresh baseline shows without a restart
    if GPU_NAME == "unknown" or doc is None:
        return None
    if not doc.get("gpu"):
        return "The Qwen 27B baseline does not say which GPU it was measured on; re-run bench/baseline_27b.py here."
    if doc["gpu"] != GPU_NAME:
        return f"The Qwen 27B baseline was measured on {doc['gpu']}, not this server's {GPU_NAME}; re-run bench/baseline_27b.py here."
    return None


def stats_body():
    body = {**pipeline.stats(CON, DEMO_BASELINE), "gpu_price": GPU_PRICE}
    warning = measurement_warning()
    if warning:
        body["measurement_warning"] = warning
    return body


def baseline_ms(sha):
    """The 27B's measured time on this image, or None if it has not been measured."""
    return DEMO_BASELINE.get(sha) or db.baselines(CON).get(sha)


def baseline_tokens(sha):
    """Tokens (prompt plus completion) the 27B used on this image when measured, or None."""
    return DEMO_TOKENS.get(sha) or db.baseline_tokens(CON, sha)


def measure_baseline(raw, mime, sha, file):
    """Times the 27B on a rejected upload that has no baseline yet, after the run, one request at a time."""
    try:
        with PIPE_LOCK:
            if sha in DEMO_BASELINE or sha in db.baselines(CON):
                return
            _rc, ms, usage = extract(raw, mime)
            db.add_baseline(CON, sha, file, ms, usage.get("completion_tokens"), usage.get("prompt_tokens"))
            print(f"27B baseline for {file}: {ms:.0f} ms", flush=True)
    except Exception as e:  # noqa: BLE001 - the image stays pending in the stats
        print(f"27B baseline for {file} failed: {e}", flush=True)
    finally:
        MEASURING.discard(sha)


UPLOAD_LONG_SIDE = 1600  # an iPhone receipt photo read the same at 1600 px as at full size, with the 27B 2.3x faster


def prepare_upload(raw, im):
    """An upload as the models should see it, made once before D3. Phone photos are shrunk to UPLOAD_LONG_SIDE (a 24 MP
    iPhone photo is over the Router's 16 MP limit, and the 27B's time grows with pixels); iPhone JPEGs are MPO (the photo
    plus an HDR gain-map image) and HEIC is the iPhone default; phones often store the photo sideways with an EXIF
    rotation tag that a model may not apply. Such uploads become one upright JPEG; small plain JPEG, PNG and WebP files
    pass through unchanged."""
    if (im.format in ("JPEG", "PNG", "WEBP") and im.getexif().get(0x0112, 1) == 1
            and max(im.size) <= UPLOAD_LONG_SIDE):
        return raw, Image.MIME[im.format]
    im.seek(0)
    out = ImageOps.exif_transpose(im).convert("RGB")
    out.thumbnail((UPLOAD_LONG_SIDE, UPLOAD_LONG_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


def demo_list():
    return [{"file": l["file"], "group": l["group"], "label": l["label"], "expected_route": l["expected_route"],
             "image_url": f"/api/demo/{l['file']}"} for l in LABELS]


def multipart_image(ctype, body):
    """The 'image' field of a multipart/form-data body: (bytes, filename)."""
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body)
    for part in msg.iter_parts():
        if part.get_param("name", header="content-disposition") == "image":
            return part.get_payload(decode=True), os.path.basename(part.get_filename() or "upload")
    raise ValueError("no 'image' field")


HEALTH_SECONDS = 60
HEALTH = {}  # last probe result, served by /health at once
HEALTH_READY = threading.Event()


def tiny_image():
    """A 64x64 PNG with a dark stripe: enough for a model to accept as an image, a few tokens to read."""
    im = Image.new("RGB", (64, 64), (240, 240, 235))
    for x in range(8, 56):
        for y in range(28, 36):
            im.putpixel((x, y), (30, 30, 30))
    buf = io.BytesIO(); im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def probe(name, fn):
    """(ok, detail): detail names the failure in plain words."""
    try:
        r = fn()
    except requests.Timeout:
        return False, "no answer within 20 s"
    except requests.ConnectionError:
        return False, "cannot reach the Router" if name == "router" else "down"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"[:120]
    if r.status_code == 200:
        return True, None
    text = r.text[:300].lower()
    if name == "qwen27b" and r.status_code == 400 and "image" in text:
        return False, "image input off"  # vLLM started with --limit-mm-per-prompt image=0
    if "model_not_found" in text:
        return False, "not served by the Router"
    if r.status_code in (404, 502, 503) and name != "router":
        return False, "down"
    return False, f"HTTP {r.status_code}"


def health_round():
    """One probe of each service through the Router, the way the pipeline calls it, kept tiny (one token out)."""
    img, url, t = tiny_image(), check.ROUTER_URL, (3, 20)
    chat = f"{url}/v1/chat/completions"
    probes = {
        "router": lambda: requests.get(f"{url}/v1/models", timeout=t),
        "d3": lambda: requests.post(check.ROUTER, timeout=t, json={
            "model": check.MODEL, "state": "Health check.", "images": [img],
            "questions": {"dark": {"type": "noul", "instructions": "The image has a dark stripe."}}}),
        "qwen27b": lambda: requests.post(chat, timeout=t, json={
            "model": "qwen27b", "max_tokens": 1, "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": img}}, {"type": "text", "text": "Say ok."}]}]}),
        "qwen4b": lambda: requests.post(chat, timeout=t, json={
            "model": "qwen4b", "max_tokens": 1, "messages": [{"role": "user", "content": "Say ok."}]}),
    }
    out, detail = {}, {}
    for name, fn in probes.items():
        out[name], why = probe(name, fn)
        if why:
            detail[name] = why
    return {"ok": all(out.values()), **out, "detail": detail,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def health_loop():
    """Probes at startup and every HEALTH_SECONDS. A round is skipped (the last result kept) while a pipeline run or a
    baseline measurement holds PIPE_LOCK, and holds the lock itself while probing, so probes never touch timings."""
    while True:
        if PIPE_LOCK.acquire(blocking=False):
            try:
                HEALTH.clear(); HEALTH.update(health_round())
            except Exception as e:  # noqa: BLE001
                print("health check failed:", e, flush=True)
            finally:
                PIPE_LOCK.release()
            HEALTH_READY.set()
        time.sleep(HEALTH_SECONDS)


def health():
    HEALTH_READY.wait(timeout=8)  # only the first request after startup can wait, for the first round
    return dict(HEALTH) or {"ok": False, "router": False, "d3": False, "qwen27b": False, "qwen4b": False,
                            "detail": {"router": "not checked yet"}, "checked_at": None}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def _json(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path, cache="no-cache"):
        if not os.path.isfile(path):
            return self._json(404, {"error": "not found"})
        data = open(path, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = unquote(urlparse(self.path).path)
        if p == "/health":
            return self._json(200, health())
        if p == "/api/demo":
            return self._json(200, demo_list())
        if p.startswith("/api/demo/"):
            name = os.path.basename(p)
            return self._file(os.path.join(DEMO, name), "max-age=3600") if name.endswith(".jpg") else self._json(404, {})
        if p.startswith("/api/uploads/"):
            uid = os.path.basename(p)
            hits = [f for f in os.listdir(UPLOADS) if os.path.splitext(f)[0] == uid]
            return self._file(os.path.join(UPLOADS, hits[0]), "max-age=3600") if hits else self._json(404, {})
        if p.startswith("/api/baseline/"):
            sha = os.path.basename(p)
            return self._json(200, {"image_sha256": sha, "ms": baseline_ms(sha), "tokens": baseline_tokens(sha),
                                    "measuring": sha in MEASURING})
        if p == "/api/stats":
            return self._json(200, stats_body())
        if p == "/api/receipts":
            return self._json(200, db.recent_receipts(CON))
        if p == "/api/rejections":
            return self._json(200, db.recent_rejections(CON))
        if p.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        # static UI; unknown paths fall back to index.html
        path = os.path.normpath(os.path.join(WEB, p.lstrip("/")))
        if not path.startswith(WEB) or not os.path.isfile(path):
            path = os.path.join(WEB, "index.html")
        if not os.path.isfile(path):
            return self._json(200, {"ok": True, "note": "UI not built yet; see docs/UI.md"})
        return self._file(path, "no-cache" if path.endswith(".html") else "max-age=31536000")

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/api/reset":
            db.reset(CON)
            return self._json(200, {"ok": True})
        if p != "/api/check":
            return self._json(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BYTES + 100_000:
            return self._json(413, {"error": "image too large (15 MB max)"})
        ctype = self.headers.get("Content-Type", "")
        try:
            if ctype.startswith("application/json"):
                name = os.path.basename(json.loads(self.rfile.read(n))["demo"])
                path = os.path.join(DEMO, name)
                if not os.path.isfile(path):
                    return self._json(404, {"error": f"no demo image {name}"})
                raw, file = open(path, "rb").read(), name
            elif ctype.startswith("multipart/form-data"):
                raw, file = multipart_image(ctype, self.rfile.read(n))
            else:
                return self._json(415, {"error": "send multipart/form-data with field 'image', or JSON {\"demo\": file}"})
            try:
                im = Image.open(io.BytesIO(raw)); im.load()
            except Exception:  # noqa: BLE001
                return self._json(415, {"error": f"{file} is not an image the demo can read. Use a JPEG, PNG, WebP or HEIC photo."})
            size = im.size
            if ctype.startswith("multipart/form-data"):
                raw, mime = prepare_upload(raw, im)
                size = Image.open(io.BytesIO(raw)).size
            else:
                mime = Image.MIME.get(im.format)
            if mime not in EXT:
                return self._json(415, {"error": f"{file} is a {im.format} image. Use a JPEG, PNG, WebP or HEIC photo."})
        except Exception as e:  # noqa: BLE001
            return self._json(400, {"error": f"Could not read the upload: {e}"})
        uid = f"u{next(SEQ)}"
        open(os.path.join(UPLOADS, uid + EXT[mime]), "wb").write(raw)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        sha = db.sha256(raw)
        rejected = False
        gone = threading.Event()  # set when the client stops the run or the connection drops
        events = queue.Queue()

        def work():
            try:
                with PIPE_LOCK:  # waits here (pings keep flowing) while another run or a baseline measurement is busy
                    if gone.is_set():
                        return
                    for item in pipeline.run(CON, uid, file, raw, mime, size, sha, cancelled=gone.is_set,
                                                  baseline_ms=baseline_ms, baseline_tokens=baseline_tokens):
                        events.put(item)
            except Exception as e:  # noqa: BLE001
                events.put(("error", {"step": "server", "message": f"{type(e).__name__}: {e}"}))
            finally:
                events.put(None)

        threading.Thread(target=work, daemon=True).start()
        while True:
            try:
                item = events.get(timeout=PING_SECONDS)
            except queue.Empty:
                item = "ping"
            if item is None:
                break
            if gone.is_set():
                continue  # drain until the worker stops at its next step
            if item == "ping":
                chunk = b": ping\n\n"  # SSE comment: tells the page the server is alive during slow steps
            else:
                event, data = item
                rejected = rejected or (event == "stored" and data["table"] == "rejections")
                chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()
            try:
                self.wfile.write(chunk)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                gone.set()  # stopped or disconnected: the run ends after its current step and nothing is stored
                print(f"{uid} {file}: client went away, run cancelled", flush=True)
        if rejected and sha not in DEMO_BASELINE and sha not in MEASURING and sha not in db.baselines(CON):
            MEASURING.add(sha)
            threading.Thread(target=measure_baseline, args=(raw, mime, sha, file), daemon=True).start()


def warm_up():
    """One D3 call per demo image shape, so the first live upload does not pay for kernel warm-up. It sends a
    re-encoded copy of each image: the Router caches D3 image features by content, and caching the real demo bytes
    here would make every live D3 time a cache hit."""
    t = time.perf_counter()
    for l in LABELS:
        try:
            im = Image.open(os.path.join(DEMO, l["file"])).convert("RGB"); im.putpixel((0, 0), (1, 2, 3))
            buf = io.BytesIO(); im.save(buf, "JPEG", quality=90)
            check.ask_d3(buf.getvalue())
        except Exception as e:  # noqa: BLE001
            print("warm-up failed:", e, flush=True); return
    print(f"warm-up done in {time.perf_counter() - t:.1f} s", flush=True)


if __name__ == "__main__":
    threading.Thread(target=health_loop, daemon=True).start()
    threading.Thread(target=warm_up, daemon=True).start()
    print(f"receipt intake demo on http://{HOST}:{PORT} (GPU: {GPU_NAME}, Router: {check.ROUTER_URL})", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
