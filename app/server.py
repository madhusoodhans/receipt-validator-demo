"""HTTP server for the demo (docs/API.md): the pipeline as Server-Sent Events, the demo set, stats, and the built UI.

  python app/server.py            # 127.0.0.1:8601, serves web/ and /api/...
Env: ROUTER_URL (https://app.vllm-sr.ai, the vllm-sr API for D3 and the LLM), VLLM_SR_API_KEY (optional and unset by
default: each visitor enters their own key in the browser, see app/keys.py; a server key, when set, serves visitors who
have not), KEY_REQUIRED, KEY_CHECKS_PER_MIN (app/keys.py), D3_MODEL / EXTRACT_MODEL / MESSAGE_MODEL (vllm-sr/d3-flash,
vllm-sr/auto, vllm-sr/auto), HOST (127.0.0.1), PORT (8601), DB (data/receipts.db), UPLOAD_ROOT (upload files, one folder
per visitor; default <temp dir>/receipt-intake-uploads), PLAY_ALL (1: show Play all; the Space sets 0), HEALTH_INTERVAL
(60 s between availability checks), TRUST_PROXY (0; 1 behind a reverse proxy such as a Hugging Face Space: per-IP limits
use the client address from X-Forwarded-For), WARM_UP (0; 1 sends one D3 call per demo image at start, for a local
Router). Limits (docs/API.md): UPLOADS_PER_MIN (6 per visitor), UPLOADS_PER_MIN_IP (20), MAX_UPLOADS (20 per visitor),
MAX_UPLOAD_GB (2), MAX_VISITORS (500), IDLE_MIN (60), CLEANUP_S (300); model calls, timeouts and the circuit breaker:
see app/gate.py.
"""
import io
import ipaddress
import itertools
import json
import queue
import mimetypes
import re
import os
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

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
import gate  # noqa: E402
import health  # noqa: E402
import keys  # noqa: E402
import pipeline  # noqa: E402
import visitors  # noqa: E402

keys.install_redaction()  # nothing key-shaped reaches the log, whatever prints it

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8601"))
WEB, DEMO = "web", "data/demo"
MAX_BYTES = 15 * 1024 * 1024
PING_SECONDS = 2  # keep-alive interval on /api/check; the page treats a long silence as a lost connection
ENV = os.environ.get
PLAY_ALL = ENV("PLAY_ALL", "1") == "1"
LANDING_URL = ENV("LANDING_URL", "")  # the page shows "All demos" back to it when set
os.makedirs(os.path.dirname(os.path.abspath(ENV("DB", db.PATH))), exist_ok=True)
CON = db.connect(ENV("DB", db.PATH))
VIS = visitors.Visitors(
    CON, ENV("UPLOAD_ROOT") or os.path.join(tempfile.gettempdir(), "receipt-intake-uploads"),
    idle_s=float(ENV("IDLE_MIN", "60")) * 60, max_uploads=int(ENV("MAX_UPLOADS", "20")),
    max_bytes=float(ENV("MAX_UPLOAD_GB", "2")) * 1024 ** 3, max_visitors=int(ENV("MAX_VISITORS", "500")),
    per_visitor_min=int(ENV("UPLOADS_PER_MIN", "6")), per_ip_min=int(ENV("UPLOADS_PER_MIN_IP", "20")))
KEYS = keys.Store(idle_s=float(ENV("IDLE_MIN", "60")) * 60, max_keys=2 * int(ENV("MAX_VISITORS", "500")))
VIS.on_purge = KEYS.forget
SEQ = itertools.count(int(time.time()) % 100000)
DECODE = threading.BoundedSemaphore(4)  # images decoded at once: a 48 MP photo is ~150 MB decoded
LABELS = json.load(open(f"{DEMO}/labels.json"))
EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
UPLOAD_LONG_SIDE = 1600  # an iPhone receipt photo read the same at 1600 px as at full size, with the LLM 2.3x faster
TRUST_PROXY = ENV("TRUST_PROXY", "0") == "1"
KEY_BODY_MAX = 4096


def origin_of(url):
    u = urlparse(url or "")
    return f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else None


# POST /api/key/check answers these origins (the landing page checks a key before handing it to a demo)
CORS_ORIGINS = {o for o in (origin_of(LANDING_URL), "http://localhost:8630", "http://127.0.0.1:8630") if o}
WARM_UP = ENV("WARM_UP", "0") == "1"


