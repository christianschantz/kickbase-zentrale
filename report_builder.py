"""
Dashboard-Redesign (2026-07-31): trennt Analyse von Darstellung. main.py
liefert weiterhin squad_classified/compared/league_board wie bisher; dieses
Modul verdichtet das zu den entscheidungsorientierten Bausteinen, die
html_report.py rendert - KPIs, Handlungsleiste, Risiko-Banner, Watchlist.

Zusätzlich: JSON-Persistenz je Liga/Tag (`data/report_<liga>_<datum>.json`)
löst das History-Problem (B4) nebenbei - der Tagesvergleich wird zu einem
einfachen Dict-Diff gegen die letzte gespeicherte Datei. Bewusst NUR ein
schlanker Ausschnitt wird persistiert (nicht der volle league_board mit
~80 Einträgen je Liga) - sonst wächst das Repo jeden Tag unnötig.
"""

import glob
import json
import os
import re

ACTION_LIMIT = 5
DATA_DIR = "data"


def _slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "liga"


# ---------- KPIs ----------

def compute_kpis(budget, squad_classified, max_squad, squad_slots, capacity,
                 net_value, max_debt, ranking, my_user_id):
    team_value = sum(s["mv"] for s in squad_classified)
    delta_24h = sum(s["tfhmvt"] for s in squad_classified)

    league_rank = league_points = points_gap = None
    season_started = False
    if ranking:
        us = ranking.get("us", []) or []
        season_started = any((u.get("sp") or 0) > 0 for u in us)
        if season_started:
            me = next((u for u in us if str(u.get("i")) == str(my_user_id)), None)
            if me:
                league_rank = me.get("spl")
                league_points = me.get("sp")
                leader = min(us, key=lambda u: u.get("spl", 999))
                points_gap = (leader.get("sp") or 0) - (league_points or 0)

    return {
        "team_value": team_value,
        "team_value_delta_24h": delta_24h,
        "capacity": capacity,
        "budget": budget,
        "net_value": net_value,
        "max_debt": max_debt,
        "league_rank": league_rank,
        "league_points": league_points,
        "points_gap_to_leader": points_gap,
        "season_started": season_started,
        "squad_slots": squad_slots,
        "max_squad": max_squad,
    }


# ---------- Handlungsleiste ----------

def build_actions(compared, squad_classified, max_items=ACTION_LIMIT):
    """
    Max. `max_items` Einträge, Priorität: klare Kauf-/Nicht-Kauf-Empfehlung >
    ablaufende Marktspieler mit Kaufbedarf > eigene Verkaufskandidaten.
    """
    actions, included_ids = [], set()

    for m in compared:
        if "KLARE KAUFEMPFEHLUNG" in m["team_verdict"]:
            b = m["bid"]
            hrs = (m.get("expiry_s") or 0) / 3600 or None
            actions.append({
                "icon": "🎯", "kind": "bid", "urgent": bool(hrs and hrs < 6),
                "text": f"Gebot auf {m['name']} ({m['team']})",
                "amount": b["recommended_bid"], "deadline_hours": hrs,
                "reason": f"Score {m['score']} · Kaufkraft "
                         f"{'reicht' if m['affordable'] else 'reicht NICHT'}",
            })
            included_ids.add(m["id"])
        elif "EHER NICHT" in m["team_verdict"]:
            actions.append({
                "icon": "⛔", "kind": "skip", "urgent": False,
                "text": f"{m['name']} ({m['team']}) eher nicht kaufen",
                "amount": None, "deadline_hours": None,
                "reason": "aktuell nicht finanzierbar",
            })
            included_ids.add(m["id"])

    # Bei freien Kaderplätzen bekommt praktisch jeder Marktspieler die
    # Headline "KAUFEN (freier Kaderplatz)" - das ist keine Empfehlung,
    # nur "nicht abgelehnt". Deshalb hier auf die 2 bestbewerteten
    # ablaufenden Kandidaten begrenzen (Score >= 60), statt die Leiste mit
    # gleichlautenden Einträgen zu fluten.
    MIN_EXPIRING_SCORE, MAX_EXPIRING = 60, 2
    expiring_added = 0
    for m in compared:
        if m["id"] in included_ids or len(actions) >= max_items:
            continue
        if expiring_added >= MAX_EXPIRING:
            break
        hrs = (m.get("expiry_s") or 0) / 3600
        if hrs and hrs < 24 and m["score"] >= MIN_EXPIRING_SCORE and "KEIN BEDARF" not in m["team_verdict"]:
            b = m["bid"]
            actions.append({
                "icon": "⏰", "kind": "expiring", "urgent": hrs < 6,
                "text": f"{m['name']} ({m['team']}) läuft ab",
                "amount": b["recommended_bid"], "deadline_hours": hrs,
                "reason": f"Score {m['score']} · " + m["team_verdict"].split(" | ")[0],
            })
            included_ids.add(m["id"])
            expiring_added += 1

    for s in squad_classified:
        if len(actions) >= max_items:
            break
        if s["verdict"] == "VERKAUFEN":
            actions.append({
                "icon": "🔻", "kind": "sell", "urgent": False,
                "text": f"Verkauf prüfen: {s['name']} ({s['pos']})",
                "amount": s["mv"], "deadline_hours": None,
                "reason": s["reasons"][0] if s["reasons"] else "",
            })

    return actions[:max_items]


