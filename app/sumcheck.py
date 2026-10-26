"""Arithmetic checks on an extracted receipt (no model). A failed check sends the receipt to review."""
from datetime import date


def _close(a, b, total):
    return abs(a - b) <= max(0.011, abs(total) * 0.005)  # cents, or 0.5 % of the total for rounding lines


def _fmt(x, cents=None):
    """Like the page shows the receipt: two decimals for every amount when any amount on it has cents, else none."""
    return f"{x:,.2f}" if (cents if cents is not None else x % 1) else f"{x:,.0f}"


def sumcheck(rc):
    """Returns {"ok": bool, "checks": [...]} as in docs/API.md. Checks that cannot run are left out."""
    checks = []
    total = rc.get("total")
    amounts = [i.get("amount") for i in rc.get("items") or []] + [rc.get(k) for k in ("subtotal", "tax", "service_charge", "discount", "total")]
    cents = any(v is not None and v % 1 for v in amounts)
    fmt = lambda x: _fmt(x, cents)  # noqa: E731
    items = [i["amount"] for i in rc.get("items") or [] if i.get("amount") is not None]
    sub, tax, svc, disc = (rc.get(k) for k in ("subtotal", "tax", "service_charge", "discount"))
    if items and total is not None:
        target, name = (sub, "subtotal") if sub is not None else (total, "total")
        s = sum(items)
        terms = f"{' + '.join(fmt(x) for x in items[:6])}{' + ...' if len(items) > 6 else ''}"
        ok = _close(s, target, total)
        detail = f"{terms} = {fmt(s)}; {name} {fmt(target)}"
        if not ok and disc and _close(s - abs(disc), target, total):
            ok, detail = True, f"{terms} - discount {fmt(abs(disc))} = {fmt(s - abs(disc))}; {name} {fmt(target)}"
        checks.append({"name": "items_sum", "label": f"Items add up to the {name}", "ok": ok, "detail": detail})
    if sub is not None and total is not None:
        plus = [(f"{name} {fmt(v)}", v) for name, v in (("tax", tax), ("service", svc)) if v]
        extra = sum(v for _t, v in plus)
        # (value, how it was formed); the detail shows the one that matched, so its numbers add up as shown
        candidates = [(sub + extra, plus, False)]
        if disc:
            candidates.append((sub + extra - abs(disc), plus, True))
        if plus:
            candidates.append((sub, [], False))  # tax and service already included in the subtotal
        hit = next((c for c in candidates if _close(c[0], total, total)), None)
        ok = hit is not None
        value, terms, minus = hit or candidates[0]
        expr = " + ".join([fmt(sub)] + [t for t, _v in terms]) + (f" - discount {fmt(abs(disc))}" if minus else "")
        detail = f"{expr} = {fmt(value)}; total {fmt(total)}" if expr != fmt(sub) else f"subtotal {fmt(sub)}; total {fmt(total)}"
        if ok and plus and not terms:
            detail += f" ({' and '.join(t.split()[0] for t, _v in plus)} included in the subtotal)"
        checks.append({"name": "total", "label": "Subtotal plus tax and service gives the total", "ok": ok,
                       "detail": detail})
    if rc.get("date"):
        try:
            d = date.fromisoformat(rc["date"]); ok = date(1990, 1, 1) <= d <= date.today()
            detail = d.isoformat()
        except ValueError:
            ok, detail = False, f"cannot read '{rc['date']}'"
        checks.append({"name": "date", "label": "The date is a real date", "ok": ok, "detail": detail})
    if total is not None:
        checks.append({"name": "total_positive", "label": "The total is above zero", "ok": total > 0, "detail": fmt(total)})
    return {"ok": all(c["ok"] for c in checks), "checks": checks}
