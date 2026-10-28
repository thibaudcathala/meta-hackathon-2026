"""Computer use shared by every agent here: the tool definitions, screenshots as API images,
the model's next batch of actions, and the run directory that keeps everything it saw."""

import base64
import json
import time
from pathlib import Path


# Checks the harness may acknowledge on the user's behalf: the app runs on a private display,
# so a generic "confirm this action" is not a risk. Anything else (e.g. suspected prompt
# injection) stops the session.
ACKABLE = {"computer_action_requires_confirmation"}


# Same action vocabulary as the built-in computer tool, offered as a plain function for models
# whose API rejects {"type": "computer"} (muse-wonder-1.0 answered "Unsupported tool type").
COMPUTER_FUNCTION = {
    "type": "function", "name": "computer",
    "description": "Operate the mouse and keyboard on the live screen. The actions run in "
                   "order, then you receive a new screenshot.",
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


def computer_function(size):
    """The function with the screen size spelled out. Told only "pixels", Wonder sometimes
    answered on a 0-1000 grid instead (Add clicked at 263,50 for 337,39); with the size stated it
    lands within 2 px (calibrate.py)."""
    w, h = size
    return {**COMPUTER_FUNCTION, "description": COMPUTER_FUNCTION["description"] + (
        f" x and y are pixels of the latest screenshot, which is {w}x{h}: x from 0 to {w - 1}, "
        f"y from 0 to {h - 1}. A click without x and y clicks where the pointer is.")}


def tool_mode(model):
    return "function" if model.startswith("muse-wonder") else "computer"


def data_url(png):
    return "data:image/png;base64," + base64.b64encode(png).decode()


def image(png):
    return {"type": "input_image", "image_url": data_url(png)}


def next_call(response):
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
