"""From a crowd run to the frictions that matter.

The facts come from the harness: who reached which funnel stage, after how many turns, and
whether the store has the order. The model reads every persona's think-aloud journal and groups
the struggles into friction points, each tied to quotes and turns that can be checked against
the screenshots. It is asked for evidence, not opinions: a friction without a quote is dropped.
"""

import json
import re
from pathlib import Path

from .meta import output_text

PROMPT = """You are a senior UX researcher. Below are think-aloud journals from a usability test
of one website task. Each participant is a simulated person with real constraints (eyesight,
tremor, time pressure, scepticism); their screenshots were degraded to match their eyesight.
The final outcome of each session was checked in the shop's database.

Task: {task}

{journals}

Find the friction points: places where one or more participants struggled, hesitated, made
mistakes, repeated actions or gave up. Group the same problem across participants. Rank by
impact (how many people, how badly). Only report what the journals show; every friction needs
at least one verbatim quote with its participant id and turn number. Ignore problems caused by
the test harness itself.

Reply with JSON only, this shape:
{{"frictions": [{{"title": "short name of the problem", "where": "page or step",
  "severity": "blocker|major|minor", "who": ["persona ids"],
  "evidence": [{{"persona": "id", "turn": 3, "quote": "verbatim words"}}],
  "why": "one sentence on the cause, as a designer would see it",
  "fix": "one concrete change to the site"}}],
 "summary": "two sentences for the site owner"}}"""


def load(crowd_dir):
    crowd_dir = Path(crowd_dir)
    summary = json.loads((crowd_dir / "crowd.json").read_text())
    sessions = []
    for r in summary["results"]:
        events_path = crowd_dir / r["persona"] / "events.jsonl"
        events = [json.loads(l) for l in events_path.open()] if events_path.exists() else []
        sessions.append({"result": r, "events": events})
    return summary, sessions


def journal(session):
    r = session["result"]
    lines = [f"## {r['persona']} ({r['name']}): outcome {r['outcome']}, order in database: "
             f"{'yes' if r.get('verified') else 'no'}"]
    for e in session["events"]:
        if e["kind"] == "turn":
            acts = ", ".join(a.get("type", "") for a in e["actions"])
            lines.append(f"turn {e['turn']}: \"{e['thought']}\" [{acts}]"
                         + (f" FAILED: {'; '.join(e['failed'])}" if e.get("failed") else ""))
        elif e["kind"] == "say":
            lines.append(f"turn {e['turn']}: \"{e['text']}\" [no action]")
        elif e["kind"] == "funnel":
            lines.append(f"  (reached {e['stage']})")
    if r.get("message"):
        lines.append(f"final words: \"{r['message']}\"")
    return "\n".join(lines)


def funnel(scenario, sessions):
    stages = [s["name"] for s in scenario.get("funnel", [])]
    rows = []
    for name in stages:
        reached = [s["result"]["persona"] for s in sessions
                   if name in (s["result"].get("funnel") or {})]
        rows.append({"stage": name, "reached": reached})
    rows.append({"stage": "verified order", "reached": [
        s["result"]["persona"] for s in sessions if s["result"].get("verified")]})
    return rows


def parse_json(text):
    match = re.search(r"\{.*\}", text, re.S)
    return json.loads(match.group(0)) if match else {"frictions": [], "summary": text[:500]}


def analyse(client, crowd_dir, scenario, model="muse-spark-1.3"):
    summary, sessions = load(crowd_dir)
    prompt = PROMPT.format(task=scenario["task"],
                           journals="\n\n".join(journal(s) for s in sessions))
    # Eight long journals once filled 6,000 tokens and cut the JSON short: leave room.
    response = client.create({"model": model, "max_output_tokens": 20000,
                              "reasoning": {"effort": "low"},
                              "input": [{"role": "user", "content": [
                                  {"type": "input_text", "text": prompt}]}]}, tag="insights")
    found = parse_json(output_text(response))
    # Keep only frictions whose quotes really appear in the journals.
    thoughts = {(s["result"]["persona"], e["turn"]): (e.get("thought") or e.get("text") or "")
                for s in sessions for e in s["events"] if e["kind"] in ("turn", "say")}
    kept = []
    for f in found.get("frictions", []):
        good = [ev for ev in f.get("evidence", [])
                if ev.get("quote", "")[:40].lower() in
                thoughts.get((ev.get("persona"), ev.get("turn")), "").lower()]
        if good:
            kept.append({**f, "evidence": good})
    result = {"summary": found.get("summary", ""), "frictions": kept,
              "dropped": len(found.get("frictions", [])) - len(kept),
              "funnel": funnel(scenario, sessions),
              "results": [s["result"] for s in sessions]}
    (Path(crowd_dir) / "insights.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    return result
