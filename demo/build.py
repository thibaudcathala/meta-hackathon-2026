"""The three-minute demo video, made from the runs themselves: slides rendered by headless
Chrome, the walls, one person's session replayed with the model's thoughts, and the screens
each person saw at the turn they spoke.

    python -m demo.build [out.mp4]

Needs the four runs below, the store's database for the order table, Chrome and ffmpeg.
"""

import html
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from footfall import wall
from footfall.senses import see

ROOT = Path(__file__).resolve().parent.parent
RUNS = {"before": "runs/20261028-171732-crowd", "r1": "runs/20261102-133233-crowd",
        "r2": "runs/20261102-134059-crowd", "r3": "runs/20261102-135252-crowd"}
DB = "postgres://postgres@localhost:5442/medusa-dtc"
REPO = "github.com/thibaudcathala/meta-hackathon-2026"
SPEED = 20  # the before/after walls play at the same clock
FADE = 0.5
LABEL_FONT = "/usr/share/fonts/opentype/inter/Inter-Bold.otf"

CSS = """
:root { --bg:#0f1215; --card:#181c21; --line:#2a3038; --ink:#eef0f2; --soft:#a3abb5;
  --accent:#6ea8ff; --ok:#3fb950; --warn:#f0a35a; --bad:#f85149; }
* { box-sizing:border-box; margin:0; }
html, body { width:1920px; height:1080px; overflow:hidden; background:var(--bg); color:var(--ink);
  font-family:Inter, sans-serif; }
body { padding:72px 96px; display:flex; flex-direction:column; gap:30px; }
.kicker { color:var(--accent); font-size:26px; font-weight:700; letter-spacing:.08em;
  text-transform:uppercase; }
h1 { font-size:60px; line-height:1.12; font-weight:800; letter-spacing:-.01em; max-width:1720px; }
h2 { font-size:32px; font-weight:700; }
p, li { font-size:32px; line-height:1.4; color:var(--soft); }
b { color:var(--ink); }
.card { background:var(--card); border:1px solid var(--line); border-radius:16px; }
.shot { border-radius:10px; border:1px solid var(--line); display:block; }
q { color:var(--ink); font-style:italic; quotes:"\\201C" "\\201D"; }
.who { font-size:24px; color:var(--soft); margin-top:8px; }
.big { font-size:44px; line-height:1.25; color:var(--ink); font-weight:700; }
"""


def esc(text):
    return html.escape(str(text or ""))


def load(run, name):
    return json.loads((ROOT / RUNS[run] / name).read_text())


def events(run, persona):
    return [json.loads(line) for line in
            (ROOT / RUNS[run] / persona / "events.jsonl").read_text().splitlines()]


def persona(run, pid):
    return next(p for p in load(run, "crowd.json")["personas"] if p["id"] == pid)


