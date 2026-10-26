"""One upload through the whole flow, as a stream of (event, data) pairs (docs/API.md, POST /api/check)."""
import json
import time

import check
import db
from extract import TooLong, extract
from message import write_message
from sumcheck import sumcheck


def bad_problems(reasons):
    """The problems a rejection counts under: its bad reasons, or only the kind when it is not a receipt."""
    bad = [r for r in reasons if r["status"] == "bad"]
    return bad[:1] if bad and bad[0]["key"] == "kind" else bad


def stats(con, demo_baseline):
    """GET /api/stats, derived from the database, so it survives a restart and matches the Receipts/Rejections tabs.

    Counts are per stored run. The model-time comparison is per distinct image (by content hash), using the first run
    of each, so clicking the same image again does not move it:
      routed   D3 + the 4B message (rejected) or the 27B read (accepted), as measured in that run.
      all_27b  what sending the same images straight to the 27B costs: the same 27B read for accepted images, and for
               rejected ones the 27B's measured time on that image (data/demo/baseline.json, or measured once in the
               background for uploads; until then the image is left out of both sides and counted as pending).
    """
    measured = {**db.baselines(con), **demo_baseline}
    out = {"uploads": 0, "extracted": 0, "needs_review": 0, "rejected": 0, "by_problem": {},
           "money_total_by_currency": {}}
    first = {}
    for r in db.all_runs(con):
        out["uploads"] += 1
        if r["tbl"] == "receipts":
            out["extracted"] += 1; out["needs_review"] += int(r["needs_review"] or 0)
            if r["currency"] and r["total"] is not None:
                m = out["money_total_by_currency"]; m[r["currency"]] = round(m.get(r["currency"], 0) + r["total"], 2)
        else:
            out["rejected"] += 1
            for x in bad_problems(json.loads(r["reasons_json"])):
                out["by_problem"][x["key"]] = out["by_problem"].get(x["key"], 0) + 1
        first.setdefault(r["image_sha256"] or f"upload:{r['upload_id']}", r)
    t = {"d3": 0.0, "message": 0.0, "extract": 0.0, "baseline_27b": 0.0}
    images = pending = 0
    for key, r in first.items():
        d3_ms = json.loads(r["d3_json"]).get("ms") or 0.0
        if r["tbl"] == "receipts":
            t["d3"] += d3_ms; t["extract"] += r["model_ms"] or 0.0
        elif key in measured:
            t["d3"] += d3_ms; t["message"] += r["model_ms"] or 0.0; t["baseline_27b"] += measured[key]
        else:
            pending += 1; continue
        images += 1
    out["comparison"] = {
        "images": images, "pending_baseline": pending, "repeats": out["uploads"] - len(first),
        "time_ms": {k: round(v, 1) for k, v in t.items()},
        "model_seconds": {"routed": round((t["d3"] + t["message"] + t["extract"]) / 1000, 2),
                          "all_27b": round((t["extract"] + t["baseline_27b"]) / 1000, 2)}}
    return out


class Cancelled(Exception):
    """The client stopped the run (Stop button or closed page); nothing is stored."""


STEP_SERVICE = {"d3": "D3 (vllm-sr/d3-mini via the Router on :8899)", "message": "Qwen 4B (via the Router on :8899)",
                "extract": "Qwen 27B (via the Router on :8899)"}
READ_TIMEOUT = {"d3": 20, "message": 30, "extract": 120}


def explain(step, e):
    """A plain-language error for the UI."""
    import requests
    if isinstance(e, TooLong):
        return str(e)  # already plain words
    who = STEP_SERVICE.get(step, step)
    if isinstance(e, requests.ConnectionError) and not isinstance(e, requests.ReadTimeout):
        return f"Cannot reach {who}. Is the model service running?"
    if isinstance(e, requests.Timeout):
        return f"{who} did not answer within {READ_TIMEOUT.get(step, '?')} s. The model service may be down or overloaded."
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"{who} returned HTTP {e.response.status_code}: {e.response.text[:200]}"
    return f"{type(e).__name__}: {e}"


def run(con, upload_id, file, image_bytes, mime, size, image_sha256=None, cancelled=lambda: False,
        baseline_ms=lambda sha: None, baseline_tokens=lambda sha: None):
    t0 = time.perf_counter()
    steps = {}
    image_sha256 = image_sha256 or db.sha256(image_bytes)
    yield "received", {"id": upload_id, "file": file, "width": size[0], "height": size[1],
                       "image_url": f"/api/uploads/{upload_id}"}
    def go(next_step):
        if cancelled():
            raise Cancelled
        return next_step

    step = "d3"
    try:
        step = go("d3")
        answers, steps["d3"], d3_tokens = check.ask_d3(image_bytes, mime, with_tokens=True)
        d3, route = check.decide(answers)
        d3 = {"model": check.MODEL, "ms": steps["d3"], "tokens": d3_tokens, **d3}
        yield "d3", d3
        yield "route", route
        if route["route"] == "reject":
            step = go("message")
            text, steps["message"], model_tokens = write_message(d3, route)
            yield "message", {"text": text, "model": "qwen4b", "ms": steps["message"], "tokens": model_tokens}
            step = go("db"); t = time.perf_counter()
            rid = db.add_rejection(con, upload_id, file, route["reasons"], text, d3, steps["message"], image_sha256)
            steps["db"] = round((time.perf_counter() - t) * 1000, 1)
            yield "stored", {"table": "rejections", "id": rid, "needs_review": 0}
        else:
            step = go("extract")
            rc, steps["extract"], usage = extract(image_bytes, mime)
            model_tokens = (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
            yield "extract", {"receipt": rc, "model": "qwen27b", "ms": steps["extract"],
                              "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")}
            step = "sumcheck"; t = time.perf_counter()
            sc = sumcheck(rc)
            steps["sumcheck"] = round((time.perf_counter() - t) * 1000, 2)
            yield "sumcheck", sc
            needs_review = route["route"] == "review" or not sc["ok"]
            step = go("db"); t = time.perf_counter()
            rid = db.add_receipt(con, upload_id, file, rc, sc, d3, needs_review, steps["extract"], image_sha256)
            steps["db"] = round((time.perf_counter() - t) * 1000, 1)
            yield "stored", {"table": "receipts", "id": rid, "needs_review": int(needs_review)}
        # This image alone: routed (D3 + 4B, or D3 + 27B) against sending it straight to the 27B. An accepted image
        # used the 27B on both sides; a rejected one uses the 27B's measured time on this image (None until measured).
        # Tokens likewise: everything D3 and the model read and wrote, against the 27B reading the image on its own.
        read = "extract" in steps
        comparison = {"image_sha256": image_sha256, "route": route["route"],
                      "routed_ms": round(steps["d3"] + (steps["extract"] if read else steps["message"]), 1),
                      "all_27b_ms": steps["extract"] if read else baseline_ms(image_sha256),
                      "routed_tokens": (d3_tokens or 0) + (model_tokens or 0), "d3_tokens": d3_tokens or 0,
                      "all_27b_tokens": model_tokens if read else baseline_tokens(image_sha256)}
        yield "done", {"total_ms": round((time.perf_counter() - t0) * 1000, 1), "steps": steps, "comparison": comparison}
    except Cancelled:
        return
    except Exception as e:  # noqa: BLE001 - reported to the UI as the step that failed
        yield "error", {"step": step, "message": explain(step, e)}
