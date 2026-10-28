"""Side-by-side film of a run: the tutorial on the left, the apprentice on the right, one
segment per step, each closed by the verifier's verdict. The apprentice side is sped up to the
tutorial's pace and says by how much."""

import json
import subprocess
import tempfile
from pathlib import Path

from PIL import ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H = 1920, 1080
PANE_W = 944
FPS = 30


def text_file(tmp, name, text):
    path = Path(tmp) / name
    path.write_text(text)
    return str(path).replace(":", r"\:")


def fit(text, font, size, width):
    """Largest font size up to `size` at which `text` fits in `width` pixels."""
    while size > 18 and ImageFont.truetype(font, size).getlength(text) > width:
        size -= 1
    return size


def step_windows(run_dir):
    """(step, start, end, ok, verdict) in seconds of screen.mp4."""
    events = [json.loads(line) for line in (Path(run_dir) / "events.jsonl").open()]
    rec0 = next((e["t"] for e in events if e["kind"] == "record_start"), None)
    if rec0 is None:  # runs recorded before the event existed: recording began just before s1
        rec0 = next(e["t"] for e in events if e["kind"] == "step_start") - 0.3
    windows, start = {}, None
    for e in events:
        if e["kind"] == "step_start":
            start = e["t"]
        elif e["kind"] == "verify":
            passed = [r["check"] for r in e["results"] if not r["error"]]
            windows[e["step"]] = (start - rec0, e["t"] - rec0, e["ok"], passed, e["attempt"])
    return windows


def describe(check):
    """A short human sentence for a check, for the verdict banner."""
    op = check["op"]
    target = check.get("object", "")
    if op == "prop":
        want = next((f"{k} {check[k]}" for k in ("eq", "approx", "gte", "lte") if k in check), "")
        want = want.replace("gte ", ">= ").replace("lte ", "<= ").replace("eq ", "= ").replace("approx ", "~ ")
        return f"{target + '.' if target else ''}{check['path']} {want}"
    if op == "count":
        return f"{check.get('type', 'object').lower()} count = {check.get('eq')}"
    if op == "distance":
        return f"{target} is {check.get('gte', '?')}+ m from {check['to']}"
    if op == "modifier":
        return f"{target} has {check['type']}"
    return f"{op} {check.get('name', target)}"


def segment(tmp, i, total, step, tut_video, t0, t1, screen, w, label_left, label_right, out):
    a0, a1, ok, passed, attempts = w
    tut_len = max(t1 - t0, 2.5)
    dur = min(max(tut_len, 5.0), 12.0)
    speed = max((a1 - a0) / (dur - 1.5), 1.0)  # leave 1.5 s of hold for the verdict
    verdict = ("VERIFIED  " if ok else "NOT VERIFIED  ") + "  |  ".join(describe(c) for c in passed[:2])
    if attempts > 1:
        verdict += f"   (attempt {attempts})"
    title = f"Step {i}/{total}   {step['instruction']}"
    files = {k: text_file(tmp, f"{i}-{k}.txt", v) for k, v in {
        "title": title,
        "left": label_left, "right": f"{label_right}   x{speed:.0f} speed",
        "verdict": verdict}.items()}
    pane_h = PANE_W * 10 // 16
    flt = (
        f"[0:v]setpts=PTS-STARTPTS,scale={PANE_W}:-2,tpad=stop_mode=clone:stop_duration={dur:.3f},trim=0:{dur:.3f},"
        f"pad={PANE_W}:{pane_h}:0:(oh-ih)/2:color=black[l];"
        f"[1:v]setpts=(PTS-STARTPTS)/{speed:.4f},scale={PANE_W}:{pane_h},"
        f"tpad=stop_mode=clone:stop_duration={dur:.3f},trim=0:{dur:.3f}[r];"
        f"color=c=0x111418:s={W}x{H}:d={dur:.3f}:r={FPS}[bg];"
        f"[bg][l]overlay=x=16:y=170[b1];[b1][r]overlay=x={W - PANE_W - 16}:y=170[b2];"
        f"[b2]drawtext=fontfile={BOLD}:textfile={files['title']}:x=32:y=60:fontsize={fit(title, BOLD, 38, W - 64)}:fontcolor=white,"
        f"drawtext=fontfile={FONT}:textfile={files['left']}:x=16:y=130:fontsize=24:fontcolor=0x8b95a5,"
        f"drawtext=fontfile={FONT}:textfile={files['right']}:x={W - PANE_W - 16}:y=130:fontsize=24:fontcolor=0x8b95a5,"
        f"drawtext=fontfile={BOLD}:textfile={files['verdict']}:x=32:y={H - 140}:fontsize={fit(verdict, BOLD, 34, W - 64)}:"
        f"fontcolor={'0x3fb950' if ok else '0xf85149'}:enable='gte(t,{dur - 1.5:.3f})',"
        f"fps={FPS},format=yuv420p[v]")
    # Seek on the inputs (fast) rather than with trim filters, which decode from the start.
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y",
                    "-ss", f"{t0:.3f}", "-t", f"{min(tut_len, dur):.3f}", "-i", str(tut_video),
                    "-ss", f"{a0:.3f}", "-t", f"{a1 - a0:.3f}", "-i", str(screen),
                    "-filter_complex", flt, "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "20", "-an", str(out)], check=True)


def build(run_dir, tutorial_dir, video, left="Tutorial video", right="Apprentice, live"):
    run_dir = Path(run_dir)
    tut = json.loads((Path(tutorial_dir) / "tutorial.json").read_text())
    windows = step_windows(run_dir)
    steps = [s for s in tut["steps"] if s["id"] in windows]
    out = run_dir / "side_by_side.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        prev_t = 0.0
        for i, step in enumerate(steps, 1):
            t1 = float(step["t"]) + 1.0
            t0 = max(prev_t, t1 - 10.0)
            part = Path(tmp) / f"part{i:02d}.mp4"
            segment(tmp, i, len(steps), step, video, t0, t1, run_dir / "screen.mp4",
                    windows[step["id"]], left, right, part)
            parts.append(part)
            prev_t = t1
        listing = Path(tmp) / "parts.txt"
        listing.write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(listing), "-c", "copy", str(out)], check=True)
    return out
