# Receipt intake demo: web UI

One page, React 19 + Arc components, Vite 5. Source in `ui/`, built into `web/` (served by `app/server.py` on :8601).
Same setup as `../code-migration-demo/ui` (Node 18, so `ui/scripts/arc-add.mjs` stands in for the shadcn CLI).

## Run

```bash
cd ui
npm install                 # once

# Mock backend (whole docs/API.md contract, canned data, realistic delays) on 127.0.0.1:8603
npm run mock                # MOCK_FAST=1 npm run mock  -> every delay at a tenth

# Dev server on http://127.0.0.1:5174, proxies /api and /health
npm run dev                 # to the mock on :8603 (default)
API=live npm run dev        # to the real backend on :8601
API_TARGET=http://host:port npm run dev

npm run build               # tsc --noEmit, then vite build into ../web
```

The mock also serves `web/`, so after a build `http://127.0.0.1:8603/` runs the built page against the mock.

### Mock behaviour

- Demo set: `data/phase0/` with `labels.json` (`MOCK_DEMO_DIR` overrides). Good images route `extract` and their line items
  come from the CORD ground truth; `11_good_cord036.jpg` routes `review` (borderline glare); every bad image routes `reject`
  with the matching D3 check or wrong kind, and a canned 4B message.
- Uploads: a file name containing a problem word (`blurry`, `glare`, `damaged`, `multiple`, `two`, `cut`, `screenshot`, ...)
  is rejected for that problem; `error` in the name ends the stream with an `error` event; anything else cycles
  extract, reject (blurry), review, reject (glare).
- Delays: d3 about 260 ms, 4B message about 410 ms, 27B extraction about 3.4 s. Stats and lists are in memory; `POST /api/reset` clears them.

## Page

- Each tab is one visitor: `src/api.ts` makes a random id (kept in `sessionStorage`) and sends it as `X-Visitor-Id` on
  every request, so a tab sees only its own results and a new tab starts empty. Upload images (`/api/uploads/...`) are
  fetched with that header and shown as object URLs (`src/useImage.ts`); demo images load directly.

- Top bar: health (GET /health every 10 s): the server's `message` in plain words, green "All systems working", amber
  "Standard retake messages in use: Qwen 4B unavailable", red "Receipt checking unavailable" (or "Demo server offline"
  when /health itself fails); the tooltip lists each model with why it is down. Reset (POST /api/reset, inline confirm; clears this tab's
  results only), theme switch.
- Demo strip (GET /api/demo, grouped good / bad). Click one image, or Play all (shown only when GET /api/config says
  `play_all`; the hosted Space turns it off): runs them in order with a 1.8 s pause; Stop
  ends the queue after the image in flight. A dot on each thumbnail shows the route it took (green extract, amber review, red reject).
- Upload drop zone (JPEG, PNG, WebP or HEIC, up to 60 MB), sent as multipart field `image`. A JPEG, PNG or WebP over
  15 MB or over 1600 px on the long side is decoded in the browser (EXIF rotation applied) and re-encoded as a JPEG at
  quality 0.9 and 1600 px long side, keeping the file name (`src/shrink.ts`). HEIC cannot be decoded in Chrome: sent as
  is up to 15 MB, refused with a plain error above that.
- The current image, and the steps as SSE events arrive (POST /api/check read with fetch and a stream reader):
  time to answer (from `done.total_ms`), D3 (kind with probability, one bar per check with a threshold marker, the bad zone
  tinted, ok / borderline / bad), route (three lanes, the taken one filled, with reasons), then the 4B message styled as an
  in-app notice, or the extracted receipt, sum checks and stored row id. Pending steps show a live timer. A message
  from the fallback (Qwen 4B down) shows "standard message" as its model and an amber "fallback: standard message
  (Qwen 4B unavailable)" badge. A failed model step shows the step with a red reason badge (Unavailable, Timed out,
  Busy), the one-line `detail` and the `message`, then each step in `error.skipped` as a dashed "Skipped" step. An
  upload refused with 503 (D3 or Qwen 27B down) shows as "Receipt checking unavailable" with the server's message.
  On wide screens check and result sit in two columns; the newest step scrolls into view.
- Side panel: "Model stats for this request", from the current run's events: one row per model (D3, Qwen 27B,
  Qwen 4B) with its model id, time (a bar against the slowest of them) and tokens (D3: image tokens read; Qwen 4B:
  total; Qwen 27B: prompt in, completion out), or why it did not run ("Not needed: the image was rejected", "Not
  used: standard message (Qwen 4B unavailable)", the failure's detail, "Skipped", "Running"); then the model time in
  all. No comparison with an all-Qwen-27B flow and no cost figures.
  Then "Your uploads this session": uploads / extracted / needs review / rejected, rejections by problem, and Receipts /
  Rejections tabs (latest 50; the tab counts are this visitor's totals). All refresh after each run ends.
- Light and dark themes (stored in localStorage, else the system preference). Layout checked at 1920x1080, 1280x720 and 390 px.

## Arc components used

Installed with `node scripts/arc-add.mjs <ids>` into `ui/src/components/arc/`:
`arc-foundation` (tokens), `button`, `badge`, `alert`, `file-dropzone`, `theme-switch`, `animated-counter`, `tabs`,
`empty-state`, `text-shimmer`, `confirm-morph`, `tooltip`.
Probability bars, route lanes, the notice and the receipt table are page CSS on Arc's semantic tokens; motion uses
`components/arc/lib/motion-tokens.ts` presets, each with a reduced-motion branch.

## What the backend has to do

- Serve `web/index.html` at `/` and `web/assets/*` at `/assets/*` (the build uses relative `./assets/...` URLs, so a path prefix also works).
- Stream `/api/check` without buffering (`Content-Type: text/event-stream`, flush per event, no gzip on that route), each
  event as `event: <name>\ndata: <json>\n\n`.
- `image_url` values must be fetchable by the page (`/api/demo/<file>`, `/api/uploads/<id>`).
- The UI requests `api/...` and `health` relative to the page, so both must be on the page's origin.
