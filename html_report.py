"""
Rendert die main.py-Ergebnisse (Liste von Liga-Reports) als eine
selbstständige index.html - keine externen Assets, läuft offline,
funktioniert als "Zum Home-Bildschirm hinzufügen"-Mini-App auf dem Handy.

Optionale Passwort-Sperre (siehe config.PAGE_PASSWORD): rein client-seitig,
SHA-256-Hash liegt im Quelltext - KEIN echter Schutz, verhindert nur
zufälliges Finden der (ohnehin öffentlichen) GitHub-Pages-URL.
"""

import html as _html

from scoring import explain

VERDICT_COLORS = {
    "VERKAUFEN": "#dc3545",
    "BEOBACHTEN": "#e0a72e",
    "STAMM": "#2e9e50",
    "HALTEN (Trading)": "#1f6fd6",
}
VERDICT_ICONS = {
    "VERKAUFEN": "🔻",
    "BEOBACHTEN": "👀",
    "STAMM": "⭐",
    "HALTEN (Trading)": "📈",
}
SQUAD_ORDER = {"VERKAUFEN": 0, "BEOBACHTEN": 1, "STAMM": 2, "HALTEN (Trading)": 3}


def _esc(s):
    return _html.escape(str(s))


def _squad_card(c):
    color = VERDICT_COLORS.get(c["verdict"], "#888")
    icon = VERDICT_ICONS.get(c["verdict"], "•")
    opponents = (f"<div class='meta'>Nächste Gegner: {_esc(', '.join(c['opponents']))}</div>"
                 if c.get("opponents") else "")
    reasons = "".join(f"<li>{_esc(r)}</li>" for r in c["reasons"])
    return f"""<div class="card" style="border-left-color:{color}">
  <div class="card-head">
    <span class="badge" style="background:{color}">{icon} {_esc(c['verdict'])}</span>
    <span class="name">{_esc(c['name'])} <span class="pos">({_esc(c['pos'])})</span></span>
  </div>
  <div class="stats">Score {c['score']} · MW {c['mv']:,.0f} € ({c['tfhmvt']:+,.0f} €/Tag)</div>
  <ul class="reasons">{reasons}</ul>
  {opponents}
</div>"""


def _market_card(m, highlight=False):
    parts = (m.get("team_verdict") or "").split(" | ")
    headline = parts[0] if parts else ""
    angles = parts[1:]
    angle_html = "".join(f"<li>{_esc(a)}</li>" for a in angles)
    bid = m.get("bid") or {}
    tick = "✅" if m.get("affordable") else "❌"
    bid_html = ""
    if bid and "KEIN BEDARF" not in headline:
        extra = ""
        if bid.get("projection_note"):
            extra += f"<div class='meta'>↳ {_esc(bid['projection_note'])}</div>"
        if bid.get("star_ceiling"):
            extra += (f"<div class='meta'>↳ Star-Ausnahme: in Einzelfällen bis "
                      f"{bid['star_ceiling']:,.0f} € belegt (nicht die Regel)</div>")
        bid_html = (f"<div class='bid'>💶 Gebot {bid.get('recommended_bid', 0):,.0f} € "
                    f"(22h-MW ~{bid.get('expected_mv_22h', 0):,.0f}, "
                    f"Puffer {bid.get('buffer_pct', 0)}%, "
                    f"WK ~{bid.get('win_probability', 0):.0%}) {tick} "
                    f"{_esc(m.get('financing', ''))}</div>{extra}")
    opponents = (f"<div class='meta'>Nächste Gegner: {_esc(', '.join(m['opponents']))}</div>"
                 if m.get("opponents") else "")
    fitness = f"<div class='meta warn'>⚠️ {_esc(m['fitness'])}</div>" if m.get("fitness") else ""
    star_badge = (f"<span class='star'>💎 Star {m['star']:.0%}</span>"
                  if m.get("banger") else "")
    cls = "card market-card banger" if highlight else "card market-card"
    comps = explain(m.get("components", {}), m.get("meta")) if m.get("components") else ""
    return f"""<div class="{cls}">
  <div class="card-head">
    <span class="name">{_esc(m['name'])} <span class="pos">({_esc(m['pos'])}, {_esc(m.get('team', ''))})</span></span>
    {star_badge}
  </div>
  <div class="stats">Score {m['score']} · MW {m['mv']:,.0f} € ({m['tfhmvt']:+,.0f} €/Tag) · Ø {m['ap']} P · ⏳ {m.get('expiry_s', 0)/3600:.0f}h</div>
  <div class="components">{_esc(comps)}</div>
  {opponents}
  {fitness}
  <div class="verdict">🎯 {_esc(headline)}</div>
  <ul class="angles">{angle_html}</ul>
  {bid_html}
</div>"""


