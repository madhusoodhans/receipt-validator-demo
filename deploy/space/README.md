---
title: Receipt Intake Demo
colorFrom: gray
colorTo: red
sdk: docker
app_port: 7860
pinned: false
short_description: Check receipt photos with D3 before an LLM reads them
---

<!-- The Space's README.md. Copy it to the root of the Space repository, with the Dockerfile, app/, web/, data/demo/
     and requirements.txt from this repo. Set VLLM_SR_API_KEY as a Space SECRET (the server will not start without it),
     and optionally LANDING_URL (the demos landing page; the top bar then links back to it) as a variable. The image
     already points at the hosted vllm-sr API and sets PLAY_ALL=0 and TRUST_PROXY=1. -->

# Receipt intake demo

Upload a photo of a receipt, or pick a demo image. A small decision model (D3) checks the photo first: is it a
receipt, is it sharp, complete, free of glare, a single receipt, with nothing added to it (such as a note telling the AI
what to do)? Good photos are read into structured data by an LLM; bad ones get a short re-upload message instead,
without ever being read. Both LLM calls go to `vllm-sr/auto`, where the vLLM Semantic Router picks the model and its
reasoning per request; the page shows what it picked.

## Before you upload

- **Do not upload receipts with personal data** (names, card numbers, addresses, loyalty numbers). Use the demo images
  or a receipt you are happy to share.
- **Uploads are processed by the hosted vllm-sr API** (app.vllm-sr.ai) for research and evaluation use, and requests
  may be logged there. Images and results are sent to it to be read.
- Your results are visible only in your own browser tab, and are deleted from this Space after 60 minutes without
  activity, when you press Reset, or when the Space restarts. Uploads are limited per minute; the demo may answer
  "The service is busy, please try again." when many people use it at once.

## Credits

- **CORD** receipts ([naver-clova-ix/cord-v2](https://huggingface.co/datasets/naver-clova-ix/cord-v2),
  CC BY 4.0, from [clovaai/cord](https://github.com/clovaai/cord)): 12 of the 24 demo images are CORD test images
  (converted to JPEG), one more is cropped from one, and one has a synthetic sticky note drawn on. Park et al., "CORD: A Consolidated Receipt Dataset for
  Post-OCR Parsing", Document Intelligence Workshop at NeurIPS 2019.
- Other demo images and their sources: see the project README (Credits and licences).
- Models: [vllm-sr/d3-flash](https://huggingface.co/vllm-sr/d3-flash) and the models behind `vllm-sr/auto`, served by
  the hosted API of the [vLLM Semantic Router](https://github.com/vllm-project/semantic-router).
