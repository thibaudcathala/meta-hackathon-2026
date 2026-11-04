"""The README's pictures, made from the demo video and the runs: stills of its slides, short
loops of the walls and of one session, and one screen beside the same screen as a persona sees it.

    python -m demo.readme [demo.mp4]

Writes docs/. Needs the demo video (python -m demo.build), the runs in demo/build.py, Chrome and
ffmpeg.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

from demo.build import ROOT, Builder, esc, header, persona, shot, thought
from footfall.senses import see

DOCS = ROOT / "docs"

# name: second of the demo video
STILLS = {"recruit": 24, "verify": 54, "eye": 83, "frictions": 95, "witness": 106,
          "denmark": 118, "rounds": 131, "meta": 164}

# name: (start second, length, speed-up)
LOOPS = {"crowd": (27, 21, 1.5), "agneta": (57, 19, 1.4), "race": (134, 23, 1.5)}


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def stills(video):
    for name, t in STILLS.items():
        ffmpeg("-ss", str(t), "-i", str(video), "-frames:v", "1",
               "-vf", "scale=1600:-1:flags=lanczos", "-q:v", "3", str(DOCS / f"{name}.jpg"))


def loops(video):
    for name, (start, length, up) in LOOPS.items():
        ffmpeg("-ss", str(start), "-t", str(length), "-i", str(video), "-filter_complex",
               f"setpts=PTS/{up},fps=12,scale=1280:-1:flags=lanczos,split[a][b];"
               "[a]palettegen=stats_mode=diff:max_colors=192[p];"
               "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",
               str(DOCS / f"{name}.gif"))


def eyes(tmp):
    """Agneta's tablet screen as it was, and as Muse Spark was shown it."""
    who, turn = "agneta", 9
    p = persona("before", who)
    raw = shot("before", who, turn, "screen")
    seen = Path(tmp) / "seen.png"
    see(Image.open(raw).convert("RGB"), p["eyes"]).save(seen)
    img = "height:760px; width:auto;"
    body = header("How a persona sees", "Muse Spark is only ever shown the screen on the right") + f"""
<div style="display:flex; gap:40px; align-items:flex-start">
  <div><img class="shot" style="{img}" src="{raw.as_uri()}">
    <div class="who">The store, on {esc(p['name'])}'s tablet</div></div>
  <div><img class="shot" style="{img}" src="{seen.as_uri()}">
    <div class="who">Through her cataracts: blur, lost contrast, a yellowed lens</div></div>
  <div style="flex:1; display:flex; flex-direction:column; gap:28px; padding-top:8px">
    <h2>{esc(p['name'])}, {p['age']}, {esc(p['details']['city'])}</h2>
    <p>A gift for her grandson. Her taps shake too, a few pixels off each time.</p>
    <p class="big"><q>{esc(thought('before', who, turn))}</q></p>
    <p>Step {turn}, thinking out loud.</p>
  </div>
</div>"""
    png = Builder(tmp).render(body)
    Image.open(png).convert("RGB").resize((1600, 900), Image.LANCZOS).save(
        DOCS / "eyes.jpg", quality=88)


def main():
    video = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "runs" / "footfall-demo.mp4")
    DOCS.mkdir(exist_ok=True)
    stills(video)
    loops(video)
    with tempfile.TemporaryDirectory() as tmp:
        eyes(tmp)


if __name__ == "__main__":
    main()
