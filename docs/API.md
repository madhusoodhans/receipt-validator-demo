# Receipt intake demo: HTTP API (contract between the pipeline and the UI)

Served by `app/server.py` on `127.0.0.1:8601` (env `HOST`, `PORT`), same origin as the built UI (`web/`). All JSON is UTF-8.
D3 and both Qwen models are called through the vllm-sr Router at `ROUTER_URL` (default `http://localhost:8899`).

## POST /api/check

Runs one image through the pipeline and streams each step as Server-Sent Events.

Request, one of:
- `multipart/form-data` with field `image` (JPEG, PNG, WebP or HEIC, up to 15 MB; the page shrinks larger JPEG, PNG and
  WebP photos, or any over 1600 px on the long side, to a 1600 px JPEG before sending), or
- `application/json` `{"demo": "<file>"}` for an image from the demo set (`GET /api/demo`).

Response: `text/event-stream`. Events arrive in this order; `message` and `extract` are mutually exclusive.

| Event | When | Data |
|---|---|---|
| `received` | at once | `{"id": "u17", "file": "13_blurry.jpg", "width": 480, "height": 640, "image_url": "/api/uploads/u17"}` |
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
"routed_ms": 260.5, "all_27b_ms": 3300.0, "routed_tokens": 2755, "d3_tokens": 2564, "all_27b_tokens": 1524}`. `routed_ms` is D3 plus the 4B message (rejected) or D3 plus the 27B read
(accepted). `all_27b_ms` is the same 27B read for an accepted image, or the 27B's measured time on this image for a
rejected one (`data/demo/baseline.json`, or measured in the background for a new upload); it is `null` until measured,
and `GET /api/baseline/<image_sha256>` returns `{"ms": 1500.2 | null, "tokens": 1524 | null, "measuring": bool}` to poll
for it. Tokens are prompt plus completion; `routed_tokens` includes D3's input tokens (`d3_tokens`), which can exceed the
27B's own, since D3 encodes the image at a higher token count. The panel shows
the last image on its own; during Play all it sums the images of that Play all. `GET /api/stats.comparison` (all distinct
images since the last reset) is kept for reports and is not shown on the page.

**Keep-alive, timeouts and cancelling.** While a run is in progress the server sends an SSE comment line
(`: ping`) every 2 s, so a client can tell a slow step from a lost connection (the UI gives up after 12 s without a
byte). Model calls fail fast when a service is unreachable (3 s connect timeout) and give up after 20 s (D3), 30 s (4B)
or 120 s (27B), each ending the stream with an `error` event in plain words. If the client closes the connection
(the UI's Stop button, or a closed page), the server cancels the run after its current step and stores nothing.

## GET /api/demo

The demo set, in play order: `[{"file": "01_good_berghotel.jpg", "group": "good" | "bad", "label": "Swiss hotel bar receipt",
"expected_route": "extract" | "reject", "image_url": "/api/demo/01_good_berghotel.jpg"}]`.

## GET /api/demo/<file>, GET /api/uploads/<id>

The image bytes.

## GET /api/stats

Totals derived from the database on every request, so they survive a server restart and match `GET /api/receipts` and
`GET /api/rejections` (cleared by `POST /api/reset`):

```json
{"uploads": 21, "extracted": 12, "needs_review": 0, "rejected": 9,
 "by_problem": {"blurry": 4, "complete": 3, "glare": 3, "kind": 3, "damaged": 2, "multiple": 1},
 "money_total_by_currency": {"CHF": 54.5, "RM": 12.0, "$": 63.38, "IDR": 17000.0, "Rp": 38000.0},
 "comparison": {"images": 21, "pending_baseline": 0, "repeats": 0,
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
  run, and is left out of both sides (counted in `pending_baseline`) until then.

Both are wall-clock seconds of model calls (request sent to answer received), not measured GPU-seconds. 27B calls use a
fresh vLLM `cache_salt`, so a repeated image is not sped up by prefix caching; the Router does cache D3 image features, so a
repeated image answers D3 faster (one reason repeats are left out).

## GET /api/receipts

Latest first, up to 50: `[{"id": 12, "file": "...", "image_url": "...", "created_at": "2026-10-16T09:10:00Z",
"needs_review": 0, "receipt": Receipt, "sumcheck_ok": true, "d3": {...same as the d3 event}}]`.

## GET /api/rejections

Latest first, up to 50: `[{"id": 4, "file": "...", "image_url": "...", "created_at": "...", "reasons": [Reason],
"message": "...", "d3": {...}}]`.

## POST /api/reset

Clears the receipts and rejections (and so the totals). Measured 27B baseline times are kept. `{"ok": true}`.

## GET /health

The server's last health probe, returned at once: `{"ok": false, "router": true, "d3": true, "qwen27b": false,
"qwen4b": true, "detail": {"qwen27b": "image input off"}, "checked_at": "2026-10-26T05:00:00Z"}`. `detail` has an entry
only for failures (`down`, `image input off`, `not served by the Router`, `no answer within 20 s`, `HTTP 500`, ...).

A background thread probes at startup and every 60 s, all through the Router: `router` = `GET /v1/models`; `d3` = a
generated 64x64 image with one question through `/v1/systemone`; `qwen27b` = the same image through
`/v1/chat/completions` with `max_tokens` 1 (fails as `image input off` when the 27B was started without image input);
`qwen4b` = text, `max_tokens` 1. A round is skipped (the last result kept) while a pipeline run or baseline measurement
is in progress, so probes never overlap a timed run. The first request after startup waits up to 8 s for the first round.
