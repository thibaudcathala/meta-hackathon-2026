"""One persona, one task, one real browser.

Each turn the model gets the persona, the goal, a short journal of what it already did and
thought, and the current screen *as the persona sees it*. It thinks aloud, then acts; its clicks
go through the persona's hands. Turns are stateless (no response chaining): the journal keeps
the context small, so a 30-turn session costs about the same per turn as the first one.

The verdict never comes from the model: the shop's database says whether the order exists.
"""

import io
import json
import re
import time
from pathlib import Path

from PIL import ImageChops, ImageDraw

from .browser import Browser
from .computer import ACKABLE, Run, computer_function, image, next_call, tool_mode
from .desktop import VirtualDesktop
from .meta import ApiError, output_text
from .senses import Hands, see, see_png
from . import store

SYSTEM = """You are {name}, {age}. {bio}

You are {where}, using a real web browser. Each screenshot shows the screen exactly as
you see it{eyes}. You act with the computer tool: mouse and keyboard, coordinates in pixels of
the screenshot ({width}x{height}), (0, 0) at the top-left{tap}.

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


# Screen sizes in CSS pixels. A phone or tablet is a narrow Chrome window with a mobile user
# agent: the site serves its mobile layout, and the persona still clicks rather than taps.
DEVICES = {"phone": (412, 870), "tablet": (820, 1100), "laptop": (1280, 800)}


def change(before, after):
    """What a person would notice between two frames even without reading them: nothing at all,
    or something small flickering in one place (a counter, a badge) while the rest stays still."""
    box = ImageChops.difference(before, after).getbbox()
    if box is None:
        return "nothing on the screen changed"
    x0, y0, x1, y1 = box
    w, h = after.size
    if (x1 - x0) * (y1 - y0) < 0.01 * w * h:
        return f"only a small spot changed on the screen, around {(x0 + x1) // 2},{(y0 + y1) // 2}"
    return ""


def unzoom(action, zoom):
    """Screen coordinates for an action aimed on an enlarged screenshot."""
    if zoom == 1:
        return action
    out = dict(action)
    for key in ("x", "y"):
        if key in out:
            out[key] = round(out[key] / zoom)
    if "path" in out:
        out["path"] = [{"x": round(pt["x"] / zoom), "y": round(pt["y"] / zoom)}
                       for pt in out["path"]]
    return out


def same(a, b, slack=25):
    """The same actions again, aimed at about the same place."""
    if not a or not b or len(a) != len(b):
        return False
    return all(x.get("type") == y.get("type") and x.get("text") == y.get("text")
               and abs(x.get("x", 0) - y.get("x", 0)) <= slack
               and abs(x.get("y", 0) - y.get("y", 0)) <= slack for x, y in zip(a, b))


LOOK = """{name} is about to {action}. Their thought: "{thought}"

