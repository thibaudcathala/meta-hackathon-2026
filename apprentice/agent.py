"""The apprentice: redo each tutorial step through the GUI, then let the verifier judge it.

Per step: checkpoint the file, run the computer-use loop until the model says it is done,
read the real scene, verify. On failure the verifier's findings go back to the model as a
teacher's remark and it tries again (optionally from the rewound checkpoint).
"""

import base64
import io
import json
import time
from pathlib import Path

from PIL import Image

from .meta import output_text
from .verify import verify

SYSTEM = """You are an apprentice redoing a Blender tutorial on a real Blender {version} window.
You receive screenshots and act with the computer tool. Coordinates are pixels in the most
recent screenshot of the current screen: (0, 0) is the top-left. Click the centre of targets.

Work only through Blender's interface, as a human following the video would: menus, buttons,
fields, shortcuts. Never open the Python console, the text editor, the file browser or the
preferences, and never run scripts. Keyboard shortcuts act on the editor under the mouse, so
move the mouse over the 3D viewport before using viewport shortcuts.

Batch only actions that do not depend on visual feedback (for example a shortcut followed by
typing a value and Enter). After opening a menu, a popup or a search, take a new screenshot
before choosing. Use wait when the UI needs time.

Do only the current step, then stop and reply with one short sentence saying what you did. Do
not claim it is done unless the screenshot shows it. If it cannot be done, say why."""


# Checks the harness may acknowledge on the user's behalf with --ack-safety: Blender runs on a
# private display inside a sandbox, so a generic "confirm this action" is not a risk. Anything
# else (e.g. suspected prompt injection) stops the step.
ACKABLE = {"computer_action_requires_confirmation"}


# Same action vocabulary as the built-in computer tool, offered as a plain function for models
# whose API rejects {"type": "computer"} (muse-wonder-1.0 answered "Unsupported tool type").
COMPUTER_FUNCTION = {
    "type": "function", "name": "computer",
    "description": "Operate the mouse and keyboard on the live screen. Coordinates are pixels "
                   "of the latest screenshot. The actions run in order, then you receive a new "
                   "screenshot.",
    "parameters": {"type": "object", "required": ["actions"], "properties": {"actions": {
        "type": "array", "items": {"type": "object", "required": ["type"], "properties": {
            "type": {"type": "string", "enum": ["click", "double_click", "drag", "keypress",
                                               "move", "scroll", "type", "wait", "screenshot"]},
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "keys": {"type": "array", "items": {"type": "string"},
                     "description": "keys held together, e.g. [\"ctrl\", \"z\"] or [\"Tab\"]"},
            "text": {"type": "string"},
            "path": {"type": "array", "items": {"type": "object", "properties": {
                "x": {"type": "integer"}, "y": {"type": "integer"}}}},
            "scroll_x": {"type": "integer"}, "scroll_y": {"type": "integer"},
            "ms": {"type": "integer"}}}}}},
}


def tool_mode(model):
    return "function" if model.startswith("muse-wonder") else "computer"


def data_url(png):
    return "data:image/png;base64," + base64.b64encode(png).decode()


def image(png):
    return {"type": "input_image", "image_url": data_url(png)}


