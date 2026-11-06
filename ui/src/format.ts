/* Number and label formatting shared by the panels. */

export const ms = (v: number | undefined | null) =>
  v == null ? "" : v > 0 && v < 1 ? "under 1 ms" : v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(v < 10000 ? 1 : 0)} s`;

export const pct = (p: number) => `${Math.round(p * 100)}%`;

/** A probability next to its threshold: one decimal when whole percents would make them look equal. */
export const pctNear = (p: number, threshold: number) =>
  Math.round(p * 100) === Math.round(threshold * 100) && p !== threshold ? `${(p * 100).toFixed(1)}%` : pct(p);

const WHOLE_CURRENCIES = ["IDR", "RP", "JPY", "KRW", "VND"];

/** True when a receipt shows no cents: a whole-number currency, or every amount on it is whole. */
export function receiptWhole(r: { currency: string | null; items: { amount: number | null }[]; total: number | null; subtotal: number | null; tax: number | null; service_charge?: number | null; discount?: number | null }) {
  if (WHOLE_CURRENCIES.includes((r.currency || "").toUpperCase())) return true;
  return r.items.every((i) => i.amount == null || Number.isInteger(i.amount))
    && [r.total, r.subtotal, r.tax, r.service_charge, r.discount].every((v) => v == null || Number.isInteger(v));
}

/** Money in the receipt's currency; `whole` (from receiptWhole) drops the cents for the whole receipt at once. */
export function money(v: number | null | undefined, currency?: string | null, whole?: boolean) {
  if (v == null) return "";
  const noCents = whole ?? (WHOLE_CURRENCIES.includes((currency || "").toUpperCase()) || Number.isInteger(v));
  return v.toLocaleString("en-US", { minimumFractionDigits: noCents ? 0 : 2, maximumFractionDigits: noCents ? 0 : 2 });
}

/** The problems a rejection counts under (as in GET /api/stats by_problem): bad reasons only, or just the kind. */
export function badReasons<R extends { key: string; status: string }>(reasons: R[]) {
  const bad = reasons.filter((r) => r.status === "bad");
  return bad[0]?.key === "kind" ? bad.slice(0, 1) : bad;
}

const KIND: Record<string, string> = {
  receipt: "Receipt", invoice: "Invoice", other_document: "Other document", several: "Several receipts or documents",
  screenshot: "Screenshot", photo: "Photo",
};
export const kindLabel = (k: string) => KIND[k] || (k.charAt(0).toUpperCase() + k.slice(1)).replace(/_/g, " ");

const PROBLEM: Record<string, string> = {
  blurry: "Blurry", complete: "Total cut off", damaged: "Torn or missing piece", glare: "Glare or shadow", multiple: "Several receipts",
  kind: "Not a receipt", added_text: "Added note or text", size: "Too small to read", kind_doubt: "May not be an invoice",
};
export const problemLabel = (k: string) => PROBLEM[k] || (k.charAt(0).toUpperCase() + k.slice(1)).replace(/_/g, " ");

/** A route reason's badge: short words per key (the server's label otherwise) and the value that reads right with them.
 * size shows its pixel size; kind_doubt has no percentage (its probability is of the other kind, so it would mislead). */
const REASON: Record<string, string> = {
  size: "Image too small to read", multiple: "More than one receipt", kind_doubt: "May not be an invoice",
  complete: "Total cut off or hidden", damaged: "Torn or missing piece",
};
export function reasonBadge(r: { key: string; label: string; prob: number; px?: number }) {
  const label = REASON[r.key] || r.label;
  if (r.key === "size") return r.px != null ? `${label}, ${r.px} px` : label;
  if (r.key === "kind_doubt") return label;
  return `${label} ${pct(r.prob)}`;
}

export function timeAgo(iso: string, now = Date.now()) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
