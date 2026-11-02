"""The three-minute demo video, made from the runs themselves: slides rendered by headless
Chrome, the walls, and the screens each person saw at the turn they spoke.

    python -m demo.build [out.mp4]

Needs the four runs below, the store's database for the order table, Chrome and ffmpeg.
"""

import html
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from footfall import report, wall
from footfall.senses import see

ROOT = Path(__file__).resolve().parent.parent
RUNS = {"before": "runs/20261028-171732-crowd", "r1": "runs/20261102-133233-crowd",
        "r2": "runs/20261102-134059-crowd", "r3": "runs/20261102-135252-crowd"}
DB = "postgres://postgres@localhost:5442/medusa-dtc"
REPO = "github.com/thibaudcathala/meta-hackathon-2026"
SPEED = 20  # the before/after walls play at the same clock
FADE = 0.5

CSS = """
:root { --bg:#0f1215; --card:#181c21; --line:#2a3038; --ink:#eef0f2; --soft:#9aa3ad;
  --accent:#6ea8ff; --ok:#3fb950; --warn:#f0a35a; --bad:#f85149; }
* { box-sizing:border-box; margin:0; }
html, body { width:1920px; height:1080px; overflow:hidden; background:var(--bg); color:var(--ink);
  font-family:Inter, sans-serif; }
body { padding:72px 96px; display:flex; flex-direction:column; gap:28px; }
.kicker { color:var(--accent); font-size:26px; font-weight:700; letter-spacing:.08em;
  text-transform:uppercase; }
h1 { font-size:60px; line-height:1.12; font-weight:800; letter-spacing:-.01em; max-width:1700px; }
h2 { font-size:30px; font-weight:700; }
p, li { font-size:30px; line-height:1.4; color:var(--soft); }
b { color:var(--ink); }
.card { background:var(--card); border:1px solid var(--line); border-radius:16px; }
.shot { border-radius:10px; border:1px solid var(--line); display:block; }
q { color:var(--ink); font-style:italic; quotes:"\\201C" "\\201D"; }
.who { font-size:22px; color:var(--soft); margin-top:8px; }
"""


def esc(text):
    return html.escape(str(text or ""))


def load(run, name):
    return json.loads((ROOT / RUNS[run] / name).read_text())