def _league_section(report):
    name = _esc(report.get("name", "?"))
    if report.get("error"):
        return f"""<section class="league">
  <h2>🏆 {name}</h2>
  <p class="empty">❌ {_esc(report['error'])}</p>
</section>"""

    squad_sorted = sorted(report.get("squad_classified", []),
                          key=lambda x: SQUAD_ORDER.get(x["verdict"], 9))
    squad_html = ("".join(_squad_card(c) for c in squad_sorted)
                 or "<p class='empty'>Kein Kader geladen.</p>")

    bangers_html = ""
    if report.get("bangers"):
        cards = "".join(_market_card(m, highlight=True) for m in report["bangers"][:3])
        bangers_html = f"<h3>💎 Banger-Ziele</h3><div class='grid'>{cards}</div>"

    market_html = ("".join(_market_card(m) for m in report.get("market", []))
                  or "<p class='empty'>Kein Marktangebot.</p>")

    free_slots = report.get("free_slots", 0)
    free_slots_note = f"<p class='note'>🟢 {free_slots} freie Kaderplätze</p>" if free_slots else ""
    fixture_note = ("" if report.get("has_fixtures")
                    else "<p class='note warn'>⚠️ Kein Spielplan verfügbar - Spielplan-Komponente neutral</p>")

    return f"""<section class="league">
  <h2>🏆 {name}</h2>
  <div class="league-meta">Budget {report['budget']:+,.0f} € · Kader {report['squad_slots']}/{report['max_squad']}</div>
  {fixture_note}
  <h3>👥 Kader-Status</h3>
  <div class="grid">{squad_html}</div>
  {bangers_html}
  <h3>🛒 Transfermarkt</h3>
  {free_slots_note}
  <div class="grid">{market_html}</div>
</section>"""


