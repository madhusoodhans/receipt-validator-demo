# Receipt intake demo: HTTP API (contract between the pipeline and the UI)

Served by `app/server.py` on `127.0.0.1:8601` (env `HOST`, `PORT`), same origin as the built UI (`web/`). All JSON is UTF-8.
D3 and both Qwen models are called through the vllm-sr Router at `ROUTER_URL` (default `http://localhost:8899`; with
`ROUTER_API_KEY` set, every call and probe carries `Authorization: Bearer <key>`).

## Visitors

Every browser tab is one visitor: the page makes a random UUID (`crypto.randomUUID`, kept in `sessionStorage`) and
sends it as `X-Visitor-Id` on every request. The server accepts only UUID-shaped ids. A visitor sees only their own
runs: `GET /api/receipts`, `/api/rejections`, `/api/stats` and `/api/uploads/<id>` are scoped to the caller, and
`POST /api/reset` clears only the caller's rows and files. A missing or unknown id gets empty lists and zero totals;
`POST /api/check` and `POST /api/reset` refuse it with 400. The demo images are shared and read-only.

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

Model availability (`app/gate.py`, `app/health.py`): each model (`d3`, `qwen27b`, `qwen4b`) is probed every
`HEALTH_INTERVAL` (30) s, and real calls feed a circuit breaker: `BREAKER_FAILS` (3) failures in a row (no connection,
timeout, HTTP 5xx, or 503 twice) mark it down for `BREAKER_S` (30) s, during which its calls fail at once without a
request; any successful call or probe marks it up. Read timeouts: `D3_TIMEOUT_S` (10), `MESSAGE_TIMEOUT_S` (15, Qwen
4B), `EXTRACT_TIMEOUT_S` (60, Qwen 27B); connect 3 s.

## POST /api/check

Runs one image through the pipeline and streams each step as Server-Sent Events.

Request, one of:
- `multipart/form-data` with field `image` (JPEG, PNG, WebP or HEIC, up to 15 MB; the page shrinks larger JPEG, PNG and
  WebP photos, or any over 1600 px on the long side, to a 1600 px JPEG before sending), or
- `application/json` `{"demo": "<file>"}` for an image from the demo set (`GET /api/demo`).

Before anything else (no file stored, no model called) the server checks availability: while D3 or Qwen 27B is down the
request is refused with 503 `{"error": "Receipt checking is unavailable right now. Please try again in a minute."}`.
Qwen 4B being down does not stop uploads (see `message`).

Response: `text/event-stream`. Events arrive in this order; `message` and `extract` are mutually exclusive.

| Event | When | Data |
|---|---|---|
| `received` | at once | `{"id": "3f9c0a1b2c3d4e5f6a7b", "file": "IMG_2041.jpg", "width": 1200, "height": 1600, "image_url": "/api/uploads/3f9c0a1b2c3d4e5f6a7b"}` (a demo image: `"id": "demo-…"`, `"image_url": "/api/demo/<file>"`) |
| `d3` | D3 answered | see below |
| `route` | routing rule applied | `{"route": "extract" \| "review" \| "reject", "reasons": [Reason], "summary": "Blurry text"}` |
| `message` | route `reject`: 4B wrote the message | `{"text": "...", "model": "qwen4b", "ms": 410, "tokens": 190}`; when the 4B is down or fails: `{"text": "This photo cannot be used: the text is blurry. Please hold the phone steady and let it focus, then try again.", "model": "template", "ms": 2.1, "tokens": null, "fallback": "standard message (Qwen 4B unavailable)"}` (`timed out` or `busy` in place of `unavailable` when that is why); the run goes on and is stored |
| `extract` | route `extract`/`review`: 27B read it | `{"receipt": Receipt, "model": "qwen27b", "ms": 3400, "prompt_tokens": 1165, "completion_tokens": 200}` |
| `sumcheck` | after `extract` | `{"ok": true, "checks": [{"name": "items_sum", "label": "Items add up to the subtotal", "ok": true, "detail": "25,000 + 38,000 = 63,000"}]}` |
| `stored` | row written | `{"table": "receipts" \| "rejections", "id": 12, "needs_review": 0}` |
| `done` | end | `{"total_ms": 3900, "steps": {"d3": 260, "extract": 3400}}` (`steps` keys: `d3`, `message` or `extract`, `sumcheck`, `db`) |
| `error` | any failure, then the stream ends; nothing is stored | `{"step": "extract", "reason": "timeout", "detail": "Qwen 27B did not answer within 60 s", "message": "Your receipt passed the checks, but reading is unavailable right now. Please try again shortly.", "skipped": ["sumcheck", "stored"]}` |

