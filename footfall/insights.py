"""From a crowd run to the frictions that matter.

The facts come from the harness: who reached which funnel stage, after how many turns, and
whether the store has the order. The model reads every persona's think-aloud journal and groups
the struggles into friction points, each tied to quotes and turns that can be checked against
the screenshots. It is asked for evidence, not opinions: a friction without a quote is dropped.

Then a witness looks: for each friction, Muse Spark sees the screens behind its quotes, before
and after the action, and says whether the site caused it or the harness did (a click that
landed a row away). Journals report what a person felt; only the screens show why.
"""

import io
import json
import re
from pathlib import Path

from PIL import Image

from .computer import image
from .meta import output_text

PROMPT = """You are a senior UX researcher. Below are think-aloud journals from a usability test
of one website task. Each participant is a simulated person with real constraints (eyesight,
tremor, time pressure, scepticism); their screenshots were degraded to match their eyesight.
The final outcome of each session was checked in the shop's database.

Task: {task}
Outcome: {outcome}

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


WITNESS = """A usability test ran a simulated person through a website. From their think-aloud
journal, an analyst reported this friction:

{friction}

Below are the screens behind the evidence. For each quoted turn, three screens in order: before
the person's previous action, when they said it, and after their next action (the pointer
shows where each action went). What they did and said:
{turns}

The test harness is imperfect: its clicks sometimes land a row away from what the person meant,
and typing can go to the wrong field. Look at the screens and decide what really happened:
- "site" if the website itself caused the trouble (layout, labels, defaults, feedback),
- "harness" if a click or keystroke landed somewhere the person did not mean and the site
  behaved correctly,
- "unclear" if the screens cannot tell.
Reply with JSON only: {{"verdict": "site|harness|unclear", "seen": "one sentence: what the
screens show", "why": "the cause, as a designer would put it, if site", "fix": "one concrete
change to the site, if site"}}"""


def screens(crowd_dir, persona, turn, longest=900):
    """The raw screens around a turn, scaled down for the witness: what someone remarks on is
    often the result of what they did the turn before."""
    out = []
    for t in (turn - 1, turn, turn + 1):
        found = sorted((Path(crowd_dir) / persona / "shots").glob(f"*-t{t:02d}-screen.png"))
        if found:
            img = Image.open(found[0]).convert("RGB")
            img.thumbnail((longest, longest))
            buf = io.BytesIO()
            img.save(buf, "PNG")
            out.append(buf.getvalue())
    return out


def witness(client, crowd_dir, friction, sessions, model="muse-spark-1.3"):
    """Spark looks at the screens behind a friction: does the site cause it, or the harness?"""
    by_turn = {(s["result"]["persona"], e["turn"]): e
               for s in sessions for e in s["events"] if e["kind"] == "turn"}
    content, turns = [], []
    looks = {}
    for s in sessions:
        for e in s["events"]:
            if e["kind"] == "look":
                looks.setdefault((s["result"]["persona"], e["turn"]), []).append(e)

    def did(persona, turn):
        e = by_turn.get((persona, turn), {})
        acts = "; ".join(f"type {a.get('text', '')!r}" if a.get("type") == "type"
                         else f"{a.get('type', '')} at {a['x']},{a['y']}" if "x" in a
                         else a.get("type", "") for a in e.get("actions", [])) or "nothing"
        moved = "; ".join(f"the harness's eye moved a click from {l['aimed'][0]},{l['aimed'][1]} "
                          f"to {l['corrected'][0]},{l['corrected'][1]}"
                          for l in looks.get((persona, turn), []))
        return acts + (f" ({moved})" if moved else "")

    for ev in friction["evidence"][:2]:
        turns.append(f"- {ev['persona']}, turn {ev['turn'] - 1} did: {did(ev['persona'], ev['turn'] - 1)}"
                     f"\n  turn {ev['turn']} said: \"{ev['quote']}\" and did: {did(ev['persona'], ev['turn'])}")
        for png in screens(crowd_dir, ev["persona"], ev["turn"]):
            content.append(image(png))
    if not content:
        return None
    brief = json.dumps({k: friction.get(k) for k in ("title", "where", "why")}, ensure_ascii=False)
    prompt = WITNESS.format(friction=brief, turns="\n".join(turns))
    response = client.create({"model": model, "max_output_tokens": 3000,
                              "reasoning": {"effort": "low"},
                              "input": [{"role": "user", "content": [
                                  {"type": "input_text", "text": prompt}, *content]}]},
                             tag="witness")
    return parse_json(output_text(response))


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
    done = sum(bool(s["result"].get("verified")) for s in sessions)
    # Counted here, not by the model: it once summed eight verified orders as "all seven".
    outcome = f"{done} of {len(sessions)} participants placed a verified order."
    prompt = PROMPT.format(task=scenario["task"], outcome=outcome,
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
    # The journals say what people felt; the screens say what happened. A friction that the
    # screens pin on the harness (a click that landed a row off) is not the site's to fix.
    for f in kept:
        seen = witness(client, crowd_dir, f, sessions, model=model) or {}
        f["witness"] = {k: seen.get(k) for k in ("verdict", "seen")}
        if seen.get("verdict") == "site":
            f["why"], f["fix"] = seen.get("why") or f.get("why"), seen.get("fix") or f.get("fix")
    harness = [f for f in kept if f["witness"].get("verdict") == "harness"]
    kept = [f for f in kept if f["witness"].get("verdict") != "harness"]
    result = {"summary": found.get("summary", ""), "frictions": kept, "harness": harness,
              "dropped": len(found.get("frictions", [])) - len(kept) - len(harness),
              "funnel": funnel(scenario, sessions),
              "results": [s["result"] for s in sessions]}
    (Path(crowd_dir) / "insights.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    return result