class Builder:
    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.segments = []  # (mp4, seconds)
        self.n = 0

    def path(self, ext):
        self.n += 1
        return self.tmp / f"{self.n:02d}.{ext}"

    def render(self, body, clear=False):
        """One 1920x1080 PNG of an HTML slide; `clear` keeps the background transparent, to lay
        the slide over a video."""
        page = self.path("html")
        css = CSS + ("html, body { background:transparent; }" if clear else "")
        page.write_text(f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style>"
                        f"</head><body>{body}</body></html>")
        png = page.with_suffix(".png")
        subprocess.run(["google-chrome", "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--no-first-run", "--allow-file-access-from-files",
                        "--default-background-color=00000000",
                        f"--user-data-dir={self.tmp / 'chrome'}", "--window-size=1920,1080",
                        f"--screenshot={png}", page.as_uri()],
                       check=True, capture_output=True)
        return png

    def ffmpeg(self, *args):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)

    def still(self, body, seconds):
        png = self.render(body)
        out = self.path("mp4")
        self.ffmpeg("-loop", "1", "-framerate", "30", "-t", str(seconds), "-i", str(png),
                    "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "16", "-tune", "stillimage", str(out))
        self.segments.append((out, seconds))

    def framed(self, steps, videos, seconds, extra="", over=False):
        """Videos and slides on one frame. `steps` is [(start second, html)], each slide showing
        until the next one starts; `videos` is [dict(mp4, x, y, w, h, up=speed-up, start=second
        to start from, look=extra filters)]. Slides go under the videos, or over them (and
        transparent) when `over`. `extra` is more filters on the finished frame."""
        inputs, graph, layers = [], [f"color=c=0x0f1215:s=1920x1080:r=30:d={seconds}[l0]"], []
        for i, (start, body) in enumerate(steps):
            end = steps[i + 1][0] if i + 1 < len(steps) else seconds + 1
            layers.append(("slide", (self.render(body, clear=over), start, end)))
        clips = [("video", v) for v in videos]
        for n, (kind, item) in enumerate(clips + layers if over else layers + clips):
            k = len([a for a in inputs if a == "-i"])
            if kind == "slide":
                png, start, end = item
                inputs += ["-loop", "1", "-framerate", "30", "-t", str(seconds), "-i", str(png)]
                graph.append(f"[l{n}][{k}:v]overlay=0:0:enable='between(t,{start},{end})'[l{n + 1}]")
            else:
                v = item
                inputs += ["-i", str(v["mp4"])]
                look = f",{v['look']}" if v.get("look") else ""
                graph.append(f"[{k}:v]trim=start={v.get('start', 0)},setpts=(PTS-STARTPTS)/"
                             f"{v.get('up', 1)}{look},scale={v['w']}:{v['h']},"
                             f"tpad=stop_mode=clone:stop_duration={seconds}[m{n}]")
                graph.append(f"[l{n}][m{n}]overlay={v['x']}:{v['y']}[l{n + 1}]")
        graph.append(f"[l{len(clips) + len(layers)}]{extra or 'null'},format=yuv420p[v]")
        script = self.path("txt")
        script.write_text(";\n".join(graph))
        out = self.path("mp4")
        self.ffmpeg(*inputs, "-filter_complex_script", str(script), "-map", "[v]",
                    "-t", str(seconds), "-r", "30", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "18", str(out))
        self.segments.append((out, seconds))

    def join(self, out):
        """Every segment, crossfaded, with a silent audio track for players that want one."""
        inputs, graph, prev, offset = [], [], "[0:v]", 0.0
        for i, (mp4, seconds) in enumerate(self.segments):
            inputs += ["-i", str(mp4)]
            if i:
                offset += self.segments[i - 1][1] - FADE
                graph.append(f"{prev}[{i}:v]xfade=transition=fade:duration={FADE}:"
                             f"offset={offset:.2f}[x{i}]")
                prev = f"[x{i}]"
        total = offset + self.segments[-1][1]
        graph.append(f"{prev}format=yuv420p[v]")
        self.ffmpeg(*inputs, "-f", "lavfi", "-t", f"{total:.2f}", "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-filter_complex", ";".join(graph), "-map", "[v]",
                    "-map", f"{len(self.segments)}:a", "-c:v", "libx264", "-preset", "slow",
                    "-crf", "18", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(out))
        return total


def kicker(text, title):
    return f"<div class='kicker'>{esc(text)}</div><h1>{esc(title)}</h1>"


def header(kick, text):
    """The band above a full-width video."""
    return f"""
<div style="height:150px; display:flex; flex-direction:column; justify-content:center; gap:10px; margin:-48px 0 0">
  <div class="kicker">{esc(kick)}</div>
  <div style="font-size:36px; font-weight:700; line-height:1.2; white-space:nowrap">{esc(text)}</div></div>"""


def shot(run, who, turn, kind="seen"):
    """The screen at a turn, as the person saw it or as the site drew it. Each turn saves the
    raw screen, then the seen one: 0025-t12-screen.png, 0026-t12-seen.png."""
    seen = next(e for e in events(run, who) if e["kind"] == "turn" and e["turn"] == turn)["shot"]
    path = ROOT / RUNS[run] / who / seen
    return path if kind == "seen" else path.with_name(f"{int(path.name[:4]) - 1:04d}-t{turn:02d}-screen.png")


def thought(run, who, turn):
    return next(e for e in events(run, who) if e["kind"] == "turn" and e["turn"] == turn)["thought"]


def totals(run):
    res = load(run, "crowd.json")["results"]
    return sum(r["turns"] for r in res), sum(r["seconds"] for r in res) / 60


def last_order(run):
    """Seconds into the recordings when the crowd's last order was confirmed (a session can
    go on a while after it, until the person says they are done)."""
    last = 0
    for pid in (r["persona"] for r in load(run, "crowd.json")["results"]):
        t = {e["kind"]: e["t"] for e in events(run, pid)
             if e["kind"] == "record_start" or e.get("stage") == "order confirmed"}
        if "funnel" in t:
            last = max(last, t["funnel"] - t["record_start"])
    return last


# ---- the segments, in the order they play ----

def opening(b):
    """The title over the original store's wall."""
    before = ROOT / RUNS["before"] / "wall.mp4"
    body = """
<div style="position:absolute; inset:0; background:linear-gradient(90deg, rgba(15,18,21,.94) 0%,
  rgba(15,18,21,.86) 55%, rgba(15,18,21,.55) 100%)"></div>
<div style="position:relative; margin:auto 0; display:flex; flex-direction:column; gap:36px">
  <div class="kicker">Meta AI Developer Hackathon 2026 · Indie track</div>
  <div style="font-size:170px; font-weight:800; letter-spacing:-.03em; line-height:1">Footfall</div>
  <p style="font-size:46px; color:var(--ink); max-width:1400px; line-height:1.3">A crowd of synthetic
    customers shops your website, in real browsers, before your real customers do.</p>
</div>"""
    b.framed([(0, body)], [dict(mp4=before, x=0, y=0, w=1920, h=1080, up=2, start=4)], 8,
             over=True)


def problem():
    return """
<div style="margin:auto 0; display:flex; flex-direction:column; gap:56px; max-width:1600px">
  <p style="font-size:56px; line-height:1.25; color:var(--ink); font-weight:700">Usability tests take
    weeks, and the testers rarely look like your customers.</p>
  <p style="font-size:42px; line-height:1.4">Footfall recruits the right crowd <b>from the site
    itself</b>, in <b>8 minutes</b>, for <b>about $3</b>. Here: <b>Medusa</b>, a popular
    open-source store, self-hosted with its real checkout and database.</p>
</div>"""


def recruit():
    cards = []
    for p in load("before", "crowd.json")["personas"]:
        d = p["details"]
        senses = [p["eyes_note"].split(",")[0]] if p.get("eyes_note") else []
        if (p.get("hands") or {}).get("tremor"):
            senses.append("shaky hands")
        cards.append(f"""
  <div class="card" style="padding:24px 28px; display:flex; flex-direction:column; gap:8px">
    <div style="display:flex; justify-content:space-between; align-items:baseline">
      <h2 style="font-size:36px">{esc(p['name'])}, {p['age']}</h2>
      <span style="font-size:24px; color:var(--accent); font-weight:600">{esc(p['device'].title())}</span></div>
    <div style="font-size:27px; color:var(--ink)">{esc(p['tag'])}</div>
    <div style="font-size:24px; color:var(--soft)">{esc(d['city'])}, {esc(d['country'])}</div>
    {f'<div style="font-size:26px; color:var(--warn); font-weight:600">{esc(", ".join(senses))}</div>' if senses else ''}
  </div>""")
    return (kicker("1 · Recruit", "Muse Spark reads the store and recruits who would really shop there")
            + f"""
<div style="display:grid; grid-template-columns:repeat(4, 1fr); gap:22px">{''.join(cards)}</div>
<p>Each one gets a <b>device</b>, <b>eyesight</b>, <b>hands</b>, a <b>language</b> and a home address.</p>""")


def shop(b):
    before = ROOT / RUNS["before"] / "wall.mp4"
    length = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "csv=p=0", str(before)],
                                  capture_output=True, text=True, check=True).stdout)
    b.framed([(0, header("2 · Shop", "Eight real Chrome browsers on the Medusa store. Task: buy a "
                         "black T-shirt, size M.")),
              (7.5, header("2 · Shop", "Muse Spark is each person's mind, Muse Wonder aims their "
                           "taps. The yellow tile: cataracts.")),
              (15, header("2 · Shop", "All eight reach “Thank you”. Each tile's verdict "
                          "comes from the store's database."))],
             [dict(mp4=before, x=134, y=150, w=1652, h=930, up=length / 22)], 22)


