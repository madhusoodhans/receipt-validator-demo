#!/bin/bash
# Checks the running demo end to end: GET /health, then one good, one borderline and one bad demo image through
# POST /api/check, each route compared with data/demo/labels.json (expected_route). Exit code 0 = all passed.
#   deploy/smoke_test.sh            three images (~15 s)
#   deploy/smoke_test.sh --all      every demo image (~1-2 min)
#   add --reset                     clear the receipts and rejections afterwards (POST /api/reset), so the page starts
#                                   empty; measured 27B baseline times are kept
# The runs are stored like any other upload; the page's Reset button clears them too.
set -uo pipefail
cd "$(dirname "$0")/.."
source deploy/common.sh
exec .venv/bin/python -I - "http://$HOST:$PORT" "$@" <<'PY'
import json
import sys

import requests

base, args = sys.argv[1], sys.argv[2:]
fails = 0


def result(ok, what):
    global fails
    fails += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {what}", flush=True)


print(f"Smoke test against {base}")
try:
    h = requests.get(f"{base}/health", timeout=10).json()
except Exception as e:  # noqa: BLE001
    print(f"  FAIL  GET /health: {e}\nIs the demo server running? (./start_server.sh --bg, or deploy/up.sh)")
    sys.exit(1)
parts = {k: v for k, v in h.items() if isinstance(v, bool) and k != "ok"}
down = [k for k, v in parts.items() if not v]
detail = h.get("detail") or {}
result(h.get("ok") is True, "GET /health " + ", ".join(f"{k} {'up' if v else 'DOWN'}" for k, v in parts.items())
       + "".join(f"\n        {k}: {detail[k]}" for k in down if k in detail))
if h.get("ok") is not True:
    sys.exit(1)

labels = json.load(open("data/demo/labels.json"))
if "--all" in args:
    picked = labels
else:
    picked = [next(l for l in labels if l["group"] == g) for g in ("good", "borderline", "bad")
              if any(l["group"] == g for l in labels)]

for l in picked:
    events, event = {}, None
    try:
        with requests.post(f"{base}/api/check", json={"demo": l["file"]}, stream=True, timeout=(5, 180)) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: ") and event:
                    events[event] = json.loads(line[6:])
    except Exception as e:  # noqa: BLE001
        result(False, f"{l['file']}: request failed: {e}")
        continue
    if "error" in events:
        result(False, f"{l['file']}: error in step {events['error'].get('step')}: {events['error'].get('message')}")
        continue
    route = events.get("route", {}).get("route")
    done = events.get("done", {})
    detail = f"{done.get('total_ms', 0) / 1000:.1f} s"
    if "message" in events:
        detail += f", 4B: {events['message']['text'][:70]!r}"
    if "extract" in events:
        rc = events["extract"]["receipt"]
        detail += f", 27B: total {rc.get('total')} {rc.get('currency') or ''}, {len(rc.get('items') or [])} items"
    ok = route == l["expected_route"] and "done" in events and "stored" in events
    result(ok, f"{l['file']}: route {route} (expected {l['expected_route']}), {detail}")

if "--reset" in args:
    ok = requests.post(f"{base}/api/reset", timeout=10).json().get("ok") is True
    result(ok, "POST /api/reset")
print(f"\n{'ALL PASSED' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
PY