`error`: every field is plain words, shown as is; model responses and exception text are only written to the server
log. `reason` is `unavailable` (no connection, HTTP 5xx, or marked down), `timeout`, `busy` (no slot, or refused twice
with 429/503), `too_long` or `error`; `detail` says in one line which model failed and how; `skipped` lists the steps
that did not run (`route`, `model` (the 27B read or the 4B message), `sumcheck`, `stored`). `message` per step: D3
"Receipt checking is unavailable right now. Please try again in a minute."; Qwen 27B "Your receipt passed the checks,
but reading is unavailable right now. Please try again shortly."; busy at any step "The service is busy, please try
again."

`d3` data:

```json
{"model": "vllm-sr/d3-mini", "ms": 260, "tokens": 2564,
 "kind": {"choice": "receipt", "probabilities": {"receipt": 0.98, "invoice": 0.01, "other_document": 0.0, "screenshot": 0.0, "photo": 0.01}},
 "checks": [
   {"key": "complete", "label": "Whole receipt visible", "prob": 0.91, "bad_when": "low",  "threshold": 0.15, "status": "ok"},
   {"key": "blurry",   "label": "Blurry text",           "prob": 0.08, "bad_when": "high", "threshold": 0.6,  "status": "ok"},
   {"key": "damaged",  "label": "Torn, crumpled or stained", "prob": 0.12, "bad_when": "high", "threshold": 0.6, "status": "ok"},
   {"key": "glare",    "label": "Glare or heavy shadow",  "prob": 0.20, "bad_when": "high", "threshold": 0.45, "status": "ok"},
   {"key": "multiple", "label": "More than one receipt",  "prob": 0.02, "bad_when": "high", "threshold": 0.5,  "status": "ok"},
   {"key": "added_text", "label": "Added note or text",   "prob": 0.05, "bad_when": "high", "threshold": 0.8,  "status": "ok"}
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

**Keep-alive, timeouts and cancelling.** While a run is in progress the server sends an SSE comment line
(`: ping`) every 2 s, so a client can tell a slow step from a lost connection (the UI gives up after 120 s without a
byte). Model calls fail fast when a service is unreachable (3 s connect timeout) and give up after 10 s (D3), 15 s (4B)
or 60 s (27B); a D3 or 27B failure ends the stream with an `error` event, a 4B failure uses the standard message. If the client closes the connection
(the UI's Stop button, or a closed page), the server cancels the run after its current step and stores nothing.

**Request errors (JSON `{"error": "..."}`, before the stream starts).** 400 no or malformed `X-Visitor-Id`; 409 a run
of this visitor is in progress; 411 no `Content-Length`; 413 over 15 MB (checked from `Content-Length` before the body
is read); 415 not an image the demo can read; 429 per-minute limit; 503 D3 or Qwen 27B unavailable. At most 4 images are decoded at once; large JPEGs
are decoded at a reduced scale (PIL draft mode) before the 1600 px resize.

## GET /api/demo

The demo set, in play order: `[{"file": "01_good_berghotel.jpg", "group": "good" | "bad", "label": "Swiss hotel bar receipt",
"expected_route": "extract" | "review" | "reject", "image_url": "/api/demo/01_good_berghotel.jpg"}]`.

## GET /api/demo/<file>, GET /api/uploads/<id>

The image bytes. An upload is served only with its owner's `X-Visitor-Id` (404 otherwise), so the page fetches it with
the header and shows an object URL; demo images are public.

## GET /api/stats

The caller's totals ("your uploads this session"), derived from the database on every request, matching their
`GET /api/receipts` and `GET /api/rejections` (cleared by their `POST /api/reset`):

```json
{"uploads": 21, "extracted": 12, "needs_review": 0, "rejected": 9,
 "by_problem": {"blurry": 4, "complete": 3, "glare": 3, "kind": 3, "damaged": 2, "multiple": 1},
 "money_total_by_currency": {"CHF": 54.5, "RM": 12.0, "$": 63.38, "IDR": 17000.0, "Rp": 38000.0}}
