"""One persona, one task, one real browser.

Each turn the model gets the persona, the goal, a short journal of what it already did and
thought, and the current screen *as the persona sees it*. It thinks aloud, then acts; its clicks
go through the persona's hands. Turns are stateless (no response chaining): the journal keeps
the context small, so a 30-turn session costs about the same per turn as the first one.

The verdict never comes from the model: the shop's database says whether the order exists.
"""

import json
import time
from pathlib import Path

from .browser import Browser
from .computer import ACKABLE, Run, computer_function, image, next_call, tool_mode
from .desktop import VirtualDesktop
from .meta import ApiError, output_text
from .senses import Hands, see_png
from . import store

SYSTEM = """You are {name}, {age}. {bio}

You are at your computer, using a real web browser. Each screenshot shows the screen exactly as
you see it{eyes}. You act with the computer tool: mouse and keyboard, coordinates in pixels of
the screenshot, (0, 0) at the top-left.

Behave as {name} really would: your habits, your pace, your patience, your doubts. You are not a
tester and you know nothing about how this website is built. Do not type web addresses; find
your way through the site itself. Use only what you can actually see on the screenshot; if
something is too faint or too small for you to read, act on what you can make out, as you
would in real life.

Before every action, think aloud in the first person, in one or two short sentences: what you
see, what you are looking for, how you feel. Then act. Take several actions at once when they
need no looking in between: in a form, click a field and type into it in the same turn, and
fill every field you can see before looking again.

When your goal is done, reply with "DONE:" and one sentence. If you would give up in real
life, reply with "I GIVE UP:" and why, in your own words."""


def describe(action):
    kind = action.get("type")
    where = f" at {action['x']},{action['y']}" if "x" in action else ""
    if kind == "keypress":
        return "pressed " + "+".join(action.get("keys", []))
    if kind == "type":
        return f"typed {action.get('text', '')!r}"
    if kind == "scroll":
        return f"scrolled {'down' if action.get('scroll_y', 0) > 0 else 'up'}{where}"
    return f"{kind}{where}"