def verify():
    crowd = load("before", "crowd.json")
    rows = subprocess.run(
        ["psql", DB, "-At", "-F", "\t", "-c",
         "select o.display_id, split_part(o.email, '.', 1), a.city, upper(a.country_code), "
         "li.variant_sku from \"order\" o join order_item oi on oi.order_id = o.id "
         "join order_line_item li on li.id = oi.item_id left join order_address a "
         "on a.id = o.shipping_address_id where o.email like '%.20261028-171732@%' "
         "order by o.display_id"], capture_output=True, text=True, check=True).stdout
    names = {p["id"]: p["name"] for p in crowd["personas"]}
    lines = rows.strip().splitlines()
    table = "".join(
        "<tr>" + "".join(f"<td>{esc(names.get(c, c))}</td>" for c in line.split("\t"))
        + "<td style='color:var(--ok); font-size:34px'>✓</td></tr>" for line in lines)
    return (kicker("3 · Verify", "Done means an order in the store's database, not a model saying so")
            + f"""
<style>td, th {{ font-size:28px; padding:9px 28px 9px 0; text-align:left;
  font-family:'DejaVu Sans Mono', monospace; }} th {{ color:var(--soft); font-weight:400; }}</style>
<div style="display:flex; gap:64px; align-items:center">
  <div class="card" style="padding:28px 32px">
    <table><tr><th>order</th><th>customer</th><th>city</th><th>country</th><th>item</th><th></th></tr>
    {table}</table></div>
  <div style="flex:1; display:flex; flex-direction:column; gap:28px">
    <div style="font-size:150px; font-weight:800; color:var(--ok); line-height:1">{len(lines)}/8</div>
    <p class="big">orders found</p>
    <p>Footfall checks the store's own database: each order must exist, from that person, with the
      right shirt.</p>
  </div></div>""")