```

Counts are per stored run (`uploads` = `extracted` + `rejected`; `needs_review` is part of `extracted`; a failed run is not
stored and not counted). `by_problem` counts each rejection under its bad reasons (only `kind` when it is not a receipt),
so one rejection can count under several problems.

## GET /api/receipts

The caller's, latest first, up to 50: `[{"id": 12, "file": "...", "image_url": "...", "created_at": "2026-10-16T09:10:00Z",
"needs_review": 0, "receipt": Receipt, "sumcheck_ok": true, "d3": {...same as the d3 event}}]`.

## GET /api/rejections

The caller's, latest first, up to 50: `[{"id": 4, "file": "...", "image_url": "...", "created_at": "...", "reasons": [Reason],
"message": "...", "d3": {...}}]`.

## POST /api/reset

Clears the caller's receipts, rejections and upload files (and so their totals); other visitors are untouched.
`{"ok": true}`.

## GET /api/config

`{"play_all": true, "max_upload_mb": 15, "uploads_per_min": 6, "landing_url": ""}`. `play_all` is env `PLAY_ALL`
(default 1; the Space sets 0 and the page hides Play all; single demo images still run). `landing_url` is env
`LANDING_URL` (default empty): when set, the top bar links back to it ("All demos", with `?theme=light|dark`).

## GET /api/metrics

For operators, no per-visitor data: `{"visitors": 30, "active_5min": 12, "running": 3, "uploads_last_hour": 180,
"model_calls": {"d3": {"running": 1, "waiting": 0}, "chat": {"running": 2, "waiting": 0}}, "model_429_503": 0,
"model_busy": 0, "model_failed": 0, "model_skipped_down": 0, "upload_disk_bytes": 11206236}`. `model_429_503` counts
429/503 answers from model calls since start, `model_busy` the calls that failed as busy, `model_failed` the calls that
failed otherwise (no connection, timeout, HTTP error), `model_skipped_down` the calls not made because the model was
marked down.

The server logs one line per upload, without image content: `upload v=<first 8 of the visitor id> demo|file
route=<route or error:step> <ms> ms 429s=<n>`. Successful GETs are not logged.

## GET /health

Each model's availability, returned at once (the first request after startup waits up to 8 s for the first probes):

```json
{"ok": false, "status": "degraded", "message": "Standard retake messages in use: Qwen 4B unavailable",
 "d3": true, "qwen27b": true, "qwen4b": false, "detail": {"qwen4b": "unreachable"}, "checked_at": "2026-11-04T05:00:00Z"}
```

`status`: `ok` (all up), `degraded` (only Qwen 4B down: uploads run, rejected images get the standard message) or `down`
(D3 or Qwen 27B down: uploads are refused); `message` is the badge text for it ("All systems working", "Standard retake
messages in use: Qwen 4B unavailable", "Receipt checking unavailable"). A model is up unless its last probe failed or
its circuit breaker is open. `detail` has an entry only for models that are down, in a few words: from a probe
`unreachable`, `timed out`, `image input off` (the 27B was started without image input), `not served` (the Router does
not know the model), `down` (HTTP 5xx) or `error`; from the breaker the reason of the last failed call (`unavailable`,
`timeout`, `busy`).

A background thread probes every `HEALTH_INTERVAL` (30) s, the three models in parallel, all through `ROUTER_URL`:
`d3` = a generated 64x64 image with one question through `/v1/systemone`; `qwen27b` = the same image through
`/v1/chat/completions` with `max_tokens` 1; `qwen4b` = text, `max_tokens` 1. A 429 answer counts as up.