CSS = """
:root {
  --bg: #f4f5f7; --card-bg: #ffffff; --text: #1a1c20; --text-dim: #5b6270;
  --border: #e2e4e9; --accent: #1f6fd6;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #14161a; --card-bg: #1e2126; --text: #eef0f3; --text-dim: #9aa1ad; --border: #2c3038; }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 0 3rem; background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 15px; line-height: 1.45;
}
header.top {
  padding: 1.2rem 1rem 0.8rem; text-align: center;
}
header.top h1 { margin: 0 0 0.2rem; font-size: 1.3rem; }
header.top .ts { color: var(--text-dim); font-size: 0.8rem; }
main { max-width: 900px; margin: 0 auto; padding: 0 0.75rem; }
section.league { margin-top: 1.5rem; }
section.league h2 { font-size: 1.15rem; margin: 0 0 0.2rem; }
section.league h3 { font-size: 0.95rem; margin: 1.1rem 0 0.5rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.03em; }
.league-meta { color: var(--text-dim); font-size: 0.85rem; margin-bottom: 0.4rem; }
.note { font-size: 0.85rem; margin: 0.3rem 0; }
.note.warn { color: #c17a00; }
.grid { display: grid; grid-template-columns: 1fr; gap: 0.6rem; }
@media (min-width: 640px) { .grid { grid-template-columns: 1fr 1fr; } }
.card {
  background: var(--card-bg); border: 1px solid var(--border); border-left: 4px solid var(--border);
  border-radius: 10px; padding: 0.7rem 0.85rem;
}
.card.banger { border-left-color: #caa23a; box-shadow: 0 0 0 1px #caa23a33; }
.card-head { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 0.3rem; }
.card-head .name { font-weight: 600; }
.card-head .pos { font-weight: 400; color: var(--text-dim); }
.badge { color: #fff; font-size: 0.72rem; padding: 0.1rem 0.45rem; border-radius: 6px; margin-right: 0.4rem; white-space: nowrap; }
.star { font-size: 0.78rem; color: #caa23a; font-weight: 600; }
.stats { font-size: 0.82rem; color: var(--text-dim); margin-top: 0.15rem; }
.components { font-size: 0.78rem; color: var(--text-dim); margin-top: 0.3rem; }
.reasons, .angles { margin: 0.35rem 0 0; padding-left: 1.1rem; font-size: 0.85rem; }
.reasons li, .angles li { margin-bottom: 0.15rem; }
.meta { font-size: 0.8rem; color: var(--text-dim); margin-top: 0.25rem; }
.meta.warn { color: #c94b4b; }
.verdict { font-size: 0.85rem; font-weight: 600; margin-top: 0.5rem; }
.bid { font-size: 0.82rem; margin-top: 0.3rem; }
.empty { color: var(--text-dim); font-size: 0.85rem; }
#gate { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 1rem; }
.gate-box { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; width: 100%; max-width: 320px; text-align: center; }
.gate-box input { width: 100%; padding: 0.6rem; margin: 0.8rem 0; border-radius: 8px; border: 1px solid var(--border); background: var(--bg); color: var(--text); font-size: 1rem; }
.gate-box button { width: 100%; padding: 0.6rem; border-radius: 8px; border: none; background: var(--accent); color: #fff; font-size: 1rem; }
.gate-box .err { color: #c94b4b; font-size: 0.85rem; }
footer { text-align: center; color: var(--text-dim); font-size: 0.75rem; margin-top: 2rem; }
"""

GATE_SCRIPT = """
const HASH = "{hash}";
async function sha256(msg) {{
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(msg));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("");
}}
function unlock() {{
  document.getElementById("gate").style.display = "none";
  document.getElementById("content").style.display = "block";
}}
async function tryUnlock() {{
  const pw = document.getElementById("pw").value;
  const h = await sha256(pw);
  if (h === HASH) {{
    localStorage.setItem("kb_auth", HASH);
    unlock();
  }} else {{
    document.getElementById("err").style.display = "block";
  }}
}}
document.getElementById("pw").addEventListener("keydown", e => {{ if (e.key === "Enter") tryUnlock(); }});
document.getElementById("go").addEventListener("click", tryUnlock);
if (localStorage.getItem("kb_auth") === HASH) {{ unlock(); }}
"""


def render_html(reports, generated_at, password_hash=None):
    ts = generated_at.strftime("%d.%m.%Y %H:%M UTC")
    sections = "".join(_league_section(r) for r in reports) or "<p class='empty'>Keine Ligen konfiguriert.</p>"

    body_inner = f"""<header class="top">
  <h1>⚽ Kickbase-Zentrale</h1>
  <div class="ts">Stand: {ts}</div>
</header>
<main>
  {sections}
  <footer>Automatisch generiert · faktenbasiert, keine Blackbox-Scores</footer>
</main>"""

    if password_hash:
        content = f"""<div id="gate">
  <div class="gate-box">
    <div style="font-size:2rem">🔒</div>
    <div>Kickbase-Zentrale</div>
    <input type="password" id="pw" placeholder="Passwort" autofocus>
    <button id="go">Öffnen</button>
    <p id="err" class="err" style="display:none">Falsch, nochmal.</p>
  </div>
</div>
<div id="content" style="display:none">
{body_inner}
</div>
<script>
{GATE_SCRIPT.format(hash=password_hash)}
</script>"""
    else:
        content = body_inner

    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Kickbase">
<meta name="theme-color" content="#1f6fd6">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>⚽</text></svg>">
<title>Kickbase-Zentrale</title>
<style>{CSS}</style>
</head>
<body>
{content}
</body>
</html>"""