def open_image(raw, upload):
    """The decoded image (call under DECODE). A large upload JPEG is decoded at a reduced scale (PIL draft mode, a
    power-of-two step that still covers UPLOAD_LONG_SIDE), so a 48 MP photo never takes ~150 MB. Returns (image, size
    before draft)."""
    im = Image.open(io.BytesIO(raw))
    size = im.size
    if upload and im.format in ("JPEG", "MPO") and max(size) > UPLOAD_LONG_SIDE:
        f = UPLOAD_LONG_SIDE / max(size)
        im.draft("RGB", (max(1, int(size[0] * f)), max(1, int(size[1] * f))))
    im.load()
    return im, size


def prepare_upload(raw, im, size=None):
    """An upload as the models should see it, made once before D3. Phone photos are shrunk to UPLOAD_LONG_SIDE (a 24 MP
    iPhone photo is over the API's image limits, and the LLM's time grows with pixels); iPhone JPEGs are MPO (the photo
    plus an HDR gain-map image) and HEIC is the iPhone default; phones often store the photo sideways with an EXIF
    rotation tag that a model may not apply. Such uploads become one upright JPEG; small plain JPEG, PNG and WebP files
    pass through unchanged."""
    if (im.format in ("JPEG", "PNG", "WEBP") and im.getexif().get(0x0112, 1) == 1
            and max(size or im.size) <= UPLOAD_LONG_SIDE):
        return raw, Image.MIME[im.format]
    im.seek(0)
    out = ImageOps.exif_transpose(im).convert("RGB")
    out.thumbnail((UPLOAD_LONG_SIDE, UPLOAD_LONG_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


def demo_list():
    return [{"file": l["file"], "group": l["group"], "label": l["label"], "expected_route": l["expected_route"],
             "image_url": f"api/demo/{l['file']}"} for l in LABELS]


def multipart_image(ctype, body):
    """The 'image' field of a multipart/form-data body: (bytes, filename). Parsed by hand: the email parser holds
    about ten copies of a photo while it works."""
    m = re.search(r'boundary="?([^";]+)"?', ctype)
    if not m:
        raise ValueError("no multipart boundary")
    for part in body.split(b"--" + m.group(1).encode())[1:-1]:
        head, sep, data = part.partition(b"\r\n\r\n")
        disposition = next((l for l in head.decode("utf-8", "replace").split("\r\n")
                            if l.lower().startswith("content-disposition:")), "")
        if sep and re.search(r';\s*name="image"', disposition):
            name = re.search(r'filename="([^"]*)"', disposition)
            return data.removesuffix(b"\r\n"), os.path.basename(name.group(1) if name else "") or "upload"
    raise ValueError("no 'image' field")


def config_body():
    return {"play_all": PLAY_ALL, "max_upload_mb": MAX_BYTES // (1024 * 1024), "uploads_per_min": VIS.per_visitor_min,
            "landing_url": LANDING_URL, "key_required": keys.REQUIRED, "key_url": keys.KEY_URL,
            "models": {"d3": check.MODEL, "extract": pipeline.EXTRACT_MODEL, "message": pipeline.MESSAGE_MODEL},
            "timeouts_s": {"d3": gate.TIMEOUT_S["d3"], "extract": gate.TIMEOUT_S["extract"], "message": gate.TIMEOUT_S["retake"]}}


def metrics_body():
    return {**VIS.metrics(), "model_calls": gate.queued(), "model_429_503": gate.STATS["retries_429"],
            "model_busy": gate.STATS["busy"], "model_failed": gate.STATS["failed"],
            "model_skipped_down": gate.STATS["skipped_down"], "upload_disk_bytes": VIS.disk_bytes()}


def read_image(ctype, body, file):
    """(raw, mime, size) of a request body, decoded at most 4 at a time; raises Refused with a plain message."""
    upload = ctype.startswith("multipart/form-data")
    with DECODE:
        try:
            im, size = open_image(body, upload)
        except Exception:  # noqa: BLE001
            raise Refused(f"{file} is not an image the demo can read. Use a JPEG, PNG, WebP or HEIC photo.") from None
        if upload:
            raw, mime = prepare_upload(body, im, size)
            if raw is not body:
                size = Image.open(io.BytesIO(raw)).size
        else:
            raw, mime = body, Image.MIME.get(im.format)
    if mime not in EXT:
        raise Refused(f"{file} is a {im.format} image. Use a JPEG, PNG, WebP or HEIC photo.")
    return raw, mime, size


def client_ip_from(forwarded, peer):
    """The client address in X-Forwarded-For headers, read from the right: proxies append the address they got the
    request from, so the rightmost public address is the one our proxy saw; addresses a client wrote itself are to its
    left and are skipped. Private and loopback hops (the proxy's own network) are passed over. Falls back to the peer."""
    hops = [h.strip() for line in forwarded for h in line.split(",") if h.strip()]
    for hop in reversed(hops):
        try:
            ip = ipaddress.ip_address(hop.split("%")[0])
        except ValueError:
            continue
        if not (ip.is_private or ip.is_loopback or ip.is_link_local):
            return str(ip)
    for hop in hops:  # only private hops (a test or an internal network): the first one
        try:
            return str(ipaddress.ip_address(hop))
        except ValueError:
            continue
    return peer


class Refused(Exception):
    """An upload the demo cannot use (415); the message is shown as is."""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def log_request(self, code="-", size="-"):
        if isinstance(code, int) and code < 400:
            return  # each page polls /health; uploads get their own line (see do_POST)
        super().log_request(code, size)

    def visitor(self):
        """The caller's visitor id (X-Visitor-Id, UUID-shaped), or None."""
        v = visitors.valid(self.headers.get("X-Visitor-Id"))
        if v:
            VIS.touch(v)
        return v

    def client_ip(self):
        """The caller's IP for the per-IP limit. Without TRUST_PROXY: the peer address (X-Forwarded-For is ignored, as
        anyone can send it). With TRUST_PROXY=1 (behind the Hugging Face proxy, where every peer address is the proxy):
        client_ip_from()."""
        if TRUST_PROXY:
            return client_ip_from(self.headers.get_all("X-Forwarded-For") or [], self.client_address[0])
        return self.client_address[0]

    def _json(self, code, body, headers=None):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, val in (headers or {}).items():
            self.send_header(k, val)
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path, cache="no-cache"):
        if not path or not os.path.isfile(path):
            return self._json(404, {"error": "not found"})
        data = open(path, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def cors(self):
        """CORS headers for POST /api/key/check: the request's Origin when it is the landing page, else none."""
        o = self.headers.get("Origin")
        h = {"Vary": "Origin"}
        if o in CORS_ORIGINS:
            h.update({"Access-Control-Allow-Origin": o, "Access-Control-Allow-Methods": "POST, OPTIONS",
                      "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "600"})
        return h

    def do_OPTIONS(self):
        if urlparse(self.path).path != "/api/key/check":
            return self.refuse(404, "not found")
        self.send_response(204)
        for k, val in self.cors().items():
            self.send_header(k, val)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def key_state(self, v):
        """The caller's key for /health: set (their own), server (the server key serves them), missing."""
        if v and KEYS.has(v):
            return "set"
        return "server" if keys.ENV_KEY else "missing"

    def do_GET(self):
        p = unquote(urlparse(self.path).path)
        if p == "/healthz":
            return self._json(200, {"ok": True})
        if p == "/health":
            health.wait_ready()
            return self._json(200, {**health.state(), "key": self.key_state(self.visitor())})
        if p == "/api/key":
            return self._json(200, KEYS.state(self.visitor()))
        if p == "/api/config":
            return self._json(200, config_body())
        if p == "/api/metrics":
            return self._json(200, metrics_body())
        if p == "/api/demo":
            return self._json(200, demo_list())
        if p.startswith("/api/demo/"):
            name = os.path.basename(p)
            return self._file(os.path.join(DEMO, name), "max-age=3600") if name.endswith(".jpg") else self._json(404, {})
        if p.startswith("/api/uploads/"):  # only the visitor who uploaded it gets the file
            return self._file(VIS.path(self.visitor(), os.path.basename(p)), "private, max-age=3600")
        if p == "/api/stats":
            return self._json(200, pipeline.stats(CON, self.visitor()))
        if p == "/api/receipts":
            v = self.visitor()
            return self._json(200, db.recent_receipts(CON, v) if v else [])
        if p == "/api/rejections":
            v = self.visitor()
            return self._json(200, db.recent_rejections(CON, v) if v else [])
        if p.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        # static UI; unknown paths fall back to index.html
        path = os.path.normpath(os.path.join(WEB, p.lstrip("/")))
        if not path.startswith(WEB) or not os.path.isfile(path):
            path = os.path.join(WEB, "index.html")
        if not os.path.isfile(path):
            return self._json(200, {"ok": True, "note": "UI not built yet; see docs/UI.md"})
        return self._file(path, "no-cache" if path.endswith(".html") else "max-age=31536000")

    def refuse(self, code, message):
        """An error before the body is read: the connection is closed, so the unread body is never taken for a request."""
        self.close_connection = True
        return self._json(code, {"error": message})

    def do_DELETE(self):
        if urlparse(self.path).path != "/api/key":
            return self.refuse(404, "not found")
        v = self.visitor()
        if v:
            KEYS.forget(v)
        return self._json(200, {"ok": True, "set": False})

    def key_body(self):
        """The "key" of a small JSON body, or None (the connection is closed when the body is not read)."""
        try:
            n = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            n = -1
        if n < 0 or n > KEY_BODY_MAX:
            self.close_connection = True
            return None
        try:
            return json.loads(self.rfile.read(n) or b"{}").get("key")
        except Exception:  # noqa: BLE001
            return None

    def post_key(self, p):
        """POST /api/key/check (validate only; CORS for the landing page) and POST /api/key (validate and keep for this
        visitor). The key is never logged or echoed."""
        cors = self.cors() if p == "/api/key/check" else {}
        v = self.visitor()
        if p == "/api/key" and not v:
            return self.refuse(400, "Missing visitor id (X-Visitor-Id). Reload the page.")
        key = self.key_body()
        if not KEYS.allow_check(v, self.client_ip()):
            return self._json(*keys.failure("too_many"), headers=cors)
        status, body = keys.check_key(key)
        print(f"key {'check' if p == '/api/key/check' else 'set'}"
              f"{f' v={v[:8]}' if v else ''}: {body.get('code') or 'ok'}", flush=True)
        if status == 200 and p == "/api/key":
            KEYS.put(v, keys.shape(key)[0])
            body = {"ok": True, "set": True}
        return self._json(status, body, headers=cors)

    def do_POST(self):
        p = urlparse(self.path).path
        if p in ("/api/key/check", "/api/key"):
            return self.post_key(p)
        if p not in ("/api/reset", "/api/check"):
            return self.refuse(404, "not found")
        v = self.visitor()
        if not v:
            return self.refuse(400, "Missing visitor id (X-Visitor-Id). Reload the page.")
        if p == "/api/reset":
            if v in VIS.running:
                return self.refuse(409, "A check is running in this tab. Stop it first.")
            VIS.reset(v)
            return self._json(200, {"ok": True})
        key = KEYS.get(v)
        if not key and keys.REQUIRED:  # nothing runs without the visitor's key
            msg = keys.message("no_key")
            self.close_connection = True
            return self._json(401, {"ok": False, "code": "no_key", "error": msg, "message": msg})
        why = health.preflight()  # D3 or the reading LLM down: refuse before storing anything or calling a model
        if why:
            return self.refuse(503, why)
        try:
            n = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            n = -1
        if n < 0:
            return self.refuse(411, "Content-Length required")
        if n > MAX_BYTES + 100_000:
            return self.refuse(413, "image too large (15 MB max)")
        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith(("application/json", "multipart/form-data")):
            return self.refuse(415, "send multipart/form-data with field 'image', or JSON {\"demo\": file}")
        demo = ctype.startswith("application/json")
        no_count = PLAY_ALL and demo  # local Play all runs the whole demo set in a minute or two
        why = VIS.admit(v, self.client_ip(), count=not no_count)
        if why:
            return self.refuse(*why)
        started = False
        try:
            body = self.rfile.read(n)
            if demo:
                name = os.path.basename(json.loads(body)["demo"])
                path = os.path.join(DEMO, name)
                if not os.path.isfile(path) or not name.endswith(".jpg"):
                    return self._json(404, {"error": f"no demo image {name}"})
                body, file = open(path, "rb").read(), name
            else:
                body, file = multipart_image(ctype, body)
            raw, mime, size = read_image(ctype, body, file)
            uid = f"{db.DEMO_PREFIX}{next(SEQ)}" if demo else VIS.save(v, raw, EXT[mime])
            started = True
        except Refused as e:
            return self._json(415, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            print(f"upload not readable: {e!r}", flush=True)
            return self._json(400, {"error": "Could not read the upload. Send a JPEG, PNG, WebP or HEIC photo."})
        finally:
            if not started:
                VIS.done(v)
        self.stream(v, uid, file, raw, mime, size, key)

    def stream(self, v, uid, file, raw, mime, size, key=None):
        """Runs the pipeline in a worker thread and streams its events (SSE), with keep-alive pings."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        sha = db.sha256(raw)
        gone = threading.Event()  # set when the client stops the run or the connection drops
        events = queue.Queue()
        out = {"route": "error", "ms": 0.0, "refused": 0}  # for the log line

        def work():
            gate.local.refused = 0
            gate.local.key = key  # every model call of this run carries the visitor's key (None: the server key)
            gate.local.on_retry = lambda model, status, wait: events.put(
                ("retry", {"model": model, "status": status, "wait_s": round(wait, 1)}))
            t = time.perf_counter()
            try:
                for item in pipeline.run(CON, uid, file, raw, mime, size, sha, cancelled=gone.is_set, visitor_id=v):
                    if item[0] == "route":
                        out["route"] = item[1]["route"]
                    if item[0] == "error" and item[1].get("reason") == "key":
                        KEYS.fail(v, item[1]["code"])  # the next action asks for a key again
                    events.put(item)
            except Exception:  # noqa: BLE001
                print(f"run failed:\n{traceback.format_exc()}", flush=True)
                events.put(("error", {"step": "server", "reason": "error", "detail": "The server failed",
                                      "message": "Something went wrong on the server. Please try again.", "skipped": []}))
            finally:
                out.update(ms=(time.perf_counter() - t) * 1000, refused=gate.local.refused)
                gate.local.on_retry = None
                VIS.done(v)  # the visitor can start the next run only when this one has really ended
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
                if event == "error":
                    out["route"] = f"error:{data['step']}"
                chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()
            try:
                self.wfile.write(chunk)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                gone.set()  # stopped or disconnected: the run ends after its current step and nothing is stored
        state = " stopped" if gone.is_set() else ""
        print(f"upload v={v[:8]} {'demo' if uid.startswith(db.DEMO_PREFIX) else 'file'} route={out['route']}{state} "
              f"{out['ms']:.0f} ms 429s={out['refused']}", flush=True)


def warm_up():
    """WARM_UP=1 only (a local Router; on the hosted API it would spend 24 calls at every start). One D3 call per demo
    image shape, so the first live upload does not pay for kernel warm-up. It sends a re-encoded copy of each image:
    the Router caches D3 image features by content, and caching the real demo bytes here would make every live D3 time
    a cache hit."""
    t = time.perf_counter()
    for l in LABELS:
        try:
            im = Image.open(os.path.join(DEMO, l["file"])).convert("RGB"); im.putpixel((0, 0), (1, 2, 3))
            buf = io.BytesIO(); im.save(buf, "JPEG", quality=90)
            check.ask_d3(buf.getvalue())
        except Exception as e:  # noqa: BLE001
            print("warm-up failed:", repr(e), flush=True); return
    print(f"warm-up done in {time.perf_counter() - t:.1f} s", flush=True)


if __name__ == "__main__":
    threading.Thread(target=health.loop, daemon=True).start()
    threading.Thread(target=VIS.loop, args=(float(ENV("CLEANUP_S", "300")),), daemon=True).start()
    threading.Thread(target=KEYS.loop, args=(float(ENV("CLEANUP_S", "300")),), daemon=True).start()
    if WARM_UP:
        threading.Thread(target=warm_up, daemon=True).start()
    print(f"receipt intake demo on http://{HOST}:{PORT} (vllm-sr API: {check.ROUTER_URL}, D3 {check.MODEL}, "
          f"LLM {pipeline.EXTRACT_MODEL} / {pipeline.MESSAGE_MODEL}, proxy trusted: {TRUST_PROXY}, server key: "
          f"{'on' if keys.ENV_KEY else 'off'}, visitor key required: {keys.REQUIRED})", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
