"""Compares D3 runs on data/phase0: per question, the problem image's yes-probability against the good receipts'.
Usage: d3_summary.py name=results.json [...]"""
import json, statistics, sys
PROBLEM = {"blurry": "13_", "complete": "14_", "damaged": "15_", "glare": "16_", "multiple": "17_"}
NONRECEIPT = {"18_": "photo", "19_": "screenshot", "20_": "other_document"}
for arg in sys.argv[1:]:
    name, path = arg.split("=")
    rows = {x["file"]: x for x in json.load(open(path))}
    ans = {f: x["result"]["answers"] for f, x in rows.items()}
    good = [f for f in ans if "_good_" in f]
    print(f"== {name}  median {statistics.median(x['ms'] for x in rows.values()):.0f} ms")
    kind_ok = sum(ans[f]["kind"]["choice"] == "receipt" for f in good) + sum(
        ans[f]["kind"]["choice"] == k for f in ans for p, k in NONRECEIPT.items() if f.startswith(p))
    print(f"   kind correct {kind_ok}/15 (12 receipts + photo, screenshot, menu); cut-off image -> {ans[[f for f in ans if f.startswith('14_')][0]]['kind']['choice']}")
    for q, p in PROBLEM.items():
        f = [x for x in ans if x.startswith(p)][0]
        g = [ans[x][q]["noul"] for x in good]
        v = ans[f][q]["noul"]
        if q == "complete":  # problem = low
            sep = min(g) - v; print(f"   {q:15} cut-off {v:.2f}  good min {min(g):.2f}  margin {sep:+.2f}")
        else:
            sep = v - max(g); print(f"   {q:15} problem {v:.2f}  good max {max(g):.2f}  margin {sep:+.2f}")
    g = [ans[x]["total_readable"]["noul"] for x in good]
    print(f"   total_readable  good receipts {min(g):.2f}-{max(g):.2f}; cut-off {ans[[f for f in ans if f.startswith('14_')][0]]['total_readable']['noul']:.2f}")
