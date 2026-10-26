// Mock of the receipt-intake backend (docs/API.md) for UI development. Plain Node 18, no dependencies.
// Serves the whole contract on 127.0.0.1:8603 with canned answers and realistic delays; proxies nothing.
// Demo images come from data/phase0 (labels.json) unless MOCK_DEMO_DIR points elsewhere.
// Usage: node mock-server.mjs [--port 8603]   (MOCK_FAST=1 shortens every delay to a tenth)
import { createServer } from "node:http";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { extname, join, resolve } from "node:path";

const HERE = new URL(".", import.meta.url).pathname;
const ROOT = resolve(HERE, "..");
const DEMO_DIR = process.env.MOCK_DEMO_DIR || join(ROOT, "data/phase0");
const WEB_DIR = join(ROOT, "web");
const PORT = Number(process.argv[process.argv.indexOf("--port") + 1]) || Number(process.env.PORT) || 8603;
const FAST = process.env.MOCK_FAST ? 0.1 : 1;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms * FAST));
const jitter = (ms, spread = 0.15) => Math.round(ms * (1 - spread + Math.random() * 2 * spread));
const now = () => new Date().toISOString().replace(/\.\d+Z$/, "Z");

// ---------- demo set ----------
const labels = existsSync(join(DEMO_DIR, "labels.json")) ? JSON.parse(readFileSync(join(DEMO_DIR, "labels.json"), "utf8")) : [];
const PROBLEM = { // labels.json problem -> what the mock D3 reports
  blurry: { key: "blurry", label: "Blurry text" },
  incomplete: { key: "complete", label: "Whole receipt visible" },
  damaged: { key: "damaged", label: "Torn, crumpled or stained" },
  glare: { key: "glare", label: "Glare or heavy shadow" },
  multiple: { key: "multiple", label: "More than one receipt" },
  not_document: { kind: "photo", label: "Photo, not a receipt" },
  screenshot: { kind: "screenshot", label: "Screenshot, not a receipt" },
  wrong_document: { kind: "other_document", label: "Other document, not a receipt" },
};
const NICE = {
  blurry: "Blurry receipt", incomplete: "Bottom cut off", damaged: "Crumpled receipt", glare: "Glare on the paper",
  multiple: "Two receipts in one photo", not_document: "Photo of a dog", screenshot: "Chat screenshot", wrong_document: "Restaurant menu",
};
const REVIEW_FILE = "11_good_cord036.jpg"; // one good receipt the mock treats as borderline

const demo = labels.map((l) => {
  const good = l.route === "extract";
  return {
    file: l.file, group: good ? "good" : "bad",
    label: good ? `Indonesian cafe receipt ${l.cord_index ?? ""}`.trim() : NICE[l.problems[0]] || "Bad upload",
    expected_route: l.file === REVIEW_FILE ? "review" : l.route,
    image_url: `/api/demo/${l.file}`,
  };
});
const labelOf = Object.fromEntries(labels.map((l) => [l.file, l]));

// ---------- helpers ----------
function jpegSize(buf) { // width/height from the SOF marker, or null
  if (buf[0] !== 0xff || buf[1] !== 0xd8) return null;
  let i = 2;
  while (i < buf.length) {
    if (buf[i] !== 0xff) return null;
    const m = buf[i + 1], len = buf.readUInt16BE(i + 2);
    if (m >= 0xc0 && m <= 0xcf && m !== 0xc4 && m !== 0xc8 && m !== 0xcc) return { height: buf.readUInt16BE(i + 5), width: buf.readUInt16BE(i + 7) };
    i += 2 + len;
  }
  return null;
}
function pngSize(buf) { return buf.slice(1, 4).toString() === "PNG" ? { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) } : null; }
const sizeOf = (buf) => jpegSize(buf) || pngSize(buf) || { width: 0, height: 0 };
const num = (s) => (s == null ? null : Number(String(s).replace(/[^\d.-]/g, "").replace(/\.(?=\d{3}\b)/g, "")) || null);
const fmt = (n) => Math.round(n).toLocaleString("en-US");

