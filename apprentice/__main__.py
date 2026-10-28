"""Command line: python -m apprentice {run,ingest,ledger,shot} ..."""

import argparse
import json
import sys
import time
from pathlib import Path

from . import report
from . import tutorial as tutorials
from .agent import Apprentice, Run
from .blender import Blender
from .budget import Ledger
from .desktop import VirtualDesktop
from .meta import HttpBackend, MetaClient, ScriptedBackend

LEDGER = Path("runs/ledger.jsonl")


def size(text):
    w, h = text.lower().split("x")
    return int(w), int(h)


def cmd_run(args):
    tut = tutorials.load(args.tutorial)
    if args.mock:
        if not tut["mock"]:
            sys.exit(f"{args.tutorial} has no mock.json")
        backend = ScriptedBackend(tut["mock"])
    else:
        backend = HttpBackend()
    ledger = Ledger(LEDGER)
    if not args.mock:
        print(f"budget ${ledger.budget:.2f}, spent ${ledger.spent():.4f}, "
              f"usable ${ledger.remaining():.4f}")
        if ledger.remaining() <= 0:
            sys.exit("no usable budget: set APPRENTICE_BUDGET_USD (paid calls are refused)")
    client = MetaClient(backend, ledger)
    run = Run(Path("runs") / time.strftime(f"%Y%m%d-%H%M%S-{Path(args.tutorial).name}"))
    run.event("start", tutorial=args.tutorial, model=args.model, mock=args.mock,
              size=list(args.size))
    print(f"run dir: {run.dir}")

    with VirtualDesktop(*args.size, log_dir=run.dir) as desktop:
        app = Blender(desktop, run.dir / "blender").launch()
        if args.record:
            desktop.record(run.dir / "screen.mp4")
        agent = Apprentice(client, desktop, app, run, model=args.model, max_turns=args.max_turns,
                           max_attempts=args.attempts, rewind=args.rewind,
                           ack_safety=args.ack_safety)
        t0 = time.time()
        results = agent.run_tutorial(tut, only=args.steps and args.steps.split(","))
        final = app.scene()
        (run.dir / "final_scene.json").write_text(json.dumps(final, indent=2))
        app.checkpoint(app.work / "final.blend")
        if args.keep_open:
            input(f"display {desktop.name} kept open, press Enter to close ")

    entries = [e for e in ledger.entries() if e["mock"] == args.mock and e["ts"] >= run.t0]
    cost = sum(e["cost_usd"] for e in entries)
    ok = sum(r["ok"] for r in results)
    summary = {"verified": ok, "steps": len(results), "seconds": round(time.time() - t0, 1),
               "calls": len(entries), "input_tokens": sum(e["input_tokens"] for e in entries),
               "output_tokens": sum(e["output_tokens"] for e in entries),
               "cost_usd": round(cost, 4), "mock": args.mock, "results": results}
    (run.dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"report: {report.build(run.dir, args.size)}")
    label = "would cost" if args.mock else "cost"
    print(f"\n{ok}/{len(results)} steps verified in {summary['seconds']}s, "
          f"{len(entries)} calls, {label} ${cost:.4f}")
    return 0 if ok == len(results) else 1


def cmd_ingest(args):
    http = HttpBackend()
    client = MetaClient(http, Ledger(LEDGER))
    tut = tutorials.ingest(client, http, args.video, args.out, model=args.model)
    print(json.dumps(tut, indent=2))


def cmd_frames(args):
    tut = json.loads((Path(args.tutorial) / "tutorial.json").read_text())
    tutorials.extract_frames(args.video, tut["steps"], args.tutorial)
    print(f"{len(tut['steps'])} frames in {Path(args.tutorial) / 'frames'}")


def cmd_ledger(args):
    ledger = Ledger(LEDGER)
    real = [e for e in ledger.entries() if not e.get("mock")]
    print(f"budget ${ledger.budget:.2f} (APPRENTICE_BUDGET_USD), margin ${ledger.margin:.2f}")
    print(f"real calls {len(real)}, spent ${ledger.spent():.4f}, usable ${ledger.remaining():.4f}")


def cmd_report(args):
    start = json.loads((Path(args.run) / "events.jsonl").open().readline())
    print(report.build(args.run, tuple(start.get("size", (1280, 800)))))


def cmd_shot(args):
    with VirtualDesktop(*args.size, log_dir="runs/shot") as desktop:
        Blender(desktop, "runs/shot/blender").launch()
        Path(args.out).write_bytes(desktop.screenshot())
    print(args.out)


def main(argv=None):
    p = argparse.ArgumentParser(prog="apprentice")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="redo a tutorial in Blender and verify every step")
    r.add_argument("tutorial", help="tutorial directory (tutorial.json, frames/, mock.json)")
    r.add_argument("--mock", action="store_true", help="replay mock.json, no network")
    r.add_argument("--model", default="muse-wonder-1.0")
    r.add_argument("--steps", help="comma-separated step ids to run")
    r.add_argument("--max-turns", type=int, default=30)
    r.add_argument("--attempts", type=int, default=3)
    r.add_argument("--rewind", action="store_true", help="reset the scene before a retry")
    r.add_argument("--ack-safety", action="store_true",
                   help="acknowledge the API's safety checks (the app runs on a private display)")
    r.add_argument("--record", action="store_true", help="film the display to screen.mp4")
    r.add_argument("--size", type=size, default=(1280, 800))
    r.add_argument("--keep-open", action="store_true")
    r.set_defaults(func=cmd_run)

    i = sub.add_parser("ingest", help="turn a tutorial video into steps with Muse Spark (paid)")
    i.add_argument("video")
    i.add_argument("out")
    i.add_argument("--model", default="muse-spark-1.3")
    i.set_defaults(func=cmd_ingest)

    f = sub.add_parser("frames", help="cut each step's reference frame out of the video")
    f.add_argument("video")
    f.add_argument("tutorial")
    f.set_defaults(func=cmd_frames)

    sub.add_parser("ledger", help="show spend against the budget").set_defaults(func=cmd_ledger)

    rep = sub.add_parser("report", help="rebuild report.html for a run directory")
    rep.add_argument("run")
    rep.set_defaults(func=cmd_report)

    s = sub.add_parser("shot", help="start Blender on a private display and save a screenshot")
    s.add_argument("out")
    s.add_argument("--size", type=size, default=(1280, 800))
    s.set_defaults(func=cmd_shot)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
