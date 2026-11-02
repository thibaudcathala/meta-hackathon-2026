"""One HTML page for the site owner: who came, how far they got, what stopped them, and, when
the same crowd ran again on a fixed site, what the fixes saved. Each friction shows the screen
as that person saw it at the turn they said it, next to their words.

The page is self-contained (screenshots inlined) so it can be mailed or attached to a ticket.
"""

import base64
import html
import io
import json
from pathlib import Path

from PIL import Image

DEVICE = {"phone": "Phone", "tablet": "Tablet", "laptop": "Laptop"}
SEVERITY = {"blocker": 0, "major": 1, "minor": 2}


def shot_of(crowd_dir, persona, turn):
    """The screenshot a persona was looking at when they wrote turn `turn`'s thought."""
    events = Path(crowd_dir) / persona / "events.jsonl"
    if not events.exists():
        return None
    for line in events.read_text().splitlines():
        e = json.loads(line)
        if e.get("kind") == "turn" and e.get("turn") == turn and e.get("shot"):
            return Path(crowd_dir) / persona / e["shot"]
    return None


def inline(path, width=480):
    img = Image.open(path).convert("RGB")
    img.thumbnail((width, width * 3))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=78)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def senses(p):
    out = []
    if p.get("eyes_note"):
        out.append(p["eyes_note"])
    if (p.get("hands") or {}).get("tremor"):
        out.append(f"tremor {p['hands']['tremor']:g} px")
    return "; ".join(out)


def esc(text):
    return html.escape(str(text or ""))


def people(crowd):
    by_id = {p["id"]: p for p in crowd.get("personas") or []}
    cards = []
    for r in crowd["results"]:
        p = by_id.get(r["persona"], {})
        ok = r.get("verified")
        cards.append(f"""
      <li class="person">
        <div class="who"><b>{esc(p.get('name', r['persona']))}</b>, {esc(p.get('age', ''))}
          <span class="device">{esc(DEVICE.get(p.get('device'), ''))}</span></div>
        <div class="tag">{esc(p.get('tag', ''))}</div>
        {f'<div class="senses">{esc(senses(p))}</div>' if senses(p) else ''}
        <div class="outcome {'ok' if ok else 'no'}">{'Order in the store' if ok else esc(r.get('outcome', 'no order'))}
          <span>{r.get('turns', '-')} turns, {r.get('seconds', 0) / 60:.1f} min</span></div>
      </li>""")
    return "".join(cards)


def funnel(found, total):
    rows = []
    for stage in found.get("funnel", []):
        n = len(stage["reached"])
        rows.append(f"""
      <div class="stage"><span class="label">{esc(stage['stage'])}</span>
        <span class="bar"><i style="width:{100 * n / max(total, 1):.0f}%"></i></span>
        <span class="count">{n}/{total}</span></div>""")
    return "".join(rows)


def frictions(found, crowd_dir, names):
    cards = []
    ordered = sorted(found.get("frictions", []), key=lambda f: (SEVERITY.get(f.get("severity"), 3),
                                                                 -len(f.get("who", []))))
    for f in ordered:
        quotes = []
        for ev in f.get("evidence", [])[:2]:
            shot = shot_of(crowd_dir, ev["persona"], ev["turn"])
            img = f'<img src="{inline(shot)}" alt="">' if shot and shot.exists() else ""
            quotes.append(f"""
          <figure>{img}
            <figcaption>“{esc(ev['quote'])}”<br><span>{esc(names.get(ev['persona'], ev['persona']))},
              turn {ev['turn']}</span></figcaption></figure>""")
        who = ", ".join(names.get(w, w) for w in f.get("who", []))
        cards.append(f"""
      <article class="friction">
        <header><span class="sev {esc(f.get('severity'))}">{esc(f.get('severity'))}</span>
          <h3>{esc(f['title'])}</h3><p class="where">{esc(f.get('where'))} · {esc(who)}</p></header>
        <div class="quotes">{''.join(quotes)}</div>
        {f'<p><b>On the screens.</b> {esc(seen)}</p>' if (seen := (f.get('witness') or {}).get('seen')) else ''}
        <p><b>Why.</b> {esc(f.get('why'))}</p>
        <p><b>Fix.</b> {esc(f.get('fix'))}</p>
      </article>""")
    return "".join(cards)


def set_aside(found, names):
    items = "".join(
        f"<li><b>{esc(f['title'])}</b> ({esc(', '.join(names.get(w, w) for w in f.get('who', [])))}): "
        f"{esc(f['witness'].get('seen'))}</li>" for f in found.get("harness", []))
    if not items:
        return ""
    return f"""
    <section><h2>Set aside: the harness, not the site</h2>
      <p class="lede">The journals reported these, but the screens show a click or keystroke
        of the test harness landing where the person did not mean it.</p>
      <ul class="aside">{items}</ul></section>"""