function receiptFrom(label) {
  const gt = label?.gt || {};
  const menu = Array.isArray(gt.menu) ? gt.menu : gt.menu ? [gt.menu] : [];
  const items = menu.map((m) => {
    const qty = num(String(m.cnt ?? "1").replace(/x/i, "")) || 1;
    const amount = num(Array.isArray(m.price) ? m.price[0] : m.price) || 0;
    const name = Array.isArray(m.nm) ? m.nm.join(" ") : m.nm || "Item";
    return { name, quantity: qty, unit_price: num(m.unitprice) ?? Math.round(amount / qty), amount };
  });
  const sum = items.reduce((a, b) => a + b.amount, 0);
  const subtotal = num(gt.sub_total?.subtotal_price) ?? null;
  const tax = num(gt.sub_total?.tax_price) ?? null;
  const service = num(gt.sub_total?.service_price) ?? null;
  const discount = num(gt.sub_total?.discount_price) ?? null;
  const total = num(gt.total?.total_price) ?? sum;
  const stores = ["Kopi Kenangan", "Roti Bakar 88", "Bakmi GM", "Warung Sederhana", "Holland Bakery", "Solaria"];
  return {
    store_name: stores[(label?.cord_index ?? 0) % stores.length], date: `2019-0${1 + ((label?.cord_index ?? 0) % 9)}-1${(label?.cord_index ?? 0) % 10}`,
    currency: "IDR", items, subtotal, tax, service_charge: service, discount, total,
  };
}
function sumcheck(r) {
  const checks = [];
  const sum = r.items.reduce((a, b) => a + (b.amount || 0), 0);
  const base = r.subtotal ?? sum;
  if (r.subtotal != null) checks.push({ name: "items_sum", label: "Items add up to the subtotal", ok: Math.abs(sum - r.subtotal) < 1, detail: `${r.items.map((i) => fmt(i.amount)).join(" + ")} = ${fmt(sum)}` });
  else checks.push({ name: "items_sum", label: "Items add up to the total", ok: Math.abs(sum - r.total) < 1, detail: `${r.items.map((i) => fmt(i.amount)).join(" + ")} = ${fmt(sum)}` });
  for (const it of r.items) if (it.quantity > 1 && Math.abs(it.quantity * it.unit_price - it.amount) >= 1) {
    checks.push({ name: "line_math", label: `${it.name}: quantity times price`, ok: false, detail: `${it.quantity} x ${fmt(it.unit_price)} = ${fmt(it.quantity * it.unit_price)}, receipt says ${fmt(it.amount)}` });
  }
  const extras = (r.tax || 0) + (r.service_charge || 0) - (r.discount || 0);
  const expect = base + extras;
  checks.push({ name: "total", label: "Subtotal plus tax and charges equals the total", ok: Math.abs(expect - r.total) < 1, detail: `${fmt(base)}${r.tax ? ` + ${fmt(r.tax)} tax` : ""}${r.service_charge ? ` + ${fmt(r.service_charge)} service` : ""}${r.discount ? ` - ${fmt(r.discount)} discount` : ""} = ${fmt(expect)}, total ${fmt(r.total)}` });
  return { ok: checks.every((c) => c.ok), checks };
}

const CHECKS = [
  { key: "complete", label: "Whole receipt visible", bad_when: "low", threshold: 0.15 },
  { key: "blurry", label: "Blurry text", bad_when: "high", threshold: 0.6 },
  { key: "damaged", label: "Torn, crumpled or stained", bad_when: "high", threshold: 0.6 },
  { key: "glare", label: "Glare or heavy shadow", bad_when: "high", threshold: 0.45 },
  { key: "multiple", label: "More than one receipt", bad_when: "high", threshold: 0.5 },
];
const BORDER = 0.12; // within this of the threshold counts as borderline
function statusOf(c, p) {
  const bad = c.bad_when === "high" ? p >= c.threshold : p <= c.threshold;
  if (bad) return "bad";
  return Math.abs(p - c.threshold) < BORDER ? "borderline" : "ok";
}
const r2 = (x) => Math.round(x * 100) / 100;
const rnd = (a, b) => r2(a + Math.random() * (b - a));

