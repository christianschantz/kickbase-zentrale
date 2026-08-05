"""
Rendert main.py-Reports als eine selbstständige index.html - keine externen
Assets, läuft offline, funktioniert als "Zum Home-Bildschirm hinzufügen"-
Mini-App auf dem Handy.

Dashboard-Redesign (2026-07-31): Die Seite zeigt ENTSCHEIDUNGEN, nicht Daten.
Reihenfolge pro Liga: Handlungsleiste (max. 5 Einträge) -> KPI-Zeile ->
Risiko-Banner (nur bei Anlass) -> Transferziele-Watchlist ->
Kader-Handlungsbedarf (nur VERKAUFEN/BEOBACHTEN) -> MW-Steiger -> alles
andere (kompletter Kader/Markt/Liga-Bestenlisten) in aufklappbaren
<details>-Blöcken. Beide Ligen in einer Datei als Tabs (JS, kein Reload,
Deep-Link über den URL-Hash).

Optionale Passwort-Sperre (siehe config.PAGE_PASSWORD): rein client-seitig,
SHA-256-Hash liegt im Quelltext - KEIN echter Schutz, verhindert nur
zufälliges Finden der (ohnehin öffentlichen) GitHub-Pages-URL.
"""

import html as _html
from datetime import timedelta

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
STATUS_ICON = {"EIGEN": "🟢", "MITSPIELER": "👤", "MARKT": "🛒", "FREI": "⚪", "UNBEKANNT": "❓"}
STATUS_LABEL = {"EIGEN": "Eigen", "MITSPIELER": "Mitspieler", "MARKT": "Markt",
                "FREI": "Frei", "UNBEKANNT": "Unbekannt"}
GROUP_LABEL = {"MARKT": "🔥 Jetzt auf dem Markt", "MITSPIELER": "🤝 Bei Mitspieler",
               "FREI": "⚪ Frei, nicht gelistet"}


def _esc(s):
    return _html.escape(str(s))


def _mio(value):
    """Millionen-Kurzform, deutsches Komma - '12,48 Mio €' statt '12.480.000 €'."""
    return f"{value / 1e6:.2f} Mio €".replace(".", ",")


def _mio_signed(value):
    sign = "+" if value >= 0 else ""
    return f"{sign}{_mio(value)}"


def _next_22_countdown(generated_at):
    """
    Grobe DST-Näherung (Ende März-Ende Oktober = UTC+2, sonst UTC+1) - wie
    beim Workflow-Cron bereits akzeptiert (CLAUDE.md: "driftet 1h bei
    Sommer-/Winterzeit-Wechsel").
    """
    offset = 2 if 3 <= generated_at.month <= 10 else 1
    local = generated_at + timedelta(hours=offset)
    target = local.replace(hour=22, minute=0, second=0, microsecond=0)
    if local >= target:
        target += timedelta(days=1)
    delta = target - local
    hrs = delta.seconds // 3600
    mins = (delta.seconds % 3600) // 60
    return f"{hrs} h {mins} min"


# ---------- Wiederverwendete Karten (Kader/Markt/Liga-Board) ----------

def _squad_card(c):
    color = VERDICT_COLORS.get(c["verdict"], "#888")
    icon = VERDICT_ICONS.get(c["verdict"], "•")
    # Nächster Gegner: bevorzugt os/ht aus /lineup (verifiziert, kein
    # Fuzzy-Matching mehr nötig), Fallback auf die alte Quotenkopplung.
    if c.get("next_opponent_verified"):
        opponents = f"<div class='meta'>Nächster Gegner: {_esc(c['next_opponent_verified'])}</div>"
    elif c.get("opponents"):
        opponents = f"<div class='meta'>Nächste Gegner: {_esc(', '.join(c['opponents']))}</div>"
    else:
        opponents = ""
    kb_color = c.get("kickbase_color")
    color_dot = (f"<span class='kb-dot kb-{_esc(kb_color)}' "
                f"title='Kickbase-Status: {_esc(kb_color)}'></span>" if kb_color else "")
    reasons = "".join(f"<li>{_esc(r)}</li>" for r in c["reasons"])
    # Fair Value dezent (SPEC_kalibrierung_fairvalue.md 4.1 - "nicht dominant",
    # ergänzt das Verdikt, ersetzt es nicht; die Reason-Zeile trägt bereits die
    # Einordnung, hier nur der nackte Wert als Kontext).
    fv_html = ""
    if c.get("fair_value") is not None:
        tag = " ⚠️" if c.get("fair_value_sell_flag") else ""
        fv_html = f"<div class='meta'>💰 Fair Value {_mio(c['fair_value'])}{tag}</div>"
    return f"""<div class="card" style="border-left-color:{color}">
  <div class="card-head">
    <span class="badge" style="background:{color}">{icon} {_esc(c['verdict'])}</span>
    {color_dot}
    <span class="name">{_esc(c['name'])} <span class="pos">({_esc(c['pos'])})</span></span>
  </div>
  <div class="stats">Score {c['score']} · MW {_mio(c['mv'])} ({c['tfhmvt']:+,.0f} €/Tag)</div>
  {fv_html}
  <ul class="reasons">{reasons}</ul>
  {opponents}
</div>"""


