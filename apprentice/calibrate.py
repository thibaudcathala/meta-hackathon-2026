"""Measure how a model's click coordinates map to the screenshot it was sent.

Draws a target at known spots on a blank canvas, asks the model to click it with the computer
tool, and fits x_click = a * x_true (same for y). The first Spark runs clicked at ~0.6x the true
x on a 1280x800 screen, which suggests the image is resampled before the model sees it.
"""

import io

from PIL import Image, ImageDraw

import copy
import json

from footfall.computer import COMPUTER_FUNCTION, data_url, image
from footfall.meta import output_items

SPOTS = [(0.2, 0.25), (0.8, 0.3), (0.35, 0.8), (0.7, 0.7)]


def canvas(size, spot):
    w, h = size
    img = Image.new("RGB", size, (40, 40, 40))
    d = ImageDraw.Draw(img)
    for gx in range(0, w, 100):
        d.line([(gx, 0), (gx, h)], fill=(60, 60, 60))
    for gy in range(0, h, 100):
        d.line([(0, gy), (w, gy)], fill=(60, 60, 60))
    x, y = spot
    d.rectangle([x - 40, y - 18, x + 40, y + 18], fill=(200, 40, 40))
    d.text((x - 20, y - 6), "CLICK", fill="white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def probe(client, model, size, spot, turns=3):
    shot = data_url(canvas(size, spot))
    response = client.create({
        "model": model, "tools": [{"type": "computer"}], "max_output_tokens": 4000,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": "Click the centre of the red button labelled CLICK."},
            {"type": "input_image", "image_url": shot}]}],
    }, tag="calibrate")
    for _ in range(turns):
        calls = output_items(response, "computer_call")
        if not calls:
            return None
        for action in calls[0]["actions"]:
            if "x" in action:
                return action["x"], action["y"]
        # The model often asks for a fresh screenshot first: send the same canvas back.
        response = client.create({
            "model": model, "tools": [{"type": "computer"}], "max_output_tokens": 4000,
            "previous_response_id": response["id"],
            "input": [{"type": "computer_call_output", "call_id": calls[0]["call_id"],
                       "acknowledged_safety_checks": calls[0].get("pending_safety_checks") or [],
                       "output": {"type": "computer_screenshot", "image_url": shot}}],
        }, tag="calibrate")
    return None


def function_tool(description):
    tool = copy.deepcopy(COMPUTER_FUNCTION)
    tool["description"] = description
    return tool


def probe_function(client, model, size, spot, description):
    """Same probe through the computer() function tool, for models without the computer tool."""
    response = client.create({
        "model": model, "tools": [function_tool(description)], "max_output_tokens": 4000,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": "Click the centre of the red button labelled CLICK."},
            image(canvas(size, spot))]}],
    }, tag="calibrate")
    for call in output_items(response, "function_call"):
        for action in json.loads(call.get("arguments") or "{}").get("actions", []):
            if "x" in action:
                return action["x"], action["y"]
    return None


def run(client, model, size, description=None):
    w, h = size
    rows = []
    for fx, fy in SPOTS:
        true = (int(fx * w), int(fy * h))
        got = (probe_function(client, model, size, true, description) if description
               else probe(client, model, size, true))
        rows.append((true, got))
    pairs = [(t, g) for t, g in rows if g]
    ax = sum(g[0] for t, g in pairs) / sum(t[0] for t, g in pairs) if pairs else None
    ay = sum(g[1] for t, g in pairs) / sum(t[1] for t, g in pairs) if pairs else None
    return {"model": model, "size": size, "samples": rows, "scale_x": ax, "scale_y": ay}
