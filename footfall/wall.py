"""The wall: every persona's session side by side, each screen shown the way that persona saw
it, sped up to the same pace, and closed by the store's verdict on the tile."""

import json
import math
import subprocess
import tempfile
from pathlib import Path

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H = 1920, 1080


def eyes_filter(eyes):
    """ffmpeg approximation of senses.see(), for display only (the model got the exact one)."""
    parts = []
    if eyes.get("colour"):
        from .senses import CVD  # sRGB approximation of the linear-light matrix
        m = CVD[eyes["colour"]]
        parts.append("colorchannelmixer=" + ":".join(
            f"{a}{b}={v:.4f}" for (a, b), v in zip(
                [(r, c) for r in "rgb" for c in "rgb"], m)))
    tint = eyes.get("tint") or (1, 1, 1)
    gain = eyes.get("brightness", 1)
    if tint != (1, 1, 1) or gain != 1:
        r, g, b = (t * gain for t in tint)
        parts.append(f"colorchannelmixer=rr={r:.3f}:gg={g:.3f}:bb={b:.3f}")
    if eyes.get("contrast", 1) != 1:
        parts.append(f"eq=contrast={eyes['contrast']}")
    if eyes.get("blur"):
        # The tile is half size, so half the blur gives the same look.
        parts.append(f"gblur=sigma={eyes['blur'] / 2:.2f}")
    return parts


def text_file(tmp, name, text):
    path = Path(tmp) / name
    path.write_text(text)
    return str(path).replace(":", r"\:")


def verdict(result):
    if result.get("verified"):
        return "ORDER PLACED", "0x3fb950"
    outcome = result.get("outcome", "")
    if outcome == "gave up":
        return "GAVE UP", "0xf85149"
    if outcome == "said done":
        return "SAID DONE, NO ORDER", "0xf0883e"
    return outcome.upper() or "NO ORDER", "0xf85149"


def build(crowd_dir, out=None, seconds=45):
    crowd_dir = Path(crowd_dir)
    summary = json.loads((crowd_dir / "crowd.json").read_text())
    personas = {p["id"]: p for p in summary.get("personas") or json.loads(
        Path(__file__).with_name("personas.json").read_text())}
    tiles = [r for r in summary["results"] if (crowd_dir / r["persona"] / "screen.mp4").exists()]
    cols = math.ceil(math.sqrt(len(tiles) * 16 / 9 / 1.6))
    rows = math.ceil(len(tiles) / cols)
    tw = W // cols
    th = min(H // rows, tw * 10 // 16 + 48)
    vh = th - 48
    longest = max(r.get("seconds", 60) for r in tiles)
    speed = max(longest / seconds, 1)
    out = Path(out or crowd_dir / "wall.mp4")
    with tempfile.TemporaryDirectory() as tmp:
        inputs, chains, stack = [], [], []
        for i, r in enumerate(tiles):
            p = personas.get(r["persona"], {})
            inputs += ["-i", str(crowd_dir / r["persona"] / "screen.mp4")]
            label, colour = verdict(r)
            end = r.get("seconds", longest) / speed
            name = text_file(tmp, f"{i}-name.txt", f"{p.get('name', r['persona'])}, {p.get('age', '')}")
            note = text_file(tmp, f"{i}-note.txt", p.get("tag", ""))
            badge = text_file(tmp, f"{i}-badge.txt", label)
            sight = ",".join(eyes_filter(p.get("eyes", {})))
            chains.append(
                f"[{i}:v]setpts=(PTS-STARTPTS)/{speed:.4f},"
                f"scale={tw}:{vh}:force_original_aspect_ratio=decrease:force_divisible_by=2"
                + (f",{sight}" if sight else "")
                + f",pad={tw}:{vh}:(ow-iw)/2:(oh-ih)/2:color=0x111418,tpad=stop_mode=clone:stop_duration={seconds + 3},trim=0:{seconds + 2},"
                f"pad={tw}:{th}:0:48:color=0x111418,"
                f"drawtext=fontfile={BOLD}:textfile={name}:x=12:y=10:fontsize=22:fontcolor=white,"
                f"drawtext=fontfile={FONT}:textfile={note}:x=12:y=34:fontsize=12:fontcolor=0x8b95a5,"
                f"drawbox=x=0:y={th // 2 - 30}:w={tw}:h=60:color=0x000000@0.75:t=fill:"
                f"enable='gte(t,{end:.2f})',"
                f"drawtext=fontfile={BOLD}:textfile={badge}:x=(w-tw)/2:y={th // 2 - 16}:"
                f"fontsize=30:fontcolor={colour}:enable='gte(t,{end:.2f})'"
                f"[t{i}]")
            stack.append(f"[t{i}]")
        layout = "|".join(f"{(i % cols) * tw}_{(i // cols) * th}" for i in range(len(tiles)))
        graph = ";".join(chains) + ";" + "".join(stack) + (
            f"xstack=inputs={len(tiles)}:layout={layout}:fill=0x111418,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x111418,fps=30,format=yuv420p[v]"
            if len(tiles) > 1 else f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x111418,fps=30,format=yuv420p[v]")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *inputs, "-filter_complex", graph,
                        "-map", "[v]", "-t", str(seconds + 2), "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "22", str(out)], check=True)
    return out, speed