class Builder:
    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.segments = []  # (mp4, seconds)
        self.n = 0

    def path(self, ext):
        self.n += 1
        return self.tmp / f"{self.n:02d}.{ext}"

    def render(self, body, header=""):
        """One 1920x1080 PNG of an HTML slide."""
        page = self.path("html")
        page.write_text(f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style>"
                        f"</head><body>{header}{body}</body></html>")
        png = page.with_suffix(".png")
        subprocess.run(["google-chrome", "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--no-first-run", "--allow-file-access-from-files",
                        f"--user-data-dir={self.tmp / 'chrome'}", "--window-size=1920,1080",
                        f"--screenshot={png}", page.as_uri()],
                       check=True, capture_output=True)
        return png

    def ffmpeg(self, *args):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)

    def still(self, body, seconds, header=""):
        png = self.render(body, header)
        out = self.path("mp4")
        self.ffmpeg("-loop", "1", "-framerate", "30", "-t", str(seconds), "-i", str(png),
                    "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "16", "-tune", "stillimage", str(out))
        self.segments.append((out, seconds))

    def framed(self, steps, videos, seconds, extra=""):
        """Videos laid onto slides: `steps` is [(start second, html)], each slide showing from
        its start; `videos` is [(mp4, x, y, w, h, speed-up)]. `extra` is more filters on [v]."""
        inputs, graph = [], []
        for i, (_, body) in enumerate(steps):
            inputs += ["-loop", "1", "-framerate", "30", "-t", str(seconds),
                       "-i", str(self.render(body))]
        graph.append("[0:v]null[b1]")
        for i, (start, _) in enumerate(steps[1:], 1):
            graph.append(f"[b{i}][{i}:v]overlay=0:0:enable='gte(t,{start})'[b{i + 1}]")
        bg = f"[b{len(steps)}]"
        for j, (mp4, x, y, w, h, up) in enumerate(videos):
            k = len(steps) + j
            inputs += ["-i", str(mp4)]
            graph.append(f"[{k}:v]setpts=(PTS-STARTPTS)/{up},scale={w}:{h},"
                         f"tpad=stop_mode=clone:stop_duration={seconds}[m{j}]")
            graph.append(f"{bg}[m{j}]overlay={x}:{y}[o{j}]")
            bg = f"[o{j}]"
        graph.append(f"{bg}{extra or 'null'},format=yuv420p[v]")
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


# ---- the slides, in the order they play ----

def title():
    return """
<div style="margin:auto 0; display:flex; flex-direction:column; gap:36px">
  <div class="kicker">Meta AI Developer Hackathon 2026 · Indie track</div>
  <div style="font-size:170px; font-weight:800; letter-spacing:-.03em; line-height:1">Footfall</div>
  <p style="font-size:44px; color:var(--ink); max-width:1500px; line-height:1.3">A crowd of synthetic
    customers shops your website, in real browsers, before your real customers do.</p>
</div>"""


def problem():
    return """
<div style="margin:auto 0; display:flex; flex-direction:column; gap:56px; max-width:1560px">
  <p style="font-size:52px; line-height:1.3; color:var(--ink)">Usability tests take weeks, cost
    thousands, and are often run with people who would never buy from you.</p>
  <p style="font-size:40px; line-height:1.4">Footfall recruits the right crowd <b>from the site
    itself</b>, sends them shopping, and checks <b>the store's database</b> for what really
    happened.</p>
</div>"""


def recruit():
    crowd = load("before", "crowd.json")
    cards = []
    for p in crowd["personas"]:
        senses = report.senses(p)
        d = p["details"]
        cards.append(f"""
  <div class="card" style="padding:22px 26px; display:flex; flex-direction:column; gap:6px">
    <div style="display:flex; justify-content:space-between; align-items:baseline">
      <h2>{esc(p['name'])}, {p['age']}</h2>
      <span style="font-size:22px; color:var(--accent); font-weight:600">{esc(report.DEVICE[p['device']])}</span></div>
    <div style="font-size:24px; color:var(--ink)">{esc(p['tag'])}</div>
    <div style="font-size:22px; color:var(--soft)">{esc(d['city'])}, {esc(d['country'])}</div>
    {f'<div style="font-size:22px; color:var(--warn)">{esc(senses)}</div>' if senses else ''}
  </div>""")
    return (kicker("1 · Recruit", "Muse Spark reads the store and recruits who would really shop there")
            + f"""
<div style="display:grid; grid-template-columns:repeat(4, 1fr); gap:20px">{''.join(cards)}</div>
<p>A shop selling a €10 black T-shirt: students, parents, a grandmother buying a gift. Each person
  gets a <b>device</b>, <b>eyesight</b>, <b>hands</b>, a <b>language</b> and a home address.</p>""")


def header(kick, text):
    """The band above a full-width video."""
    return f"""
<div style="height:150px; display:flex; flex-direction:column; justify-content:center; gap:10px; margin:-48px 0 0">
  <div class="kicker">{esc(kick)}</div>
  <div style="font-size:36px; font-weight:700; line-height:1.2; white-space:nowrap">{esc(text)}</div></div>"""


def turn_shot(run, persona, turn, kind="seen"):
    """The screen at a turn: as the person saw it, or the raw one the site drew."""
    shot = report.shot_of(ROOT / RUNS[run], persona, turn)
    # Each turn saves the raw screen, then the seen one: 0025-t12-screen.png, 0026-t12-seen.png
    return shot if kind == "seen" else shot.with_name(f"{int(shot.name[:4]) - 1:04d}-t{turn:02d}-screen.png")


def thought(run, persona, turn):
    for line in (ROOT / RUNS[run] / persona / "events.jsonl").read_text().splitlines():
        e = json.loads(line)
        if e.get("kind") == "turn" and e.get("turn") == turn:
            return e["thought"]


def eyesight():
    screen, seen = turn_shot("before", "agneta", 10, "screen"), turn_shot("before", "agneta", 10)
    return (kicker("3 · Look, think, act", "Each turn, the model gets only what that person sees")
            + f"""
<div style="display:flex; gap:56px; align-items:flex-start">
  <figure><img class="shot" src="{screen.as_uri()}" style="height:690px">
    <p style="font-size:24px; margin-top:10px">What the site draws</p></figure>
  <figure><img class="shot" src="{seen.as_uri()}" style="height:690px">
    <p style="font-size:24px; margin-top:10px">What Agneta, 68, sees: cataracts</p></figure>
  <div style="flex:1; display:flex; flex-direction:column; gap:28px; padding-top:20px">
    <p style="font-size:38px; line-height:1.35"><q>{esc(thought('before', 'agneta', 10))}</q></p>
    <p class="who">Agneta, turn 10, thinking out loud</p>
    <p>Her taps also carry a 6 px <b>tremor</b>.</p>
  </div></div>""")


def eye_crop(tmp):
    """Wonder's view at Agneta's turn 12: a zoomed crop, still through her eyes, with where
    Spark aimed (ring) and where Wonder put the tap (cross)."""
    run = ROOT / RUNS["before"]
    look = next(e for e in map(json.loads, (run / "agneta/events.jsonl").read_text().splitlines())
                if e.get("kind") == "look" and e.get("turn") == 12)
    eyes = next(p for p in load("before", "crowd.json")["personas"] if p["id"] == "agneta")["eyes"]
    img = see(Image.open(turn_shot("before", "agneta", 12, "screen")).convert("RGB"), eyes)
    (ax, ay), (cx, cy) = look["aimed"], look["corrected"]
    mx, my, half, k = (ax + cx) // 2, (ay + cy) // 2, 190, 2
    crop = img.crop((mx - half, my - half, mx + half, my + half)).resize((2 * half * k,) * 2)
    d = ImageDraw.Draw(crop)
    ax, ay, cx, cy = ((v - o + half) * k for v, o in ((ax, mx), (ay, my), (cx, mx), (cy, my)))
    d.line((ax, ay - 30, cx, cy + 40), fill=(248, 81, 73), width=4)
    d.polygon([(cx, cy + 34), (cx - 14, cy + 58), (cx + 14, cy + 58)], fill=(248, 81, 73))
    d.ellipse((ax - 26, ay - 26, ax + 26, ay + 26), outline=(110, 168, 255), width=7)
    d.line((cx - 30, cy, cx + 30, cy), fill=(248, 81, 73), width=7)
    d.line((cx, cy - 30, cx, cy + 30), fill=(248, 81, 73), width=7)
    out = Path(tmp) / "eye.png"
    crop.save(out)
    seen = Image.open(turn_shot("before", "agneta", 12)).convert("RGB")
    z = seen.height / img.height  # the persona's view is the screen, enlarged
    ImageDraw.Draw(seen).rectangle([round(v * z) for v in (mx - half, my - half, mx + half, my + half)],
                                   outline=(248, 81, 73), width=5)
    framed = Path(tmp) / "eye-where.png"
    seen.save(framed)
    return out, framed, look


def eye(tmp):
    crop, seen, look = eye_crop(tmp)
    return (kicker("3 · Look, think, act", "Muse Wonder is the eye: it checks where the finger lands")
            + f"""
<div style="display:flex; gap:56px; align-items:flex-start">
  <img class="shot" src="{seen.as_uri()}" style="height:690px">
  <img class="shot" src="{crop.as_uri()}" style="height:690px">
  <div style="flex:1; display:flex; flex-direction:column; gap:26px; padding-top:10px">
    <p style="font-size:34px; line-height:1.35"><q>{esc(thought('before', 'agneta', 12))}</q></p>
    <p><span style="color:var(--accent); font-weight:700">◯</span> Muse Spark aimed from her
      blurry view: empty space.</p>
    <p><span style="color:var(--bad); font-weight:700">✛</span> Muse Wonder looked again at a
      zoomed crop, still through her eyes, and moved the tap onto the quantity.</p>
  </div></div>""")


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
    table = "".join(
        "<tr>" + "".join(f"<td>{esc(names.get(c, c))}</td>" for c in line.split("\t"))
        + "<td style='color:var(--ok)'>✓</td></tr>" for line in rows.strip().splitlines())
    return (kicker("4 · Verify", "Done means an order in the store's database, not a model saying so")
            + f"""
<style>td, th {{ font-size:28px; padding:9px 28px 9px 0; text-align:left;
  font-family:'DejaVu Sans Mono', monospace; }} th {{ color:var(--soft); font-weight:400; }}</style>
<div style="display:flex; gap:56px; align-items:flex-start">
  <div class="card" style="padding:28px 32px">
    <table><tr><th>order</th><th>customer</th><th>city</th><th>country</th><th>item</th><th></th></tr>
    {table}</table></div>
  <div style="flex:1; display:flex; flex-direction:column; gap:28px; padding-top:12px">
    <p style="font-size:44px; color:var(--ink); font-weight:700">8 of 8 orders found</p>
    <p>Footfall reads the store's own Postgres after each person stops: the order must carry their
      email and the right item, a black T-shirt in size M.</p>
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
    <img class="shot" src="{turn_shot('before', who, turn).as_uri()}" style="height:600px">
    <div style="display:flex; flex-direction:column; gap:16px">
      <div style="font-size:22px; font-weight:700; color:var(--warn)">{len(f['who'])} OF 8 PEOPLE</div>
      <h2 style="font-size:30px; line-height:1.2">{esc(title_)}</h2>
      <p style="font-size:25px; line-height:1.35"><q>{esc(quote)}</q></p>
      <p class="who">{esc(names[who])}, turn {turn}</p>
    </div></div>""")
    return (kicker("5 · Find", "What got in their way, in their words, on the screen they saw")
            + f"<div style='display:flex; gap:24px'>{''.join(cards)}</div>")


def witness():
    found = load("before", "insights.json")
    kept = next(x for x in found["frictions"] if x["title"].startswith("No add-to-cart"))
    aside = found["harness"][0]
    return (kicker("5 · Find", "Then Muse Spark looks again: was it the site, or the test harness?")
            + f"""
<p style="max-width:1500px">For each friction it sees the screens just before, at and after every
  quote, with what the person did, and gives a verdict.</p>
<div style="display:flex; gap:28px">
  <div class="card" style="padding:32px; flex:1; border-color:var(--ok)">
    <div style="font-size:24px; font-weight:700; color:var(--ok)">KEPT · THE SITE</div>
    <h2 style="margin:12px 0">{esc(kept['title'])}</h2>
    <p>{esc(kept['witness']['seen'])}</p></div>
  <div class="card" style="padding:32px; flex:1; border-color:var(--soft)">
    <div style="font-size:24px; font-weight:700; color:var(--soft)">SET ASIDE · THE HARNESS</div>
    <h2 style="margin:12px 0">{esc(aside['title'])}</h2>
    <p>{esc(aside['witness']['seen'])} A keystroke of the harness, not a fault of the site.</p></div>
</div>""")


def rounds():
    rows = [("Original store", "before", None, "var(--soft)"),
            ("Round 1", "r1", "Products on the home page, size not preselected, add-to-cart "
             "confirmation, checkout button kept on screen, country first", "var(--accent)"),
            ("Round 2", "r2", "Country guessed from the browser's languages", "var(--accent)"),
            ("Round 3", "r3", "Product details unstuck, bigger size buttons, the only payment "
             "preselected", "var(--ok)")]
    out = []
    for label, run, what, colour in rows:
        turns, minutes = totals(run)
        done = sum(bool(r.get("verified")) for r in load(run, "crowd.json")["results"])
        out.append(f"""
  <div style="display:grid; grid-template-columns:260px 1fr; gap:28px; align-items:center">
    <h2>{label}</h2>
    <div><div style="display:flex; align-items:center; gap:22px">
      <div style="height:46px; width:{turns / 216 * 960:.0f}px; background:{colour}; border-radius:8px"></div>
      <span style="font-size:30px; font-weight:700; white-space:nowrap">{turns} turns · {minutes:.0f} min · {done}/8 orders</span></div>
      {f'<p style="font-size:23px; margin-top:6px">{esc(what)}</p>' if what else ''}</div></div>""")
    return (kicker("6 · Fix", "Fix the store, send the same eight people again")
            + f"<div style='display:flex; flex-direction:column; gap:30px; margin-top:16px'>{''.join(out)}</div>"
            + "<p style='margin-top:8px'>The whole crowd needed <b>53 % fewer turns</b> to buy the same "
              "T-shirt. The fixes are plain patches on the open-source Medusa storefront.</p>")


def denmark():
    camille, chloe = turn_shot("r1", "camille", 7), turn_shot("r1", "chloe", 5)
    q = lambda who, turn: esc(thought("r1", who, turn))
    return (kicker("6 · Fix", "Round 1: the crowd caught my own bug")
            + f"""
<div style="display:flex; gap:40px; align-items:flex-start">
  <img class="shot" src="{camille.as_uri()}" style="height:700px">
  <img class="shot" src="{chloe.as_uri()}" style="height:700px">
  <div style="flex:1; display:flex; flex-direction:column; gap:26px; padding-top:6px">
    <div><p style="font-size:34px"><q>{q('camille', 7)}</q></p><p class="who">Camille, Lyon</p></div>
    <div><p style="font-size:34px"><q>{q('chloe', 5)}</q></p><p class="who">Chloe, Manchester</p></div>
    <p>My fix preselected the country from the URL: <b>Denmark</b>, for everyone. 7 of 8 had to
      change it. Round 2 reads the browser's languages, and each person's Chrome now speaks their
      own.</p>
  </div></div>""")


def meta():
    col = lambda name, what: f"""
  <div class="card" style="padding:36px; flex:1">
    <h2 style="font-size:40px; color:var(--accent)">{name}</h2>
    <ul style="margin-top:18px; padding-left:30px; display:flex; flex-direction:column; gap:12px">
      {''.join(f'<li>{esc(w)}</li>' for w in what)}</ul></div>"""
    return (kicker("Under the hood", "Two Meta models, each doing what it is best at")
            + "<div style='display:flex; gap:28px'>"
            + col("Muse Spark 1.3", ["recruits the crowd from the site and its audience",
                                     "is each person's mind, seeing only their screen",
                                     "groups frictions, keeps only real quotes",
                                     "re-checks every friction on the screens"])
            + col("Muse Wonder 1.0", ["is the eye: closed-loop pointing on a zoomed crop",
                                      "looks through the person's own eyesight",
                                      "knows which tap of the turn it is correcting",
                                      "so a tap lands where the person meant it"])
            + "</div><p style='font-size:36px; color:var(--ink)'>One crowd of eight: "
              "<b>about $3</b> and <b>under 8 minutes</b>. Real Chrome, a real store, its real "
              "database.</p>")


def end():
    log = subprocess.run(["git", "log", "--format=%ad", "--date=format:%b %d"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.split("\n")
    log = [d for d in log if d]
    return f"""
<div style="margin:auto 0; display:flex; flex-direction:column; gap:34px">
  <div style="font-size:150px; font-weight:800; letter-spacing:-.03em; line-height:1">Footfall</div>
  <p style="font-size:44px; color:var(--ink)">{REPO}</p>
  <p style="font-size:34px">Built during the hackathon, Oct 26 – Nov 4, 2026: first commit
    {log[-1]}, {len(log)} commits so far.</p>
</div>"""


def build(out):
    with tempfile.TemporaryDirectory() as tmp:
        b = Builder(tmp)
        b.still(title(), 6)
        b.still(problem(), 9)
        b.still(recruit(), 14)
        before = ROOT / RUNS["before"] / "wall.mp4"
        length = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                       "-of", "csv=p=0", str(before)],
                                      capture_output=True, text=True, check=True).stdout)
        b.framed([(0, header("2 · Shop", "Eight real Chrome browsers at once. The task: buy one "
                             "black T-shirt, size M. No script.")),
                  (8, header("2 · Shop", "Each screen as that person sees it: Agneta's cataracts, "
                             "Mikkel's red-green colour blindness")),
                  (16, header("2 · Shop", "Every tile ends on what the store's database says"))],
                 [(before, 134, 150, 1652, 930, length / 24)], 24)
        b.still(eyesight(), 9)
        b.still(eye(tmp), 11)
        b.still(verify(), 10)
        b.still(frictions(), 14)
        b.still(witness(), 10)
        b.still(rounds(), 14)
        b.still(denmark(), 13)
        side_by_side(b, tmp)
        b.still(meta(), 11)
        b.still(end(), 7)
        total = b.join(out)
    return out, total