def thumbnail(path, width=640):
    """Tutorial frames go in small: they are a hint of the result, and a full-size frame of an
    older UI got its coordinates mistaken for the live screen's (first Spark runs)."""
    img = Image.open(path).convert("RGB")
    img.thumbnail((width, width))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return {"type": "input_image",
            "image_url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}


class Run:
    """A run directory: one JSONL event log plus every screenshot the model was shown."""

    def __init__(self, root):
        self.dir = Path(root)
        (self.dir / "shots").mkdir(parents=True, exist_ok=True)
        self.t0 = time.time()
        self.n = 0
        self.log = (self.dir / "events.jsonl").open("a")
        self.responses = (self.dir / "responses.jsonl").open("a")

    def event(self, kind, **data):
        data = {"t": round(time.time() - self.t0, 3), "kind": kind, **data}
        self.log.write(json.dumps(data) + "\n")
        self.log.flush()
        return data

    def response(self, body):
        """Raw API responses, kept for audit and for replaying a run without paying again."""
        self.responses.write(json.dumps(body) + "\n")
        self.responses.flush()

    def shot(self, png, label):
        self.n += 1
        path = self.dir / "shots" / f"{self.n:04d}-{label}.png"
        path.write_bytes(png)
        return str(path.relative_to(self.dir))


class Apprentice:
    def __init__(self, client, desktop, app, run, model="muse-wonder-1.0", max_turns=30,
                 max_attempts=3, rewind=False, ack_safety=False, version="5.2", frames=True,
                 effort=None, tool=None, say=print):
        self.client, self.desktop, self.app, self.run = client, desktop, app, run
        self.model = model
        self.max_turns = max_turns
        self.max_attempts = max_attempts
        self.rewind = rewind
        self.ack_safety = ack_safety
        self.frames = frames
        self.tool = tool or tool_mode(model)
        self.effort = effort
        self.instructions = SYSTEM.format(version=version)
        self.say = say

    def request(self, input_items, previous=None, tag=""):
        # Reasoning shares the output allowance: keep room so a turn is not cut mid-thought.
        tools = [COMPUTER_FUNCTION] if self.tool == "function" else [{"type": "computer"}]
        payload = {"model": self.model, "instructions": self.instructions,
                   "tools": tools, "input": input_items,
                   "max_output_tokens": 8000}
        if self.effort:
            payload["reasoning"] = {"effort": self.effort}
        if previous:
            payload["previous_response_id"] = previous
        response = self.client.create(payload, tag=tag)
        self.run.response(response)
        if response.get("status") == "incomplete":
            self.run.event("incomplete", details=response.get("incomplete_details"))
        return response

    def act(self, response, step_id):
        """Drive one step's observe-act loop. Returns (last response, final message)."""
        for turn in range(self.max_turns):
            call = self.next_call(response)
            if call is None:
                return response, output_text(response)
            checks = call.get("pending_safety_checks") or []
            if checks:
                ok = self.ack_safety and all(c.get("code") in ACKABLE for c in checks)
                self.run.event("safety_check", step=step_id, checks=checks, acked=ok)
                if not ok:
                    return response, f"stopped: safety check {[c.get('code') for c in checks]}"
            for action in call["actions"]:
                self.desktop.execute(action)
            self.desktop.settle()
            png = self.desktop.screenshot()
            self.run.event("actions", step=step_id, turn=turn, actions=call["actions"],
                           shot=self.run.shot(png, f"{step_id}-t{turn}"))
            if call["kind"] == "function":
                reply = [{"type": "function_call_output", "call_id": call["call_id"],
                          "output": "Done. The screen after your actions follows."},
                         {"role": "user", "content": [image(png)]}]
            else:
                reply = [{"type": "computer_call_output", "call_id": call["call_id"],
                          "acknowledged_safety_checks": checks,
                          "output": {"type": "computer_screenshot", "image_url": data_url(png)}}]
            response = self.request(reply, previous=response["id"], tag=step_id)
        return response, "stopped: turn budget reached"

    def next_call(self, response):
        """The model's next batch of actions, from a computer_call or a computer() function call."""
        for item in response.get("output", []):
            if item.get("type") == "computer_call":
                return {**item, "kind": "computer"}
            if item.get("type") == "function_call" and item.get("name") == "computer":
                try:
                    actions = json.loads(item.get("arguments") or "{}").get("actions", [])
                except json.JSONDecodeError:
                    actions = []
                return {"kind": "function", "call_id": item["call_id"], "actions": actions}
        return None

    def step_prompt(self, tutorial, index):
        step = tutorial["steps"][index]
        done = "\n".join(f"  [x] {s['instruction']}" for s in tutorial["steps"][:index])
        text = (f"Tutorial: {tutorial['title']}\nGoal: {tutorial['goal']}\n"
                f"Already done:\n{done or '  (nothing yet)'}\n\n"
                f"STEP {step['id']} ({index + 1}/{len(tutorial['steps'])}): {step['instruction']}")
        content = [{"type": "input_text", "text": text}]
        if step.get("frame") and self.frames:
            content += [{"type": "input_text", "text": "Small frame from the tutorial video right "
                         "after this step. It may show an older Blender with a different layout: "
                         "use it to see the intended result, never for coordinates."},
                        thumbnail(step["frame"])]
        return content

    def do_step(self, tutorial, index):
        step = tutorial["steps"][index]
        sid = step["id"]
        checkpoint = self.app.work / "checkpoints" / f"{sid}.blend"
        checkpoint.parent.mkdir(exist_ok=True)
        self.app.checkpoint(checkpoint)
        before = self.app.scene()
        self.run.event("step_start", step=sid, instruction=step["instruction"])
        self.say(f"[{sid}] {step['instruction']}")

        png = self.desktop.screenshot()
        content = self.step_prompt(tutorial, index) + [
            {"type": "input_text", "text": "Current screen:"}, image(png)]
        self.run.event("observe", step=sid, shot=self.run.shot(png, f"{sid}-start"))
        response = self.request([{"role": "user", "content": content}], tag=sid)

        for attempt in range(1, self.max_attempts + 1):
            response, message = self.act(response, sid)
            self.desktop.settle()
            scene = self.app.scene()
            ok, results = verify(step["expect"], scene, before)
            errors = [r["error"] for r in results if r["error"]]
            png = self.desktop.screenshot()
            self.run.event("verify", step=sid, attempt=attempt, ok=ok, message=message,
                           results=results, shot=self.run.shot(png, f"{sid}-a{attempt}"))
            self.say(f"  attempt {attempt}: {'VERIFIED' if ok else 'FAILED'} - {message}")
            for e in errors:
                self.say(f"    x {e}")
            if ok or message.startswith("stopped:") or attempt == self.max_attempts:
                return {"step": sid, "ok": ok, "attempts": attempt, "message": message,
                        "errors": errors}
            if self.rewind:
                self.app.rewind(checkpoint)
                png = self.desktop.screenshot()
            remark = (f"STEP {sid} is not done yet. A program read the real Blender scene and "
                      f"found: {'; '.join(errors)}. "
                      + ("The scene was reset to how it was before this step. " if self.rewind else "")
                      + "Fix it, then reply when the screen shows it done.")
            response = self.request([{"role": "user", "content": [
                {"type": "input_text", "text": remark}, image(png)]}],
                previous=response["id"], tag=sid)

    def run_tutorial(self, tutorial, only=None, stop_on_failure=True):
        results = []
        for index, step in enumerate(tutorial["steps"]):
            if only and step["id"] not in only:
                continue
            result = self.do_step(tutorial, index)
            results.append(result)
            if not result["ok"] and stop_on_failure:
                break
        self.run.event("done", results=results)
        return results