def _market_card(m, highlight=False):
    parts = (m.get("team_verdict") or "").split(" | ")
    headline = parts[0] if parts else ""
    angles = parts[1:]
    angle_html = "".join(f"<li>{_esc(a)}</li>" for a in angles)
    bid = m.get("bid") or {}
    bid_html = ""
    if bid and "KEIN BEDARF" not in headline:
        extra = ""
        if bid.get("projection_note"):
            extra += f"<div class='meta'>↳ {_esc(bid['projection_note'])}</div>"
        if bid.get("star_ceiling"):
            extra += (f"<div class='meta'>↳ Star-Ausnahme: in Einzelfällen bis "
                      f"{_mio(bid['star_ceiling'])} belegt (nicht die Regel)</div>")
        if bid.get("verdict") == "nicht_bieten":
            bid_html = (f"<div class='bid nicht-bieten'>🚫 NICHT BIETEN - "
                        f"{_esc(bid.get('verdict_reason', 'zu teuer'))}</div>{extra}")
        else:
            tick = "✅" if m.get("affordable") else "❌"
            bid_html = (f"<div class='bid'>💶 Gebot {_mio(bid.get('recommended_bid', 0))} "
                        f"(22h-MW ~{_mio(bid.get('expected_mv_22h', 0))}, "
                        f"Puffer {bid.get('buffer_pct', 0)}%, "
                        f"WK ~{bid.get('win_probability', 0):.0%}) {tick} "
                        f"{_esc(m.get('financing', ''))}</div>{extra}")
    # SPEC_kalibrierung_fairvalue.md 3.1/3.4: Mindestpreis-Spieler sind
    # zensierte Beobachtungen ("neutral", nie "überbewertet" - sie können
    # nicht billiger sein). Sonst beide Richtungen derselben Kurve zeigen:
    # Fair Value (Punkte -> MW) UND was der aktuelle MW an Punkten "üblich"
    # wäre (MW -> Punkte, scoring.expected_points).
    fair_value_html = ""
    if m.get("min_price_player"):
        fair_value_html = "<div class='fair-value'>💰 Fair Value: neutral - Mindestpreis</div>"
    elif m.get("fair_value") is not None and m.get("mv"):
        diff_pct = (m["fair_value"] - m["mv"]) / m["mv"]
        urteil = ("unterbewertet" if diff_pct > 0.05 else
                  "überbewertet" if diff_pct < -0.05 else "fair bewertet")
        fv_cls = "up" if diff_pct > 0.05 else ("down" if diff_pct < -0.05 else "")
        expected_line = ""
        if m.get("expected_ap_for_mv") is not None:
            expected_line = (f"<div class='meta'>liefert Ø {m['ap']:.0f} P, für {_mio(m['mv'])} "
                             f"üblich wären {m['expected_ap_for_mv']:.0f} P</div>")
        fair_value_html = (f"<div class='fair-value {fv_cls}'>💰 Fair Value {_mio(m['fair_value'])} "
                           f"· aktuell {_mio(m['mv'])} ({diff_pct:+.0%}) - {urteil}</div>{expected_line}")
    opponents = (f"<div class='meta'>Nächste Gegner: {_esc(', '.join(m['opponents']))}</div>"
                 if m.get("opponents") else "")
    fitness = f"<div class='meta warn'>⚠️ {_esc(m['fitness'])}</div>" if m.get("fitness") else ""
    star_badge = (f"<span class='star'>💎 Star {m['star']:.0%}</span>"
                  if m.get("banger") else "")
    kb_color = m.get("kickbase_color")
    color_dot = (f"<span class='kb-dot kb-{_esc(kb_color)}' "
                f"title='Kickbase-Status: {_esc(kb_color)}'></span>" if kb_color else "")
    cls = "card market-card banger" if highlight else "card market-card"
    comps = explain(m.get("components", {}), m.get("meta")) if m.get("components") else ""
    return f"""<div class="{cls}">
  <div class="card-head">
    {color_dot}
    <span class="name">{_esc(m['name'])} <span class="pos">({_esc(m['pos'])}, {_esc(m.get('team', ''))})</span></span>
    {star_badge}
  </div>
  <div class="stats">Score {m['score']} · MW {_mio(m['mv'])} ({m['tfhmvt']:+,.0f} €/Tag) · Ø {m['ap']} P · ⏳ {m.get('expiry_s', 0)/3600:.0f}h</div>
  {fair_value_html}
  <div class="components">{_esc(comps)}</div>
  {opponents}
  {fitness}
  <div class="verdict">🎯 {_esc(headline)}</div>
  <ul class="angles">{angle_html}</ul>
  {bid_html}
</div>"""


def _board_row(e, score_key):
    status = e.get("status", "UNBEKANNT")
    icon = STATUS_ICON.get(status, "❓")
    owner = f" ({_esc(e['owner'])})" if e.get("owner") else ""
    cls = "board-row eigen" if status == "EIGEN" else "board-row"
    both_badge = " <span class='both-badge'>⭐ auch in anderer Liste</span>" if e.get("in_both") else ""
    residual_html = ""
    if e.get("residual_pct") is not None:
        sign = "+" if e["residual_abs"] >= 0 else ""
        residual_html = (f"<div class='board-residual'>Ø {e['ap']} P - für {_mio(e['mv'])} "
                         f"erwartbar wären {e['expected_ap']:.0f} P - {sign}{e['residual_abs']:.0f} P "
                         f"({sign}{e['residual_pct']:.0f}%) ggü. Preiserwartung</div>")
    bid_html = ""
    if e.get("bid"):
        b = e["bid"]
        note = f" ↳ {_esc(b['projection_note'])}" if b.get("projection_note") else ""
        bid_html = (f"<div class='board-bid'>💶 Gebot {_mio(b['recommended_bid'])} "
                    f"(WK ~{b['win_probability']:.0%}){note}</div>")
    return f"""<div class="{cls}">
  <span class="board-status">{icon} {STATUS_LABEL.get(status, status)}{owner}</span>
  <span class="board-name">{_esc(e['name'])} <span class="pos">({_esc(e['team'])})</span></span>
  <span class="board-stats">Score {e[score_key]} · MW {_mio(e['mv'])} · Ø {e['ap']} P</span>{both_badge}
  {residual_html}
  {bid_html}
</div>"""


