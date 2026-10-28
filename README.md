# Footfall

Usability testing with a crowd that does not exist. Give Footfall a website and a task; a crowd
of synthetic people, each with real constraints on how they see and move, tries to do it in
real browsers, thinking aloud. The shop's own database decides who succeeded, and the crowd's
journals become a ranked list of friction points, each backed by quotes and screenshots.

Built for the Meta Global AI Developer Hackathon 2026 (October 26 to November 4).

- **The crowd fits the site.** Muse Spark looks at the site as a first visitor would, reads
  the owner's words about the audience if any, and recruits people who would really come:
  ages, jobs, languages, the device they would use, and impairments at the rate they occur in
  that audience. A developer tool gets developers; a cheap clothing shop gets a young mum on
  her phone and, sometimes, a grandmother buying a gift. The crowd is a JSON file to review.
- **Every persona is Muse Spark** driving a real Chrome through the computer tool: screenshots
  in, mouse and keyboard out. It never sees the DOM and is told not to type addresses. Phones
  are emulated through DevTools (viewport, touch, mobile user agent).
- **Its senses are simulated.** Each screenshot is degraded to match the persona's eyesight
  before the model sees it: blur and lost contrast for macular degeneration, a hazy yellow
  glare for cataracts, the Machado (2009) colour-vision matrices for red-green blindness. Its
  hands are too: a tremor moves every click by a Gaussian offset before it reaches the screen.
- **Muse Wonder is its eye.** Before each click lands, Wonder looks at a close-up of the spot,
  through the persona's eyes, and corrects the aim, as people do while they move a pointer.
  Without it, Spark aimed a row too high on phone screens and the crowd reported harness
  misses as site bugs.
- **Success is not the model's word.** After each session, a read-only SQL query asks the
  store whether an order with the right item exists for that persona's email.
- **Muse Spark reads every journal** and groups the struggles into friction points. A friction
  is kept only if its quotes appear verbatim in the cited turn.

Status: working on a self-hosted [Medusa](https://github.com/medusajs/dtc-starter) storefront
(Next.js, MIT), told it is a cheap European basics shop. The recruited crowd of eight (five on
phones, one on a tablet, two on laptops; one colour-blind man, one 68-year-old with cataracts
and a slight tremor) was asked to buy one black T-shirt in size M: eight orders verified in the
database, 22 to 41 turns each, $3.87 of API calls.

What the crowd found, all of it real and none of it planted:

| Friction | Who | Fix the crowd suggested |
| --- | --- | --- |
| "Add to cart" gives no feedback but a tiny counter: Agneta, through cataracts, tapped 13 times in one run | Agneta, Camille | instant confirmation, mini-cart, no double submit |
| Country left empty below the address fields | 7 of 8 | preselect the shop's country |
| Size preselected to XL; on phones, choosing it takes open, pick, close | 7 of 8 | no default size, inline size buttons |
| Checkout and Place order below the fold | 7 of 8 | sticky action buttons |
| Delivery cost hidden in a collapsed panel at the foot of the page; product details all "-" | Marco, the sceptic | delivery cost and time next to the button |
| Home page shows no products, "Menu" is small faint text (earlier crowd) | Margaret, 78 | products or a large Store entry on the home page |

## How it works

```
site + task ──Muse Spark──▶ crowd fitted to the site's audience (eyes, hands, device, habits)
                    │
   ┌─────────── one private desktop per persona (Xvfb + Chrome) ───────────┐
   │  screenshot ─▶ eyes(blur, contrast, tint, colour) ─▶ Muse Spark       │
   │       ▲                                                   │ click     │
   │       │                    close-up through their eyes ─▶ Muse Wonder │
   │       │                                                   │ aim       │
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
$0.004). The journal also carries what a person would notice without reading the screen:
that an action changed nothing, that only a small spot flickered (a counter in a corner), or
that this is the fourth tap on the same place.

| Module | Role |
| --- | --- |
| `footfall/session.py` | one persona's observe-think-act loop, funnel tracking, ground truth |
| `footfall/senses.py` | eyesight filters on screenshots, tremor on clicks |
| `footfall/recruit.py` | recruits a crowd from the site's real audience, at realistic rates |
| `footfall/personas.json` | a hand-made crowd: who they are, how they see, how their hands move |
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
FOOTFALL_BUDGET_USD=5 .venv/bin/python -m footfall recruit scenarios/medusa-tshirt.json \
    --count 8 --audience "a small European brand selling cheap everyday basics"
# read and edit crowds/medusa-tshirt.json, then
FOOTFALL_BUDGET_USD=5 .venv/bin/python -m footfall crowd scenarios/medusa-tshirt.json \
    --crowd crowds/medusa-tshirt.json --parallel 4
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
