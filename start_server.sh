#!/bin/bash
# Starts the receipt intake demo server (UI + API) on $HOST:$PORT (default 127.0.0.1:8601), replacing one already
# running on that port. It needs the vllm-sr Router (with d3-mini) and the qwen27b / qwen4b vLLM servers, image input
# on: deploy/up.sh starts all of them and then this script. --bg runs it detached, logging to logs/server.log.
# Environment: HOST, PORT, ROUTER_URL, DB, GPU_USD_PER_HOUR (see README.md, Configuration).
cd "$(dirname "$0")"
HOST="${HOST:-127.0.0.1}" PORT="${PORT:-8601}"
export HOST PORT
mkdir -p logs
PID=$(ss -ltnp 2>/dev/null | grep -E "[:.]$PORT " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
[ -n "$PID" ] && kill "$PID" && sleep 1
if [ "$1" = "--bg" ]; then
  nohup .venv/bin/python -I app/server.py > logs/server.log 2>&1 &
  CHECK=$HOST; case "$CHECK" in 0.0.0.0|::|"") CHECK=127.0.0.1 ;; esac
  sleep 2; curl -s "http://$CHECK:$PORT/health"; echo
else
  exec .venv/bin/python -I app/server.py
fi
