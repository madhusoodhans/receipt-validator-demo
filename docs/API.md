# Receipt intake demo: HTTP API (contract between the pipeline and the UI)

Served by `app/server.py` on `127.0.0.1:8601` (env `HOST`, `PORT`), same origin as the built UI (`web/`). All JSON is UTF-8.
D3 and the LLM are called on the vllm-sr API at `ROUTER_URL` (default the hosted API, `https://app.vllm-sr.ai`): D3
(`D3_MODEL`, default `vllm-sr/d3-flash`) on `/v1/systemone`, the LLM on `/v1/chat/completions` with `EXTRACT_MODEL` and
`MESSAGE_MODEL` (default `vllm-sr/auto` for both: the vllm-sr Router picks the model and its reasoning per request).
Every model call carries `Authorization: Bearer <key>` with the key of the visitor whose run it is (see "API key"
below), else the optional server key `VLLM_SR_API_KEY`; availability checks are public GETs and send no key. LLM calls also send `X-Vsr-Debug: true`, so the Router reports what it picked in response headers
(`x-vsr-selected-model`, `x-vsr-selected-decision`, `x-vsr-selected-reasoning`); the `message` and `extract` events
carry them as `routed`.

## Visitors

Every browser tab is one visitor: the page makes a random UUID (`crypto.randomUUID`, kept in `sessionStorage`) and
sends it as `X-Visitor-Id` on every request. The server accepts only UUID-shaped ids. A visitor sees only their own
runs: `GET /api/receipts`, `/api/rejections`, `/api/stats` and `/api/uploads/<id>` are scoped to the caller, and
`POST /api/reset` clears only the caller's rows and files. A missing or unknown id gets empty lists and zero totals;
`POST /api/check` and `POST /api/reset` refuse it with 400. The demo images are shared and read-only.

Upload files are stored on disk under `UPLOAD_ROOT/<visitor id>/` (default `<temp dir>/receipt-intake-uploads`) with
random ids. A run on a demo image stores no file (its rows point at `/api/demo/<file>`).

Limits (env, default): one run at a time per visitor (409); `UPLOADS_PER_MIN` (6) checks a minute per visitor and
`UPLOADS_PER_MIN_IP` (20) per IP (the peer address; with `TRUST_PROXY=1`, behind a reverse proxy such as a Hugging
Face Space, the rightmost public address in `X-Forwarded-For`, so addresses a client writes itself are skipped), refused with 429 and a
plain `error` before any model call (with `PLAY_ALL=1`, the local default, demo images are not counted, so Play all
runs); `MAX_UPLOADS` (20) uploads kept per visitor, oldest dropped with their rows (and 100 runs in all);
`MAX_UPLOAD_GB` (2) of upload files in all, least recently active visitors removed first; `MAX_VISITORS` (500), least
recently active removed first. A cleanup thread every `CLEANUP_S` (300) s removes visitors idle for `IDLE_MIN` (60)
minutes, rows and files.

Model calls (`app/gate.py`): at most `MODEL_CONCURRENCY` (12) in flight per API (D3 `/v1/systemone`, chat completions),
up to `MODEL_QUEUE` (24) more waiting; a call that waits over `MODEL_WAIT_S` (15) s, or finds the queue full, fails its
step with `{"step": "d3", "message": "The service is busy, please try again."}`. A 429 or 503 answer is retried once
after its `Retry-After` when that is at most 5 s, then fails the same way; a longer `Retry-After` is not retried. Either
way, calls of that kind wait out a short `Retry-After` and fail as busy at once, without a request, during a long one.

Model availability (`app/gate.py`, `app/health.py`): each model role (`d3`; `extract`, the LLM that reads receipts;
`retake`, the LLM that writes retake messages) is checked every `HEALTH_INTERVAL` (60) s with free GETs (no model
call, no quota), and real calls feed a circuit breaker: `BREAKER_FAILS` (3) failures in a row (no connection, timeout,
HTTP 5xx, or 503 twice) mark it down for `BREAKER_S` (30) s, during which its calls fail at once without a request; any
successful call or check marks it up. Read timeouts, all under the hosted API's 35 s limit: `D3_TIMEOUT_S` (15),
`MESSAGE_TIMEOUT_S` (20), `EXTRACT_TIMEOUT_S` (30); connect 5 s.

## API key

