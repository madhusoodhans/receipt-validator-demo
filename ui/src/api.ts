/* Types and I/O for the receipt-intake API (docs/API.md). All paths are relative: same origin as the page. */

/** This tab's visitor id, sent as X-Visitor-Id on every call: the server shows each visitor only their own results.
 * Kept in sessionStorage, so it lasts as long as the tab (reloads included) and every new tab starts empty. */
export const VISITOR: string = (() => {
  const make = () => {
    if (crypto.randomUUID) return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));  // plain-http pages have no randomUUID
    b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
    const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  };
  try {
    const v = sessionStorage.getItem("visitor") || make();
    sessionStorage.setItem("visitor", v);
    return v;
  } catch { return make(); }  // storage blocked: a fresh id per page load
})();
const VISITOR_HEADER = { "X-Visitor-Id": VISITOR };

export type Status = "ok" | "borderline" | "bad";
export type RouteName = "extract" | "review" | "reject";

export interface Check { key: string; label: string; prob: number; bad_when: "low" | "high"; threshold: number; status: Status }
/** The size gate run before D3 (no model): px is sqrt(width x height) of the upload; bad below threshold, borderline
 * below borderline_from. */
export interface SizeGate { key: "size"; label: string; px: number; status: Status; threshold: number; borderline_from: number }
export interface D3 { model: string; ms: number; tokens?: number; kind: { choice: string; probabilities: Record<string, number> }; checks: Check[]; size?: SizeGate }
/** px only on a "size" reason. */
export interface Reason { key: string; label: string; prob: number; status: Status; px?: number }
export interface LineItem { name: string; quantity: number | null; unit_price: number | null; amount: number | null }
export interface Receipt {
  store_name: string | null; date: string | null; currency: string | null; items: LineItem[];
  subtotal: number | null; tax: number | null; service_charge: number | null; discount: number | null;
  /** The printed label of the final total line; null (with total null) when no total line is visible. */
  total_label?: string | null; total: number | null;
}
export interface SumCheck { ok: boolean; checks: { name: string; label: string; ok: boolean; detail?: string }[] }

/** What the vllm-sr Router picked for an LLM call (its x-vsr-selected-* headers); null each when not routed by vllm-sr. */
export interface Routed { model: string | null; decision: string | null; reasoning: string | null }

export interface Events {
  received: { id: string; file: string; width: number; height: number; image_url: string };
  d3: D3;
  route: { route: RouteName; reasons: Reason[]; summary: string };
  /** model is the model asked for (vllm-sr/auto), routed what the Router picked. model "template" with `fallback` set
   * ("standard message (LLM unavailable)") when the LLM could not write it. */
  message: { text: string; model: string; routed?: Routed; ms: number; tokens?: number | null; completion_tokens?: number | null;
    reasoning_tokens?: number | null; fallback?: string };
  extract: { receipt: Receipt; model: string; routed?: Routed; ms: number; prompt_tokens?: number; completion_tokens?: number;
    reasoning_tokens?: number | null };
  sumcheck: SumCheck;
  stored: { table: "receipts" | "rejections"; id: number; needs_review: number };
  done: { total_ms: number; steps: Record<string, number> };
  /** Plain words only. reason: unavailable, timeout, busy, too_long, key or error; detail: one line on what failed;
   * skipped: the steps that did not run because of it (route, model, sumcheck, stored). reason "key": the visitor's API
   * key failed (code invalid, disabled or quota); the server has dropped it. */
  error: { step: string; message: string; reason?: string; code?: KeyCode; detail?: string; skipped?: string[] };
}
export type EventName = keyof Events;

export interface DemoItem { file: string; group: "good" | "borderline" | "bad"; label: string; expected_route: RouteName; image_url: string }
export interface Stats {
  uploads: number; extracted: number; needs_review: number; rejected: number;
  by_problem: Record<string, number>;
  money_total_by_currency?: Record<string, number>;
}
export interface Config { play_all: boolean; max_upload_mb: number; uploads_per_min: number; landing_url?: string;
  /** False when the server has its own key (VLLM_SR_API_KEY): the page then runs without one. */
  key_required?: boolean; key_url?: string;
  /** The model names the server asks for (D3_MODEL, EXTRACT_MODEL, MESSAGE_MODEL). */
  models?: { d3: string; extract: string; message: string } }

