#!/bin/bash
# Starts the receipt intake demo server (UI + API) on $HOST:$PORT (default 127.0.0.1:8601), replacing one already
# running on that port. By default it calls the hosted vllm-sr API (https://app.vllm-sr.ai) with each visitor's own key
# (entered in the page); VLLM_SR_API_KEY is optional. For the local stack deploy/up.sh sets ROUTER_URL and the model names and then runs this script.
# --bg runs it detached, logging to logs/server.log.
# Environment: HOST, PORT, VLLM_SR_API_KEY, ROUTER_URL, D3_MODEL, EXTRACT_MODEL, MESSAGE_MODEL, DB, UPLOAD_ROOT,
# PLAY_ALL, LANDING_URL, TRUST_PROXY, HEALTH_INTERVAL, the timeouts and the limits (see README.md, Configuration).
cd "$(dirname "$0")"
HOST="${HOST:-127.0.0.1}" PORT="${PORT:-8601}"
MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"  # glibc keeps one heap per thread otherwise: ~4x the memory under load
export HOST PORT MALLOC_ARENA_MAX
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