function d3For(outcome) { // outcome: {route, problem?}
  const prob = Object.fromEntries(CHECKS.map((c) => [c.key, c.bad_when === "low" ? rnd(0.82, 0.97) : rnd(0.02, 0.18)]));
  let kind = "receipt";
  const p = outcome.problem && PROBLEM[outcome.problem];
  if (p?.kind) { kind = p.kind; for (const k in prob) prob[k] = CHECKS.find((c) => c.key === k).bad_when === "low" ? rnd(0.3, 0.6) : rnd(0.05, 0.3); }
  else if (p?.key) prob[p.key] = p.key === "complete" ? rnd(0.03, 0.1) : rnd(0.86, 0.97);
  if (outcome.route === "review") prob.glare = rnd(0.36, 0.42);
  const kinds = ["receipt", "invoice", "other_document", "screenshot", "photo"];
  const top = kind === "receipt" ? rnd(0.95, 0.99) : rnd(0.88, 0.96);
  const rest = (1 - top) / (kinds.length - 1);
  const probabilities = Object.fromEntries(kinds.map((k) => [k, k === kind ? top : r2(rest)]));
  return {
    model: "vllm-sr/d3-mini", ms: jitter(260),
    kind: { choice: kind, probabilities },
    checks: CHECKS.map((c) => ({ ...c, prob: prob[c.key], status: statusOf(c, prob[c.key]) })),
  };
}
function routeFor(d3) {
  if (d3.kind.choice !== "receipt") {
    const label = Object.values(PROBLEM).find((p) => p.kind === d3.kind.choice)?.label || "Not a receipt";
    return { route: "reject", reasons: [{ key: "kind", label, prob: d3.kind.probabilities[d3.kind.choice], status: "bad" }], summary: label };
  }
  const bad = d3.checks.filter((c) => c.status === "bad");
  if (bad.length) return { route: "reject", reasons: bad.map(({ key, label, prob, status }) => ({ key, label, prob, status })), summary: bad.map((c) => c.key === "complete" ? "Receipt cut off" : c.label).join(", ") };
  const border = d3.checks.filter((c) => c.status === "borderline");
  if (border.length) return { route: "review", reasons: border.map(({ key, label, prob, status }) => ({ key, label, prob, status })), summary: `Borderline: ${border.map((c) => c.label.toLowerCase()).join(", ")}` };
  return { route: "extract", reasons: [], summary: "Readable receipt" };
}
const MESSAGES = {
  blurry: "The text on this receipt is too blurry to read. Hold your phone steady, tap the screen to focus on the receipt, and take the photo again in good light.",
  complete: "Part of the receipt is cut off, so the total is missing. Step back a little so the whole receipt, from the store name to the total, fits in the frame.",
  damaged: "This receipt is crumpled, so some lines can't be read. Flatten it on a table, smooth out the folds, and take the photo again from straight above.",
  glare: "There's a bright reflection over part of the receipt. Tilt the receipt or move away from the lamp or window, then take the photo again.",
  multiple: "This photo has more than one receipt in it. Please upload one receipt per photo so each one gets its own entry.",
  photo: "This looks like a photo, not a receipt. Please take a picture of the paper receipt you want to file.",
  screenshot: "This is a screenshot, not a receipt. Please upload a photo of the paper receipt, or the receipt image itself.",
  other_document: "This looks like a menu, not a receipt. Please upload the receipt you got after paying.",
};

// ---------- state ----------
let state;
function reset() {
  state = { nextUpload: 1, uploads: new Map(), receipts: [], rejections: [], seq: { receipts: 0, rejections: 0 },
    stats: { uploads: 0, extracted: 0, needs_review: 0, rejected: 0, by_problem: {}, money: {} }, firstRun: new Map() };
}
reset();
let uploadCycle = 0;
// Like the real backend: the comparison counts the first run of each distinct image (here: by file name), and a
// rejected image costs the 27B a canned "measured" time (the real one reads data/demo/baseline.json).
function stats() {
  const s = state.stats, t = { d3: 0, message: 0, extract: 0, baseline_27b: 0 };
  for (const f of state.firstRun.values()) {
    t.d3 += f.d3; if (f.message != null) { t.message += f.message; t.baseline_27b += f.baseline; } else t.extract += f.extract;
  }
  return {
    uploads: s.uploads, extracted: s.extracted, needs_review: s.needs_review, rejected: s.rejected, by_problem: s.by_problem,
    money_total_by_currency: s.money,
    comparison: { images: state.firstRun.size, pending_baseline: 0, repeats: s.uploads - state.firstRun.size, time_ms: t,
      model_seconds: { routed: r2((t.d3 + t.message + t.extract) / 1000), all_27b: r2((t.extract + t.baseline_27b) / 1000) } },
  };
}

