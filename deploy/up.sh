#!/bin/bash
# Brings the whole demo up on one AMD Instinct GPU: Qwen 27B and Qwen 4B (vLLM containers), the vllm-sr Router with
# d3-mini, then the demo server (./start_server.sh --bg). Run from anywhere; takes ~5-15 minutes, most of it model
# loading. Re-running is safe: parts that are already up and healthy are left alone.
#
# Environment (all optional): DOCKER ("sudo docker" where Docker needs sudo), VLLM_SR (vllm-sr CLI path),
# GPU (GPU index, default 0), HF_HOME, HOST/PORT (demo server, default 127.0.0.1:8601), GPU_USD_PER_HOUR,
# plus GPU_UTIL_27B / GPU_UTIL_4B (vLLM memory shares, default 0.62 / 0.08).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/common.sh
UTIL_27B="${GPU_UTIL_27B:-0.62}"
UTIL_4B="${GPU_UTIL_4B:-0.08}"
D3_GIB=24   # rough GPU memory for the Router's d3-mini runtime (weights ~8.5 GB plus working memory)

say "Checks"
command -v rocm-smi >/dev/null || die "rocm-smi not found: install ROCm (the AMD GPU driver stack) first"
$DOCKER info >/dev/null 2>&1 || die "'$DOCKER info' failed: install Docker, or set DOCKER=\"sudo docker\" if it needs sudo"
command -v "$VLLM_SR" >/dev/null || die "vllm-sr CLI not found at $VLLM_SR (see README.md: python3 -m venv .venv-sr ...), or set VLLM_SR"
[ -x .venv/bin/python ] || die ".venv missing: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
HUB="${HF_HOME:-$HOME/.cache/huggingface}/hub"
for m in Qwen--Qwen3.8-27B Qwen--Qwen3.5-4B; do
  [ -d "$HUB/models--$m" ] || die "model $m not in $HUB: run deploy/download_models.sh"
done
touch "$STATE_FILE"
started() { grep -qx "$1" "$STATE_FILE" || echo "$1" >> "$STATE_FILE"; }

need_27b=1; need_4b=1; need_router=1
curl -sf -m 3 http://127.0.0.1:8102/health >/dev/null && need_27b=0 && echo "Qwen 27B already answers on :8102, leaving it"
curl -sf -m 3 http://127.0.0.1:8104/health >/dev/null && need_4b=0 && echo "Qwen 4B already answers on :8104, leaving it"
curl -sf -m 3 "$ROUTER_URL/v1/models" >/dev/null && need_router=0 && echo "Router already answers at $ROUTER_URL, leaving it"
[ $need_27b = 1 ] && port_busy 8102 && die "port 8102 is in use by something that is not a healthy Qwen 27B"
[ $need_4b = 1 ] && port_busy 8104 && die "port 8104 is in use by something that is not a healthy Qwen 4B"
if [ $need_router = 1 ]; then
  port_busy 8899 && die "port 8899 is in use by something that is not a healthy Router"
  container_exists "$ROUTER_CONTAINER" && die "a vllm-sr Router stack ($ROUTER_CONTAINER) already exists on this host; \
vllm-sr runs one per host. Stop it ('$VLLM_SR stop') if it is not needed, or reuse it with deploy/router.yaml's models added."
fi

# Free GPU memory: vLLM's --gpu-memory-utilization is a share of the whole GPU, and the Router needs room for d3-mini.
read -r total free < <(gpu_mem) || die "could not read GPU $GPU memory from rocm-smi"
need=$(awk -v t="$total" -v a="$UTIL_27B" -v b="$UTIL_4B" -v n27="$need_27b" -v n4="$need_4b" -v nr="$need_router" -v d="$D3_GIB" \
  'BEGIN{printf "%.0f", t*(a*n27 + b*n4) + nr*d*2^30}')
echo "GPU $GPU ($(.venv/bin/python -I -c 'import sys; sys.path.insert(0, "app"); import gpuinfo; print(gpuinfo.gpu_name())' 2>/dev/null || echo unknown)):" \
  "$((free >> 30)) GiB free of $((total >> 30)) GiB, about $((need >> 30)) GiB needed"
[ "$free" -ge "$need" ] || die "not enough free GPU memory. Stop other GPU work, or lower GPU_UTIL_27B/GPU_UTIL_4B (less KV cache)."

# The Router reaches the vLLM containers by name on this network; vllm-sr reuses it when it exists.
$DOCKER network inspect vllm-sr-network >/dev/null 2>&1 || $DOCKER network create vllm-sr-network >/dev/null

# One vLLM server at a time: each measures the GPU's free memory while it loads, so two loading together on one GPU
# can leave the second with no room for its KV cache ("Engine core initialization failed").
if [ $need_27b = 1 ]; then
  say "Starting Qwen 27B (rid-qwen27b, :8102; ~3-5 minutes, the first start compiles kernels and takes longer)"
  GPU_UTIL="$UTIL_27B" deploy/serve_qwen.sh qwen27b; started qwen27b
  wait_http http://127.0.0.1:8102/health 1800 rid-qwen27b GPU_UTIL_27B || die "Qwen 27B did not come up"
fi
echo "Qwen 27B ready"
if [ $need_4b = 1 ]; then
  say "Starting Qwen 4B (rid-qwen4b, :8104; ~2-3 minutes)"
  need_free "$(awk -v t="$total" -v b="$UTIL_4B" -v nr="$need_router" -v d="$D3_GIB" 'BEGIN{printf "%.0f", t*b + nr*d*2^30}')" \
    "Qwen 4B$([ "$need_router" = 1 ] && echo " and the Router's d3-mini")"
  GPU_UTIL="$UTIL_4B" deploy/serve_qwen.sh qwen4b; started qwen4b
  wait_http http://127.0.0.1:8104/health 1800 rid-qwen4b GPU_UTIL_4B || die "Qwen 4B did not come up"
fi
echo "Qwen 4B ready"

if [ $need_router = 1 ]; then
  say "Starting the vllm-sr Router with d3-mini (:8899; the first start downloads and compiles, up to ~30 minutes)"
  need_free $((D3_GIB << 30)) "the Router's d3-mini"
  # Run from deploy/: vllm-sr keeps its state (.vllm-sr/) and model cache (models/) next to the config file.
  (cd deploy && "${SR[@]}" serve --config router.yaml --platform rocm --minimal --replace-active-config)
  started router
fi
wait_http "$ROUTER_URL/v1/models" 300 || die "the Router does not answer at $ROUTER_URL"
echo "Router ready"

say "Starting the demo server"
curl -sf -m 3 "http://$HOST:$PORT/health" >/dev/null && echo "demo server already running on $HOST:$PORT (restarting it)"
./start_server.sh --bg >/dev/null
started server
wait_http "http://$HOST:$PORT/health" 60 || die "demo server did not start; see logs/server.log"
# The server probes every part through the Router (a tiny image to D3 and the 27B); allow a few rounds.
for _ in $(seq 12); do
  health=$(curl -s -m 30 "http://$HOST:$PORT/health")
  case "$health" in *'"ok": true'*|*'"ok":true'*) break ;; esac
  sleep 5
done
echo "health: $health"
case "$health" in *'"ok": true'*|*'"ok":true'*) ;; *) die "health is not ok: the false entry names the part that is down" ;; esac

say "Up. Open http://$HOST:$PORT (from a laptop: ssh -L $PORT:localhost:$PORT <user>@<this server>)"
echo "First start on this server? Run deploy/measure.sh once (~2 min) so the comparison uses this GPU's numbers."
echo "Next: deploy/smoke_test.sh"
