---
title: Receipt Intake Demo
colorFrom: gray
colorTo: red
sdk: docker
app_port: 7860
pinned: false
short_description: Check receipt photos with D3 before an LLM reads them
---

<!-- Draft: the Space's README.md. Copy it to the root of the Space repository, with the Dockerfile, app/, web/,
     data/demo/ and requirements.txt from this repo. Set ROUTER_URL (and any API key) as Space variables/secrets. -->

# Receipt intake demo

Upload a photo of a receipt, or pick a demo image. A small decision model (D3) checks the photo first: is it a
receipt, is it sharp, complete, free of glare, a single receipt, with nothing added to it (such as a note telling the AI
what to do)? Good photos are read into structured data by Qwen 27B; bad ones get a short re-upload message from Qwen 4B,
without the large model ever seeing them.

## Before you upload

- **Do not upload receipts with personal data** (names, card numbers, addresses, loyalty numbers). Use the demo images
  or a receipt you are happy to share.
- **Requests to the model APIs may be logged** by the model provider. Images and results are sent to them to be read.
- Your results are visible only in your own browser tab, and are deleted from this Space after 60 minutes without
  activity, when you press Reset, or when the Space restarts. Uploads are limited per minute; the demo may answer
  "The service is busy, please try again." when many people use it at once.

## Credits

- **CORD** receipts ([naver-clova-ix/cord-v2](https://huggingface.co/datasets/naver-clova-ix/cord-v2),
  CC BY 4.0, from [clovaai/cord](https://github.com/clovaai/cord)): 12 of the 24 demo images are CORD test images
  (converted to JPEG), one more is cropped from one, and one has a synthetic sticky note drawn on. Park et al., "CORD: A Consolidated Receipt Dataset for
  Post-OCR Parsing", Document Intelligence Workshop at NeurIPS 2019.
- Other demo images and their sources: see the project README (Credits and licences).
- Models: [vllm-sr/d3-mini](https://huggingface.co/vllm-sr/d3-mini), [Qwen/Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B),
  [Qwen/Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) (Apache-2.0), served through the
  [vLLM Semantic Router](https://github.com/vllm-project/semantic-router).