def live(b):
    """Agneta's session replayed through her eyes, with Muse Spark's thought at each step as it
    happened, and the call that produced it."""
    who, first, last = "agneta", 9, 13
    ev = events("before", who)
    rec = next(e["t"] for e in ev if e["kind"] == "record_start")
    turns = {e["turn"]: e for e in ev if e["kind"] == "turn"}
    looked = {e["turn"] for e in ev if e["kind"] == "look"}
    calls = [json.loads(line) for line in
             (ROOT / RUNS["before"] / who / "responses.jsonl").read_text().splitlines()]
    start = turns[first]["t"] - rec - 4
    seconds = 20
    up = (turns[last]["t"] - rec + 7 - start) / seconds
    steps = []
    for turn in range(first, last + 1):
        call = calls[turn]
        eye = ("<p style='font-size:28px; margin-top:18px'><b style='color:var(--bad)'>Muse "
               "Wonder</b> checked where her finger lands</p>") if turn in looked else ""
        steps.append((0 if turn == first else (turns[turn]["t"] - rec - start - 1.5) / up,
                      kicker("4 · Look, think, act", "Agneta, live: Muse Spark plays her from what "
                             "she sees")
                      + f"""
<div style="position:absolute; left:860px; top:330px; width:960px; display:flex; flex-direction:column; gap:18px">
  <div style="font-size:26px; color:var(--accent); font-weight:700">STEP {turn}, THINKING OUT LOUD</div>
  <p style="font-size:46px; line-height:1.3"><q>{esc(turns[turn]['thought'])}</q></p>
  <div class="card" style="padding:16px 22px; margin-top:12px; font-family:'DejaVu Sans Mono', monospace;
    font-size:24px; color:var(--soft)">{esc(call['model'])} · {call['completed_at'] - call['created_at']:.0f} s ·
    {call['usage']['input_tokens']:,} tokens in, one screenshot</div>
  {eye}
</div>"""))
    p = persona("before", who)
    b.framed(steps, [dict(mp4=ROOT / RUNS["before"] / who / "screen.mp4", x=96, y=270, w=582, h=780,
                          up=up, start=start, look=",".join(wall.eyes_filter(p["eyes"])))],
             seconds)


