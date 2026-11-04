# Receipt intake demo: HTTP API (contract between the pipeline and the UI)

Served by `app/server.py` on `127.0.0.1:8601` (env `HOST`, `PORT`), same origin as the built UI (`web/`). All JSON is UTF-8.
D3 and both Qwen models are called through the vllm-sr Router at `ROUTER_URL` (default `http://localhost:8899`).

## Visitors

Every browser tab is one visitor: the page makes a random UUID (`crypto.randomUUID`, kept in `sessionStorage`) and
sends it as `X-Visitor-Id` on every request. The server accepts only UUID-shaped ids. A visitor sees only their own
runs: `GET /api/receipts`, `/api/rejections`, `/api/stats` and `/api/uploads/<id>` are scoped to the caller, and
`POST /api/reset` clears only the caller's rows and files. A missing or unknown id gets empty lists and zero totals;
`POST /api/check` and `POST /api/reset` refuse it with 400. The demo images and their 27B baseline times are shared and
read-only.

Upload files are stored on disk under `UPLOAD_ROOT/<visitor id>/` (default `<temp dir>/receipt-intake-uploads`) with
random ids. A run on a demo image stores no file (its rows point at `/api/demo/<file>`).

Limits (env, default): one run at a time per visitor (409); `UPLOADS_PER_MIN` (6) checks a minute per visitor and
`UPLOADS_PER_MIN_IP` (20) per IP (the first `X-Forwarded-For` hop when present, else the peer), refused with 429 and a
plain `error` before any model call (with `PLAY_ALL=1`, the local default, demo images are not counted, so Play all
runs); `MAX_UPLOADS` (20) uploads kept per visitor, oldest dropped with their rows (and 100 runs in all);
`MAX_UPLOAD_GB` (2) of upload files in all, least recently active visitors removed first; `MAX_VISITORS` (500), least
recently active removed first. A cleanup thread every `CLEANUP_S` (300) s removes visitors idle for `IDLE_MIN` (60)
minutes, rows and files.

Model calls (`app/gate.py`): at most `MODEL_CONCURRENCY` (12) in flight per API (D3 `/v1/systemone`, chat completions),
up to `MODEL_QUEUE` (24) more waiting; a call that waits over `MODEL_WAIT_S` (15) s, or finds the queue full, fails its
step with `{"step": "d3", "message": "The service is busy, please try again."}`. A 429 or 503 answer is retried once
after its `Retry-After` (at most 5 s), then fails the same way.

## POST /api/check

Runs one image through the pipeline and streams each step as Server-Sent Events.

Request, one of:
- `multipart/form-data` with field `image` (JPEG, PNG, WebP or HEIC, up to 15 MB; the page shrinks larger JPEG, PNG and
  WebP photos, or any over 1600 px on the long side, to a 1600 px JPEG before sending), or
- `application/json` `{"demo": "<file>"}` for an image from the demo set (`GET /api/demo`).

Response: `text/event-stream`. Events arrive in this order; `message` and `extract` are mutually exclusive.

| Event | When | Data |
|---|---|---|
| `received` | at once | `{"id": "3f9c0a1b2c3d4e5f6a7b", "file": "IMG_2041.jpg", "width": 1200, "height": 1600, "image_url": "/api/uploads/3f9c0a1b2c3d4e5f6a7b"}` (a demo image: `"id": "demo-…"`, `"image_url": "/api/demo/<file>"`) |
| `d3` | D3 answered | see below |
| `route` | routing rule applied | `{"route": "extract" \| "review" \| "reject", "reasons": [Reason], "summary": "Blurry text"}` |
| `message` | route `reject`: 4B wrote the message | `{"text": "...", "model": "qwen4b", "ms": 410, "tokens": 190}` |
| `extract` | route `extract`/`review`: 27B read it | `{"receipt": Receipt, "model": "qwen27b", "ms": 3400, "prompt_tokens": 1165, "completion_tokens": 200}` |
| `sumcheck` | after `extract` | `{"ok": true, "checks": [{"name": "items_sum", "label": "Items add up to the subtotal", "ok": true, "detail": "25,000 + 38,000 = 63,000"}]}` |
| `stored` | row written | `{"table": "receipts" \| "rejections", "id": 12, "needs_review": 0}` |
| `done` | end | `{"total_ms": 3900, "steps": {"d3": 260, "extract": 3400}}` (`steps` keys: `d3`, `message` or `extract`, `sumcheck`, `db`) |
| `error` | any failure, then the stream ends | `{"step": "extract", "message": "..."}` (`message` is plain words, shown as is) |

