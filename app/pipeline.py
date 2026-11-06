"""One upload through the whole flow, as a stream of (event, data) pairs (docs/API.md, POST /api/check)."""
import json
import time
import traceback

import check
import db
import gate
import keys
import readable
from extract import MODEL as EXTRACT_MODEL, TooLong, extract
from message import MODEL as MESSAGE_MODEL, template, write_message
from sumcheck import sumcheck


def bad_problems(reasons):
    """The problems a rejection counts under: its bad reasons, or only the kind when it is not a receipt."""
    bad = [r for r in reasons if r["status"] == "bad"]
    return bad[:1] if bad and bad[0]["key"] == "kind" else bad


def stats(con, visitor_id):
    """GET /api/stats for one visitor, derived from the database, so it matches their Receipts/Rejections tabs.
    Counts are per stored run."""
    out = {"uploads": 0, "extracted": 0, "needs_review": 0, "rejected": 0, "by_problem": {},
           "money_total_by_currency": {}}
    for r in db.all_runs(con, visitor_id):
        out["uploads"] += 1
        if r["tbl"] == "receipts":
            out["extracted"] += 1; out["needs_review"] += int(r["needs_review"] or 0)
            if r["currency"] and r["total"] is not None:
                m = out["money_total_by_currency"]; m[r["currency"]] = round(m.get(r["currency"], 0) + r["total"], 2)
        else:
            out["rejected"] += 1
            for x in bad_problems(json.loads(r["reasons_json"])):
                out["by_problem"][x["key"]] = out["by_problem"].get(x["key"], 0) + 1
    return out


def reasoning_tokens(usage):
    """Of the completion tokens, those the model spent reasoning (None when the API does not say)."""
    return (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")


class Cancelled(Exception):
    """The client stopped the run (Stop button or closed page); nothing is stored."""


STEP_MODEL = {"d3": "d3", "message": "retake", "extract": "extract"}
UNAVAILABLE = {"d3": "Receipt checking is unavailable right now. Please try again in a minute.",
               "extract": "Your receipt passed the checks, but reading is unavailable right now. Please try again shortly."}
SKIPPED = {"d3": ["route", "model", "stored"], "extract": ["sumcheck", "stored"], "message": ["stored"]}  # steps that did not run after a failure
FALLBACK_WORDS = {"timeout": "timed out", "busy": "busy"}  # else "unavailable"


def failure(step, e):
    """The `error` event for a failed step, in plain words only: the reason, one line on what failed, the message for
    the user and the steps skipped. Exception text and model responses never reach it (they go to the log)."""
    if isinstance(e, gate.KeyFailed):  # the visitor's key, not the model: said as such, never hidden by a fallback
        return {"step": step, "reason": "key", "code": e.code, "detail": e.line(STEP_MODEL.get(step)),
                "message": keys.run_message(e.code), "skipped": SKIPPED.get(step, [])}
    if isinstance(e, TooLong):
        reason, detail, message = "too_long", "The receipt is too long to read", str(e)  # already plain words
    elif isinstance(e, gate.ModelError):
        reason, detail = e.reason, e.line(STEP_MODEL.get(step))
        message = gate.BUSY if e.reason == "busy" else UNAVAILABLE.get(step, "Something went wrong. Please try again.")
    else:
        print(f"step {step} failed:\n{traceback.format_exc()}", flush=True)
        reason, detail = "error", "Something went wrong in this step"
        message = UNAVAILABLE.get(step, "Something went wrong. Please try again.")
    return {"step": step, "reason": reason, "detail": detail, "message": message, "skipped": SKIPPED.get(step, [])}


def standard_message(route, e):
    """The re-upload message when the LLM fails: message.template() and the label the step shows."""
    if not isinstance(e, gate.ModelError):
        print(f"LLM message failed:\n{traceback.format_exc()}", flush=True)
    why = FALLBACK_WORDS.get(getattr(e, "reason", None), "unavailable")
    return template(route), f"standard message (LLM {why})"


def run(con, upload_id, file, image_bytes, mime, size, image_sha256=None, cancelled=lambda: False, visitor_id=None):
    t0 = time.perf_counter()
    steps = {}
    image_sha256 = image_sha256 or db.sha256(image_bytes)
    yield "received", {"id": upload_id, "file": file, "width": size[0], "height": size[1],
                       "image_url": db.image_url({"upload_id": upload_id, "file": file})}
    def go(next_step):
        if cancelled():
            raise Cancelled
        return next_step

    step = "d3"
    try:
        step = go("d3")
        size_gate = readable.judge(size)  # before D3, no model: too few pixels to read is a reason of its own
        answers, steps["d3"], d3_tokens = check.ask_d3(image_bytes, mime, with_tokens=True)
        d3, route = check.decide(answers, size_gate)
        d3 = {"model": check.MODEL, "ms": steps["d3"], "tokens": d3_tokens, **d3}
        yield "d3", d3
        yield "route", route
        if route["route"] == "reject":
            step = go("message"); t = time.perf_counter()
            try:
                text, steps["message"], usage, picked = write_message(d3, route)
                msg = {"text": text, "model": MESSAGE_MODEL, "routed": picked, "ms": steps["message"],
                       "tokens": usage.get("total_tokens"), "completion_tokens": usage.get("completion_tokens"),
                       "reasoning_tokens": reasoning_tokens(usage)}
            except gate.KeyFailed:
                raise  # the key failed, not the LLM: the run stops with a key error (no standard message)
            except Exception as e:  # noqa: BLE001 - any LLM failure: a fixed message from D3's reasons instead
                text, fallback = standard_message(route, e)
                steps["message"] = round((time.perf_counter() - t) * 1000, 1)
                msg = {"text": text, "model": "template", "ms": steps["message"], "tokens": None, "fallback": fallback}
            yield "message", msg
            step = go("db"); t = time.perf_counter()
            rid = db.add_rejection(con, upload_id, file, route["reasons"], text, d3, steps["message"], image_sha256,
                                   visitor_id)
            steps["db"] = round((time.perf_counter() - t) * 1000, 1)
            yield "stored", {"table": "rejections", "id": rid, "needs_review": 0}
        else:
            step = go("extract")
            rc, steps["extract"], usage, picked = extract(image_bytes, mime)
            yield "extract", {"receipt": rc, "model": EXTRACT_MODEL, "routed": picked, "ms": steps["extract"],
                              "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                              "reasoning_tokens": reasoning_tokens(usage)}
            step = "sumcheck"; t = time.perf_counter()
            sc = sumcheck(rc)
            steps["sumcheck"] = round((time.perf_counter() - t) * 1000, 2)
            yield "sumcheck", sc
            needs_review = route["route"] == "review" or not sc["ok"]
            step = go("db"); t = time.perf_counter()
            rid = db.add_receipt(con, upload_id, file, rc, sc, d3, needs_review, steps["extract"], image_sha256, visitor_id)
            steps["db"] = round((time.perf_counter() - t) * 1000, 1)
            yield "stored", {"table": "receipts", "id": rid, "needs_review": int(needs_review)}
        yield "done", {"total_ms": round((time.perf_counter() - t0) * 1000, 1), "steps": steps}
    except Cancelled:
        return
    except Exception as e:  # noqa: BLE001 - reported to the UI as the step that failed, in plain words
        yield "error", failure(step, e)