def build_squad_action_items(squad_classified):
    """Nur VERKAUFEN/BEOBACHTEN - STAMM/HALTEN sind Bestätigungen ohne Handlungsbedarf."""
    order = {"VERKAUFEN": 0, "BEOBACHTEN": 1}
    items = [s for s in squad_classified if s["verdict"] in order]
    return sorted(items, key=lambda x: order[x["verdict"]])


# ---------- Watchlist ----------

def build_targets(board, max_per_group=5):
    """
    Spieler mit in_both (Top 5 Qualität UND Deals) plus Top 3 aus Liste A je
    Position, ohne EIGEN, gruppiert nach Erreichbarkeit (MARKT/MITSPIELER/FREI).
    """
    seen, candidates = set(), []
    for entries in board.get("quality", {}).values():
        for i, e in enumerate(entries):
            if e["status"] == "EIGEN" or e["id"] in seen:
                continue
            if e.get("in_both") or i < 3:
                seen.add(e["id"])
                candidates.append(e)

    groups = {"MARKT": [], "MITSPIELER": [], "FREI": []}
    for e in candidates:
        if e["status"] in groups:
            groups[e["status"]].append(e)
    for k in groups:
        groups[k].sort(key=lambda x: -x["quality_score"])
        groups[k] = groups[k][:max_per_group]
    return groups


def build_mitspieler_appendix(board, max_items=4):
    """
    Transfermodul-Umbau (Spec 5.1): EIN Modul "Transfermarkt" statt zwei
    getrennter - Tagesmarkt (main.py/compared) ist der Hauptteil auf der
    Startseite, die große positionsweise Transferziel-Liste (build_targets)
    entfällt dort und wandert in die Vertiefung. Nur ein kompakter Anhang
    bleibt vorne: die 3-4 besten ERREICHBAREN Spieler aus fremden Kadern
    (Top nach Qualitäts-Score, unabhängig von Position, ohne EIGEN/Duplikate).
    """
    seen, candidates = set(), []
    for entries in board.get("quality", {}).values():
        for e in entries:
            if e["status"] == "MITSPIELER" and e["id"] not in seen:
                seen.add(e["id"])
                candidates.append(e)
    candidates.sort(key=lambda x: -x["quality_score"])
    return candidates[:max_items]


# ---------- Risiko-Banner ----------