def _climber_row(rank, e):
    """Einzeilig, ohne Positionsgruppierung (Spec 5.2 - Top 5 gesamt statt
    4x10 große Karten)."""
    status = e.get("status", "UNBEKANNT")
    icon = STATUS_ICON.get(status, "❓")
    owner = f" {_esc(e['owner'])}" if e.get("owner") else ""
    sdmvt = e.get("sdmvt") or 0
    return f"""<div class="climber-row">
  <span class="climber-rank">{rank}.</span>
  <span class="board-name">{_esc(e['name'])} <span class="pos">({_esc(e['team'])})</span></span>
  <span class="climber-delta">{sdmvt:+,.0f} €/7T</span>
  <span class="board-status">{icon}{owner}</span>
</div>"""


def _board_list(entries_by_pos, score_key):
    groups = []
    for pos in ("TW", "ABW", "MF", "ANG"):
        entries = entries_by_pos.get(pos) or []
        if not entries:
            continue
        rows = "".join(_board_row(e, score_key) for e in entries)
        groups.append(f"<div class='board-group'><h4>{pos}</h4>{rows}</div>")
    return "".join(groups)


# ---------- Dashboard-Bausteine ----------

def _kpi_tile(label, value, sub, cls=""):
    return f"""<div class="kpi-tile {cls}">
  <div class="kpi-label">{_esc(label)}</div>
  <div class="kpi-value">{_esc(value)}</div>
  <div class="kpi-sub">{_esc(sub)}</div>
</div>"""


def _kpi_grid(kpis):
    if not kpis:
        return ""
    delta = kpis.get("team_value_delta_24h", 0)
    delta_cls = "up" if delta > 0 else ("down" if delta < 0 else "")
    tiles = [
        _kpi_tile("Teamwert", _mio(kpis.get("team_value", 0)), f"{_mio_signed(delta)}/24h", delta_cls),
        _kpi_tile("Kaufkraft", _mio(kpis.get("capacity", 0)), "33%-Kreditregel"),
        _kpi_tile("Budget", _mio(kpis.get("budget", 0)), ""),
    ]
    if kpis.get("season_started"):
        gap = kpis.get("points_gap_to_leader") or 0
        tiles.append(_kpi_tile("Ligaplatz", f"#{kpis.get('league_rank', '?')}",
                               f"{gap} Pkt. Rückstand" if gap else "Tabellenführer"))
    else:
        tiles.append(_kpi_tile("Ligaplatz", "–", "Saison startet bald"))
    tiles.append(_kpi_tile("Kaderplätze", f"{kpis.get('squad_slots', 0)}/{kpis.get('max_squad', 0)}", ""))
    tiles.append(_kpi_tile("Tagesertrag", _mio_signed(delta), "Dein Kader heute", delta_cls))
    return "<div class='kpi-grid'>" + "".join(tiles) + "</div>"


def _action_list(actions):
    if not actions:
        return "<div class='actions-empty'>✅ Heute keine dringenden Aktionen.</div>"
    items = []
    for a in actions:
        cls = " urgent" if a.get("urgent") else ""
        amount = f" <span class='action-amount'>{_mio(a['amount'])}</span>" if a.get("amount") else ""
        deadline = (f" · ⏳ {a['deadline_hours']:.0f} h" if a.get("deadline_hours") is not None else "")
        items.append(f"""<div class="action-item{cls}">
  <span class="action-icon">{a['icon']}</span>
  <div class="action-body">
    <div class="action-text">{_esc(a['text'])}{amount}</div>
    <div class="action-reason">{_esc(a['reason'])}{deadline}</div>
  </div>
</div>""")
    return "<div class='actions'>" + "".join(items) + "</div>"


def _risk_banner(risks):
    if not risks:
        return ""
    lines = "".join(f"<div class='risk-line {r['level']}'>{r['icon']} {_esc(r['text'])}</div>" for r in risks)
    return f"<div class='risk-banner'>{lines}</div>"


def _model_health_banner(report):
    """
    SPEC_kalibrierung_fairvalue.md 1.1/2.1/3.3: Selbstprüfungen und
    algorithmisch erkannte Duelle gehören sichtbar ins Dashboard, nicht
    stillschweigend verrechnet oder nur in die Konsole geloggt.
    """
    lines = []
    calibration = report.get("calibration")
    if calibration and not calibration["plausible"]:
        lines.append(
            f"<div class='risk-line warn'>⚠️ Kalibrierung: Median-Prognose "
            f"{calibration['median_prognose']:.0f} P weicht {calibration['deviation']:+.0%} "
            f"vom Vorsaison-Anker ({calibration['anchor']:.0f} P/Spieltag) ab</div>")
    if report.get("fair_value_ok") is False:
        lines.append("<div class='risk-line warn'>⚠️ Preiskurve wirkt verzerrt (Selbstprüfung "
                     "40-60% verletzt) - Fair Value heute unterdrückt</div>")
    for txt in (report.get("self_play_conflicts") or [])[:5]:
        lines.append(f"<div class='risk-line warn'>⚔️ {_esc(txt)}</div>")
    if not lines:
        return ""
    return f"<div class='risk-banner'>{''.join(lines)}</div>"


