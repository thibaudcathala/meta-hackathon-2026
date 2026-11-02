"""The wall: every persona's session side by side, each screen shown the way that persona saw
it, sped up to the same pace, and closed by the store's verdict on the tile."""

import json
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


def aspect(video):
    """Width over height of a recording."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True, check=True).stdout
    w, h = map(int, out.strip().split(",")[:2])
    return w / h


def split(aspects, count):
    """Cut a run of tiles into `count` rows of about the same total width."""
    rows, row, target = [], [], sum(aspects) / count
    for i, a in enumerate(aspects):
        left = len(aspects) - i
        if row and (sum(row) + a / 2 > target or left < count - len(rows)) and len(rows) < count - 1:
            rows.append(row)
            row = []
        row.append(a)
    return rows + [row]


def layout(aspects, label=48):
    """Justified rows: phones, tablets and laptops each keep their shape, at one height per
    row, using as much of the frame as possible. Returns (x, y, width, video height) per tile."""
    best = None
    for count in range(1, len(aspects) + 1):
        rows = split(aspects, count)
        height = min(min(W / sum(r) for r in rows), H / count - label)
        if best is None or height * height * sum(aspects) > best[0]:
            best = height * height * sum(aspects), rows, int(height) // 2 * 2
    _, rows, vh = best
    boxes, y = [], (H - len(rows) * (vh + label)) // 2
    for row in rows:
        widths = [int(a * vh) // 2 * 2 for a in row]
        x = (W - sum(widths)) // 2
        for w in widths:
            boxes.append((x, y, w, vh))
            x += w
        y += vh + label
    return boxes


def build(crowd_dir, out=None, seconds=45, speed=None):
    """`speed` fixes the pace, so two runs of the same crowd play side by side at the same clock."""
    crowd_dir = Path(crowd_dir)
    summary = json.loads((crowd_dir / "crowd.json").read_text())
    personas = {p["id"]: p for p in summary.get("personas") or json.loads(
        Path(__file__).with_name("personas.json").read_text())}
    tiles = [r for r in summary["results"] if (crowd_dir / r["persona"] / "screen.mp4").exists()]
    shapes = {r["persona"]: aspect(crowd_dir / r["persona"] / "screen.mp4") for r in tiles}
    tiles.sort(key=lambda r: -shapes[r["persona"]])  # laptops, then tablets, then phones
    boxes = layout([shapes[r["persona"]] for r in tiles])
    longest = max(r.get("seconds", 60) for r in tiles)
    speed = speed or max(longest / seconds, 1)
    out = Path(out or crowd_dir / "wall.mp4")
    with tempfile.TemporaryDirectory() as tmp:
        inputs, chains = [], []
        for i, (r, (_, _, tw, vh)) in enumerate(zip(tiles, boxes)):
            th = vh + 48
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
                # The verdict sits at the foot of the tile, under the page's own "Thank you".
                f"drawbox=x=0:y={th - 72}:w={tw}:h=60:color=0x000000@0.8:t=fill:"
                f"enable='gte(t,{end:.2f})',"
                f"drawtext=fontfile={BOLD}:textfile={badge}:x=(w-tw)/2:y={th - 58}:"
                f"fontsize={min(30, tw // 9)}:fontcolor={colour}:enable='gte(t,{end:.2f})'"
                f"[t{i}]")
        # Every tile laid onto one frame-sized background at its place.
        graph = ";".join(chains) + f";color=c=0x111418:s={W}x{H}:d={seconds + 2}[bg0]"
        for i, (x, y, _, _) in enumerate(boxes):
            last = i == len(boxes) - 1
            graph += (f";[bg{i}][t{i}]overlay={x}:{y}" + (",fps=30,format=yuv420p[v]" if last
                                                          else f"[bg{i + 1}]"))
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *inputs, "-filter_complex", graph,
                        "-map", "[v]", "-t", str(seconds + 2), "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "22", str(out)], check=True)
    return out, speed
