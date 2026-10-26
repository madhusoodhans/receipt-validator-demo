"""Raw d3-mini probabilities on data/demo (via the Router) -> results/d3_demo.json and a table."""
import json, sys
sys.path.insert(0, "app")
import check
L = json.load(open("data/demo/labels.json")); out = []
for l in L:
    a, ms = check.ask_d3(open(f"data/demo/{l['file']}", "rb").read())
    d3, route = check.decide(a); out.append({"file": l["file"], "ms": ms, "answers": a, "route": route["route"]})
json.dump(out, open("results/d3_demo.json", "w"), indent=1)
keys = list(check.CHECKS)
print(f"{'file':28}{'kind':>15}" + "".join(f"{k[:8]:>9}" for k in keys) + f"{'ms':>6} route (expected)")
for l, x in zip(L, out):
    a = x["answers"]
    print(f"{l['file'][:27]:28}{a['kind']['choice'][:14]:>15}" + "".join(f"{a[k]['noul']:9.2f}" for k in keys)
          + f"{x['ms']:6.0f} {x['route']} ({l['expected_route']})")
