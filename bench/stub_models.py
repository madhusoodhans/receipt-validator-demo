"""A fake Router for load tests: /v1/systemone (D3), /v1/chat/completions (qwen27b extraction JSON, qwen4b message),
/v1/models, with realistic delays and no GPU.

D3 answers come from results/d3_demo.json (real d3-mini answers on the demo set, matched by name); demo images not in it,
or whose old answers no longer give the expected route, get answers made from data/demo/labels.json. The image in a request is matched to a demo image by a 16x16 thumbnail,
so uploads of demo photos (re-encoded or shrunk) answer like the demo image; anything else answers as a good receipt.

  python bench/stub_models.py            # 127.0.0.1:8931
Env: STUB_PORT (8931), STUB_SPEED (1.0: delays x this), STUB_429 (0.0: share of model calls answered 429 with
Retry-After: 1), STUB_SLOW (0.0: share of calls 10x slower). POST /stub/config changes them live, plus per-model
failures (models d3, qwen27b, qwen4b; probes included):
  {"speed": 1.0, "p429": 0.0, "slow": 0.0,
   "down": ["qwen4b"],          # the connection is dropped without an answer, as with a stopped container
   "delay": {"qwen27b": 30},    # seconds added before answering (timeouts)
   "status": {"d3": 502}}       # answered with this HTTP status and a Router-style JSON error body
Each of down, delay and status replaces the previous value. GET /stub/stats returns calls, 429s and the most calls in
flight at once per endpoint.
"""
import base64
import io
import json
import os
import random
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "app"))
import check  # noqa: E402

DEMO = os.path.join(ROOT, "data/demo")
LABELS = json.load(open(os.path.join(DEMO, "labels.json")))
CONF = {"speed": float(os.environ.get("STUB_SPEED", "1")), "p429": float(os.environ.get("STUB_429", "0")),
        "slow": float(os.environ.get("STUB_SLOW", "0")), "down": [], "delay": {}, "status": {}}
STATS = {}
LOCK = threading.Lock()


def thumb(raw):
    im = Image.open(io.BytesIO(raw)).convert("L").resize((16, 16))
    return list(im.tobytes())


def made_answers(label):
    """D3-shaped answers for a demo image d3_demo.json does not have, from its expected route and problems."""
    p = {"complete": 0.95, "blurry": 0.05, "damaged": 0.05, "glare": 0.05, "multiple": 0.02, "added_text": 0.05}
    kind = "receipt"
    for prob in label.get("problems") or []:
        if prob == "kind":
            kind = "screenshot" if "screenshot" in label["file"] else "photo" if "photo" in label["file"] else "other_document"
        else:
            p[prob] = 0.08 if prob == "complete" else 0.85
    if label["expected_route"] == "review":
        p["blurry" if "focus" in label["file"] else "damaged"] = 0.5
    probs = {k: 0.01 for k in check.KIND["criteria"]}; probs[kind] = 0.95
    out = {"kind": {"type": "choice", "choice": kind, "probabilities": probs}}
    out.update({k: {"type": "noul", "noul": v} for k, v in p.items()})
    return out


def load_demo():
    real = {"_".join(x["file"].split("_")[1:]).removesuffix(".jpg"): x["answers"]
            for x in json.load(open(os.path.join(ROOT, "results/d3_demo.json")))}
    out = []
    for l in LABELS:
        key = "_".join(l["file"].split("_")[1:]).removesuffix(".jpg")
        raw = open(os.path.join(DEMO, l["file"]), "rb").read()
        answers = real.get(key)
        for k, (*_s, bad_when, _b, _t) in check.CHECKS.items():  # checks added since the file was made: clearly fine
            if answers and k not in answers:
                answers[k] = {"type": "noul", "noul": 0.95 if bad_when == "low" else 0.05}
        if not answers or check.decide(answers)[1]["route"] != l["expected_route"]:  # thresholds moved since
            answers = made_answers(l)
        out.append({"label": l, "answers": answers, "thumb": thumb(check.for_d3(raw, "image/jpeg")[0])})
    return out


DEMO_SET = load_demo()


def match(raw):
    t = thumb(raw)
    best = min(DEMO_SET, key=lambda d: sum(abs(a - b) for a, b in zip(t, d["thumb"])))
    return best if sum(abs(a - b) for a, b in zip(t, best["thumb"])) < 16 * 16 * 25 else None


