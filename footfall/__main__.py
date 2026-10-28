"""Command line: python -m footfall {crowd,ledger} ..."""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .budget import Ledger
from .meta import HttpBackend, MetaClient
from .session import Session

LEDGER = Path("runs/ledger.jsonl")
PERSONAS = Path(__file__).with_name("personas.json")


def load_personas(names):
    every = json.loads(PERSONAS.read_text())
    if names == "all":
        return every
    wanted = names.split(",")
    found = [p for p in every if p["id"] in wanted]
    missing = set(wanted) - {p["id"] for p in found}
    if missing:
        sys.exit(f"unknown personas: {', '.join(sorted(missing))}")
    return found


def cmd_crowd(args):
    ledger = Ledger(LEDGER)
    if ledger.remaining() <= 0:
        sys.exit("no usable budget: set FOOTFALL_BUDGET_USD (paid calls are refused)")
    print(f"budget ${ledger.budget:.2f}, spent ${ledger.spent():.4f}, usable ${ledger.remaining():.4f}")
    scenario = json.loads(Path(args.scenario).read_text())
    personas = load_personas(args.personas)
    run_id = time.strftime("%Y%m%d-%H%M%S")
    out = Path("runs") / f"{run_id}-crowd"
    client = MetaClient(HttpBackend(), ledger)
    spent0 = ledger.spent()
    print(f"{len(personas)} personas on {scenario['url']} ({scenario['name']}), {args.parallel} at a time")
    print(f"run dir: {out}")

    def one(persona):
        session = Session(client, persona, scenario, out / persona["id"], model=args.model,
                          size=tuple(args.size), effort=args.effort, run_id=run_id)
        try:
            return session()
        except Exception as exc:  # one broken session must not take the crowd down
            return {"persona": persona["id"], "name": persona["name"], "outcome": "error",
                    "message": repr(exc), "verified": False}

    with ThreadPoolExecutor(args.parallel) as pool:
        results = list(pool.map(one, personas))
    summary = {"scenario": scenario["name"], "url": scenario["url"], "model": args.model,
               "results": results, "cost_usd": round(ledger.spent() - spent0, 4)}
    (out / "crowd.json").write_text(json.dumps(summary, indent=1))
    print()
    for r in results:
        verdict = "VERIFIED" if r.get("verified") else "not done"
        print(f"{r['name']:<10} {verdict:<9} {r['outcome']:<16} {r.get('turns', '-'):>3} turns  "
              f"{r.get('message', '')[:90]}")
    ok = sum(bool(r.get("verified")) for r in results)
    print(f"\n{ok}/{len(results)} reached the goal, cost ${summary['cost_usd']:.4f}")


def cmd_ledger(args):
    ledger = Ledger(LEDGER)
    print(f"budget ${ledger.budget:.2f} (FOOTFALL_BUDGET_USD), margin ${ledger.margin:.2f}")
    real = [e for e in ledger.entries() if not e.get("mock")]
    print(f"real calls {len(real)}, spent ${ledger.spent():.4f}, usable ${ledger.remaining():.4f}")
    by_model = {}
    for e in real:
        calls, cost = by_model.get(e["model"], (0, 0.0))
        by_model[e["model"]] = (calls + 1, cost + e["cost_usd"])
    for model, (calls, cost) in sorted(by_model.items()):
        print(f"  {model:<30} {calls:>3} calls  ${cost:.4f}")


def main():
    parser = argparse.ArgumentParser(prog="footfall")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crowd", help="send personas through a scenario in real browsers")
    c.add_argument("scenario")
    c.add_argument("--personas", default="all", help="comma-separated ids, or all")
    c.add_argument("--model", default="muse-spark-1.3")
    c.add_argument("--parallel", type=int, default=2)
    c.add_argument("--effort", default="low")
    c.add_argument("--size", type=int, nargs=2, default=[1280, 800])
    c.set_defaults(func=cmd_crowd)
    sub.add_parser("ledger", help="show spend so far").set_defaults(func=cmd_ledger)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