def _watchlist(targets):
    if not targets:
        return "<p class='empty'>Keine besonderen Transferziele erkannt.</p>"
    groups_html = []
    for key in ("MARKT", "MITSPIELER", "FREI"):
        entries = targets.get(key) or []
        if not entries:
            continue
        rows = "".join(_board_row(e, "quality_score") for e in entries)
        groups_html.append(f"<div class='watch-group'><h4>{GROUP_LABEL[key]}</h4>{rows}</div>")
    return "".join(groups_html) or "<p class='empty'>Keine besonderen Transferziele erkannt.</p>"


def _squad_action_section(items):
    if not items:
        return "<p class='empty'>✅ Kein Handlungsbedarf im Kader.</p>"
    return "<div class='grid'>" + "".join(_squad_card(c) for c in items) + "</div>"


def _climbers_block(climbers):
    if not climbers:
        return "<p class='empty'>Noch keine Daten.</p>"
    rows = "".join(_climber_row(i + 1, e) for i, e in enumerate(climbers[:5]))
    return f"<div class='climber-list'>{rows}</div>"


def _changes_block(changes):
    if not changes:
        return "<p class='note'>Noch kein Vortags-Snapshot vorhanden - der Vergleich erscheint ab morgen.</p>"
    parts = []
    if changes.get("team_value_change") is not None:
        parts.append(f"<div class='change-line'>Teamwert seit {_esc(changes['since_date'])}: "
                     f"{_mio_signed(changes['team_value_change'])}</div>")
    for vc in changes.get("verdict_changes", [])[:5]:
        parts.append(f"<div class='change-line'>{_esc(vc['name'])}: "
                     f"{_esc(vc['from'])} → {_esc(vc['to'])}</div>")
    for sc in changes.get("status_changes", [])[:5]:
        parts.append(f"<div class='change-line'>🩺 {_esc(sc)}</div>")
    for nm in changes.get("new_market", [])[:5]:
        parts.append(f"<div class='change-line'>🆕 Neu auf dem Markt: {_esc(nm)}</div>")
    return "".join(parts) or "<p class='note'>Keine nennenswerten Änderungen seit gestern.</p>"


def _llm_block(insights, status="ok"):
    """
    KI-Kurzreport + Flags (llm_insights.py). Spec-Fix 2026-08-05 Punkt 2.1:
    stilles Verschwinden ist der schlechteste Fall - bei fehlendem Key ein
    dezenter Hinweis, bei einem Fehltag (Kontingent/API-Fehler) ein
    deutlicher Warnhinweis statt gar nichts anzuzeigen.
    """
    if not insights:
        if status == "no_key":
            return ("<div class='llm-block llm-off'><div class='llm-label'>🤖 KI-Einordnung</div>"
                    "<p class='note'>Nicht konfiguriert (kein GEMINI_API_KEY).</p></div>")
        return ("<div class='llm-block llm-warn'><div class='llm-label'>🤖 KI-Einordnung</div>"
                "<p class='note warn'>⚠️ Heute nicht verfügbar (Kontingent oder API-Fehler) - "
                "morgen wieder versucht.</p></div>")
    flags_html = ""
    flags = insights.get("player_flags") or []
    if flags:
        rows = "".join(
            f"<div class='flag-row'><span class='flag-name'>{_esc(f['player_name'])}</span> "
            f"<span class='flag-tag'>{_esc(f['flag'])}</span> "
            f"<span class='flag-conf'>{_esc(f['confidence'])}</span>"
            f"<div class='flag-note'>{_esc(f['note'])}</div></div>"
            for f in flags
        )
        flags_html = f"<div class='llm-flags'>{rows}</div>"
    outlook_html = ""
    outlook = insights.get("matchday_outlook") or []
    if outlook:
        rows = "".join(
            f"<div class='flag-row'><span class='flag-name'>{_esc(o['match'])}</span> "
            f"<span class='flag-tag'>{_esc('/'.join(o.get('beneficiary_positions', [])))}</span>"
            f"<div class='flag-note'>{_esc(o['expected_script'])} - {_esc(o['reason'])}</div></div>"
            for o in outlook
        )
        outlook_html = f"<div class='llm-flags'>{rows}</div>"
    return f"""<div class="llm-block">
  <div class="llm-label">🤖 KI-Einordnung</div>
  <div class="llm-report">{_esc(insights.get('report', ''))}</div>
  {flags_html}
  {outlook_html}
</div>"""


def _mitspieler_appendix(entries):
    """Kompakter Anhang zum Transfermarkt-Modul (Spec 5.1) - 3-4 beste
    erreichbare Spieler aus fremden Kadern, statt der grossen positionsweisen
    Transferziel-Liste auf der Startseite."""
    if not entries:
        return ""
    rows = "".join(_board_row(e, "quality_score") for e in entries)
    return f"<div class='mitspieler-appendix'><h4 class='board-sub'>🤝 Bei Mitspielern</h4>{rows}</div>"