`d3` data:

```json
{"model": "vllm-sr/d3-mini", "ms": 260, "tokens": 2564,
 "kind": {"choice": "receipt", "probabilities": {"receipt": 0.98, "invoice": 0.01, "other_document": 0.0, "screenshot": 0.0, "photo": 0.01}},
 "checks": [
   {"key": "complete", "label": "Whole receipt visible", "prob": 0.91, "bad_when": "low",  "threshold": 0.15, "status": "ok"},
   {"key": "blurry",   "label": "Blurry text",           "prob": 0.08, "bad_when": "high", "threshold": 0.6,  "status": "ok"},
   {"key": "damaged",  "label": "Torn, crumpled or stained", "prob": 0.12, "bad_when": "high", "threshold": 0.6, "status": "ok"},
   {"key": "glare",    "label": "Glare or heavy shadow",  "prob": 0.20, "bad_when": "high", "threshold": 0.45, "status": "ok"},
   {"key": "multiple", "label": "More than one receipt",  "prob": 0.02, "bad_when": "high", "threshold": 0.5,  "status": "ok"}
 ]}
```

`status` is `ok`, `borderline` or `bad`. The list of checks and thresholds may change during tuning; the UI renders
whatever comes back (one probability bar per check, a marker at `threshold`).

`Reason`: `{"key": "blurry", "label": "Blurry text", "prob": 0.94, "status": "bad"}`; for a wrong kind:
`{"key": "kind", "label": "Screenshot, not a receipt", "prob": 0.92, "status": "bad"}`.

`Receipt` (27B output, JSON schema enforced):

```json
{"store_name": "Berghotel Grosse Scheidegg", "date": "2007-07-30", "currency": "CHF",
 "items": [{"name": "Latte Macchiato", "quantity": 2, "unit_price": 4.5, "amount": 9.0}],
 "subtotal": null, "tax": 3.85, "service_charge": null, "discount": null, "total": 54.5}
```

A receipt too long for the 27B's 4,096 output tokens (the reply was cut off, or its JSON does not parse) ends with
`{"step": "extract", "message": "This receipt is too long for the demo: Qwen 27B hit its 4,096-token output limit after
reading about 70 items. Receipts up to about 60 items are supported."}` (the item count is left out when none was read).