def eye_crop(tmp):
    """Wonder's view at Agneta's turn 12: a zoomed crop, still through her eyes, with where
    Spark aimed and where Wonder put the tap, and where that crop sits on her screen."""
    look = next(e for e in events("before", "agneta") if e["kind"] == "look" and e["turn"] == 12)
    img = see(Image.open(shot("before", "agneta", 12, "screen")).convert("RGB"),
              persona("before", "agneta")["eyes"])
    (ax, ay), (cx, cy) = look["aimed"], look["corrected"]
    mx, my, half, k = (ax + cx) // 2, (ay + cy) // 2, 190, 2
    crop = img.crop((mx - half, my - half, mx + half, my + half)).resize((2 * half * k,) * 2)
    d = ImageDraw.Draw(crop)
    font = ImageFont.truetype(LABEL_FONT, 34)
    ax, ay, cx, cy = ((v - o + half) * k for v, o in ((ax, mx), (ay, my), (cx, mx), (cy, my)))
    blue, red = (46, 110, 230), (220, 50, 40)
    d.line((ax, ay - 34, cx, cy + 46), fill=red, width=5)
    d.polygon([(cx, cy + 36), (cx - 15, cy + 62), (cx + 15, cy + 62)], fill=red)
    d.ellipse((ax - 28, ay - 28, ax + 28, ay + 28), outline=blue, width=8)
    d.ellipse((cx - 34, cy - 34, cx + 34, cy + 34), outline=red, width=8)
    d.text((ax + 48, ay - 20), "Spark aimed here", font=font, fill=blue)
    moved = "Wonder moved it here"
    d.text((cx, cy - 50), moved, font=font, fill=red, anchor="mb")
    out = Path(tmp) / "eye.png"
    crop.save(out)
    seen = Image.open(shot("before", "agneta", 12)).convert("RGB")
    z = seen.height / img.height  # the persona's view is the screen, enlarged
    ImageDraw.Draw(seen).rectangle([round(v * z) for v in (mx - half, my - half, mx + half, my + half)],
                                   outline=red, width=6)
    where = Path(tmp) / "eye-where.png"
    seen.save(where)
    return out, where


def eye(tmp):
    crop, where = eye_crop(tmp)
    return (kicker("4 · Look, think, act", "Muse Wonder is the eye: it checks where the finger lands")
            + f"""
<div style="display:flex; gap:56px; align-items:flex-start">
  <img class="shot" src="{where.as_uri()}" style="height:700px">
  <img class="shot" src="{crop.as_uri()}" style="height:700px">
  <div style="flex:1; display:flex; flex-direction:column; gap:30px; padding-top:10px">
    <p class="big">Step 12: Spark aimed at empty space.</p>
    <p>Wonder looked again at a zoomed crop, still through her cataracts, and moved the tap onto
      the quantity.</p>
  </div></div>""")


def frictions():
    found = load("before", "insights.json")
    picks = [("Country left empty after address fill", "lea", 11),
             ("Checkout button not visible in cart", "mikkel", 13),
             ("No add-to-cart feedback causes duplicate quantities", "camille", 8)]
    names = {p["id"]: p["name"] for p in load("before", "crowd.json")["personas"]}
    cards = []
    for title_, who, turn in picks:
        f = next(x for x in found["frictions"] if x["title"] == title_)
        quote = next(e["quote"] for e in f["evidence"] if e["persona"] == who and e["turn"] == turn)
        cards.append(f"""
  <div class="card" style="padding:20px; display:flex; gap:22px; flex:1">
    <img class="shot" src="{shot('before', who, turn).as_uri()}" style="height:620px">
    <div style="display:flex; flex-direction:column; gap:16px">
      <div style="font-size:30px; font-weight:800; color:var(--warn)">{len(f['who'])} of 8 people</div>
      <h2 style="font-size:30px; line-height:1.2">{esc(title_)}</h2>
      <p style="font-size:25px; line-height:1.35"><q>{esc(quote)}</q></p>
      <p class="who">{esc(names[who])}</p>
    </div></div>""")
    return (kicker("5 · Find", "What got in their way, in their words, on the screen they saw")
            + f"<div style='display:flex; gap:24px'>{''.join(cards)}</div>")


