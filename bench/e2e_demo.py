"""All demo images end to end through a running demo server (POST /api/check, as the page sends them), against
whatever API the server is configured for. Per image: the route vs labels.json, the extracted total and line items vs
the label's truth, the sum check, time per step, and what the vllm-sr Router picked for each LLM call
(model, decision, reasoning). 3 visitors in parallel (one run each at a time). Writes results/<out>.

  .venv/bin/python bench/e2e_demo.py [--base http://127.0.0.1:8641] [--out e2e_hosted.json] [files...]
  .venv/bin/python bench/e2e_demo.py --rescore --out e2e_hosted.json   # the summary of a saved run, no calls
Truth: `truth` in labels.json (total, item amounts), or for CORD images `gt` (total.total_price, menu prices).
"""
import argparse
import json
import pathlib
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent


def num(x):
    """A CORD price string ("17,500", "91.000") as a number: CORD receipts are in rupiah, no cents."""
    return float("".join(ch for ch in str(x) if ch.isdigit()) or "nan")


def truth_of(label):
    if label.get("truth"):
        t = label["truth"]
        return t.get("total"), [float(a) for a in t.get("items") or []]
    gt = label.get("gt")
    if gt:
        total = num((gt.get("total") or {}).get("total_price"))
        menu = gt.get("menu") or []
        menu = [menu] if isinstance(menu, dict) else menu
        return total, [num(m["price"]) for m in menu if m.get("price")]
    return None, []


def score(row, label):
    ev = row.get("events") or {}
    total, items = truth_of(label)
    if ev.get("extract") and total is not None:
        rc = ev["extract"]["receipt"]
        got_items = sorted(round(float(i["amount"]), 2) for i in rc.get("items") or [] if i.get("amount") is not None)
        row["check"] = {"total": rc.get("total"), "truth_total": total,
                        "total_ok": rc.get("total") is not None and abs(float(rc["total"]) - float(total)) < 0.01,
                        "items_ok": got_items == sorted(round(a, 2) for a in items) if items else None,
                        "sumcheck_ok": (ev.get("sumcheck") or {}).get("ok")}
    return row


def run(base, label):
    hdr = {"X-Visitor-Id": str(uuid.uuid4())}
    for _ in range(12):
        t = time.perf_counter()
        r = requests.post(f"{base}/api/check", json={"demo": label["file"]}, headers=hdr, stream=True, timeout=(5, 180))
        if r.status_code != 429:
            break
        r.close(); time.sleep(10)
    if r.status_code != 200:
        return {"file": label["file"], "status": r.status_code, "error": r.text[:300]}
    ev, name = {}, None
    for line in r.iter_lines(decode_unicode=True):
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            ev[name] = json.loads(line[5:])
    wall = round((time.perf_counter() - t) * 1000)
    row = {"file": label["file"], "expected": label["expected_route"], "route": (ev.get("route") or {}).get("route"),
           "summary": (ev.get("route") or {}).get("summary"), "wall_ms": wall, "steps": (ev.get("done") or {}).get("steps"),
           "error": ev.get("error"), "events": ev}
    llm = ev.get("extract") or ev.get("message")
    if llm:
        row["llm"] = {"model": llm.get("model"), "routed": llm.get("routed"), "completion_tokens": llm.get("completion_tokens"),
                      "reasoning_tokens": llm.get("reasoning_tokens"), "fallback": llm.get("fallback")}
    score(row, label)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8641")
    ap.add_argument("--out", default="e2e_hosted.json")
    ap.add_argument("--rescore", action="store_true")
    ap.add_argument("files", nargs="*", help="only these demo images; their rows replace those in --out")
    a = ap.parse_args()
    labels = json.loads((ROOT / "data/demo/labels.json").read_text())
    by_file = {l["file"]: l for l in labels}
    out = ROOT / "results" / a.out
    saved = json.loads(out.read_text())["rows"] if out.exists() and (a.rescore or a.files) else []
    if a.rescore:
        rows = [score(r, by_file[r["file"]]) for r in saved]
    else:
        print("health:", requests.get(f"{a.base}/health", timeout=10).json())
        todo = [l for l in labels if not a.files or l["file"] in a.files]
        with ThreadPoolExecutor(3) as ex:
            new = {r["file"]: r for r in ex.map(lambda l: run(a.base, l), todo)}
        rows = [new.get(r["file"], r) for r in saved] if saved else list(new.values())
    for r in rows:
        s = r.get("steps") or {}
        llm = r.get("llm") or {}
        ro = llm.get("routed") or {}
        c = r.get("check") or {}
        print(f"{r['file']:32} {r.get('expected', ''):8} {r.get('route') or 'ERROR':8} "
              f"{'ok' if r.get('route') == r.get('expected') else 'XX'} d3 {s.get('d3', 0):6.0f} ms "
              f"llm {s.get('extract', s.get('message', 0)) or 0:7.0f} ms {ro.get('model')}/{ro.get('decision')}/{ro.get('reasoning')} "
              f"{'total ' + ('ok' if c.get('total_ok') else 'XX') if c else ''} {'items ' + str(c.get('items_ok')) if c else ''} "
              f"{'sum ' + str(c.get('sumcheck_ok')) if c else ''} {r.get('error') or ''}")
    ok = sum(r.get("route") == r.get("expected") for r in rows)
    checked = [r["check"] for r in rows if r.get("check")]
    print(f"routes {ok}/{len(rows)}; totals right {sum(c['total_ok'] for c in checked)}/{len(checked)}; "
          f"items right {sum(bool(c['items_ok']) for c in checked)}/{sum(c['items_ok'] is not None for c in checked)}; "
          f"sum check ok {sum(bool(c['sumcheck_ok']) for c in checked)}/{len(checked)}")
    out.write_text(json.dumps({"base": a.base, "rows": rows}, indent=1, ensure_ascii=False))
    print("->", out)


if __name__ == "__main__":
    main()
