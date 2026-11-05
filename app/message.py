"""The LLM writes the re-upload message from D3's answers (text only; it never sees the image), through the vllm-sr
API's chat completions (vllm-sr/auto by default: the Router picks the model). When the LLM is down, template() builds a
fixed message from the same answers, with no model call."""
import os

import gate
from check import ROUTER_URL
from extract import DEBUG, routed

ROUTER = f"{ROUTER_URL}/v1/chat/completions"
MODEL = os.environ.get("MESSAGE_MODEL", "vllm-sr/auto")
SYSTEM = ("You write the message a receipt scanning app shows when an upload cannot be used. Write one or two short, "
          "friendly sentences: say what is wrong, then exactly how to retake the photo. Name every problem listed. "
          "If the image is not a receipt, ask for a photo of the paper receipt. "
          "No greeting, no apology, no technical terms, no probabilities.")
ADVICE = {
    "kind": "the image is not a receipt",
    "complete": "part of the receipt is cut off (often the total at the bottom)",
    "blurry": "the text is blurry or out of focus",
    "damaged": "the receipt is crumpled, torn, faded or stained",
    "glare": "glare or a shadow covers part of the text",
    "multiple": "there is more than one receipt in the photo",
    "added_text": "the receipt seems to have added notes or text, so ask for a photo of the original receipt only",
}
# template(): what is wrong and how to fix it, per problem, in the words the LLM is asked to use
PROBLEM = {
    "complete": ("part of the receipt is cut off", "fit the whole receipt in the frame, from the store name to the total"),
    "blurry": ("the text is blurry", "hold the phone steady and let it focus"),
    "damaged": ("the receipt is crumpled, torn or stained", "flatten it on a table"),
    "glare": ("glare or a shadow covers part of the text", "move out of direct light"),
    "multiple": ("there is more than one receipt in the photo", "photograph one receipt at a time"),
    "added_text": ("the receipt seems to have added notes or text", "upload a photo of the original receipt only"),
}


def bad_reasons(route):
    bad = [r for r in route["reasons"] if r["status"] == "bad"]
    return bad[:1] if bad and bad[0]["key"] == "kind" else bad  # not a receipt: the receipt checks do not apply


def describe(d3, route):
    lines = [f"What the image shows: {d3['kind']['choice'].replace('_', ' ')} "
             f"(confidence {d3['kind']['probabilities'][d3['kind']['choice']]:.2f})."]
    lines.append("Problems found:")
    for r in bad_reasons(route):
        what = ADVICE[r["key"]] if r["key"] != "kind" else f"{ADVICE['kind']} ({r['label'].split(',')[0].lower()})"
        lines.append(f"- {what} (confidence {r['prob']:.2f})")
    return "\n".join(lines)


def template(route):
    """A fixed re-upload message from D3's reasons, for when the LLM cannot write one."""
    bad = [r["key"] for r in bad_reasons(route)]
    if not bad or bad[0] == "kind":
        return "This image does not look like a receipt. Please upload a photo of the paper receipt."
    known = [PROBLEM[k] for k in bad if k in PROBLEM]
    join = lambda xs: xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]  # noqa: E731
    return (f"This photo cannot be used: {join([w for w, _ in known])}. "
            f"Please {join([f for _, f in known])}, then try again.")


MAX_TOKENS = 600  # the message is ~40 tokens; room in case the Router turns reasoning on, which counts too


def write_message(d3, route, timeout=None):
    """Returns (text, ms, usage, routed) with usage the response's token counts (prompt, completion, total) and routed
    what the Router picked. Raises gate.ModelError; an empty answer raises ValueError (the caller uses template())."""
    body = {"model": MODEL, "temperature": 0, "max_tokens": MAX_TOKENS,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": describe(d3, route)}]}
    r, ms = gate.post("chat", ROUTER, body, timeout or gate.timeout("retake"), model="retake", headers=DEBUG)
    out = r.json()
    text = (out["choices"][0]["message"].get("content") or "").strip()
    if not text:
        raise ValueError(f"empty message (finish_reason {out['choices'][0].get('finish_reason')})")
    return text, ms, out.get("usage") or {}, routed(r)
