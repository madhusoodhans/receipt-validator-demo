# Hugging Face Space (Docker SDK) for the demo server only: no models in the image. D3 and the Qwen models are called
# at ROUTER_URL, set as a Space variable. Serves the committed web/; a fresh database and upload folder in /tmp on
# every start. Space settings: PLAY_ALL=0 (no Play all button),
# LANDING_URL (optional: the demos landing page the top bar links back to). For hosted model APIs set ROUTER_API_KEY as a secret.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2 \
    HOST=0.0.0.0 PORT=7860 PLAY_ALL=0 LANDING_URL= \
    DB=/tmp/receipt-intake/receipts.db UPLOAD_ROOT=/tmp/receipt-intake/uploads

RUN useradd -m -u 1000 user
WORKDIR /home/user/app

COPY requirements.txt .
# huggingface_hub is only for downloading models on the GPU host
RUN grep -v '^huggingface_hub' requirements.txt > /tmp/req.txt && pip install --no-cache-dir -r /tmp/req.txt

COPY --chown=user app/ app/
COPY --chown=user web/ web/
COPY --chown=user data/demo/ data/demo/

USER user
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/healthz')"
CMD ["python", "-I", "app/server.py"]