The image is a close-up of {name}'s screen around that spot, enlarged {k}x, as they see it. The
red cross marks exactly where the pointer is about to land. If it is on what they mean to
click, reply {{"ok": true}}. If what they mean is elsewhere in the close-up, reply with its
centre in the close-up's pixels: {{"x": 123, "y": 45}}. If it is not in the close-up at all,
reply {{"ok": true}}. JSON only."""


class Session:
    def __init__(self, client, persona, scenario, out_dir, model="muse-spark-1.3", size=None,
                 record=True, effort="low", say=print, run_id=None, max_turns=60,
                 closed_loop=True):
        self.client, self.persona, self.scenario = client, persona, scenario
        self.dir = Path(out_dir)
        self.device = persona.get("device", "laptop")
        self.size = tuple(size or DEVICES.get(self.device, DEVICES["laptop"]))
        # Spark aims badly on small images: on a 412x870 phone it clicked the colour row when it
        # meant the size row below. Screens are sent enlarged to about 1300 pixels on the long
        # side (as a phone's own pixel density would), and its coordinates scaled back.
        self.zoom = max(1.0, 1300 / max(self.size))
        self.sent_size = tuple(round(v * self.zoom) for v in self.size)
        self.model, self.record = model, record
        self.effort, self.say = effort, say
        self.max_turns = max_turns  # a cost ceiling; giving up is the persona's own call
        self.closed_loop = closed_loop
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
        where = {"phone": "on your phone", "tablet": "on your tablet"}.get(self.device,
                                                                          "at your computer")
        tap = "; a click is a tap of your finger" if self.device in ("phone", "tablet") else ""
        w, h = self.sent_size
        return SYSTEM.format(name=p["name"], age=p["age"], bio=p["bio"], eyes=eyes,
                             where=where, tap=tap, width=w, height=h)

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
        tools = ([computer_function(self.sent_size)] if self.tool == "function"
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

    def look(self, action, thought, turn, half=90, k=3):
        """Closed-loop pointing: before a click lands, the persona looks at the spot, as people
        do while they move a mouse, and corrects its aim. The model alone points open-loop and
        misses small targets (dropdown rows, size buttons) by a row. The close-up is taken
        through the persona's own eyes, so poor sight still makes poor corrections."""
        if action.get("type") not in ("click", "double_click") or "x" not in action:
            return action
        w, h = self.size
        x, y = action["x"], action["y"]
        box = (max(0, x - half), max(0, y - half), min(w, x + half), min(h, y + half))
        crop = see(self.desktop.grab(cursor=False), self.persona.get("eyes")).crop(box)
        crop = crop.resize((crop.width * k, crop.height * k))
        cx, cy = (x - box[0]) * k, (y - box[1]) * k
        d = ImageDraw.Draw(crop)
        d.line([(cx - 14, cy), (cx + 14, cy)], fill="red", width=3)
        d.line([(cx, cy - 14), (cx, cy + 14)], fill="red", width=3)
        buf = io.BytesIO()
        crop.save(buf, "PNG")
        prompt = LOOK.format(name=self.persona["name"], action=describe(action),
                             thought=thought[:300], k=k)
        try:
            response = self.client.create({
                "model": self.model, "max_output_tokens": 2000, "reasoning": {"effort": "low"},
                "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt},
                                                       image(buf.getvalue())]}]},
                tag=self.persona["id"] + "-look")
            found = json.loads(re.search(r"\{.*?\}", output_text(response), re.S).group(0))
        except (ApiError, AttributeError, ValueError):
            return action
        if "x" not in found or "y" not in found:
            return action
        fixed = {**action, "x": round(box[0] + float(found["x"]) / k),
                 "y": round(box[1] + float(found["y"]) / k)}
        self.run.event("look", turn=turn, aimed=[x, y], corrected=[fixed["x"], fixed["y"]])
        return fixed

    def act(self, call, turn, thought=""):
        failed = []
        for i, action in enumerate(call["actions"]):
            meant = self.look(unzoom(action, self.zoom), thought, turn) if self.closed_loop \
                else unzoom(action, self.zoom)
            aimed = self.hands.aim(meant)
            try:
                self.desktop.execute(aimed)
            except (KeyError, TypeError, ValueError) as exc:
                failed.append(f"{describe(action)} did not work ({exc})")
                break
            if aimed != meant:
                self.run.event("tremor", turn=turn, aimed=action, landed=aimed)
        return failed

    def play(self):
        p = self.persona
        start = time.time()
        self.run.event("start", persona=p["id"], model=self.model, scenario=self.scenario["name"],
                       email=self.email, size=list(self.size))
        outcome, message, turns = "turn limit", "", 0
        before, last, repeats = None, None, 0
        for turn in range(self.max_turns):
            turns = turn + 1
            self.desktop.settle(quiet=0.5, timeout=6)
            # A person notices when a click changes nothing, or only makes a small counter
            # flicker in a corner; the model, looking at one frame at a time, does not, and
            # keeps pressing the same button (one persona put 16 shirts in the cart).
            bare = self.desktop.grab(cursor=False)
            if before is not None and self.journal:
                noticed = change(before, bare)
                if noticed:
                    self.journal[-1] += " - " + noticed
            before = bare
            raw = self.desktop.screenshot()
            seen = see_png(raw, p.get("eyes"), self.zoom)
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
            failed = self.act(call, turn, thought)
            self.run.event("turn", turn=turn, thought=thought, actions=call["actions"],
                           failed=failed, shot=shot)
            self.say(f"  [{p['id']}] {turn:2d} {thought[:110]!r} -> "
                     + ", ".join(describe(a) for a in call["actions"]))
            entry = (f'You thought: "{thought[:240]}"; you ' if thought else "You ")
            entry += ", ".join(describe(a) for a in call["actions"])
            # People know when they are pressing the same thing for the fourth time.
            repeats = repeats + 1 if same(call["actions"], last) else 0
            last = call["actions"]
            if repeats >= 2:
                entry += f" (the same as your previous {repeats} actions)"
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
            self.browser = Browser(desktop, self.scenario["url"],
                                   mobile=self.device in ("phone", "tablet")).launch()
            try:
                if not self.browser.wait_loaded():
                    raise RuntimeError("the site did not load")
                if self.device == "phone":
                    self.browser.emulate()
                if self.record:
                    desktop.record(self.dir / "screen.mp4")
                    self.run.event("record_start")
                result = self.play()
            finally:
                desktop.stop_recording()
                self.browser.close()
        (self.dir / "result.json").write_text(json.dumps(result, indent=1))
        return result
