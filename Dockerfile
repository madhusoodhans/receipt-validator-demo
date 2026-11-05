# Hugging Face Space (Docker SDK): the demo server only, no models. D3 (vllm-sr/d3-flash) and the LLM (vllm-sr/auto)
# are called on the hosted vllm-sr API at https://app.vllm-sr.ai. Set VLLM_SR_API_KEY as a Space secret (the server
# refuses to start without it). Serves the committed web/; a fresh database and upload folder in /tmp on every start.
# Space settings: PLAY_ALL=0 (no Play all button), TRUST_PROXY=1 (per-IP limits read the client address the HF proxy
# adds to X-Forwarded-For), LANDING_URL (optional: the demos landing page the top bar links back to).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2 \
    HOST=0.0.0.0 PORT=7860 PLAY_ALL=0 TRUST_PROXY=1 LANDING_URL= \
    ROUTER_URL=https://app.vllm-sr.ai D3_MODEL=vllm-sr/d3-flash EXTRACT_MODEL=vllm-sr/auto MESSAGE_MODEL=vllm-sr/auto \
    DB=/tmp/receipt-intake/receipts.db UPLOAD_ROOT=/tmp/receipt-intake/uploads

RUN useradd -m -u 1000 user
WORKDIR /home/user/app

COPY requirements.txt .
# huggingface_hub is only for downloading models for the local stack (deploy/)
RUN grep -v '^huggingface_hub' requirements.txt > /tmp/req.txt && pip install --no-cache-dir -r /tmp/req.txt

COPY --chown=user app/ app/
COPY --chown=user web/ web/
COPY --chown=user data/demo/ data/demo/

USER user
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/healthz')"
CMD ["python", "-I", "app/server.py"]
