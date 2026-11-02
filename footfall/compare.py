"""Before and after: the same crowd on two versions of a site.

A fix is worth what it saves the people who use the site. Given two crowd runs with the same
personas, this lines them up: who got there, in how many turns and seconds, and where along the
funnel the turns went (finding the product, choosing it, the cart, the checkout).
"""

import json
from pathlib import Path


def segments(funnel_names, result):
    """Turns spent between consecutive funnel stages, from the first screen to the last."""
    reached = result.get("funnel") or {}
    marks = [("start", 0)] + [(name, reached[name]) for name in funnel_names if name in reached]
    end = result.get("turns", marks[-1][1])  # someone who gave up spent the rest where they were
    return {name: next_turn - turn
            for (name, turn), (_, next_turn) in zip(marks, marks[1:] + [(None, end)])}


def side(crowd_dir):
    crowd = json.loads((Path(crowd_dir) / "crowd.json").read_text())
    scenario = json.loads(Path(crowd["scenario_file"]).read_text())
    names = [stage["name"] for stage in scenario.get("funnel", [])]
    return crowd, names, {r["persona"]: r for r in crowd["results"]}


def compare(before_dir, after_dir):
    before, names, b = side(before_dir)
    after, _, a = side(after_dir)
    rows = []
    for pid in [p for p in b if p in a]:
        rows.append({"persona": pid, "name": a[pid]["name"],
                     "before": pick(b[pid], names), "after": pick(a[pid], names)})
    stages = ["start"] + names
    totals = {}
    for key in ("before", "after"):
        done = [r[key] for r in rows]
        totals[key] = {
            "verified": sum(r["verified"] for r in done),
            "turns": sum(r["turns"] for r in done),
            "seconds": round(sum(r["seconds"] for r in done), 1),
            "stages": {s: sum(r["spans"].get(s, 0) for r in done) for s in stages},
            "cost_usd": (before if key == "before" else after).get("cost_usd")}
    return {"before": str(before_dir), "after": str(after_dir), "stages": stages,
            "people": rows, "totals": totals}


def pick(result, names):
    return {"verified": bool(result.get("verified")), "turns": result.get("turns", 0),
            "seconds": result.get("seconds", 0.0), "spans": segments(names, result)}


LABELS = {"start": "to the product", "product page": "product to cart", "cart": "cart to checkout",
          "checkout": "checkout to order", "order confirmed": "after the order"}


def report(found):
    lines = [f"{'':10} {'order':>11} {'turns':>9} {'minutes':>11}"]
    for r in found["people"]:
        b, a = r["before"], r["after"]
        ok = lambda x: "yes" if x["verified"] else "no"
        lines.append(f"{r['name']:<10} {ok(b):>4} > {ok(a):<4} {b['turns']:>3} > {a['turns']:<3} "
                     f"{b['seconds'] / 60:>4.1f} > {a['seconds'] / 60:<4.1f}")
    tb, ta = found["totals"]["before"], found["totals"]["after"]
    n = len(found["people"])
    lines.append(f"{'crowd':<10} {tb['verified']:>2}/{n} > {ta['verified']}/{n} {tb['turns']:>3} > "
                 f"{ta['turns']:<3} {tb['seconds'] / 60:>4.0f} > {ta['seconds'] / 60:<4.0f}")
    lines.append("")
    lines.append("turns spent by the whole crowd")
    for s in found["stages"]:
        if tb["stages"][s] or ta["stages"][s]:
            lines.append(f"  {LABELS.get(s, s):<18} {tb['stages'][s]:>4} > {ta['stages'][s]:<4}")
    return "\n".join(lines)
