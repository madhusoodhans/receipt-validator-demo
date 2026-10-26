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

- Top bar: health (GET /health every 10 s, the server's last probe; names a failure plainly, such as "Vision off on
  Qwen 27B" or "D3 down"; tooltip lists each service), Reset (POST /api/reset, inline confirm), theme switch.
- Demo strip (GET /api/demo, grouped good / bad). Click one image, or Play all: runs them in order with a 1.8 s pause; Stop
  ends the queue after the image in flight. A dot on each thumbnail shows the route it took (green extract, amber review, red reject).
- Upload drop zone (JPEG, PNG, WebP or HEIC, up to 60 MB), sent as multipart field `image`. A JPEG, PNG or WebP over
  15 MB or over 1600 px on the long side is decoded in the browser (EXIF rotation applied) and re-encoded as a JPEG at
  quality 0.9 and 1600 px long side, keeping the file name (`src/shrink.ts`). HEIC cannot be decoded in Chrome: sent as
  is up to 15 MB, refused with a plain error above that.
- The current image, and the steps as SSE events arrive (POST /api/check read with fetch and a stream reader):
  time to answer (from `done.total_ms`), D3 (kind with probability, one bar per check with a threshold marker, the bad zone
  tinted, ok / borderline / bad), route (three lanes, the taken one filled, with reasons), then the 4B message styled as an
  in-app notice, or the extracted receipt, sum checks and stored row id. Pending steps show a live timer. On wide screens
  check and result sit in two columns; the newest step scrolls into view.
- Side panel: one heading, "Inference stats for this request" (or for the images of the current Play all), and four
  boxes in the same layout (the saving as headline, then "Routed with D3" and "Everything on Qwen 27B" bars, a note), all
  from that request: Inference time (wall clock of each model call through the Router; in Play all the note says the
  saving depends on the share of bad uploads, and the demo set's share), LLM tokens (Qwen prompt plus completion; D3's
  image tokens in the note), Dollars per million requests like this one (inference hours x `gpu_price`), and Inference
  hours per million requests like this one (both measured one request at a time, so an upper bound). A
  `measurement_warning`, when present, shows as a small warning line at the top.
  Then uploads / extracted / needs review / rejected, rejections by problem, and Receipts / Rejections tabs (latest 50;
  the tab counts are the database totals). All refresh after each run ends, and every 3 s while a new rejected upload
  is being timed on the 27B.
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
