# Footfall

Usability testing with a crowd that does not exist. Give Footfall a website and a task; a crowd
of synthetic people, each with real constraints on how they see and move, tries to do it in
real browsers, thinking aloud. The shop's own database decides who succeeded, and the crowd's
journals become a ranked list of friction points, each backed by quotes and screenshots.

Built for the Meta Global AI Developer Hackathon 2026 (October 26 to November 4).

- **Every persona is Muse Spark** driving a real Chrome through the computer tool: screenshots
  in, mouse and keyboard out. It never sees the DOM and is told not to type addresses.
- **Its senses are simulated.** Each screenshot is degraded to match the persona's eyesight
  before the model sees it: blur and lost contrast for macular degeneration, a hazy yellow
  glare for cataracts, the Machado (2009) colour-vision matrices for red-green blindness. Its
  hands are too: a tremor moves every click by a Gaussian offset before it reaches the screen.
- **Success is not the model's word.** After each session, a read-only SQL query asks the
  store whether an order with the right item exists for that persona's email.
- **Muse Spark reads every journal** and groups the struggles into friction points. A friction
  is kept only if its quotes appear verbatim in the cited turn.

Status: working on a self-hosted [Medusa](https://github.com/medusajs/dtc-starter) storefront
(Next.js, MIT). Six personas asked to buy one black T-shirt in size M, three at a time: five
orders verified in the database, for $0.68 of API calls. Margaret, 78, with macular
degeneration, spent 42 turns on a home page that shows no products before finding the menu,
then kept clicking just below an "Add to cart" button she could barely see, and ran out of
turns. In a second run she found the menu at once and finished, after 15 turns fighting the
country list.

What the crowd found, all of it real and none of it planted:

| Friction | Who | Fix the crowd suggested |
| --- | --- | --- |
| Home page has no products, only a hero; "Menu" is small faint text | Margaret | product grid or a large Store entry on the home page |
| No clear feedback after "Add to cart": two shirts by mistake, repeated clicks | Lucía, Agnes, Margaret | persistent high-contrast confirmation, visible cart count |
| Country select easy to miss and mis-pick | Agnes, Karim, Lucía, Jonas | preselect the shop's country, validate inline |
| Size preselected to another value | Karim, Jonas, Lucía, Tom, Agnes | no default size, require a choice |

## How it works

```
scenario (url, task, success query, funnel)      personas (eyes, hands, habits, details)
                    │                                        │
                    ▼                                        ▼
   ┌─────────── one private desktop per persona (Xvfb + Chrome) ───────────┐
   │  screenshot ─▶ eyes(blur, contrast, tint, colour) ─▶ Muse Spark       │
   │       ▲                                                   │ actions   │
   │       └─────── XTEST input ◀── hands(tremor) ◀────────────┘           │
   │  URL from DevTools ─▶ funnel stage        journal of thoughts ─▶ log  │
   └───────────────────────────────────────────────────────────────────────┘
                    │                                        │
          SQL on the store: order?                 Muse Spark: frictions + quotes
                    ▼                                        ▼
            verified outcome                insights.json, wall.mp4 (every screen)
```

Each turn is stateless: the model gets the persona, the goal, the last 15 journal entries and
one degraded screenshot, so a long session costs the same per turn as a short one (about
$0.004). When an action changes nothing on screen, the journal says so, as a person would
notice.

| Module | Role |
| --- | --- |
| `footfall/session.py` | one persona's observe-think-act loop, funnel tracking, ground truth |
| `footfall/senses.py` | eyesight filters on screenshots, tremor on clicks |
| `footfall/personas.json` | the crowd: who they are, how they see, how their hands move |
| `footfall/browser.py` | a throwaway Chrome profile per persona, with Chrome's own bubbles off |
| `footfall/desktop.py` | private X display (Xvfb), screenshots, XTEST input |
| `footfall/store.py` | read-only SQL against the store to check an outcome |
| `footfall/insights.py` | friction points from the journals, quotes checked against the log |
| `footfall/wall.py` | one video of every screen, each as its persona saw it, with the verdict |
| `footfall/meta.py`, `computer.py` | Responses API client and the computer tool's action set |
| `footfall/budget.py` | prices every call before sending it and refuses anything over budget |

## Run it

Needs Linux, `Xvfb`, `ffmpeg`, Google Chrome and `psql`, plus a site to test with a database
you can read. The scenario in `scenarios/` targets Medusa's `dtc-starter` running locally
(storefront on :8000, Postgres on :5442; `FOOTFALL_STORE_DB` points elsewhere).

```sh
python3 -m venv .venv && .venv/bin/pip install httpx python-xlib pillow websocket-client
.venv/bin/python -m unittest discover -s tests -t .

export MODEL_API_KEY=...
# paid calls are refused unless a budget is set
FOOTFALL_BUDGET_USD=5 .venv/bin/python -m footfall crowd scenarios/medusa-tshirt.json --parallel 3
FOOTFALL_BUDGET_USD=5 .venv/bin/python -m footfall insights runs/<time>-crowd
.venv/bin/python -m footfall wall runs/<time>-crowd
.venv/bin/python -m footfall ledger
```

Each crowd writes `runs/<time>-crowd/`: per persona `events.jsonl`, raw API responses, every
screenshot both raw and as seen, `screen.mp4` and `result.json`; then `crowd.json`,
`insights.json` and `wall.mp4`.

## Earlier experiment: Apprentice

The hackathon started with `apprentice/`: Muse Spark watched a Blender tutorial video and wrote
checked steps, then Muse Spark or Muse Wonder redid them in Blender through the GUI, with a
verifier reading the scene after each step. Both finished Blender Studio's "Creating Meshes"
lesson, 6/6 steps verified. The desktop, budget and API code Footfall runs on came from it.

```sh
.venv/bin/python -m apprentice run tutorials/pebble --mock --record   # offline replay
```
