#!/bin/bash
# Re-measures this server's "everything on Qwen 27B" numbers for the comparison panel. Run once on every new server
# (and again after changing GPU, models, vLLM settings or the demo set); otherwise the page warns that its numbers came
# from a different GPU.
#
#   bench/baseline_27b.py   Qwen 27B reads every demo image, one at a time -> data/demo/baseline.json
#                           (the 27B's time and tokens per image; records the GPU it was measured on)
#
# Takes about 2 minutes. Nothing else should use the GPU meanwhile (that includes presenting the demo), or the times
# come out too high. The models must be up (deploy/up.sh). Restarts the demo server afterwards so it loads the file.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/common.sh

health=$(curl -s -m 5 "http://$HOST:$PORT/health" || true)
case "$health" in
  *'"ok": true'*|*'"ok":true'*) ;;
  "") echo "Demo server not answering on $HOST:$PORT; checking the models directly." ;;
  *) die "not all parts are up: $health (run deploy/up.sh)" ;;
esac
curl -sf -m 3 "$ROUTER_URL/v1/models" >/dev/null || die "the Router does not answer at $ROUTER_URL (run deploy/up.sh)"

say "Qwen 27B baseline on the demo images (about 2 minutes)"
.venv/bin/python -I bench/baseline_27b.py

say "Done. Restarting the demo server so it loads the new numbers"
./start_server.sh --bg
