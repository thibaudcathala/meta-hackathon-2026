"""Tutorials: a video turned into verifiable steps by Muse Spark, plus reference frames.

On disk a tutorial is a directory:
  tutorial.json   {"title", "app", "goal", "source": {...}, "steps": [{"id", "t", "instruction", "expect"}]}
  frames/<id>.png the video frame at time t, shown to the agent as "this is what it should look like"
  mock.json       optional scripted trajectory for offline runs (see meta.ScriptedBackend)
"""

import json
import subprocess
from pathlib import Path

from . import verify
from .meta import output_text

# Generous estimate for the pre-call budget check; Spark samples frames plus the audio track.
VIDEO_TOKENS_PER_SECOND = 400

INGEST_PROMPT = """You are watching a Blender tutorial video. Turn it into a list of small steps
that someone could redo in Blender {version} starting from the default scene (cube, light,
camera), using only the GUI.

For each step give:
- id: "s1", "s2", ...
- t: the time in seconds where the *result* of the step is clearly visible on screen.
- instruction: one imperative sentence with the exact menu path, shortcut and values that the
  video shows or says (e.g. "Add a Bevel modifier and set Amount to 0.1 m, Segments to 3").
- expect: checks that a program reading the scene through Blender's Python API can use to prove
  the step was done. Use only these forms:
{dsl}
Only write checks for things the video makes certain; when a direction or an exact value is
not stated, check something that holds whichever way it is done (size, distance, gte). Prefer "@active" or "@new" for objects
whose final name you cannot see. Lengths are metres, angles radians, colours linear RGB 0..1.

Skip intros, talk and camera moves that do not change the scene.

Reply with JSON only, no prose and no code fence, shaped like:
{{"title": "...", "goal": "one sentence",
  "steps": [{{"id": "s1", "t": 8.0, "instruction": "...",
             "expect": [{{"op": "exists", "type": "MESH", "name": "Sphere*"}}]}}]}}"""


def load(path):
    path = Path(path)
    tut = json.loads((path / "tutorial.json").read_text())
    for step in tut["steps"]:
        frame = path / "frames" / f"{step['id']}.png"
        step["frame"] = str(frame) if frame.exists() else None
    mock = path / "mock.json"
    tut["mock"] = json.loads(mock.read_text()) if mock.exists() else None
    return tut


def duration(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(video)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def extract_frames(video, steps, out_dir):
    frames = Path(out_dir) / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    for step in steps:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(step["t"]), "-i",
                        str(video), "-frames:v", "1", str(frames / f"{step['id']}.png")],
                       check=True)


def ingest(client, http, video, out_dir, model="muse-spark-1.3", version="5.2"):
    """Video -> tutorial directory. Costs one Spark call; refused by the guard if over budget.

    The answer is plain JSON: with a json_schema response format Spark 1.3 fell into a loop
    (the same step repeated, keys such as "eq ").
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    seconds = duration(video)
    uploaded = http.upload(video)
    dsl = "\n\n".join(verify.__doc__.split("\n\n")[2:4])
    response = client.create({
        "model": model,
        "max_output_tokens": 16000,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": INGEST_PROMPT.format(version=version, dsl=dsl)},
            {"type": "input_file", "file_id": uploaded["id"]},
        ]}],
    }, tag="ingest", extra_input_tokens=int(seconds * VIDEO_TOKENS_PER_SECOND))
    text = output_text(response)
    (out / "raw.txt").write_text(text)
    tut = json.loads(text[text.find("{"):text.rfind("}") + 1])
    tut.update(app="blender", source={"video": str(video), "file_id": uploaded["id"],
                                      "duration": seconds, "model": model})
    (out / "tutorial.json").write_text(json.dumps(tut, indent=2) + "\n")
    extract_frames(video, tut["steps"], out)
    return tut