export interface ReceiptRow { id: number; file: string; image_url: string; created_at: string; needs_review: number; receipt: Receipt; sumcheck_ok: boolean; d3?: D3 }
export interface RejectionRow { id: number; file: string; image_url: string; created_at: string; reasons: Reason[]; message: string; d3?: D3 }
export type Service = "d3" | "extract" | "retake";
/** GET /health: each model's availability (free GET checks every 60 s plus the circuit breaker on real calls). status
 * "down" (D3 or the reading LLM unavailable: uploads are refused), "degraded" (the LLM for retake messages only:
 * standard messages), or "ok"; `message` is the badge text; `detail` names each failure ("unreachable", "not served", ...). */
export type Health = {
  ok: boolean; status?: "ok" | "degraded" | "down"; message?: string;
  detail?: Partial<Record<Service, string>>; checked_at?: string | null;
  /** This visitor's key: set, missing, or server (the server's own key is in use). */
  key?: "set" | "missing" | "server";
} & Partial<Record<Service, boolean>>;

/** Plain JSON calls give up after this long, so a dead server shows as an error instead of hanging. */
const JSON_TIMEOUT_MS = 8000;

/** fetch() rejects with a TypeError when the server cannot be reached at all; say that in plain words. */
export function unreachable(e: unknown): boolean {
  return e instanceof TypeError;
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(path, { cache: "no-store", signal: AbortSignal.timeout(JSON_TIMEOUT_MS), ...init, headers: VISITOR_HEADER });
  } catch (e) {
    if ((e as Error)?.name === "TimeoutError") throw new Error(`The demo server did not answer within ${JSON_TIMEOUT_MS / 1000} s`);
    if (unreachable(e)) throw new Error("Cannot reach the demo server");
    throw e;
  }
  const text = await r.text();
  let data: unknown = text;
  try { data = JSON.parse(text); } catch { /* not JSON */ }
  if (!r.ok) throw new Error((data as { error?: string; detail?: string })?.error || (data as { detail?: string })?.detail || `${r.status} ${r.statusText}`);
  return data as T;
}

export const getDemo = () => getJson<DemoItem[]>("api/demo");
export const getStats = () => getJson<Stats>("api/stats");
export const getReceipts = () => getJson<ReceiptRow[]>("api/receipts");
export const getRejections = () => getJson<RejectionRow[]>("api/rejections");
export const getHealth = () => getJson<Health>("health");
/** Older servers have no /api/config: everything on, as before. */
export const getConfig = () => getJson<Config>("api/config").catch((): Config => ({ play_all: true, max_upload_mb: 15, uploads_per_min: 0 }));

/** Upload images are served only with this tab's visitor id, which an <img> cannot send: fetch them and show an object
 * URL. Kept per URL (at most the 20 uploads the server keeps per visitor, plus the current one). */
