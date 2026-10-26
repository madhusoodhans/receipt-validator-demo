# Shared settings for deploy/*.sh (sourced, not run).
DOCKER="${DOCKER:-docker}"
VLLM_SR="${VLLM_SR:-.venv-sr/bin/vllm-sr}"       # the vllm-sr CLI, pinned version, see README.md
case "$VLLM_SR" in /*) ;; */*) VLLM_SR="$PWD/$VLLM_SR" ;; esac   # absolute, since the Router is started from deploy/
GPU="${GPU:-0}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8601}"
ROUTER_URL="${ROUTER_URL:-http://localhost:8899}"
export HOST PORT ROUTER_URL
STATE_FILE=deploy/.state                          # what up.sh started, so down.sh stops only that
ROUTER_CONTAINER=vllm-sr-router-container         # fixed by vllm-sr: one Router stack per host
# vllm-sr drives Docker itself, so where Docker needs sudo the CLI runs under sudo too.
SR=("$VLLM_SR")
case "$DOCKER" in sudo\ *) SR=(sudo -H env "VLLM_SR_AMD_ROUTER_VISIBLE_DEVICES=$GPU" ${HF_TOKEN:+"HF_TOKEN=$HF_TOKEN"} "$VLLM_SR") ;; esac
export VLLM_SR_AMD_ROUTER_VISIBLE_DEVICES="$GPU"

say() { printf '\n== %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
port_busy() { ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$1\$"; }
container_running() { [ "$($DOCKER inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]; }
container_exists() { $DOCKER inspect "$1" >/dev/null 2>&1; }
# wait_http <url> <seconds> [container]: until the URL answers 200; gives up early if the container stops.
wait_http() {
  local url=$1 limit=$2 box=${3:-} t=0
  until curl -sf -m 3 "$url" >/dev/null; do
    if [ -n "$box" ] && ! container_running "$box"; then
      echo "  $box stopped; last log lines:"; $DOCKER logs --tail 30 "$box" 2>&1 | sed 's/^/    /'; return 1
    fi
    [ "$t" -ge "$limit" ] && { echo "  no answer from $url after $limit s"; return 1; }
    sleep 5; t=$((t + 5))
  done
}