def _transfermarkt_section(report):
    """
    Ein Modul "Transfermarkt" statt zwei getrennter (Spec 5.1): Tagesmarkt
    ist der Hauptteil (alle aktuell verfügbaren Spieler mit Verdikt,
    Gebotskorridor, Restlaufzeit - jetzt direkt sichtbar statt in <details>
    versteckt), "Bei Mitspielern" ein kompakter Anhang. Die grosse
    positionsweise Transferziel-Liste (build_targets) wandert in die
    Vertiefung, s. _league_panel.
    """
    market = report.get("market", [])
    market_html = ("".join(_market_card(m) for m in market)
                  or "<p class='empty'>Kein Marktangebot.</p>")
    bangers_html = ""
    if report.get("bangers"):
        cards = "".join(_market_card(m, highlight=True) for m in report["bangers"][:3])
        bangers_html = f"<h4 class='board-sub'>💎 Banger-Ziele</h4><div class='grid'>{cards}</div>"
    free_slots = report.get("free_slots", 0)
    free_slots_note = f"<p class='note'>🟢 {free_slots} freie Kaderplätze</p>" if free_slots else ""
    appendix_html = _mitspieler_appendix(report.get("mitspieler_appendix") or [])
    return f"""{free_slots_note}
{bangers_html}
<div class="grid">{market_html}</div>
{appendix_html}"""


def _lineup_row(p):
    f = p.get("ep_factors", {})
    stats = (f"{p['expected_points']} P (Basis {f.get('basis')} × Einsatz "
            f"{f.get('einsatzfaktor')} × Gegner {f.get('gegnerfaktor')} × "
            f"Form {f.get('formfaktor', 1.0)} × Verlauf {f.get('spielverlaufsfaktor')}"
            + (f" + Zu-Null {f['zu_null_bonus']:+.1f}" if f.get("zu_null_bonus") else "")
            + ")")
    bandwidth = f.get("bandbreite")
    duel_note = (" ⚔️ gedämpft (direktes Duell im Kader)" if f.get("direktduell_gedaempft") else "")
    sub = (f"<div class='meta'>Bandbreite {bandwidth[0]:.0f}-{bandwidth[1]:.0f} P{duel_note}</div>"
          if bandwidth else "")
    return f"""<div class="board-row">
  <span class="board-status">{_esc(p['pos'])}</span>
  <span class="board-name">{_esc(p['name'])}</span>
  <span class="board-stats">{_esc(stats)}</span>
  {sub}
</div>"""


def _lineup_block(lineup, lineup_status=None, swaps=None, missing=None):
    """
    Aufstellungsempfehlung (Punkt 6) - beste Elf nach erwarteten Punkten je
    Formation, PLUS Abgleich mit der echten gesetzten Aufstellung über
    GET /lineup (verifiziert 2026-08-05, s. coach.py-Docstring): freie Slots
    und konkrete Wechselvorschläge ggü. der Ist-Aufstellung.
    """
    if not lineup or not lineup.get("best"):
        return "<p class='empty'>Kein Kader für eine Aufstellungsempfehlung geladen.</p>"
    best = lineup["formations"][lineup["best"]]
    rows = "".join(_lineup_row(p) for p in sorted(best["xi"], key=lambda x: -x["expected_points"]))
    alts = sorted(((n, r["total_points"]) for n, r in lineup["formations"].items()
                  if n != lineup["best"]), key=lambda x: -x[1])[:3]
    alts_txt = ", ".join(f"{n} ({t - lineup['best_total']:+.1f})" for n, t in alts)
    alts_html = f"<p class='note'>Alternativen: {_esc(alts_txt)}</p>" if alts_txt else ""

    status_html = ""
    if lineup_status:
        n_filled = len(lineup_status["xi"])
        if lineup_status["empty_slots"]:
            gap_txt = ", ".join(f"{n}× {pos}" for pos, n in (missing or {}).items()) or "Position unklar"
            status_html += (f"<div class='risk-line warn'>⚠️ Aktuell gesetzte Elf: nur "
                            f"{n_filled}/11 Slots belegt - fehlt: {_esc(gap_txt)}</div>")
        else:
            status_html += f"<p class='note'>Aktuell gesetzte Elf: {n_filled}/11 Slots belegt.</p>"
        if swaps:
            swap_rows = "".join(
                f"<div class='board-row'><span class='board-status'>Slot {s['slot']}</span>"
                f"<span class='board-name'>{_esc(s['out']['name'])} raus, {_esc(s['in']['name'])} rein</span>"
                f"<span class='board-stats'>{s['diff']:+.1f} P</span></div>"
                for s in swaps)
            status_html += f"<p class='note'>Wechselvorschläge ggü. der Ist-Aufstellung:</p><div class='board'>{swap_rows}</div>"
    else:
        status_html = ("<p class='note warn'>⚠️ Kein Abgleich mit der aktuell im Spiel gesetzten "
                       "Aufstellung möglich - das ist die rechnerisch beste Elf aus dem Kader.</p>")

    return f"""<p class="note"><strong>{_esc(lineup['best'])}</strong> - {lineup['best_total']} erwartete
Punkte · Deadline 20:29 Uhr</p>
<div class="board">{rows}</div>
{alts_html}
{status_html}"""


