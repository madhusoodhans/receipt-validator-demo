"""Phase 0: Qwen reads receipts into a JSON schema; scored against CORD labels.
Usage: extract_phase0.py <port> <model> <out.json> [files...]   (default: the 12 CORD good receipts in data/phase0)"""
import base64, json, re, sys, time
import requests

PORT, MODEL, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
IMG = "data/phase0"
labels = {l["file"]: l for l in json.load(open(f"{IMG}/labels.json"))}
files = sys.argv[4:] or [f for f, l in labels.items() if l["source"] == "cord"]

SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["store_name", "date", "currency", "items", "subtotal", "tax", "service_charge", "discount", "total"],
          "properties": {
              "store_name": {"type": ["string", "null"]}, "date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
              "currency": {"type": ["string", "null"]},
              "items": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                        "required": ["name", "quantity", "amount"],
                        "properties": {"name": {"type": "string"}, "quantity": {"type": "number"},
                                       "unit_price": {"type": ["number", "null"]}, "amount": {"type": "number"}}}},
              "subtotal": {"type": ["number", "null"]}, "tax": {"type": ["number", "null"]},
              "service_charge": {"type": ["number", "null"]}, "discount": {"type": ["number", "null"]},
              "total": {"type": "number"}}}
PROMPT = ("Read this receipt into JSON. Copy item names as printed. Amounts are plain numbers in the receipt's currency "
          "(for example 'Rp 45.500' or '45,500' is 45500). Use null for anything not printed or not readable "
          "(store names and addresses may be blurred out).")

def num(s):
    if s is None: return None
    if isinstance(s, list): s = s[0]
    s = str(s).strip()
    if isinstance(s, str) and re.search(r"[.,]\d{2}$", s) and not re.search(r"[.,]\d{3}$", s):
        s = s[:-3]  # "45,000.00" -> "45,000": drop cents
    d = re.sub(r"[^\d]", "", s); return int(d) if d else None

def truth(gt):
    menu = gt.get("menu", []); menu = menu if isinstance(menu, list) else [menu]
    st, tt = gt.get("sub_total", {}) or {}, gt.get("total", {}) or {}
    return {"total": num(tt.get("total_price")), "subtotal": num(st.get("subtotal_price")), "tax": num(st.get("tax_price")),
            "items": [(m.get("nm") if isinstance(m.get("nm"), str) else str(m.get("nm")), num(m.get("price"))) for m in menu]}

out = []
for f in files:
    b64 = base64.b64encode(open(f"{IMG}/{f}", "rb").read()).decode()
    body = {"model": MODEL, "temperature": 0, "max_tokens": 1500,
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                                                      {"type": "text", "text": PROMPT}]}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "receipt", "schema": SCHEMA, "strict": True}}}
    t = time.perf_counter(); r = requests.post(f"http://localhost:{PORT}/v1/chat/completions", json=body, timeout=300).json()
    s = time.perf_counter() - t
    txt = r["choices"][0]["message"]["content"]; got = json.loads(txt); u = r["usage"]
    row = {"file": f, "s": round(s, 2), "prompt_tokens": u["prompt_tokens"], "completion_tokens": u["completion_tokens"], "got": got}
    if "gt" in labels[f]:
        tr = truth(labels[f]["gt"]); row["truth"] = tr
        row["total_ok"] = num(got["total"]) == tr["total"]
        row["subtotal_ok"] = tr["subtotal"] is None or num(got["subtotal"]) == tr["subtotal"]
        row["tax_ok"] = tr["tax"] is None or num(got["tax"]) == tr["tax"]
        gp = sorted(x for x in (num(i["amount"]) for i in got["items"]) if x); tp = sorted(p for _, p in tr["items"] if p)  # zero-priced add-ons ignored
        row["items_ok"] = gp == tp
    out.append(row)
    print(f, f"{s:.1f}s", u["prompt_tokens"], u["completion_tokens"],
          {k: row[k] for k in ("total_ok", "subtotal_ok", "tax_ok", "items_ok") if k in row}, flush=True)
json.dump(out, open(OUT, "w"), indent=1, ensure_ascii=False)