def last_order(run):
    """Seconds into the recordings when the crowd's last order was confirmed (a session can
    go on a while after it, until the person says they are done)."""
    last = 0
    for events in (ROOT / RUNS[run]).glob("*/events.jsonl"):
        t = {}
        for e in map(json.loads, events.read_text().splitlines()):
            if e["kind"] == "record_start" or e.get("stage") == "order confirmed":
                t[e["kind"]] = e["t"]
        if "funnel" in t:
            last = max(last, t["funnel"] - t["record_start"])
    return last


def totals(run):
    res = load(run, "crowd.json")["results"]
    return sum(r["turns"] for r in res), sum(r["seconds"] for r in res) / 60


def side_by_side(b, tmp):
    """The original store and round 3 at the same clock, which stops at each crowd's last order."""
    walls = []
    for run in ("before", "r3"):
        mp4 = Path(tmp) / f"wall-{run}.mp4"
        wall.build(ROOT / RUNS[run], mp4, seconds=24, speed=SPEED)
        walls.append((mp4, last_order(run)))
    (tb, mb), (ta, ma) = totals("before"), totals("r3")
    w, h, y = 900, 506, 280
    label = lambda x, text: (f"<div style='position:absolute; left:{x}px; top:{y - 64}px; "
                             f"font-size:32px; font-weight:700'>{text}</div>")
    body = (header("6 · Fix", "The same eight people, at the same speed: before, and after "
                   "three rounds of fixes")
            + label(40, "Original store") + label(980, "After round 3")
            + f"<p style='position:absolute; left:40px; top:{y + h + 50}px; font-size:30px'>"
              "Clock: real time, until the crowd's last order. Whole crowd: "
              f"<b>{tb} → {ta} turns</b>, <b>{mb:.0f} → {ma:.0f} minutes</b>.</p>")
    clocks = []
    for (_, longest), x in zip(walls, (40, 980)):
        t = f"min(t*{SPEED}\\,{longest:.0f})"
        clocks.append(f"drawtext=fontfile={wall.BOLD}:fontsize=40:fontcolor=white:"
                      f"x={x + w}-tw:y={y - 64}:text='%{{eif\\:trunc({t}/60)\\:d}}\\:"
                      f"%{{eif\\:mod(trunc({t})\\,60)\\:d\\:2}}'")
    b.framed([(0, body)], [(walls[0][0], 40, y, w, h, 1), (walls[1][0], 980, y, w, h, 1)], 24,
             extra=",".join(clocks))


if __name__ == "__main__":
    out, total = build(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "runs/demo.mp4"))
    print(f"{out}: {total:.0f} s")