`done.comparison` (this image only, used by the Inference time and LLM tokens boxes): `{"image_sha256": "...", "route": "reject",
"routed_ms": 260.5, "all_27b_ms": 3300.0, "routed_tokens": 2755, "d3_tokens": 2564, "all_27b_tokens": 1524,
"baseline_pending": false}`. `routed_ms` is D3 plus the 4B message (rejected) or D3 plus the 27B read
(accepted). `all_27b_ms` is the same 27B read for an accepted image, or the 27B's measured time on this image for a
rejected one (`data/demo/baseline.json`, or measured in the background for a new upload when `MEASURE_BASELINE=1`, the
local default). It is `null` for a rejected upload without one: `baseline_pending` is `true` while it is being measured,
and `false` when the server does not measure uploads (`MEASURE_BASELINE=0`, as on the Space: the page then says "no
baseline"). `GET /api/baseline/<image_sha256>` returns `{"ms": 1500.2 | null, "tokens": 1524 | null, "measuring": bool}` to poll
for it. Tokens are prompt plus completion; `routed_tokens` includes D3's input tokens (`d3_tokens`), which can exceed the
27B's own, since D3 encodes the image at a higher token count. The panel shows
the last image on its own; during Play all it sums the images of that Play all. `GET /api/stats.comparison` (the visitor's distinct
images since their last reset) is kept for reports and is not shown on the page.

**Keep-alive, timeouts and cancelling.** While a run is in progress the server sends an SSE comment line
(`: ping`) every 2 s, so a client can tell a slow step from a lost connection (the UI gives up after 12 s without a
byte). Model calls fail fast when a service is unreachable (3 s connect timeout) and give up after 20 s (D3), 30 s (4B)
or 120 s (27B), each ending the stream with an `error` event in plain words. If the client closes the connection
(the UI's Stop button, or a closed page), the server cancels the run after its current step and stores nothing.

**Request errors (JSON `{"error": "..."}`, before the stream starts).** 400 no or malformed `X-Visitor-Id`; 409 a run
of this visitor is in progress; 411 no `Content-Length`; 413 over 15 MB (checked from `Content-Length` before the body
is read); 415 not an image the demo can read; 429 per-minute limit. At most 4 images are decoded at once; large JPEGs
are decoded at a reduced scale (PIL draft mode) before the 1600 px resize.

## GET /api/demo

The demo set, in play order: `[{"file": "01_good_berghotel.jpg", "group": "good" | "bad", "label": "Swiss hotel bar receipt",
"expected_route": "extract" | "reject", "image_url": "/api/demo/01_good_berghotel.jpg"}]`.

## GET /api/demo/<file>, GET /api/uploads/<id>

The image bytes. An upload is served only with its owner's `X-Visitor-Id` (404 otherwise), so the page fetches it with
the header and shows an object URL; demo images are public.

## GET /api/stats

The caller's totals ("your uploads this session"), derived from the database on every request, matching their
`GET /api/receipts` and `GET /api/rejections` (cleared by their `POST /api/reset`):

```json
{"uploads": 21, "extracted": 12, "needs_review": 0, "rejected": 9,
 "by_problem": {"blurry": 4, "complete": 3, "glare": 3, "kind": 3, "damaged": 2, "multiple": 1},
 "money_total_by_currency": {"CHF": 54.5, "RM": 12.0, "$": 63.38, "IDR": 17000.0, "Rp": 38000.0},
 "comparison": {"images": 21, "pending_baseline": 0, "no_baseline": 0, "repeats": 0,
                "time_ms": {"d3": 2378.4, "message": 1542.7, "extract": 46199.6, "baseline_27b": 17199.9},
                "model_seconds": {"routed": 50.12, "all_27b": 63.4}},
 "gpu_price": {"usd_per_hour": 2.59, "source": "DigitalOcean GPU Droplet, MI300X, on-demand", "checked": "2026-10-26"},
 "measurement_warning": "The Qwen 27B baseline was measured on ..., not this server's AMD Instinct MI300X; re-run bench/baseline_27b.py here."}
```

Counts are per stored run (`uploads` = `extracted` + `rejected`; `needs_review` is part of `extracted`; a failed run is not
stored and not counted). `by_problem` counts each rejection under its bad reasons (only `kind` when it is not a receipt),
so one rejection can count under several problems.

`gpu_price` is the price of one GPU hour: by default DigitalOcean's on-demand MI300X GPU Droplet price per GPU
(https://www.digitalocean.com/pricing/gpu-droplets); env `GPU_USD_PER_HOUR` overrides it (`source` then says so). The
page prices the inference time of the current request (or Play all) with it: inference hours per million requests like
it = average model time per request x 1e6 / 3600, one request at a time, so an upper bound (a busy server batches many
requests on the GPU at once).

`measurement_warning` is present only when `data/demo/baseline.json` does not record a `gpu`, or records a different
one than the server read from `rocm-smi` at startup. Left out when they match or the GPU cannot be read.

`comparison` is per distinct image, identified by content (SHA-256), using the first stored run of each image; later runs
of the same image are `repeats` and do not change it.
- `routed` = D3 + Qwen 4B (rejected images) + Qwen 27B (accepted images), as measured in those runs.
- `all_27b` = the same images sent straight to the 27B: the same 27B read for accepted images, plus, for rejected images,
  the 27B's measured time on that image (`baseline_27b`). Demo images have it in `data/demo/baseline.json`
  (`bench/baseline_27b.py`); a rejected upload outside the demo set is timed on the 27B once, in the background after its
  run, and is left out of both sides (counted in `pending_baseline`) until then. With `MEASURE_BASELINE=0` it is never
  timed and stays out of both sides (counted in `no_baseline`).

Both are wall-clock seconds of model calls (request sent to answer received), not measured GPU-seconds. 27B calls use a
fresh vLLM `cache_salt`, so a repeated image is not sped up by prefix caching; the Router does cache D3 image features, so a
repeated image answers D3 faster (one reason repeats are left out).

## GET /api/receipts

The caller's, latest first, up to 50: `[{"id": 12, "file": "...", "image_url": "...", "created_at": "2026-10-16T09:10:00Z",
"needs_review": 0, "receipt": Receipt, "sumcheck_ok": true, "d3": {...same as the d3 event}}]`.

## GET /api/rejections

The caller's, latest first, up to 50: `[{"id": 4, "file": "...", "image_url": "...", "created_at": "...", "reasons": [Reason],
"message": "...", "d3": {...}}]`.

## POST /api/reset

Clears the caller's receipts, rejections and upload files (and so their totals); other visitors are untouched.
Measured 27B baseline times are kept. `{"ok": true}`.

## GET /api/config

`{"play_all": true, "measure_baseline": true, "max_upload_mb": 15, "uploads_per_min": 6}`. `play_all` is env `PLAY_ALL`
(default 1; the Space sets 0 and the page hides Play all; single demo images still run). `measure_baseline` is env
`MEASURE_BASELINE` (default 1; the Space sets 0).

## GET /api/metrics

For operators, no per-visitor data: `{"visitors": 30, "active_5min": 12, "running": 3, "uploads_last_hour": 180,
"model_calls": {"d3": {"running": 1, "waiting": 0}, "chat": {"running": 2, "waiting": 0}}, "model_429_503": 0,
"model_busy": 0, "upload_disk_bytes": 11206236}`. `model_429_503` counts 429/503 answers from model calls since start,
`model_busy` the steps that failed as busy.

The server logs one line per upload, without image content: `upload v=<first 8 of the visitor id> demo|file
route=<route or error:step> <ms> ms 429s=<n>`. Successful GETs are not logged.

## GET /healthz

Liveness only, no model calls: `{"ok": true}`.

## GET /health

The server's last health probe, returned at once: `{"ok": false, "router": true, "d3": true, "qwen27b": false,
"qwen4b": true, "detail": {"qwen27b": "image input off"}, "checked_at": "2026-10-26T05:00:00Z"}`. `detail` has an entry
only for failures (`down`, `image input off`, `not served by the Router`, `no answer within 20 s`, `HTTP 500`, ...).

A background thread probes at startup and every 60 s, all through the Router: `router` = `GET /v1/models`; `d3` = a
generated 64x64 image with one question through `/v1/systemone`; `qwen27b` = the same image through
`/v1/chat/completions` with `max_tokens` 1 (fails as `image input off` when the 27B was started without image input);
`qwen4b` = text, `max_tokens` 1. A round is skipped (the last result kept) while any run is in progress, so probes rarely
overlap a timed run, unless the last result is over 5 minutes old. The first request after startup waits up to 8 s for
the first round.