def witness():
    found = load("before", "insights.json")
    kept = next(x for x in found["frictions"] if x["title"].startswith("No add-to-cart"))
    aside = found["harness"][0]
    card = lambda colour, label, title_, text: f"""
  <div class="card" style="padding:36px; flex:1; border:3px solid {colour}">
    <div style="font-size:28px; font-weight:800; color:{colour}">{label}</div>
    <h2 style="margin:14px 0; font-size:36px">{esc(title_)}</h2>
    <p>{text}</p></div>"""
    return (kicker("5 · Find", "Then Muse Spark double-checks every complaint on the screens")
            + "<p>It sees the screens before, at and after each quote, and what the person did.</p>"
            + "<div style='display:flex; gap:32px'>"
            + card("var(--ok)", "✓ KEPT: THE SITE", kept["title"],
                   f"Spark's note: <q>{esc(kept['witness']['seen'])}</q>")
            + card("var(--soft)", "✗ SET ASIDE: OUR TEST TOOL", aside["title"],
                   "The harness itself typed <b>MadridMadrid</b> in the city field. Not the "
                   "site's fault, so it is not reported.")
            + "</div>")


def denmark():
    q = lambda who, turn: esc(thought("r1", who, turn))
    img = lambda who, turn, label: f"""
  <figure><img class="shot" src="{shot('r1', who, turn).as_uri()}" style="height:640px">
    <p class="who">{label}</p></figure>"""
    return (kicker("6 · Fix", "Round 1: the crowd caught my own bug")
            + f"""
<div style="display:flex; gap:40px; align-items:flex-start">
  {img('camille', 7, 'Camille, Lyon')}
  {img('chloe', 5, 'Chloe, Manchester')}
  <div style="flex:1; display:flex; flex-direction:column; gap:26px; padding-top:6px">
    <p class="big" style="font-size:50px">My fix set everyone's country to <span style="color:var(--bad)">Denmark</span>.
      7 of 8 had to change it.</p>
    <p style="font-size:30px"><q>{q('camille', 7)}</q> <span class="who">Camille</span></p>
    <p style="font-size:30px"><q>{q('chloe', 5)}</q> <span class="who">Chloe</span></p>
    <p>Round 2 guesses the country from the browser's languages, and each person's Chrome now
      speaks their own.</p>
  </div></div>""")


def rounds():
    rows = [("Original store", "before", None, "var(--soft)"),
            ("Round 1", "r1", "Add-to-cart confirmation, checkout button always on screen, "
             "size chosen by the shopper, country first <span style='color:var(--bad)'>(and the "
             "Denmark bug)</span>", "var(--accent)"),
            ("Round 2", "r2", "Country guessed from the browser's languages", "var(--accent)"),
            ("Round 3", "r3", "Product details no longer stuck, bigger size buttons, the only "
             "payment preselected", "var(--ok)")]
    out = []
    for label, run, what, colour in rows:
        turns, minutes = totals(run)
        done = sum(bool(r.get("verified")) for r in load(run, "crowd.json")["results"])
        out.append(f"""
  <div style="display:grid; grid-template-columns:250px 1fr; gap:28px; align-items:center">
    <h2>{label}</h2>
    <div><div style="display:flex; align-items:center; gap:22px">
      <div style="height:50px; width:{turns / 216 * 900:.0f}px; background:{colour}; border-radius:8px"></div>
      <span style="font-size:32px; font-weight:700; white-space:nowrap">{turns} turns · {minutes:.0f} min · {done}/8 orders</span></div>
      {f'<p style="font-size:25px; margin-top:6px">{what}</p>' if what else ''}</div></div>""")
    tb, ta = totals("before")[0], totals("r3")[0]
    return (kicker("6 · Fix", "Fix the store, send the same eight people again")
            + f"<div style='display:flex; flex-direction:column; gap:30px; margin-top:6px'>{''.join(out)}</div>"
            + f"""<div style="display:flex; align-items:baseline; gap:32px; margin-top:12px">
  <div style="font-size:96px; font-weight:800; color:var(--ok); white-space:nowrap">−{100 - 100 * ta / tb:.0f} %</div>
  <p>turns to buy the same T-shirt. A turn is one look, think and act step of one person;
    minutes are the eight people's added up.</p></div>""")


