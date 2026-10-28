"""Recruiting a crowd that fits the site.

A grandmother testing a command-line tool's docs proves nothing. Before a crowd runs, Muse
Spark looks at the site the way a visitor first meets it (the top of the home page and two
screens further down), reads the owner's own words about the audience if there are any, and
recruits people who would really come: their ages, jobs, devices, languages and reasons follow
the audience, and their impairments follow how common those are in that audience, not a
showcase of every condition. Each recruit says why they are in the crowd.

The crowd file is plain JSON in the same shape as personas.json, meant to be read and edited by
the owner before the run.
"""

import json
import re
from pathlib import Path

from .browser import Browser
from .computer import image
from .desktop import VirtualDesktop
from .meta import output_text
from .senses import CVD
from .session import DEVICES

PROMPT = """You recruit participants for a usability test of the website in the screenshots
(the top of its home page, then two screens further down).

Task they will be given: {task}
{audience}{ships}
First work out who really uses this site: the audience, their ages, jobs, devices, languages,
habits and why they come. Then recruit {count} people from that audience, as a professional
recruiter would for a moderated study: a realistic spread, not a showcase. Include the people a
designer tends to forget (older or less confident users, non-native speakers, people in a hurry,
sceptics) only where they are plausibly part of this audience.

Give someone a perception or motor impairment only at a rate that is realistic for this
audience, using these base rates: red-green colour blindness about 8% of men and 0.5% of women
of Northern European descent; tritanopia is rare (1 in 10,000); uncorrected or low vision rises
steeply after 60 (macular degeneration about 10% of people over 75, cataracts common over 70);
tremor (essential tremor, Parkinson's) a few percent after 60; tired or dry eyes and screen
glare are common at any age. A young technical audience will have few impairments; a general
shop will have more. Say nothing about impairments for someone who has none. Give each person the device they
would really use for this visit, at the share that device has in this audience.

The harness can simulate only these, so describe impairments with them:
- eyes.blur: Gaussian blur radius in pixels, 0 to 4 (1 = slightly soft, 3.5 = small text is a smudge)
- eyes.contrast: 0.5 to 1 (1 = normal)
- eyes.brightness: 0.8 to 1.2
- eyes.tint: [r, g, b] multipliers, e.g. [1, 0.93, 0.7] for the yellowing of cataracts
- eyes.colour: one of {colours}
- hands.tremor: standard deviation of a click's miss in pixels, 0 to 14

Reply with JSON only:
{{"audience": "two or three sentences: who uses this site and what you saw that says so",
 "personas": [{{"id": "lowercase first name, unique", "name": "first name", "age": 34,
   "tag": "a few words shown under their name",
   "bio": "three or four sentences about their life: job, situation, habits with computers, why they would come to this site today, how they read and decide",
   "why": "why this person belongs in this site's crowd, one sentence",
   "device": "phone, tablet or laptop: what they would really use for this visit",
   "eyes": {{}}, "eyes_note": "plain words for their eyesight, or empty",
   "hands": {{"tremor": 0}},
   "details": {{"first_name": "...", "last_name": "...", "address": "...", "city": "...",
     "postal_code": "...", "country": "...", "phone": "..."}}}}]}}
The details are what they would type in forms; make them consistent with where they live.
They are fictional people: use phone numbers from ranges reserved for fiction where a country
has one (UK +44 7700 900xxx), otherwise numbers whose last digits are 555 0xx."""


def capture(url, size=(1280, 800), out_dir="runs/recruit"):
    """Screenshots of the site as a first visitor meets it: top, then two screens down."""
    shots = []
    with VirtualDesktop(*size, log_dir=out_dir) as desktop:
        browser = Browser(desktop, url).launch()
        try:
            if not browser.wait_loaded():
                raise RuntimeError(f"{url} did not load")
            for i in range(3):
                if i:
                    desktop.execute({"type": "scroll", "x": size[0] // 2, "y": size[1] // 2,
                                     "scroll_x": 0, "scroll_y": size[1] - 100})
                    desktop.settle(quiet=0.5, timeout=6)
                shots.append(desktop.grab(cursor=False))
        finally:
            browser.close()
    paths = []
    for i, img in enumerate(shots):
        path = Path(out_dir) / f"site-{i}.png"
        img.save(path)
        paths.append(path)
    return paths


def clamp(value, low, high):
    return max(low, min(high, float(value)))


def clean(persona):
    """Keep a recruit within what the harness can simulate."""
    eyes = {}
    raw = persona.get("eyes") or {}
    if raw.get("blur"):
        eyes["blur"] = clamp(raw["blur"], 0, 4)
    if raw.get("contrast") not in (None, 1):
        eyes["contrast"] = clamp(raw["contrast"], 0.5, 1)
    if raw.get("brightness") not in (None, 1):
        eyes["brightness"] = clamp(raw["brightness"], 0.8, 1.2)
    if isinstance(raw.get("tint"), list) and len(raw["tint"]) == 3:
        eyes["tint"] = [clamp(t, 0.5, 1.2) for t in raw["tint"]]
    if raw.get("colour") in CVD:
        eyes["colour"] = raw["colour"]
    tremor = clamp((persona.get("hands") or {}).get("tremor", 0), 0, 14)
    pid = re.sub(r"[^a-z0-9]", "", persona["id"].lower()) or "anon"
    device = persona.get("device") if persona.get("device") in DEVICES else "laptop"
    return {**persona, "id": pid, "device": device, "eyes": eyes,
            "eyes_note": persona.get("eyes_note", "") if eyes else "",
            "hands": {"tremor": tremor}}


def recruit(client, scenario, count=8, audience=None, model="muse-spark-1.3", out_dir="runs/recruit"):
    shots = capture(scenario["url"], out_dir=out_dir)
    prompt = PROMPT.format(
        task=scenario["task"], count=count, colours=", ".join(CVD),
        audience=f"What the site's owner says about the audience: {audience}\n" if audience else "",
        ships=(f"The shop delivers to: {', '.join(scenario['ships_to'])}. Everyone lives there.\n"
               if scenario.get("ships_to") else ""))
    content = [{"type": "input_text", "text": prompt}]
    content += [image(p.read_bytes()) for p in shots]
    response = client.create({"model": model, "max_output_tokens": 8000,
                              "input": [{"role": "user", "content": content}]}, tag="recruit")
    text = output_text(response)
    match = re.search(r"\{.*\}", text, re.S)
    found = json.loads(match.group(0))
    personas, seen = [], set()
    for p in found.get("personas", []):
        p = clean(p)
        while p["id"] in seen:
            p["id"] += "x"
        seen.add(p["id"])
        personas.append(p)
    return {"site": scenario["url"], "audience": found.get("audience", ""),
            "owner_said": audience or "", "personas": personas}
