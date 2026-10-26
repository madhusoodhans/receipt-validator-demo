"""Scores the extractions in results/batch.json against the labels: CORD gt (total, subtotal, tax, item amounts) and
the hand labels of the user's receipts (store, date, currency, total, tax, item amounts)."""
import json, re, sys

def num(s):
    if s is None: return None
    if isinstance(s, list): s = s[0]
    s = str(s).strip()
    if re.search(r"[.,]\d{2}$", s) and not re.search(r"[.,]\d{3}$", s): s = s[:-3]
    d = re.sub(r"[^\d]", "", s); return int(d) if d else None

runs = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "results/batch.json"))["runs"]
score = {}
def mark(k, ok): score.setdefault(k, [0, 0]); score[k][0] += bool(ok); score[k][1] += 1
for r in runs:
    l, ev = r["label"], r["events"]
    mark("route", (ev.get("route", {}).get("route") == "reject") == (l["expected_route"] == "reject"))
    if "extract" not in ev or l["group"] != "good": continue
    rc = ev["extract"]["receipt"]
    if "gt" in l:
        gt = l["gt"]; st, tt = gt.get("sub_total") or {}, gt.get("total") or {}
        menu = gt["menu"] if isinstance(gt["menu"], list) else [gt["menu"]]
        mark("total", num(rc["total"]) == num(tt.get("total_price")))
        if st.get("subtotal_price"): mark("subtotal", num(rc["subtotal"]) == num(st["subtotal_price"]))
        if st.get("tax_price"): mark("tax", num(rc["tax"]) == num(st["tax_price"]))
        mark("items", sorted(x for x in (num(i["amount"]) for i in rc["items"]) if x) == sorted(p for p in (num(m.get("price")) for m in menu) if p))
    else:
        t = l["truth"]
        mark("total", abs(rc["total"] - t["total"]) < 0.005)
        if "tax" in t: mark("tax", rc["tax"] is not None and abs(rc["tax"] - t["tax"]) < 0.005)
        mark("items", sorted(round(i["amount"], 2) for i in rc["items"] if i["amount"]) == sorted(t["items"]))
        mark("currency", (rc["currency"] or "").upper() in {t["currency"].upper(), {"$": "USD"}.get(t["currency"], "")})
        if "date" in t: mark("date", rc["date"] == t["date"])
        if "store_name" in t: mark("store", (rc["store_name"] or "").lower() == t["store_name"].lower())
    mark("sumcheck_ok", ev["sumcheck"]["ok"])
print({k: f"{a}/{b}" for k, (a, b) in score.items()})