const imageCache = new Map<string, Promise<string>>();
export function imageSrc(url: string): Promise<string> {
  if (!url.includes("/api/uploads/")) return Promise.resolve(url);
  let p = imageCache.get(url);
  if (!p) {
    p = fetch(url, { headers: VISITOR_HEADER }).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status}`);
      return URL.createObjectURL(await r.blob());
    });
    p.catch(() => imageCache.delete(url));
    imageCache.set(url, p);
  }
  return p;
}
export function forgetImages() {
  for (const p of imageCache.values()) p.then(URL.revokeObjectURL, () => {});
  imageCache.clear();
}
export const postReset = () => getJson<{ ok: boolean }>("api/reset", { method: "POST" });

/** The server answered but refused the upload (wrong file type, too large, unreadable; 503: checking unavailable;
 * 401 code no_key: this visitor has no API key). */
export class Refused extends Error {
  status: number;
  code?: string;
  constructor(message: string, status = 0, code?: string) { super(message); this.status = status; this.code = code; }
}

// ---------- API key (docs/API.md, "API key") ----------
export type KeyCode = "format" | "wrong_kind" | "invalid" | "disabled" | "quota" | "rate_limited" | "unreachable" | "error" | "no_key";
export interface KeyResult { ok: boolean; code?: KeyCode; message?: string; retry_after?: number }
export interface KeyState { set: boolean; required: boolean; failed: null | { code: KeyCode; message: string } }
export const KEY_URL = "https://app.vllm-sr.ai/#key";

/** The key lives only in this tab's sessionStorage (cleared when the tab closes), never in localStorage or a URL. */
const KEY_STORE = "vsr-key";
export const storedKey = (): string | null => { try { return sessionStorage.getItem(KEY_STORE); } catch { return null; } };
export const storeKey = (k: string) => { try { sessionStorage.setItem(KEY_STORE, k); } catch { /* storage blocked */ } };
export const dropStoredKey = () => { try { sessionStorage.removeItem(KEY_STORE); sessionStorage.removeItem("vsr-key-handoff"); } catch { /* blocked */ } };
/** True once, right after the landing page handed a key over in the URL fragment (index.html moved it to storage). */
export const takeHandoff = (): boolean => {
  try { const h = sessionStorage.getItem("vsr-key-handoff") === "1"; sessionStorage.removeItem("vsr-key-handoff"); return h; } catch { return false; }
};

async function keyCall(method: string, key?: string): Promise<KeyResult> {
  let r: Response;
  try {
    r = await fetch("api/key", {
      method, cache: "no-store", signal: AbortSignal.timeout(30000),
      headers: { ...VISITOR_HEADER, ...(key !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: key !== undefined ? JSON.stringify({ key }) : undefined,
    });
  } catch (e) {
    if ((e as Error)?.name === "TimeoutError") return { ok: false, code: "unreachable", message: "The key check took too long. Try again." };
    return { ok: false, code: "unreachable", message: "Cannot reach the demo server." };
  }
  try {
    const j = await r.json();
    if (r.ok) return { ok: true, ...j };
    return { ok: false, code: j.code ?? "error", message: j.message ?? j.error ?? `${r.status} ${r.statusText}`, retry_after: j.retry_after };
  } catch { return r.ok ? { ok: true } : { ok: false, code: "error", message: `${r.status} ${r.statusText}` }; }
}
/** POST /api/key: validates the key on the hosted API (one tiny call per system) and keeps it for this tab's visitor. */
export const postKey = (key: string) => keyCall("POST", key);
export const deleteKey = () => keyCall("DELETE");
export const getKey = () => getJson<KeyState>("api/key");

/** POST /api/check and feed each SSE event to onEvent. Resolves when the stream ends. */
export async function streamCheck(
  input: { demo: string } | { file: File },
  onEvent: <E extends EventName>(event: E, data: Events[E]) => void,
  signal?: AbortSignal,
  /** Called on every chunk received, keep-alive pings included. */
  onActivity?: () => void,
): Promise<void> {
  let body: BodyInit, headers: Record<string, string> = { ...VISITOR_HEADER };
  if ("demo" in input) { body = JSON.stringify({ demo: input.demo }); headers["Content-Type"] = "application/json"; }
  else { const form = new FormData(); form.append("image", input.file, input.file.name); body = form; }
  const r = await fetch("api/check", { method: "POST", body, headers, signal, cache: "no-store" });
  if (!r.ok || !r.body) {
    const text = await r.text().catch(() => "");
    let detail = text;
    let code: string | undefined;
    try { const j = JSON.parse(text); detail = j.message || j.error || j.detail || text; code = j.code; } catch { /* plain text */ }
    throw new Refused(detail || `${r.status} ${r.statusText}`, r.status, code);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const flush = (block: string) => {
    let event = "message", data = "";
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data += (data ? "\n" : "") + line.slice(5).replace(/^ /, "");
    }
    if (!data) return;
    let parsed: unknown;
    try { parsed = JSON.parse(data); } catch { return; }
    // A bare "message" event (no event: line) would collide with the SSE default; the contract always names events.
    onEvent(event as EventName, parsed as never);
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    onActivity?.();
    buffer += decoder.decode(value, { stream: true });
    let cut: RegExpExecArray | null;
    while ((cut = /\r?\n\r?\n/.exec(buffer))) {
      flush(buffer.slice(0, cut.index));
      buffer = buffer.slice(cut.index + cut[0].length);
    }
  }
  if (buffer.trim()) flush(buffer);
}

export const errText = (e: unknown) => String((e as Error)?.message || e);
