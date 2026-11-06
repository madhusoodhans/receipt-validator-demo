"""fixes-1 and fixes-2 rules without model calls: the sum check (R3 null total and subtotal copies, R6 tax-inclusive item lines),
the size gate (R4) and its place in the routing rule. Receipts here are made up, shaped like the formats the fixes
are for; no eval image is used.

  .venv/bin/python tests/test_fixes.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import check  # noqa: E402
import readable  # noqa: E402
from sumcheck import sumcheck  # noqa: E402


def rc(items, total, subtotal=None, tax=None, label="TOTAL", **kw):
    return {"store_name": "X", "date": "2018-01-02", "currency": "RM", "items": [{"name": f"i{k}", "quantity": 1, "amount": a}
            for k, a in enumerate(items)], "subtotal": subtotal, "tax": tax, "service_charge": kw.get("svc"),
            "discount": kw.get("disc"), "total_label": label, "total": total}


def failed(r):
    return [c["name"] for c in sumcheck(r)["checks"] if not c["ok"]]


def test_r6_tax_inclusive_items():
    # GST receipt: item lines include 6 % tax, subtotal excludes it, total = subtotal + tax = sum of items
    assert failed(rc([10.60, 21.20], 31.80, subtotal=30.00, tax=1.80)) == []
    # with a rounding line (0.5 % tolerance) and a discount
    assert failed(rc([10.60, 21.20], 31.80, subtotal=30.00, tax=1.80, disc=0.0)) == []
    assert failed(rc([10.60, 21.20, 5.30], 31.80, subtotal=30.00, tax=1.80, disc=5.30)) == []
    # still caught: items that match neither the subtotal nor the total
    assert "items_sum" in failed(rc([10.60, 25.00], 31.80, subtotal=30.00, tax=1.80))
    # no tax line: items equal to the total but not the subtotal stay a failure (nothing explains the gap)
    assert "items_sum" in failed(rc([10.60, 21.20], 31.80, subtotal=30.00))
    # the ordinary case is unchanged
    assert failed(rc([10.0, 20.0], 31.8, subtotal=30.0, tax=1.8)) == []


def test_r3_null_total_goes_to_review():
    r = sumcheck(rc([10.0, 20.0], None, subtotal=30.0, label=None))
    assert not r["ok"] and [c["name"] for c in r["checks"] if not c["ok"]] == ["total_read"]


def test_r3_subtotal_copy():
    assert "total_line" in failed(rc([10.0, 20.0], 30.0, subtotal=30.0, label=None))      # no total line read
    assert "total_line" in failed(rc([10.0, 20.0], 30.0, subtotal=30.0, label="SubTTL"))  # read from the subtotal line
    assert "total_line" in failed(rc([10.0, 20.0], 30.0, subtotal=30.0, label="Sub Total"))
    assert failed(rc([10.0, 20.0], 30.0, subtotal=30.0, label="GRAND TOTAL")) == []
    assert failed(rc([10.0, 20.0], 30.0, label="Total")) == []
    # an extraction without total_label (older stored rows) is not judged on it
    old = rc([10.0, 20.0], 30.0); del old["total_label"]
    assert failed(old) == []


ANSWERS = {"kind": {"choice": "receipt", "probabilities": {"receipt": 0.99}},
           **{k: {"noul": 0.9 if bad == "low" else 0.05} for k, (_s, _l, bad, _b, _t) in check.CHECKS.items()}}


def test_r4_size_gate():
    assert readable.judge((864, 1296))["status"] == "ok"
    assert readable.judge((228, 336))["status"] == "ok"  # the smallest real tuning photo
    assert readable.judge((150, 230))["status"] == "borderline"
    assert readable.judge((100, 160))["status"] == "bad"
    assert check.decide(ANSWERS, readable.judge((864, 1296)))[1]["route"] == "extract"
    assert check.decide(ANSWERS, readable.judge((150, 230)))[1]["route"] == "review"
    d3, route = check.decide(ANSWERS, readable.judge((100, 160)))
    assert route["route"] == "reject" and route["reasons"][0]["key"] == "size" and d3["size"]["status"] == "bad"
    assert check.decide(ANSWERS)[1]["route"] == "extract"  # no size given: as before


def kind(choice, **p):
    probs = {k: 0.0 for k in check.KIND["criteria"]}; probs.update(p); probs.setdefault(choice, 0.9)
    return {"choice": choice, "probabilities": probs}


def test_r5_kind_in_one_question():
    """fixes-2: the fixes-1 `kind_strict` and `multiple` questions are folded into the one kind question."""
    inv = {**ANSWERS, "kind": kind("invoice", invoice=0.8, other_document=0.1)}
    assert check.decide(inv)[1]["route"] == "extract"
    # an invoice that may be a budget, statement or quote: read, stored for review
    d3, route = check.decide({**inv, "kind": kind("invoice", invoice=0.6, other_document=0.35)})
    assert route["route"] == "review" and route["reasons"][0]["key"] == "kind_doubt"
    # another document that may still be an invoice: read for review, not sent back; a clear other document is
    assert check.decide({**ANSWERS, "kind": kind("other_document", other_document=0.7, invoice=0.25)})[1]["route"] == "review"
    assert check.decide({**ANSWERS, "kind": kind("other_document", other_document=0.85, invoice=0.1)})[1]["route"] == "reject"
    # ... and a document kept on p(invoice) gets the invoice rule for the total: review, not reject
    cut = {**ANSWERS, "complete": {"noul": 0.1}, "kind": kind("other_document", other_document=0.7, invoice=0.25)}
    assert check.decide(cut)[1]["route"] == "review"
    # several receipts: rejected with the "multiple" reason (the retake message names it), unless it may be an invoice
    d3, route = check.decide({**ANSWERS, "kind": kind("several", several=0.95, invoice=0.01)})
    assert route["route"] == "reject" and [r["key"] for r in route["reasons"]] == ["multiple"]
    # a receipt is not second-guessed, and screenshots and photos are rejected whatever p(invoice) is
    assert check.decide({**ANSWERS, "kind": kind("receipt", receipt=0.7, other_document=0.3)})[1]["route"] == "extract"
    assert check.decide({**ANSWERS, "kind": kind("photo", photo=0.6, invoice=0.3)})[1]["route"] == "reject"
    assert list(check.questions()) == ["kind", "complete", "blurry", "damaged", "glare", "added_text"]


def test_complete_total_merged():
    """fixes-2: one question for a cut-off receipt and a hidden total; for an invoice it only sends to review."""
    cut = {**ANSWERS, "complete": {"noul": 0.2}}
    assert check.decide(cut)[1]["route"] == "reject"
    assert check.decide({**cut, "complete": {"noul": 0.5}})[1]["route"] == "review"
    assert check.decide({**cut, "kind": kind("invoice", invoice=0.9)})[1]["route"] == "review"


def test_retake_template():
    import message
    _, route = check.decide({**ANSWERS, "kind": kind("several", several=0.95)})
    assert "more than one receipt" in message.template(route)
    _, route = check.decide({**ANSWERS, "complete": {"noul": 0.1}})
    assert "total" in message.template(route)


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f(); print("ok", name)
