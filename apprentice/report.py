"""Turn a run directory into a single report.html: every step, every action, every verdict."""

import html
import json
from pathlib import Path

CSS = """
:root { --bg: #111418; --card: #1a1f26; --line: #2a313b; --text: #e6e9ee; --dim: #8b95a5;
        --ok: #3fb950; --bad: #f85149; --act: #f0b72f; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px; background: var(--bg); color: var(--text);
       font: 14px/1.5 system-ui, sans-serif; }
h1 { font-size: 22px; margin: 0 0 4px; }
.meta { color: var(--dim); margin-bottom: 24px; }
.step { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
        padding: 16px; margin-bottom: 16px; }
.step h2 { font-size: 16px; margin: 0 0 12px; display: flex; gap: 10px; align-items: center; }
.badge { font-size: 12px; padding: 2px 8px; border-radius: 99px; font-weight: 600; }
.ok { background: color-mix(in srgb, var(--ok) 20%, transparent); color: var(--ok); }
.bad { background: color-mix(in srgb, var(--bad) 20%, transparent); color: var(--bad); }
.strip { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 6px; }
figure { margin: 0; flex: 0 0 300px; }
.shot { position: relative; }
.shot img { width: 100%; display: block; border-radius: 4px; border: 1px solid var(--line); }
.mark { position: absolute; width: 14px; height: 14px; margin: -7px 0 0 -7px; border-radius: 50%;
        border: 2px solid var(--act); background: color-mix(in srgb, var(--act) 35%, transparent); }
figcaption { color: var(--dim); font: 12px/1.4 ui-monospace, monospace; margin-top: 4px;
             word-break: break-word; }
.verdict { margin-top: 10px; padding: 8px 10px; border-radius: 6px; background: var(--bg); }
.verdict li { margin-left: 16px; }
.pass { color: var(--ok); } .fail { color: var(--bad); }
"""


def describe(action):
    kind = action["type"]
    if kind in ("click", "double_click", "move"):
        return f"{kind} {action['x']},{action['y']}"
    if kind == "keypress":
        return "key " + "+".join(action["keys"])
    if kind == "type":
        return f"type {action['text']!r}"
    if kind == "scroll":
        return f"scroll {action.get('scroll_y', 0)} at {action['x']},{action['y']}"
    return kind


def figure(shot, caption, actions=(), size=(1280, 800)):
    marks = "".join(
        f'<span class="mark" style="left:{a["x"] / size[0] * 100:.2f}%;top:{a["y"] / size[1] * 100:.2f}%"></span>'
        for a in actions if "x" in a)
    return (f'<figure><div class="shot"><img src="{html.escape(shot)}" loading="lazy">{marks}'
            f'</div><figcaption>{html.escape(caption)}</figcaption></figure>')


def build(run_dir, size=(1280, 800)):
    run_dir = Path(run_dir)
    events = [json.loads(line) for line in (run_dir / "events.jsonl").open()]
    summary = json.loads((run_dir / "summary.json").read_text()) if (run_dir / "summary.json").exists() else {}
    start = next((e for e in events if e["kind"] == "start"), {})

    steps, current, last_shot = [], None, None
    for e in events:
        if e["kind"] == "step_start":
            current = {"id": e["step"], "instruction": e["instruction"], "figs": [], "verdicts": []}
            steps.append(current)
        elif e["kind"] == "observe":
            last_shot = e["shot"]
        elif e["kind"] == "actions":
            # Coordinates refer to the screen the model saw, i.e. the previous capture.
            text = " · ".join(describe(a) for a in e["actions"])
            current["figs"].append(figure(last_shot, f"t={e['t']:.1f}s  {text}", e["actions"], size))
            last_shot = e["shot"]
        elif e["kind"] == "verify":
            current["figs"].append(figure(e["shot"], f"t={e['t']:.1f}s  attempt {e['attempt']}: "
                                          f"{e['message']}", (), size))
            current["verdicts"].append(e)
            last_shot = e["shot"]

    body = []
    for s in steps:
        ok = bool(s["verdicts"]) and s["verdicts"][-1]["ok"]
        badge = f'<span class="badge {"ok" if ok else "bad"}">{"VERIFIED" if ok else "NOT VERIFIED"}</span>'
        verdicts = []
        for v in s["verdicts"]:
            items = "".join(
                f'<li class="{"fail" if r["error"] else "pass"}">'
                f'{html.escape(r["error"] or json.dumps(r["check"]))}</li>' for r in v["results"])
            verdicts.append(f'<div class="verdict">Attempt {v["attempt"]}: '
                            f'<b class="{"pass" if v["ok"] else "fail"}">{"pass" if v["ok"] else "fail"}</b>'
                            f'<ul>{items}</ul></div>')
        body.append(f'<section class="step"><h2>{html.escape(s["id"])} · '
                    f'{html.escape(s["instruction"])} {badge}</h2>'
                    f'<div class="strip">{"".join(s["figs"])}</div>{"".join(verdicts)}</section>')

    cost_label = "would cost" if summary.get("mock") else "cost"
    meta = (f'{html.escape(str(start.get("tutorial", "")))} · model {html.escape(str(start.get("model", "")))}'
            f'{" (mock replay)" if start.get("mock") else ""} · '
            f'{summary.get("verified", "?")}/{summary.get("steps", "?")} steps verified · '
            f'{summary.get("seconds", "?")} s · {summary.get("calls", "?")} calls · '
            f'{cost_label} ${summary.get("cost_usd", 0):.3f}')
    page = (f'<!doctype html><html lang="en"><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>Apprentice run</title><style>{CSS}</style>'
            f'<h1>Apprentice run {html.escape(run_dir.name)}</h1><div class="meta">{meta}</div>'
            f'{"".join(body)}</html>')
    out = run_dir / "report.html"
    out.write_text(page)
    return out