// ---------- pipeline ----------
async function runCheck(res, { file, bytes, outcome, imageUrl }) {
  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive", "X-Accel-Buffering": "no" });
  let closed = false;
  res.on("close", () => { closed = true; });
  const send = (event, data) => { if (!closed) res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`); };
  const t0 = Date.now();
  const id = `u${state.nextUpload++}`;
  state.uploads.set(id, bytes);
  const { width, height } = sizeOf(bytes);
  send("received", { id, file, width, height, image_url: imageUrl || `/api/uploads/${id}` });

  const d3 = d3For(outcome);
  await sleep(d3.ms);
  send("d3", d3);
  const route = routeFor(d3);
  await sleep(15);
  send("route", route);
  const steps = { d3: d3.ms };

  if (outcome.forceError) { await sleep(300); send("error", { step: "extract", message: "Qwen 27B did not answer within 30 s (mock error)" }); return res.end(); }

  if (route.route === "reject") {
    const ms = jitter(410);
    await sleep(ms);
    const key = route.reasons[0].key === "kind" ? d3.kind.choice : route.reasons[0].key;
    const text = MESSAGES[key] || "We couldn't read this photo. Please take it again with the whole receipt in view.";
    send("message", { text, model: "qwen4b", ms });
    steps.message = ms;
    state.stats.uploads++; state.stats.rejected++;
    if (!state.firstRun.has(file)) state.firstRun.set(file, { d3: d3.ms, message: ms, baseline: jitter(1900, 0.3) });
    const bad = route.reasons.filter((r) => r.status === "bad");
    for (const r of bad[0]?.key === "kind" ? bad.slice(0, 1) : bad) state.stats.by_problem[r.key] = (state.stats.by_problem[r.key] || 0) + 1;
    await sleep(8);
    const rid = ++state.seq.rejections;
    state.rejections.unshift({ id: rid, file, image_url: imageUrl || `/api/uploads/${id}`, created_at: now(), reasons: route.reasons, message: text, d3 });
    send("stored", { table: "rejections", id: rid, needs_review: 0 });
    steps.db = 8;
  } else {
    const ms = jitter(3400, 0.12);
    await sleep(ms);
    const receipt = receiptFrom(outcome.label);
    send("extract", { receipt, model: "qwen27b", ms, prompt_tokens: 1165, completion_tokens: 120 + receipt.items.length * 28 });
    steps.extract = ms;
    if (!state.firstRun.has(file)) state.firstRun.set(file, { d3: d3.ms, extract: ms });
    await sleep(4);
    const sc = sumcheck(receipt);
    send("sumcheck", sc);
    steps.sumcheck = 4;
    const review = route.route === "review" || !sc.ok ? 1 : 0;
    state.stats.uploads++; state.stats.extracted++;
    if (review) state.stats.needs_review++;
    state.stats.money[receipt.currency] = (state.stats.money[receipt.currency] || 0) + (receipt.total || 0);
    await sleep(8);
    const rid = ++state.seq.receipts;
    state.receipts.unshift({ id: rid, file, image_url: imageUrl || `/api/uploads/${id}`, created_at: now(), needs_review: review, receipt, sumcheck_ok: sc.ok, d3 });
    send("stored", { table: "receipts", id: rid, needs_review: review });
    steps.sumcheck = 4; steps.db = 8;
  }
  // Per-image comparison (docs/API.md, done.comparison); the mock uses a fixed 2.8 s 27B time for rejected images.
  const read = steps.extract != null;
  const comparison = { image_sha256: `mock-${file}`, route: route.route, routed_ms: (steps.d3 || 0) + (read ? steps.extract : steps.message || 0),
    all_27b_ms: read ? steps.extract : 2800 };
  send("done", { total_ms: Date.now() - t0, steps, comparison });
  res.end();
}

function outcomeForDemo(file) {
  const l = labelOf[file];
  if (!l) return { route: "extract" };
  if (file === REVIEW_FILE) return { route: "review", label: l };
  return l.route === "extract" ? { route: "extract", label: l } : { route: "reject", problem: l.problems[0] };
}
function outcomeForUpload(name) {
  const n = name.toLowerCase();
  if (n.includes("error")) return { forceError: true, route: "extract" };
  for (const p of Object.keys(PROBLEM)) if (n.includes(p) || (p === "multiple" && n.includes("two")) || (p === "incomplete" && n.includes("cut"))) return { route: "reject", problem: p };
  const good = labels.filter((l) => l.route === "extract");
  const cycle = [{ route: "extract" }, { route: "reject", problem: "blurry" }, { route: "review" }, { route: "reject", problem: "glare" }];
  const o = cycle[uploadCycle++ % cycle.length];
  return { ...o, label: good[uploadCycle % Math.max(1, good.length)] };
}

// ---------- http ----------
function readBody(req, limit = 16 * 1024 * 1024) {
  return new Promise((ok, fail) => {
    const parts = []; let n = 0;
    req.on("data", (c) => { n += c.length; if (n > limit) { fail(new Error("too large")); req.destroy(); } else parts.push(c); });
    req.on("end", () => ok(Buffer.concat(parts)));
    req.on("error", fail);
  });
}
function multipartFile(buf, contentType) {
  const b = /boundary=(?:"([^"]+)"|([^;]+))/.exec(contentType || "");
  if (!b) return null;
  const sep = Buffer.from(`--${b[1] || b[2]}`);
  let i = buf.indexOf(sep);
  while (i >= 0) {
    const start = i + sep.length + 2;
    const headEnd = buf.indexOf("\r\n\r\n", start);
    if (headEnd < 0) break;
    const head = buf.slice(start, headEnd).toString();
    const next = buf.indexOf(sep, headEnd);
    if (/name="image"/.test(head)) {
      const name = /filename="([^"]*)"/.exec(head)?.[1] || "upload.jpg";
      return { name, bytes: buf.slice(headEnd + 4, next - 2) };
    }
    i = next;
  }
  return null;
}
const json = (res, code, data) => { res.writeHead(code, { "Content-Type": "application/json" }); res.end(JSON.stringify(data)); };
const TYPES = { ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".woff2": "font/woff2", ".json": "application/json" };
function sniff(bytes) { return bytes[0] === 0x89 ? "image/png" : bytes.slice(8, 12).toString() === "WEBP" ? "image/webp" : "image/jpeg"; }

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  const path = decodeURIComponent(url.pathname);
  try {
    if (req.method === "GET" && path === "/health") return json(res, 200, { ok: true, router: true, qwen27b: true, qwen4b: true });
    if (req.method === "GET" && path === "/api/demo") return json(res, 200, demo);
    if (req.method === "GET" && path.startsWith("/api/demo/")) {
      const f = path.slice("/api/demo/".length);
      if (!labelOf[f] || f.includes("/")) return json(res, 404, { detail: "not found" });
      res.writeHead(200, { "Content-Type": TYPES[extname(f)] || "image/jpeg", "Cache-Control": "max-age=3600" });
      return res.end(readFileSync(join(DEMO_DIR, f)));
    }
    if (req.method === "GET" && path.startsWith("/api/uploads/")) {
      const b = state.uploads.get(path.slice("/api/uploads/".length));
      if (!b) return json(res, 404, { detail: "not found" });
      res.writeHead(200, { "Content-Type": sniff(b) });
      return res.end(b);
    }
    if (req.method === "GET" && path === "/api/stats") return json(res, 200, stats());
    if (req.method === "GET" && path === "/api/receipts") return json(res, 200, state.receipts.slice(0, 50));
    if (req.method === "GET" && path === "/api/rejections") return json(res, 200, state.rejections.slice(0, 50));
    if (req.method === "POST" && path === "/api/reset") { reset(); await sleep(150); return json(res, 200, { ok: true }); }
    if (req.method === "POST" && path === "/api/check") {
      const ct = req.headers["content-type"] || "";
      const body = await readBody(req);
      if (ct.startsWith("application/json")) {
        const { demo: f } = JSON.parse(body.toString() || "{}");
        if (!labelOf[f]) return json(res, 404, { detail: `unknown demo image ${f}` });
        return runCheck(res, { file: f, bytes: readFileSync(join(DEMO_DIR, f)), outcome: outcomeForDemo(f), imageUrl: `/api/demo/${f}` });
      }
      const up = multipartFile(body, ct);
      if (!up || !up.bytes.length) return json(res, 400, { detail: "expected multipart field 'image'" });
      return runCheck(res, { file: up.name, bytes: up.bytes, outcome: outcomeForUpload(up.name) });
    }
    // Built UI (web/), so the mock can also stand in for the backend's static serving.
    if (req.method === "GET" && existsSync(WEB_DIR)) {
      let f = join(WEB_DIR, path === "/" ? "index.html" : path);
      if (!f.startsWith(WEB_DIR) || !existsSync(f) || statSync(f).isDirectory()) f = join(WEB_DIR, "index.html");
      if (existsSync(f)) { res.writeHead(200, { "Content-Type": TYPES[extname(f)] || "application/octet-stream" }); return res.end(readFileSync(f)); }
    }
    json(res, 404, { detail: "not found" });
  } catch (e) {
    if (!res.headersSent) json(res, 500, { detail: String(e?.message || e) }); else res.end();
  }
});
server.listen(PORT, "127.0.0.1", () => console.log(`mock receipt-intake API on http://127.0.0.1:${PORT} (${demo.length} demo images from ${DEMO_DIR}${readdirSync(DEMO_DIR).length ? "" : ", empty"})`));