def receipt_for(label):
    """A 27B-shaped receipt: the label's truth when it has one, else a small made-up one."""
    t = (label or {}).get("truth")
    if t:
        items = [{"name": f"Item {n + 1}", "quantity": 1, "unit_price": a, "amount": a} for n, a in enumerate(t["items"])]
        return {"store_name": t.get("store_name"), "date": t.get("date"), "currency": t.get("currency"), "items": items,
                "subtotal": t.get("subtotal"), "tax": t.get("tax"), "service_charge": None,
                "discount": t.get("discount"), "total": t["total"]}
    amounts = [25000.0, 38000.0, 12000.0]
    return {"store_name": "Stub Cafe", "date": None, "currency": "Rp",
            "items": [{"name": f"Menu {n + 1}", "quantity": 1, "unit_price": a, "amount": a} for n, a in enumerate(amounts)],
            "subtotal": None, "tax": None, "service_charge": None, "discount": None, "total": sum(amounts)}


def image_bytes(url):
    return base64.b64decode(url.split(",", 1)[1])


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, code, body, headers=None):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/v1/models":
            return self.send(200, {"data": [{"id": m} for m in (check.MODEL, "qwen27b", "qwen4b")]})
        if self.path == "/stub/stats":
            with LOCK:
                return self.send(200, {"config": CONF, "endpoints": STATS})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/stub/config":
            CONF.update({k: float(v) for k, v in body.items() if k in ("speed", "p429", "slow")})
            CONF.update({k: v for k, v in body.items() if k in ("down", "delay", "status")})
            with LOCK:
                STATS.clear()
            return self.send(200, CONF)
        if self.path == "/v1/systemone":
            name = "d3"
        elif self.path == "/v1/chat/completions":
            name = body.get("model", "chat")
        else:
            return self.send(404, {"error": "not found"})
        with LOCK:
            s = STATS.setdefault(name, {"calls": 0, "refused": 0, "in_flight": 0, "max_in_flight": 0})
            s["calls"] += 1; s["in_flight"] += 1; s["max_in_flight"] = max(s["max_in_flight"], s["in_flight"])
        try:
            if name in CONF["down"]:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)  # no answer at all: the client sees a dropped connection
                return
            if CONF["delay"].get(name):
                time.sleep(float(CONF["delay"][name]))
            if CONF["status"].get(name):
                code = int(CONF["status"][name])
                return self.send(code, {"error": {"message": f"upstream connect error or disconnect/reset before headers "
                                                             f"({name})", "type": "server_error", "code": code}})
            if random.random() < CONF["p429"]:
                with LOCK:
                    s["refused"] += 1
                return self.send(429, {"error": "rate limited"}, {"Retry-After": "1"})
            slow = 10 if random.random() < CONF["slow"] else 1
            if name == "d3":
                return self.d3(body, slow)
            if name == "qwen27b":
                return self.extract(body, slow)
            return self.message(body, slow)
        finally:
            with LOCK:
                s["in_flight"] -= 1

    def wait(self, mean_s, slow):
        time.sleep(max(0.01, random.gauss(mean_s, mean_s * 0.15)) * CONF["speed"] * slow)

    def d3(self, body, slow):
        imgs = [image_bytes(u) for u in body.get("images", [])]
        hit = match(imgs[0]) if imgs else None
        answers = hit["answers"] if hit else made_answers({"file": "upload", "expected_route": "extract"})
        answers = {k: v for k, v in answers.items() if k in body.get("questions", answers)}
        self.wait(0.26, slow)
        self.send(200, {"answers": answers, "usage": {"input_tokens": 2564, "output_tokens": 0}})

    def extract(self, body, slow):
        img = next(c["image_url"]["url"] for c in body["messages"][0]["content"] if c["type"] == "image_url")
        if body.get("max_tokens") == 1:  # a health probe
            return self.send(200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "length"}], "usage": {}})
        hit = match(image_bytes(img))
        text = json.dumps(receipt_for(hit and hit["label"]))
        self.wait(3.4, slow)
        self.send(200, {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 1165, "completion_tokens": 200, "total_tokens": 1365}})

    def message(self, body, slow):
        self.wait(0.41, slow)
        self.send(200, {"choices": [{"message": {"content": "The photo is hard to read. Retake it flat, in good light, "
                                                            "with the whole receipt in the frame."}, "finish_reason": "stop"}],
                        "usage": {"total_tokens": 190}})


if __name__ == "__main__":
    port = int(os.environ.get("STUB_PORT", "8931"))
    print(f"stub models on http://127.0.0.1:{port} ({len(DEMO_SET)} demo images)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