def _league_teams_table(teams, my_uid):
    """
    Modul 3 (SPEC_gebote_ki_team_KOMPLETT.md) - "das wichtigste Modul laut
    Nutzer": Übersicht aller Liga-Manager, Prognose beruht auf der ECHTEN
    gesetzten Aufstellung (GET managers/{uid}/squad, verifiziert), keine
    Bestmöglich-Annahme mehr.
    """
    if not teams:
        return "<p class='empty'>Keine Manager-Daten geladen.</p>"
    rows = []
    for i, m in enumerate(teams, 1):
        is_me = str(m["uid"]) == str(my_uid)
        cls = "team-row me" if is_me else "team-row"
        rng = f"({m['prognose_range'][0]:.0f}-{m['prognose_range'][1]:.0f})"
        eff = f"{m['effizienz']:.0f}%" if m["effizienz"] is not None else "?"
        ks = f"{m['kaderstaerke']:.0f}" if m["kaderstaerke"] is not None else "?"
        warn = ""
        if m["empty_slots"]:
            warn += f"<span class='team-warn'>⚠️ {m['empty_slots']} Slot(s) frei</span>"
        if m["klumpenrisiko"] and m["klumpenrisiko"] >= 30:
            warn += f"<span class='team-warn'>⚠️ {m['klumpenrisiko']:.0f}% aus {_esc(m['top_team'])}</span>"
        hint = (f"<div class='meta'>ℹ️ {_esc(m['formation_hint'])}</div>"
               if m.get("formation_hint") else "")
        rows.append(f"""<div class="{cls}">
  <span class="team-rank">{i}</span>
  <span class="team-name">{_esc(m['name'])}{' ⭐' if is_me else ''}</span>
  <span class="team-stat">{m['prognose']:.0f} P <span class="team-sub">{rng}</span></span>
  <span class="team-stat">{ks} <span class="team-sub">Kaderstärke</span></span>
  <span class="team-stat">{eff} <span class="team-sub">Effizienz</span></span>
  <span class="team-stat">{_esc(m['formation'] or '?')}</span>
  {warn}
  {hint}
</div>""")
    return "<div class='team-table'>" + "".join(rows) + "</div>"


def _league_panel(report, panel_id):
    name = _esc(report.get("name", "?"))
    if report.get("error"):
        return f"""<div id="{panel_id}" class="league-panel">
  <h2>🏆 {name}</h2>
  <p class="empty">❌ {_esc(report['error'])}</p>
</div>"""

    kpis = report.get("kpis", {})
    board = report.get("league_board") or {}
    squad_sorted = sorted(report.get("squad_classified", []),
                          key=lambda x: SQUAD_ORDER.get(x["verdict"], 9))
    full_squad_html = ("".join(_squad_card(c) for c in squad_sorted)
                       or "<p class='empty'>Kein Kader geladen.</p>")

    fixture_note = ("" if report.get("has_fixtures")
                    else "<p class='note warn'>⚠️ Kein Spielplan verfügbar - "
                        "Spielplan-Komponente lief neutral</p>")

    return f"""<div id="{panel_id}" class="league-panel">
  <h2>🏆 {name}</h2>
  {fixture_note}

  <h3 class="section-h">📋 Heute zu erledigen</h3>
  {_action_list(report.get("actions", []))}

  {_llm_block(report.get("llm_insights"), report.get("llm_status", "ok"))}

  {_kpi_grid(kpis)}

  {_risk_banner(report.get("risks", []))}
  {_model_health_banner(report)}

  <h3 class="section-h">🧠 Aufstellungsempfehlung</h3>
  {_lineup_block(report.get("lineup"), report.get("lineup_status"),
                report.get("lineup_swaps"), report.get("lineup_missing"))}

  <h3 class="section-h">👥 Spieltagsprognose - alle Manager</h3>
  <p class="note">Beruht auf der ECHTEN gesetzten Aufstellung jedes Managers
  (nicht auf einer Bestmöglich-Annahme).</p>
  {_league_teams_table(report.get("league_teams") or [], report.get("my_uid"))}

  <h3 class="section-h">🛒 Transfermarkt</h3>
  {_transfermarkt_section(report)}

  <h3 class="section-h">👥 Kader-Handlungsbedarf</h3>
  {_squad_action_section(report.get("squad_action_items", []))}

  <h3 class="section-h">📈 Stärkste MW-Steiger der Liga</h3>
  <p class="note">In der Saisonvorbereitung die einzige echte Bewegung im Spiel -
  ab Saisonstart durch echte Formwerte ersetzt.</p>
  {_climbers_block(board.get("climbers") or [])}

  <details class="deep">
    <summary>🔄 Was hat sich seit gestern geändert?</summary>
    <div class="deep-body">{_changes_block(report.get("changes"))}</div>
  </details>
  <details class="deep">
    <summary>👥 Kompletter Kader ({len(report.get('squad_classified', []))} Spieler)</summary>
    <div class="deep-body"><div class="grid">{full_squad_html}</div></div>
  </details>
  <details class="deep">
    <summary>🎯 Weitere Transferziele (liga-weit, nach Position)</summary>
    <div class="deep-body">{_watchlist(report.get("targets", {}))}</div>
  </details>
  <details class="deep">
    <summary>🏆 Liga-Bestenlisten (Qualität &amp; Deals, gesamte Competition)</summary>
    <div class="deep-body">
      <h4 class="board-sub">Qualität (preisunabhängig)</h4>
      <div class="board">{_board_list(board.get("quality") or {}, "quality_score")}</div>
      <h4 class="board-sub">Beste Deals (Preis-Leistung/Trading)</h4>
      <div class="board">{_board_list(board.get("value") or {}, "value_score")}</div>
    </div>
  </details>
</div>"""


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
  position: sticky; top: 0; z-index: 10; background: var(--bg);
  padding: 1rem 1rem 0.6rem; text-align: center; border-bottom: 1px solid var(--border);
}
header.top h1 { margin: 0 0 0.2rem; font-size: 1.2rem; }
header.top .ts { color: var(--text-dim); font-size: 0.78rem; }
.tabs { display: flex; gap: 0.4rem; justify-content: center; margin-top: 0.6rem; flex-wrap: wrap; }
.tab-btn {
  font: inherit; padding: 0.4rem 0.9rem; border-radius: 999px; border: 1px solid var(--border);
  background: var(--card-bg); color: var(--text); cursor: pointer;
}
.tab-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
main { max-width: 640px; margin: 0 auto; padding: 0.75rem; }
.league-panel h2 { font-size: 1.1rem; margin: 0.4rem 0 0.6rem; }
.section-h { font-size: 0.9rem; margin: 1.3rem 0 0.5rem; color: var(--text-dim);
             text-transform: uppercase; letter-spacing: 0.03em; }
