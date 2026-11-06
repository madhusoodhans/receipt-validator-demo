"""Size gate before D3 (no model): an upload with too few pixels cannot have readable receipt text, whatever D3 says.
D3 reads a small image as it is and often passes it (a 100 x 160 px receipt photo scored as a clean receipt), and the
LLM then guesses the numbers.

The measure is the image's size as sqrt(width x height) of the upload as the models get it (after prepare_upload).
Thresholds come from the tuning data (bench/fixes/tune_report.py, results/fixes-1/TUNING.md): CORD train receipts
shrunk until their words are 4, 6 and 9 px tall, against every real tuning photo (CORD train and test, demo images,
RVL-CDIP pages), the smallest of which is 271 px (a 228 x 336 CORD photo whose total reads correctly).

A line-pitch estimate of character height was tried first and dropped: on real photos it was too noisy (5th
percentile 7 px on full-size CORD photos, the same as 4 px text), see results/fixes-1/tune_r4_pitch_trial.jsonl.
"""
REJECT_PX = 180  # sqrt(w*h) below this: sent back ("too small to read")
REVIEW_PX = 240  # below this: read, but stored for review

LABEL = "Image too small to read"


def size_px(size):
    w, h = size
    return (w * h) ** 0.5


def judge(size):
    """{"key": "size", "label", "px", "status": bad | borderline | ok} for an upload of `size` (width, height)."""
    px = size_px(size)
    status = "bad" if px < REJECT_PX else "borderline" if px < REVIEW_PX else "ok"
    return {"key": "size", "label": LABEL, "px": round(px), "status": status,
            "threshold": REJECT_PX, "borderline_from": REVIEW_PX}