def before_after(found):
    from .compare import LABELS
    tb, ta = found["totals"]["before"], found["totals"]["after"]
    n = len(found["people"])
    stages = "".join(
        f"<tr><td>{esc(LABELS.get(s, s))}</td><td>{tb['stages'][s]}</td><td>{ta['stages'][s]}</td></tr>"
        for s in found["stages"][:-1] if tb["stages"][s] or ta["stages"][s])
    rows = "".join(
        f"<tr><td>{esc(r['name'])}</td><td>{r['before']['turns']}</td><td>{r['after']['turns']}</td>"
        f"<td>{r['before']['seconds'] / 60:.1f}</td><td>{r['after']['seconds'] / 60:.1f}</td></tr>"
        for r in found["people"])
    return f"""
    <section>
      <h2>After the fixes</h2>
      <p class="lede">The same {n} people, the same task, on the fixed site.</p>
      <div class="kpis">
        <div><b>{tb['verified']}/{n} → {ta['verified']}/{n}</b><span>orders in the store</span></div>
        <div><b>{tb['turns']} → {ta['turns']}</b><span>turns, whole crowd</span></div>
        <div><b>{tb['seconds'] / 60:.0f} → {ta['seconds'] / 60:.0f} min</b><span>time, whole crowd</span></div>
      </div>
      <div class="tables">
        <table><thead><tr><th>Where the turns went</th><th>Before</th><th>After</th></tr></thead>
          <tbody>{stages}</tbody></table>
        <table><thead><tr><th>Person</th><th>Turns</th><th>After</th><th>Min</th><th>After</th></tr></thead>
          <tbody>{rows}</tbody></table>
      </div>
    </section>"""


def build(crowd_dir, out=None):
    crowd_dir = Path(crowd_dir)
    crowd = json.loads((crowd_dir / "crowd.json").read_text())
    found = json.loads((crowd_dir / "insights.json").read_text())
    names = {p["id"]: p["name"] for p in crowd.get("personas") or []}
    total = len(crowd["results"])
    done = sum(bool(r.get("verified")) for r in crowd["results"])
    compare = crowd_dir / "compare.json"
    after = before_after(json.loads(compare.read_text())) if compare.exists() else ""
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Footfall report</title>
<style>
:root {{ --bg:#f7f7f4; --card:#fff; --ink:#1b1d1f; --soft:#5f6670; --line:#e3e3de;
  --ok:#1f7a3d; --bad:#b4372b; --warn:#a6620c; --accent:#2b5cd6; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141619; --card:#1c1f23; --ink:#ececea;
  --soft:#9aa1aa; --line:#2c3036; --ok:#5cc47f; --bad:#f07a6e; --warn:#e3a24a; --accent:#7ea2ff; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 system-ui, sans-serif; }}
main {{ max-width:1080px; margin:0 auto; padding:32px 16px 64px; }}
h1 {{ font-size:28px; margin:0 0 4px; }} h2 {{ font-size:20px; margin:40px 0 8px; }}
h3 {{ font-size:17px; margin:4px 0; }}
.lede, .where, .tag, .senses, figcaption span, .kpis span, .aside {{ color:var(--soft); }}
.aside li {{ margin:6px 0; }}
.summary {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; }}
ul.people {{ list-style:none; padding:0; display:grid; gap:12px;
  grid-template-columns:repeat(auto-fill, minmax(230px, 1fr)); }}
.person, .friction, table {{ background:var(--card); border:1px solid var(--line); border-radius:10px; }}
.person {{ padding:12px 14px; }} .device {{ float:right; font-size:12px; color:var(--soft); }}
.tag, .senses {{ font-size:13px; }} .senses {{ font-style:italic; }}
.outcome {{ margin-top:8px; font-weight:600; font-size:13px; }}
.outcome span {{ display:block; font-weight:400; color:var(--soft); }}
.ok {{ color:var(--ok); }} .no {{ color:var(--bad); }}
.stage {{ display:grid; grid-template-columns:140px 1fr 48px; gap:12px; align-items:center; margin:6px 0; }}
.bar {{ height:12px; background:var(--line); border-radius:6px; overflow:hidden; }}
.bar i {{ display:block; height:100%; background:var(--accent); }}
.count {{ text-align:right; font-variant-numeric:tabular-nums; }}
.friction {{ padding:16px; margin:16px 0; }}
.sev {{ font-size:12px; text-transform:uppercase; letter-spacing:.05em; font-weight:700; }}
.sev.blocker {{ color:var(--bad); }} .sev.major {{ color:var(--warn); }} .sev.minor {{ color:var(--soft); }}
.quotes {{ display:flex; flex-wrap:wrap; gap:16px; margin:12px 0; }}
figure {{ margin:0; flex:1 1 300px; max-width:480px; display:flex; gap:12px; align-items:flex-start; }}
figure img {{ max-width:240px; max-height:320px; border-radius:6px; border:1px solid var(--line); }}
figcaption {{ font-size:14px; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; }}
.kpis div {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; }}
.kpis b {{ display:block; font-size:24px; font-variant-numeric:tabular-nums; }}
.tables {{ display:grid; align-items:start; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:12px; margin-top:12px; }}
table {{ border-collapse:separate; border-spacing:0; width:100%; font-variant-numeric:tabular-nums; }}
th, td {{ padding:8px 12px; text-align:right; border-bottom:1px solid var(--line); }}
th:first-child, td:first-child {{ text-align:left; }} tr:last-child td {{ border-bottom:0; }}
</style></head>
<body><main>
  <h1>{esc(crowd['scenario'])}</h1>
  <p class="lede">{esc(crowd['url'])} · {total} people · {done} orders found in the store's database
    · ${crowd.get('cost_usd') or 0:.2f} of model calls</p>
  <p class="summary">{esc(found.get('summary'))}</p>
  {after}
  <section><h2>Who came</h2><ul class="people">{people(crowd)}</ul></section>
  <section><h2>How far they got</h2>{funnel(found, total)}</section>
  <section><h2>What got in their way</h2>
    <p class="lede">Each friction is kept only if its quotes are in the journal at that turn,
      and a second look at the screens around them confirms the site caused it. The screen
      shown is the one that person was looking at, as they saw it.</p>
    {frictions(found, crowd_dir, names)}</section>
  {set_aside(found, names)}
</main></body></html>
"""
    out = Path(out or crowd_dir / "report.html")
    out.write_text(page)
    return out
