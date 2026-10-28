# L'Apprenti

An apprentice that learns software the way people do: it watches a tutorial video, then redoes
it in the real application, click by click, and every step is checked against the app's real
state instead of the model's word.

Built for the Meta Global AI Developer Hackathon 2026 (October 26 to November 4).

- **Muse Spark** watches the tutorial video (frames and narration) and writes the steps, each
  with machine-checkable expectations.
- **Muse Wonder** sees only screenshots and drives Blender through the GUI with the computer
  tool: mouse, keyboard, menus.
- **A verifier** reads the scene through Blender's Python API after each step. When a step is
  wrong, the findings go back to the apprentice as a teacher's remark and it tries again.

Status: proof of concept. The harness, verifier, budget guard and an offline scripted run work;
the first runs against the hosted models are next.

## How it works

```
tutorial video ──Muse Spark──▶ steps + checks + reference frames
                                   │
             ┌─────────────────────▼──────────────────────┐
             │ for each step                              │
             │   checkpoint the .blend                    │
             │   screenshot ─▶ Muse Wonder ─▶ actions ─┐  │
             │        ▲                                │  │
             │        └──── XTEST input on Xvfb ◀──────┘  │
             │   probe reads the scene ─▶ verify checks   │
             │   fail ─▶ teacher's remark, retry          │
             └────────────────────────────────────────────┘
```

| Module | Role |
| --- | --- |
| `apprentice/desktop.py` | private X display (Xvfb), screenshots, XTEST input for the computer tool's action set |
| `apprentice/blender.py` | launches Blender in a bubblewrap sandbox with private prefs; checkpoint and rewind |
| `apprentice/blender_probe.py` | runs inside Blender: read-only scene state as JSON over a local socket |
| `apprentice/verify.py` | the check language and its evaluation against the scene |
| `apprentice/agent.py` | the observe-act loop per step, verification, retries |
| `apprentice/meta.py` | Responses API client, retries, scripted offline backend |
| `apprentice/budget.py` | prices every call before sending it and refuses anything over budget |
| `apprentice/tutorial.py` | video to steps with Muse Spark, reference frames with ffmpeg |
| `apprentice/report.py` | one HTML page per run: every screenshot, action and verdict |

The agent never touches the probe and is told to stay in the GUI; the probe never acts. Blender
runs with a read-only system, an empty home directory and only its run directory writable.

## Run it

Needs Linux, `Xvfb`, `ffmpeg`, `bwrap` and Blender 5.2.

```sh
python3 -m venv .venv && .venv/bin/pip install httpx python-xlib pillow
.venv/bin/python -m unittest discover -s tests -t .

# offline: replays a scripted trajectory, including one wrong step that the verifier catches
.venv/bin/python -m apprentice run tutorials/pebble --mock --record

# hosted models: paid calls are refused unless a budget is set
export MODEL_API_KEY=...
APPRENTICE_BUDGET_USD=20 .venv/bin/python -m apprentice ingest tutorial.mp4 tutorials/mine
APPRENTICE_BUDGET_USD=20 .venv/bin/python -m apprentice run tutorials/mine --record
.venv/bin/python -m apprentice ledger
```

Each run writes `runs/<time>-<tutorial>/`: `events.jsonl`, raw API responses, every screenshot
the model saw, checkpoints, `screen.mp4` and `report.html`.