.note { font-size: 0.85rem; margin: 0.3rem 0; color: var(--text-dim); }
.note.warn { color: #c17a00; }
.grid { display: grid; grid-template-columns: 1fr; gap: 0.6rem; }
.card {
  background: var(--card-bg); border: 1px solid var(--border); border-left: 4px solid var(--border);
  border-radius: 10px; padding: 0.7rem 0.85rem;
}
.card.banger { border-left-color: #caa23a; box-shadow: 0 0 0 1px #caa23a33; }
.card-head { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 0.3rem; }
.card-head .name { font-weight: 600; }
.card-head .pos { font-weight: 400; color: var(--text-dim); }
.badge { color: #fff; font-size: 0.72rem; padding: 0.1rem 0.45rem; border-radius: 6px; margin-right: 0.4rem; white-space: nowrap; }
.kb-dot { display: inline-block; width: 0.6rem; height: 0.6rem; border-radius: 50%; margin-right: 0.35rem; vertical-align: middle; border: 1px solid #0003; }
.kb-blau { background: #2e6fd6; }
.kb-grün { background: #2e9e50; }
.kb-gelb { background: #e0c02e; }
.kb-rot { background: #d64545; }
.kb-grau { background: #9aa1ad; }
.star { font-size: 0.78rem; color: #caa23a; font-weight: 600; }
.stats { font-size: 0.82rem; color: var(--text-dim); margin-top: 0.15rem; }
.components { font-size: 0.78rem; color: var(--text-dim); margin-top: 0.3rem; }
.reasons, .angles { margin: 0.35rem 0 0; padding-left: 1.1rem; font-size: 0.85rem; }
.reasons li, .angles li { margin-bottom: 0.15rem; }
.meta { font-size: 0.8rem; color: var(--text-dim); margin-top: 0.25rem; }
.meta.warn { color: #c94b4b; }
.verdict { font-size: 0.85rem; font-weight: 600; margin-top: 0.5rem; }
.bid { font-size: 0.82rem; margin-top: 0.3rem; }
.bid.nicht-bieten { color: #c94b4b; font-weight: 600; }
.fair-value { font-size: 0.82rem; margin-top: 0.3rem; font-weight: 600; }
.fair-value.up { color: #2e9e50; }
.fair-value.down { color: #c94b4b; }
.team-table { display: flex; flex-direction: column; gap: 0.35rem; margin-top: 0.5rem; }
.team-row {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.6rem;
  padding: 0.5rem 0.7rem; border: 1px solid var(--border); border-radius: 8px;
  background: var(--card-bg); font-size: 0.85rem;
}
.team-row.me { border-left: 3px solid var(--accent); }
.team-rank { color: var(--text-dim); width: 1.3rem; }
.team-name { font-weight: 600; }
.team-stat { color: var(--text); }
.team-sub { color: var(--text-dim); font-size: 0.72rem; }
.team-warn { flex-basis: 100%; font-size: 0.78rem; color: #c17a00; }
.empty { color: var(--text-dim); font-size: 0.85rem; }
#gate { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 1rem; }
.gate-box { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; width: 100%; max-width: 320px; text-align: center; }
.gate-box input { width: 100%; padding: 0.6rem; margin: 0.8rem 0; border-radius: 8px; border: 1px solid var(--border); background: var(--bg); color: var(--text); font-size: 1rem; }
.gate-box button { width: 100%; padding: 0.6rem; border-radius: 8px; border: none; background: var(--accent); color: #fff; font-size: 1rem; }
.gate-box .err { color: #c94b4b; font-size: 0.85rem; }
.board-group { margin-bottom: 0.8rem; }
.board-group h4 { font-size: 0.8rem; color: var(--text-dim); margin: 0.6rem 0 0.3rem; text-transform: uppercase; letter-spacing: 0.03em; }
.board-row {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem;
  padding: 0.4rem 0.6rem; border: 1px solid var(--border); border-radius: 8px;
  background: var(--card-bg); margin-bottom: 0.35rem; font-size: 0.85rem;
}
.board-row.eigen { border-left: 3px solid #2e9e50; }
.board-row .board-status { white-space: nowrap; color: var(--text-dim); font-size: 0.8rem; }
.board-row .board-name { font-weight: 600; }
.board-row .board-stats { color: var(--text-dim); font-size: 0.8rem; margin-left: auto; }
.board-row .board-bid { flex-basis: 100%; font-size: 0.8rem; }
.board-row .board-residual { flex-basis: 100%; font-size: 0.78rem; color: var(--text-dim); }
.board-sub { font-size: 0.85rem; margin: 0.8rem 0 0.4rem; color: var(--text-dim); }
.climber-list { display: flex; flex-direction: column; gap: 0.3rem; margin-top: 0.6rem; }
.climber-row {
  display: flex; align-items: baseline; gap: 0.5rem; padding: 0.35rem 0.6rem;
  border: 1px solid var(--border); border-radius: 8px; background: var(--card-bg); font-size: 0.85rem;
}
.climber-rank { color: var(--text-dim); width: 1.2rem; }
.climber-delta { color: #2e9e50; font-weight: 600; margin-left: auto; }
.both-badge { font-size: 0.72rem; color: #caa23a; font-weight: 600; }
.kpi-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.5rem; margin-top: 0.8rem; }
@media (min-width: 480px) { .kpi-grid { grid-template-columns: repeat(3, 1fr); } }
.kpi-tile { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 0.6rem 0.7rem; }
.kpi-label { font-size: 0.72rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.02em; }
.kpi-value { font-size: 1.05rem; font-weight: 700; margin-top: 0.1rem; }
.kpi-sub { font-size: 0.72rem; color: var(--text-dim); margin-top: 0.1rem; }
.kpi-tile.up .kpi-value { color: #2e9e50; }
.kpi-tile.down .kpi-value { color: #c94b4b; }
.actions { display: flex; flex-direction: column; gap: 0.5rem; margin-top: 0.6rem; }
.actions-empty { margin-top: 0.6rem; font-size: 0.9rem; color: var(--text-dim); }
.action-item {
  display: flex; gap: 0.6rem; align-items: flex-start; background: var(--card-bg);
  border: 1px solid var(--border); border-left: 4px solid var(--accent);
  border-radius: 10px; padding: 0.6rem 0.75rem;
}
.action-item.urgent { border-left-color: #c94b4b; }
.action-icon { font-size: 1.2rem; line-height: 1.2; }
.action-text { font-weight: 600; font-size: 0.92rem; }
.action-amount { color: var(--accent); font-weight: 700; }
.action-reason { font-size: 0.8rem; color: var(--text-dim); margin-top: 0.15rem; }
.risk-banner { margin-top: 0.8rem; display: flex; flex-direction: column; gap: 0.35rem; }
.risk-line { padding: 0.5rem 0.7rem; border-radius: 8px; font-size: 0.85rem; }
.risk-line.warn { background: #f7d97a22; border: 1px solid #c17a00; color: #c17a00; }
.risk-line.info { background: #1f6fd622; border: 1px solid var(--accent); color: var(--accent); }
.watch-group { margin-bottom: 0.9rem; }
.watch-group h4 { font-size: 0.82rem; margin: 0.4rem 0; }
details.deep { margin-top: 0.8rem; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
details.deep summary {
  padding: 0.65rem 0.85rem; cursor: pointer; font-size: 0.88rem; font-weight: 600;
  background: var(--card-bg); list-style: none;
}
details.deep summary::-webkit-details-marker { display: none; }
details.deep summary::after { content: "▸"; float: right; color: var(--text-dim); }
details.deep[open] summary::after { content: "▾"; }
.deep-body { padding: 0.75rem 0.85rem; }
.change-line { font-size: 0.85rem; padding: 0.3rem 0; border-bottom: 1px solid var(--border); }
.change-line:last-child { border-bottom: none; }
.llm-block {
  margin-top: 0.8rem; padding: 0.75rem 0.85rem; border-radius: 10px;
  background: linear-gradient(135deg, #6d5bd022, #1f6fd622);
  border: 1px solid var(--accent);
}
.llm-block.llm-off { background: none; border-color: var(--border); opacity: 0.7; }
.llm-block.llm-warn { background: #f7d97a22; border-color: #c17a00; }
.llm-label { font-size: 0.75rem; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: 0.03em; }
.llm-report { font-size: 0.88rem; margin-top: 0.35rem; }
.llm-flags { margin-top: 0.6rem; display: flex; flex-direction: column; gap: 0.35rem; }
.flag-row { font-size: 0.8rem; background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 0.4rem 0.6rem; }
.flag-name { font-weight: 600; }
.flag-tag { color: var(--accent); font-weight: 600; }
.flag-conf { color: var(--text-dim); font-size: 0.75rem; }
.flag-note { color: var(--text-dim); font-size: 0.78rem; margin-top: 0.15rem; }
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

TABS_SCRIPT = """
function showLeague(id) {
  document.querySelectorAll('.league-panel').forEach(function(p) {
    p.style.display = (p.id === id ? 'block' : 'none');
  });
  document.querySelectorAll('.tab-btn').forEach(function(b) {
    b.classList.toggle('active', b.dataset.league === id);
  });
  if (history.replaceState) { history.replaceState(null, '', '#' + id); }
}
(function() {
  var btns = document.querySelectorAll('.tab-btn');
  btns.forEach(function(b) {
    b.addEventListener('click', function() { showLeague(b.dataset.league); });
  });
  var initial = location.hash.slice(1);
  var valid = initial && document.getElementById(initial);
  if (btns.length) { showLeague(valid ? initial : btns[0].dataset.league); }
})();
"""


def render_html(reports, generated_at, password_hash=None):
    ts = generated_at.strftime("%d.%m.%Y %H:%M UTC")
    countdown = _next_22_countdown(generated_at)

    tabs_html = "".join(
        f'<button class="tab-btn" data-league="league-{i}">{_esc(r.get("name", "?"))}</button>'
        for i, r in enumerate(reports)
    )
    panels_html = ("".join(_league_panel(r, f"league-{i}") for i, r in enumerate(reports))
                  or "<p class='empty'>Keine Ligen konfiguriert.</p>")

    body_inner = f"""<header class="top">
  <h1>⚽ Kickbase-Zentrale</h1>
  <div class="ts">Stand: {ts} · MW-Update in ~{countdown}</div>
  <div class="tabs">{tabs_html}</div>
</header>
<main>
  {panels_html}
  <footer>Automatisch generiert · faktenbasiert, keine Blackbox-Scores</footer>
</main>
<script>{TABS_SCRIPT}</script>"""

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