def build_risks(capacity, net_value, budget, compared, has_fixtures):
    risks = []
    squad_value = net_value - budget
    if squad_value > 0:
        buffer_ratio = capacity / squad_value
        if buffer_ratio < 0.05:
            risks.append({
                "level": "warn", "icon": "⚠️",
                "text": f"Kaufkraft-Puffer nur noch {capacity:,.0f} € "
                       f"({buffer_ratio:.0%} des Kaderwerts) - nah an der 33%-Kreditgrenze",
            })

    club_blocked = [m for m in compared if "VEREINSLIMIT" in m["team_verdict"]]
    if club_blocked:
        names = ", ".join(m["name"] for m in club_blocked[:3])
        risks.append({
            "level": "warn", "icon": "⚠️",
            "text": f"Vereinslimit erreicht bei Kaufkandidat(en): {names}",
        })

    if not has_fixtures:
        risks.append({
            "level": "info", "icon": "ℹ️",
            "text": "Kein Spielplan verfügbar - Spielplan-Komponente lief neutral",
        })

    return risks


# ---------- Season-Phase ----------

def season_phase(kpis):
    return "laufend" if kpis.get("season_started") else "vorbereitung"


# ---------- JSON-Persistenz + Tagesvergleich ----------

def _trim_for_persistence(report):
    """Schlanker Ausschnitt für die Snapshot-Datei - nur was der Diff braucht."""
    return {
        "league": report["league"],
        "kpis": report["kpis"],
        "squad": [{"id": s["id"], "name": s["name"], "pos": s["pos"], "mv": s["mv"],
                   "verdict": s["verdict"], "fit": s.get("fit")}
                  for s in report["squad_classified"]],
        "market": [{"id": m["id"], "name": m["name"], "team": m["team"], "mv": m["mv"],
                    "headline": m["team_verdict"].split(" | ")[0]}
                   for m in report["market"]],
    }


def save_report(report, out_dir=DATA_DIR):
    os.makedirs(out_dir, exist_ok=True)
    slug = _slugify(report["league"]["name"])
    date = report["league"]["generated_at"][:10]
    path = os.path.join(out_dir, f"report_{slug}_{date}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_trim_for_persistence(report), f, ensure_ascii=False, indent=2)
    return path


def load_previous_snapshot(league_name, before_date, out_dir=DATA_DIR):
    """Jüngste gespeicherte Datei VOR before_date (YYYY-MM-DD) - None wenn keine existiert."""
    slug = _slugify(league_name)
    candidates = []
    for path in glob.glob(os.path.join(out_dir, f"report_{slug}_*.json")):
        m = re.search(r"_(\d{4}-\d{2}-\d{2})\.json$", path)
        if m and m.group(1) < before_date:
            candidates.append((m.group(1), path))
    if not candidates:
        return None
    candidates.sort()
    with open(candidates[-1][1], encoding="utf-8") as f:
        return json.load(f)


def diff_reports(report, previous_snapshot):
    """
    Tagesvergleich (B4): neue Marktspieler, Verdikt-/Statuswechsel,
    Teamwert-Änderung ggü. dem letzten Snapshot. None wenn kein Vortag
    vorliegt (erster Lauf).
    """
    if not previous_snapshot:
        return None

    prev_squad = {s["id"]: s for s in previous_snapshot.get("squad", [])}
    prev_market_ids = {m["id"] for m in previous_snapshot.get("market", [])}

    verdict_changes, status_changes = [], []
    for s in report["squad_classified"]:
        prev = prev_squad.get(s["id"])
        if not prev:
            continue
        if prev["verdict"] != s["verdict"]:
            verdict_changes.append({"name": s["name"], "from": prev["verdict"], "to": s["verdict"]})
        if prev.get("fit") is False and s.get("fit") is True:
            status_changes.append(f"{s['name']} wieder fit")
        elif prev.get("fit") is True and s.get("fit") is False:
            status_changes.append(f"{s['name']} neu angeschlagen/verletzt")

    new_market = [m["name"] for m in report["market"] if m["id"] not in prev_market_ids][:10]

    prev_tv = previous_snapshot.get("kpis", {}).get("team_value")
    team_value_change = (report["kpis"]["team_value"] - prev_tv) if prev_tv is not None else None

    return {
        "since_date": previous_snapshot["league"]["generated_at"][:10],
        "verdict_changes": verdict_changes,
        "status_changes": status_changes,
        "new_market": new_market,
        "team_value_change": team_value_change,
    }