Each visitor brings their own vllm-sr API key (from <https://app.vllm-sr.ai/#key>). The server checks it, keeps it in
memory for that visitor only (dropped on `DELETE /api/key`, when a run fails because of it, when the visitor is idle
for `IDLE_MIN` or removed), and sends it on every model call that visitor's runs make. It is never written to disk, the
database, an event or the log: the log output passes through a filter that replaces anything matching
`sk-(vsr|pt)-[A-Za-z0-9_-]+` with `sk-***`. `KEY_REQUIRED` (auto: required unless `VLLM_SR_API_KEY` is set or
`ROUTER_URL` is on localhost).

Check: the key is trimmed and must look like `sk-vsr-...` (8 to 200 letters, digits, `_` or `-` after the prefix; a
`sk-pt-` key works for chat only and is refused). Then one tiny System One call (`vllm-sr/d3-flash`, state "ok", one
noul question) and one tiny System Two call (`vllm-sr/auto`, "Say OK", `max_tokens` 1) run in parallel; both must
succeed. At most `KEY_CHECKS_PER_MIN` (10) checks a minute per visitor and per IP.

Failure body: `{"ok": false, "code": "...", "message": "..."}` (`message` is plain words, shown as is):

| code | HTTP | when |
|---|---|---|
| `format` | 400 | not a `sk-vsr-` key |
| `wrong_kind` | 400 | a `sk-pt-` key ("This demo needs a key from app.vllm-sr.ai: sk-pt keys only work for chat.") |
| `invalid` | 401 | the API answered 401 |
| `disabled` | 403 | the API answered 403 (`key_disabled`, `access_denied`, `model_not_allowed`) |
| `quota` | 429 | 429 `quota_exceeded` (the message says it resets at 00:00 UTC) |
| `rate_limited` | 429 | another 429; `retry_after` (seconds) added |
| `too_many` | 429 | over `KEY_CHECKS_PER_MIN` |
| `unreachable` | 502 | no connection, timeout (20 s), HTTP 5xx |
| `error` | 502 | anything else |

- `POST /api/key/check` `{"key": "..."}`: checks only, stores nothing, needs no visitor id. 200 `{"ok": true}`. CORS
  for this endpoint only (OPTIONS preflight included): the origin of `LANDING_URL`, `http://localhost:8630` and
  `http://127.0.0.1:8630` get `Access-Control-Allow-Origin`.
- `POST /api/key` `{"key": "..."}` with `X-Visitor-Id`: checks, then keeps it for this visitor. 200 `{"ok": true,
  "set": true}`.
- `GET /api/key`: `{"set": false, "required": true, "failed": null}`; never the key or any part of it. `failed` is
  `{"code", "message"}` when the key was dropped because a run failed with it.
- `DELETE /api/key`: forgets it, `{"ok": true, "set": false}`.

Without a key (when one is required) `POST /api/check` is refused before anything runs: 401 `{"ok": false, "code":
"no_key", "error": "Enter your vllm-sr API key to run the demo.", "message": "..."}`.

## POST /api/check

Runs one image through the pipeline and streams each step as Server-Sent Events.

Request, one of:
- `multipart/form-data` with field `image` (JPEG, PNG, WebP or HEIC, up to 15 MB; the page shrinks larger JPEG, PNG and
  WebP photos, or any over 1600 px on the long side, to a 1600 px JPEG before sending), or
- `application/json` `{"demo": "<file>"}` for an image from the demo set (`GET /api/demo`).

Before anything else (no file stored, no model called) the server checks availability: while D3 or the reading LLM is
down the request is refused with 503 `{"error": "Receipt checking is unavailable right now. Please try again in a
minute."}`. The retake-message LLM being down does not stop uploads (see `message`).

Response: `text/event-stream`. Events arrive in this order; `message` and `extract` are mutually exclusive.

| Event | When | Data |
|---|---|---|
| `received` | at once | `{"id": "3f9c0a1b2c3d4e5f6a7b", "file": "IMG_2041.jpg", "width": 1200, "height": 1600, "image_url": "/api/uploads/3f9c0a1b2c3d4e5f6a7b", "size": {"key": "size", "label": "Image too small to read", "px": 1386, "status": "ok", "threshold": 180, "borderline_from": 240, "ms": 0.01}}` (a demo image: `"id": "demo-…"`, `"image_url": "/api/demo/<file>"`; `size` is the size check run before D3, no model, the same object as `d3.size`) |
| `retry` | a model call was refused with 429/503 and is tried once more (0 or more times, any point) | `{"model": "extract", "status": 429, "wait_s": 1.0}` (`model`: d3, extract or retake; the run goes on) |
| `d3` | D3 answered | see below |
| `route` | routing rule applied | `{"route": "extract" \| "review" \| "reject", "reasons": [Reason], "summary": "Blurry text"}` |
| `message` | route `reject`: the LLM wrote the message | `{"text": "...", "model": "vllm-sr/auto", "routed": {"model": "qwen/qwen3.8-27b", "decision": "fast", "reasoning": "off"}, "ms": 610, "tokens": 190, "completion_tokens": 38, "reasoning_tokens": 0}` (`model` is the model asked for, `routed` what the Router picked (each `null` when the API does not say), `tokens` the total, `completion_tokens` what the LLM wrote, reasoning included); when the LLM is down or fails: `{"text": "This photo cannot be used: the text is blurry. Please hold the phone steady and let it focus, then try again.", "model": "template", "ms": 2.1, "tokens": null, "fallback": "standard message (LLM unavailable)"}` (`timed out` or `busy` in place of `unavailable` when that is why); the run goes on and is stored |
| `extract` | route `extract`/`review`: the LLM read it | `{"receipt": Receipt, "model": "vllm-sr/auto", "routed": {"model": "qwen/qwen3.8-27b", "decision": "vision", "reasoning": "on"}, "ms": 9800, "prompt_tokens": 1500, "completion_tokens": 760, "reasoning_tokens": 370}` |
| `sumcheck` | after `extract` | `{"ok": true, "checks": [{"name": "items_sum", "label": "Items add up to the subtotal", "ok": true, "detail": "25,000 + 38,000 = 63,000"}]}` |
| `stored` | row written | `{"table": "receipts" \| "rejections", "id": 12, "needs_review": 0}` |
| `done` | end | `{"total_ms": 3900, "steps": {"d3": 260, "extract": 3400}}` (`steps` keys: `d3`, `message` or `extract`, `sumcheck`, `db`) |
| `error` | any failure, then the stream ends; nothing is stored | `{"step": "extract", "reason": "timeout", "detail": "The LLM did not answer within 30 s", "message": "Your receipt passed the checks, but reading is unavailable right now. Please try again shortly.", "skipped": ["sumcheck", "stored"]}` |

A model call refused because of the key (HTTP 401, 403, or 429 with `quota_exceeded`) is not retried and does not
feed the breaker; the run ends with `{"step": "d3" | "extract" | "message", "reason": "key", "code": "invalid" |
"disabled" | "quota", "detail": "The LLM call was refused: the key's daily quota is used up", "message": "Your API key
has used its daily quota, so the run stopped. The quota resets at 00:00 UTC; or enter another key.", "skipped":
["stored"]}`. This includes the retake message: a key error there is never replaced by the standard message. The
server then forgets the key (`GET /api/key` shows `failed`), so the next upload gets 401 `no_key`.

`error`: every field is plain words, shown as is; model responses and exception text are only written to the server
log. `reason` is `key` (above), `unavailable` (no connection, HTTP 5xx, or marked down), `timeout`, `busy` (no slot, or refused twice
with 429/503), `too_long` or `error`; `detail` says in one line which model failed and how; `skipped` lists the steps
that did not run (`route`, `model` (the LLM read or message), `sumcheck`, `stored`). `message` per step: D3
"Receipt checking is unavailable right now. Please try again in a minute."; reading "Your receipt passed the checks,
but reading is unavailable right now. Please try again shortly."; busy at any step "The service is busy, please try
again."

`d3` data:

```json
{"model": "vllm-sr/d3-flash", "ms": 380, "tokens": 4032,
 "kind": {"choice": "receipt", "probabilities": {"receipt": 0.98, "invoice": 0.01, "other_document": 0.0, "several": 0.0, "screenshot": 0.0, "photo": 0.01}},
 "checks": [
   {"key": "complete", "label": "Total cut off or hidden", "prob": 0.95, "bad_when": "low", "threshold": 0.35, "borderline_from": 0.70, "status": "ok"},
   {"key": "blurry",   "label": "Blurry text",           "prob": 0.72, "bad_when": "low",  "threshold": 0.19, "borderline_from": 0.37, "status": "ok"},
   {"key": "damaged",  "label": "Torn or missing piece",  "prob": 0.12, "bad_when": "high", "threshold": 0.70, "borderline_from": 0.55, "status": "ok"},
   {"key": "glare",    "label": "Glare or heavy shadow",  "prob": 0.20, "bad_when": "high", "threshold": 0.83, "borderline_from": 0.54, "status": "ok"},
   {"key": "added_text", "label": "Added note or text",   "prob": 0.05, "bad_when": "high", "threshold": 0.73, "borderline_from": 0.55, "status": "ok"}
 ],
 "size": {"key": "size", "label": "Image too small to read", "px": 1058, "status": "ok", "threshold": 180, "borderline_from": 240}}
```

`size` is the size gate run before D3 (`app/readable.py`, no model); a `bad` or `borderline` size is also a route
reason (`{"key": "size", ..., "px": 150}`; its `prob` is px / borderline_from). A `kind_doubt` reason (borderline)
means the kind is an invoice that may be a budget, statement or quote (p(other_document) >= 0.30), or another document
that may still be an invoice (p(invoice) >= 0.20); the image is read and stored for review. When the kind is
`several` (more than one receipt in the photo) the reject reason has the key `multiple`.

`status` is `ok`, `borderline` or `bad`. The list of checks and thresholds may change during tuning; the UI renders
whatever comes back (one probability bar per check, a marker at `threshold`).

`Reason`: `{"key": "blurry", "label": "Blurry text", "prob": 0.94, "status": "bad"}`; for a wrong kind:
`{"key": "kind", "label": "Screenshot, not a receipt", "prob": 0.92, "status": "bad"}`.

`Receipt` (the LLM's output, JSON schema enforced with `response_format`):

```json
{"store_name": "Berghotel Grosse Scheidegg", "date": "2007-07-30", "currency": "CHF",
 "items": [{"name": "Latte Macchiato", "quantity": 2, "unit_price": 4.5, "amount": 9.0}],
 "subtotal": null, "tax": 3.85, "service_charge": null, "discount": null, "total_label": "Total", "total": 54.5}
```

`total` and `total_label` are null when no total line is visible (for example a photo cut off above it); the sum check
then fails `total_read` and the receipt is stored with `needs_review`. A total read from a subtotal line, or with no
total line label, fails `total_line`.

A receipt too long for the LLM's 4,096 output tokens (the reply was cut off, or its JSON does not parse) ends with
`{"step": "extract", "message": "This receipt is too long for the demo: the LLM hit its 4,096-token output limit after
reading about 70 items. Receipts up to about 60 items are supported."}` (the item count is left out when none was read).

**Keep-alive, timeouts and cancelling.** While a run is in progress the server sends an SSE comment line
(`: ping`) every 2 s, so a client can tell a slow step from a lost connection (the UI gives up after 120 s without a
byte). Model calls fail fast when a service is unreachable (5 s connect timeout) and give up after 15 s (D3), 20 s (the
retake message) or 30 s (reading); a D3 or reading failure ends the stream with an `error` event, a message failure uses
the standard message. If the client closes the connection
(the UI's Stop button, or a closed page), the server cancels the run after its current step and stores nothing.

**Request errors (JSON `{"error": "..."}`, before the stream starts).** 400 no or malformed `X-Visitor-Id`; 409 a run
of this visitor is in progress; 411 no `Content-Length`; 413 over 15 MB (checked from `Content-Length` before the body
is read); 401 `no_key`; 415 not an image the demo can read; 429 per-minute limit; 503 D3 or the reading LLM unavailable. At most 4 images are decoded at once; large JPEGs
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

`{"play_all": true, "max_upload_mb": 15, "uploads_per_min": 6, "landing_url": "", "key_required": true,
"key_url": "https://app.vllm-sr.ai/#key", "models": {"d3": "vllm-sr/d3-flash",
"extract": "vllm-sr/auto", "message": "vllm-sr/auto"}, "timeouts_s": {"d3": 15, "extract": 30, "message": 20}}` (`models`: the model names the server asks for; `timeouts_s`: how long the server waits for each model's answer). `play_all` is env `PLAY_ALL`
(default 1; the Space sets 0 and the page hides Play all; single demo images still run). `landing_url` is env
`LANDING_URL` (default empty): when set, the top bar links back to it ("All demos", with `?theme=light|dark`).
`key_required`: whether the page must ask for a key (see "API key").

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

Each model role's availability, returned at once (the first request after startup waits up to 8 s for the first check):

```json
{"ok": false, "status": "degraded", "message": "Standard retake messages in use: LLM unavailable",
 "d3": true, "extract": true, "retake": false, "detail": {"retake": "unavailable"}, "checked_at": "2026-11-05T05:00:00Z",
 "key": "missing"}
```

`key` is the caller's key state, separate from model availability (the checks need no key): `set` (their own key),
`server` (the server key serves them) or `missing`.

`status`: `ok` (all up), `degraded` (only the retake-message LLM down: uploads run, rejected images get the standard
message) or `down` (D3 or the reading LLM down: uploads are refused); `message` is the badge text for it ("All systems
working", "Standard retake messages in use: LLM unavailable", "Receipt checking unavailable"). A role is up unless its
last check failed or its circuit breaker is open. `detail` has an entry only for roles that are down, in a few words:
from a check `unreachable`, `timed out`, `not served` (the API does not list the model), `down` (HTTP 5xx) or `error`;
from the breaker the reason of the last failed call (`unavailable`, `timeout`, `busy`).

A background thread checks every `HEALTH_INTERVAL` (60) s with two GETs and no model call, so visitors cost no quota:
`GET /v1/models` must list `D3_MODEL`; `GET /pt/api/info` (System Two) must list `EXTRACT_MODEL` and `MESSAGE_MODEL`
(a local Router without that path: `GET /v1/models`). A model that answers these but fails real calls is marked down by
the circuit breaker.
