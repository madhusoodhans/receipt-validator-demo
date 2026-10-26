/* Types and I/O for the receipt-intake API (docs/API.md). All paths are relative: same origin as the page. */

export type Status = "ok" | "borderline" | "bad";
export type RouteName = "extract" | "review" | "reject";

export interface Check { key: string; label: string; prob: number; bad_when: "low" | "high"; threshold: number; status: Status }
export interface D3 { model: string; ms: number; tokens?: number; kind: { choice: string; probabilities: Record<string, number> }; checks: Check[] }
export interface Reason { key: string; label: string; prob: number; status: Status }
export interface LineItem { name: string; quantity: number | null; unit_price: number | null; amount: number | null }
export interface Receipt {
  store_name: string | null; date: string | null; currency: string | null; items: LineItem[];
  subtotal: number | null; tax: number | null; service_charge: number | null; discount: number | null; total: number | null;
}
export interface SumCheck { ok: boolean; checks: { name: string; label: string; ok: boolean; detail?: string }[] }

export interface Events {
  received: { id: string; file: string; width: number; height: number; image_url: string };
  d3: D3;
  route: { route: RouteName; reasons: Reason[]; summary: string };
  message: { text: string; model: string; ms: number; tokens?: number };
  extract: { receipt: Receipt; model: string; ms: number; prompt_tokens?: number; completion_tokens?: number };
  sumcheck: SumCheck;
  stored: { table: "receipts" | "rejections"; id: number; needs_review: number };
  done: { total_ms: number; steps: Record<string, number>; comparison?: Comparison };
  error: { step: string; message: string };
}
export type EventName = keyof Events;

export interface DemoItem { file: string; group: "good" | "borderline" | "bad"; label: string; expected_route: RouteName; image_url: string }
export interface Stats {
  uploads: number; extracted: number; needs_review: number; rejected: number;
  by_problem: Record<string, number>;
  money_total_by_currency?: Record<string, number>;
  /** Model time per distinct image (first run of each), routed vs the same images all on Qwen 27B. */
  comparison: {
    images: number; pending_baseline: number; repeats: number;
    time_ms: { d3: number; message: number; extract: number; baseline_27b: number };
    model_seconds: { routed: number; all_27b: number };
  };
  /** Price of one GPU hour for the dollar box (server env GPU_USD_PER_HOUR overrides the default). */
  gpu_price?: { usd_per_hour: number; source: string; checked: string };
  /** Set when the 27B baseline was measured on another GPU than this server's. */
  measurement_warning?: string;
}

/** One image: routed (D3 + 4B, or D3 + 27B) against sending it straight to Qwen 27B. The all_27b fields are null while
 * the 27B's run on a new rejected image is still being measured. Tokens are prompt plus completion, D3's included. */
export interface Comparison {
  image_sha256: string; route: RouteName; routed_ms: number; all_27b_ms: number | null;
  routed_tokens?: number; d3_tokens?: number; all_27b_tokens?: number | null;
}
export const getBaseline = (sha: string) =>
  getJson<{ image_sha256: string; ms: number | null; tokens: number | null; measuring: boolean }>(`api/baseline/${sha}`);

export interface ReceiptRow { id: number; file: string; image_url: string; created_at: string; needs_review: number; receipt: Receipt; sumcheck_ok: boolean; d3?: D3 }
export interface RejectionRow { id: number; file: string; image_url: string; created_at: string; reasons: Reason[]; message: string; d3?: D3 }
export type Service = "router" | "d3" | "qwen27b" | "qwen4b";
/** GET /health: the server's last probe (every 60 s); `detail` names each failure ("down", "image input off", ...). */
export type Health = { ok: boolean; detail?: Partial<Record<Service, string>>; checked_at?: string | null } & Partial<Record<Service, boolean>>;

/** Plain JSON calls give up after this long, so a dead server shows as an error instead of hanging. */
const JSON_TIMEOUT_MS = 8000;

/** fetch() rejects with a TypeError when the server cannot be reached at all; say that in plain words. */
export function unreachable(e: unknown): boolean {
  return e instanceof TypeError;
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(path, { cache: "no-store", signal: AbortSignal.timeout(JSON_TIMEOUT_MS), ...init });
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
export const postReset = () => getJson<{ ok: boolean }>("api/reset", { method: "POST" });

/** The server answered but refused the upload (wrong file type, too large, unreadable). */
export class Refused extends Error {}

/** POST /api/check and feed each SSE event to onEvent. Resolves when the stream ends. */
export async function streamCheck(
  input: { demo: string } | { file: File },
  onEvent: <E extends EventName>(event: E, data: Events[E]) => void,
  signal?: AbortSignal,
  /** Called on every chunk received, keep-alive pings included. */
  onActivity?: () => void,
): Promise<void> {
  let body: BodyInit, headers: HeadersInit = {};
  if ("demo" in input) { body = JSON.stringify({ demo: input.demo }); headers = { "Content-Type": "application/json" }; }
  else { const form = new FormData(); form.append("image", input.file, input.file.name); body = form; }
  const r = await fetch("api/check", { method: "POST", body, headers, signal, cache: "no-store" });
  if (!r.ok || !r.body) {
    const text = await r.text().catch(() => "");
    let detail = text;
    try { const j = JSON.parse(text); detail = j.error || j.detail || text; } catch { /* plain text */ }
    throw new Refused(detail || `${r.status} ${r.statusText}`);
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