class Session:
    def __init__(self, client, persona, scenario, out_dir, model="muse-spark-1.3", size=(1280, 800),
                 record=True, effort="low", say=print, run_id=None, max_turns=60):
        self.client, self.persona, self.scenario = client, persona, scenario
        self.dir = Path(out_dir)
        self.model, self.size, self.record = model, size, record
        self.effort, self.say = effort, say
        self.max_turns = max_turns  # a cost ceiling; giving up is the persona's own call
        self.tool = tool_mode(model)
        self.run = Run(self.dir)
        self.hands = Hands(persona.get("hands", {}).get("tremor", 0), seed=persona["id"])
        self.email = f"{persona['id']}.{run_id or int(time.time())}@footfall.test"
        self.journal = []
        self.funnel = {}

    # -- model ---------------------------------------------------------------------------

    def instructions(self):
        p = self.persona
        eyes = f" with your own eyes ({p['eyes_note']})" if p.get("eyes_note") else ""
        return SYSTEM.format(name=p["name"], age=p["age"], bio=p["bio"], eyes=eyes)

    def prompt(self):
        details = {**self.persona["details"], "email": self.email}
        lines = [f"Your goal: {self.scenario['task']}",
                 "What you know by heart, for forms: "
                 + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in details.items()) + "."]
        if self.journal:
            lines.append("What you have done so far:")
            lines += [f"{i}. {entry}" for i, entry in enumerate(self.journal[-15:],
                                                                   max(1, len(self.journal) - 14))]
        lines.append("This is your screen now. What do you do next?")
        return "\n".join(lines)

    def ask(self, seen_png):
        tools = ([computer_function(self.size)] if self.tool == "function"
                 else [{"type": "computer"}])
        payload = {"model": self.model, "instructions": self.instructions(), "tools": tools,
                   "max_output_tokens": 4000,
                   "input": [{"role": "user", "content": [
                       {"type": "input_text", "text": self.prompt()}, image(seen_png)]}]}
        if self.effort:
            payload["reasoning"] = {"effort": self.effort}
        for wait in (3, 10, None):
            try:
                response = self.client.create(payload, tag=self.persona["id"])
                self.run.response(response)
                return response
            except ApiError as exc:
                if wait is None or exc.status < 500 and exc.status != 429:
                    raise
                time.sleep(wait)

    # -- loop ----------------------------------------------------------------------------

    def track(self, url, turn):
        for stage in self.scenario.get("funnel", []):
            if url and stage["url"] in url and stage["name"] not in self.funnel:
                self.funnel[stage["name"]] = turn
                self.run.event("funnel", stage=stage["name"], turn=turn, url=url)

    def act(self, call, turn):
        failed = []
        for i, action in enumerate(call["actions"]):
            aimed = self.hands.aim(action)
            try:
                self.desktop.execute(aimed)
            except (KeyError, TypeError, ValueError) as exc:
                failed.append(f"{describe(action)} did not work ({exc})")
                break
            if aimed is not action:
                self.run.event("tremor", turn=turn, aimed=action, landed=aimed)
        return failed

    def play(self):
        p = self.persona
        start = time.time()
        self.run.event("start", persona=p["id"], model=self.model, scenario=self.scenario["name"],
                       email=self.email, size=list(self.size))
        outcome, message, turns = "turn limit", "", 0
        before = None
        for turn in range(self.max_turns):
            turns = turn + 1
            self.desktop.settle(quiet=0.5, timeout=6)
            # A person notices when a click changes nothing; the model, looking at one blurred
            # frame at a time, does not, and keeps clicking the same empty spot.
            bare = self.desktop.grab(cursor=False).tobytes()
            if before is not None and bare == before and self.journal:
                self.journal[-1] += " - nothing on the screen changed"
            before = bare
            raw = self.desktop.screenshot()
            seen = see_png(raw, p.get("eyes"))
            self.run.shot(raw, f"t{turn:02d}-screen")
            shot = self.run.shot(seen, f"t{turn:02d}-seen")
            response = self.ask(seen)
            thought = output_text(response).strip()
            call = next_call(response)
            checks = (call or {}).get("pending_safety_checks") or []
            if any(c.get("code") not in ACKABLE for c in checks):
                outcome, message = "stopped", f"safety check {[c.get('code') for c in checks]}"
                break
            if call is None:
                self.run.event("say", turn=turn, text=thought, shot=shot)
                upper = thought.upper()
                if "I GIVE UP" in upper:
                    outcome, message = "gave up", thought.split(":", 1)[-1].strip()
                    break
                if "DONE" in upper:
                    outcome, message = "said done", thought.split(":", 1)[-1].strip()
                    break
                self.journal.append(f'You thought: "{thought[:240]}" (no action)')
                before = None
                continue
            failed = self.act(call, turn)
            self.run.event("turn", turn=turn, thought=thought, actions=call["actions"],
                           failed=failed, shot=shot)
            self.say(f"  [{p['id']}] {turn:2d} {thought[:110]!r} -> "
                     + ", ".join(describe(a) for a in call["actions"]))
            entry = (f'You thought: "{thought[:240]}"; you ' if thought else "You ")
            entry += ", ".join(describe(a) for a in call["actions"])
            if failed:
                entry += " (" + "; ".join(failed) + ")"
            self.journal.append(entry)
            self.desktop.settle(quiet=0.5, timeout=6)
            self.track(self.browser.url(), turn)
        self.track(self.browser.url(), turns)
        truth = store.check(self.scenario["success"], self.email, start)
        result = {"persona": p["id"], "name": p["name"], "outcome": outcome, "message": message,
                  "verified": truth["ok"], "truth": truth, "turns": turns,
                  "seconds": round(time.time() - start, 1), "funnel": self.funnel,
                  "email": self.email}
        self.run.event("end", **result)
        self.say(f"  [{p['id']}] END {outcome}, order in store: {'yes' if truth['ok'] else 'no'}"
                 f" - {message[:120]}")
        return result

    def __call__(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        with VirtualDesktop(*self.size, log_dir=self.dir) as desktop:
            self.desktop = desktop
            self.browser = Browser(desktop, self.scenario["url"]).launch()
            try:
                if not self.browser.wait_loaded():
                    raise RuntimeError("the site did not load")
                if self.record:
                    desktop.record(self.dir / "screen.mp4")
                    self.run.event("record_start")
                result = self.play()
            finally:
                desktop.stop_recording()
                self.browser.close()
        (self.dir / "result.json").write_text(json.dumps(result, indent=1))
        return result