def side_by_side(b, tmp):
    """The original store and round 3 at the same clock, which stops at each crowd's last order."""
    walls = []
    for run in ("before", "r3"):
        mp4 = Path(tmp) / f"wall-{run}.mp4"
        wall.build(ROOT / RUNS[run], mp4, seconds=24, speed=SPEED)
        walls.append((mp4, last_order(run)))
    w, h, y = 900, 506, 280
    label = lambda x, text: (f"<div style='position:absolute; left:{x}px; top:{y - 70}px; "
                             f"font-size:34px; font-weight:700'>{text}</div>")
    body = (header("6 · Fix", "The same eight people at once, before and after the fixes")
            + label(40, "Original store <span style='color:var(--soft); font-weight:400'>· run Oct 28</span>")
            + label(980, "After round 3 <span style='color:var(--soft); font-weight:400'>· run Nov 2</span>")
            + f"<p style='position:absolute; left:40px; top:{y + h + 50}px; font-size:32px'>"
              "The clock is real time, until the crowd's last order.</p>")
    marks = []
    for (_, last), x in zip(walls, (40, 980)):
        t = f"min(t*{SPEED}\\,{last:.0f})"
        marks.append(f"drawtext=fontfile={wall.BOLD}:fontsize=44:fontcolor=white:"
                     f"x={x + w}-tw:y={y - 74}:text='%{{eif\\:trunc({t}/60)\\:d}}\\:"
                     f"%{{eif\\:mod(trunc({t})\\,60)\\:d\\:2}}'")
        marks.append(f"drawtext=fontfile={wall.BOLD}:fontsize=36:fontcolor=0x3fb950:"
                     f"x={x + w}-tw:y={y + h + 50}:text='ALL 8 ORDERED':"
                     f"enable='gte(t,{last / SPEED:.2f})'")
    b.framed([(0, body)], [dict(mp4=walls[0][0], x=40, y=y, w=w, h=h),
                           dict(mp4=walls[1][0], x=980, y=y, w=w, h=h)], 24,
             extra=",".join(marks))


def meta():
    col = lambda name, what: f"""
  <div class="card" style="padding:36px; flex:1">
    <h2 style="font-size:42px; color:var(--accent)">{name}</h2>
    <ul style="margin-top:18px; padding-left:30px; display:flex; flex-direction:column; gap:12px">
      {''.join(f'<li style="color:var(--ink)">{esc(w)}</li>' for w in what)}</ul></div>"""
    return (kicker("Under the hood", "One crowd of eight: about $3 and under 8 minutes")
            + "<div style='display:flex; gap:28px'>"
            + col("Muse Spark 1.3", ["recruits the crowd from the site",
                                     "plays each shopper, seeing only their screen",
                                     "writes up what went wrong, with quotes",
                                     "double-checks each problem on the screens"])
            + col("Muse Wonder 1.0", ["is the eye: re-checks every tap on a zoomed crop",
                                      "through the person's own eyesight",
                                      "so taps land where the person meant"])
            + "</div><p class='big' style='margin-top:20px'>Every shopper who gives up is a lost "
              "sale. Footfall finds what stops them before real customers arrive.</p>")


def end():
    log = subprocess.run(["git", "log", "--format=%ad", "--date=format:%b %d"], cwd=ROOT,
                         capture_output=True, text=True, check=True,
                         env={**os.environ, "LC_ALL": "C"}).stdout.split()
    first = " ".join(log[-2:])
    return f"""
<div style="margin:auto 0; display:flex; flex-direction:column; gap:36px">
  <div style="font-size:150px; font-weight:800; letter-spacing:-.03em; line-height:1">Footfall</div>
  <p style="font-size:48px; color:var(--ink)">{REPO}</p>
  <p style="font-size:36px">Built during the hackathon: first commit {first},
    {len(log) // 2} commits.</p>
</div>"""


def build(out):
    with tempfile.TemporaryDirectory() as tmp:
        b = Builder(tmp)
        opening(b)
        b.still(problem(), 8)
        b.still(recruit(), 12)
        shop(b)
        b.still(verify(), 9)
        live(b)
        b.still(eye(tmp), 10)
        b.still(frictions(), 15)
        b.still(witness(), 10)
        b.still(denmark(), 12)
        b.still(rounds(), 13)
        side_by_side(b, tmp)
        b.still(meta(), 10)
        b.still(end(), 7)
        total = b.join(out)
    return out, total


if __name__ == "__main__":
    out, total = build(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "runs/demo.mp4"))
    print(f"{out}: {total:.0f} s")
