"""
Transparentes Spieler-Scoring (0-100) mit Einzelkomponenten und
Datenlage-Erkennung.

Wichtig: Ein Spieler ohne Kickbase-Punktehistorie (Neuzugang aus anderer
Liga) wird NICHT abgestraft. Erkennung: ap und p fehlen/0. Dann:
- Form wird aus dem Marktwert geschätzt (die Community bepreist erwartete
  Leistung - ein 8-Mio-Neuzugang ist kein 20-Punkte-Spieler)
- Preis-Leistung wird neutral (0.5) gesetzt statt 0
- data_complete=False wird ausgewiesen, damit das Briefing es transparent macht

Zusätzlich wird ein "sporting_core" berechnet (Score OHNE Momentum):
Grundlage für die Kader-Verdikte, damit ein Stammspieler Stamm bleibt,
egal was sein Marktwert gerade macht.
"""

DEFAULT_WEIGHTS = {
    "value_efficiency": 0.22,
    "momentum": 0.26,
    "availability": 0.20,
    "fixtures": 0.14,
    "form": 0.18,
}

PROB_SCORE = {1: 1.0, 2: 0.75, 3: 0.5, 4: 0.25, 5: 0.0}
STATUS_PENALTY = {0: 1.0, 1: 0.6, 2: 0.25}


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def mv_implied_form(mv):
    """MW-basierte Leistungserwartung: 1M -> schwach, 10M+ -> Topspieler."""
    return _clamp((mv - 1_000_000) / 9_000_000)


def score_player(market_entry, details, fix_ease, weights=None):
    w = weights or DEFAULT_WEIGHTS

    mv = details.get("mv") or market_entry.get("mv", 0)
    ap = market_entry.get("ap") or details.get("ap") or 0
    total_p = market_entry.get("p") or details.get("p") or 0
    tfhmvt = details.get("tfhmvt", 0) or 0

    # Datenlage: keine Punktehistorie vorhanden?
    data_complete = bool(ap or total_p)

    if data_complete:
        ppm = (ap / (mv / 1_000_000)) if mv > 0 else 0
        value_eff = _clamp((ppm - 3) / 12)
        form = _clamp((ap - 20) / 60)
    else:
        value_eff = 0.5                # neutral, nicht bestrafen
        form = mv_implied_form(mv)     # Community-Preis als Leistungs-Proxy

    daily_pct = (tfhmvt / mv) if mv > 0 else 0
    momentum = _clamp((daily_pct + 0.015) / 0.03)

    st = details.get("st", 0)
    prob = details.get("prob", 3)
    availability = STATUS_PENALTY.get(st, 0.5) * PROB_SCORE.get(prob, 0.5)

    components = {
        "value_efficiency": value_eff,
        "momentum": momentum,
        "availability": availability,
        "fixtures": fix_ease,
        "form": form,
    }
    total = sum(components[k] * w[k] for k in w) * 100

    # Sportlicher Kern: gleiche Komponenten OHNE Momentum, neu normiert.
    core_keys = [k for k in w if k != "momentum"]
    core_weight = sum(w[k] for k in core_keys)
    sporting_core = sum(components[k] * w[k] for k in core_keys) / core_weight

    return round(total, 1), components, {
        "sporting_core": round(sporting_core, 3),
        "data_complete": data_complete,
        "daily_pct": daily_pct,
    }


def player_reliability_profile(details):
    """
    "Punktetyp": punktet der Spieler zuverlässig unabhängig vom Spielausgang
    seines Teams (Rohpunkte-Typ) oder vor allem dann, wenn sein Team gewinnt
    (Scorer-Typ)? Quelle: 'ph' (Punkte je Spieltag, chronologisch bis zum
    aktuellen 'day') + 'mdsum' (Ergebnisse je Spieltag, gleiche Tage) aus
    get_player_details - beide Felder verifiziert, aber nur ~3-5 Spiele
    Stichprobe (Basis-Signal, keine Statistik). Funktioniert auch in der
    Sommerpause, weil 'mdsum'/'ph' die letzten Spiele der VORSAISON zeigen.
    """
    tid = str(details.get("tid", "") or "")
    day = details.get("day")
    ph = details.get("ph") or []
    mdsum = details.get("mdsum") or []
    if not tid or day is None or not ph or not mdsum:
        return None

    n = len(ph)
    by_day = {day - (n - 1 - idx): entry for idx, entry in enumerate(ph)}

    games = []
    for md in mdsum:
        if md.get("mdst") != 2:  # nur beendete Spiele zählen
            continue
        entry = by_day.get(md.get("day"))
        if not entry or not entry.get("hp") or entry.get("p") is None:
            continue
        t1, t2 = str(md.get("t1")), str(md.get("t2"))
        t1g, t2g = md.get("t1g", 0) or 0, md.get("t2g", 0) or 0
        if tid == t1:
            gf, ga = t1g, t2g
        elif tid == t2:
            gf, ga = t2g, t1g
        else:
            continue
        result = "Sieg" if gf > ga else "Niederlage" if gf < ga else "Unentschieden"
        games.append({"day": md.get("day"), "result": result, "points": entry["p"]})

    if not games:
        return None

    def _avg(res):
        pts = [g["points"] for g in games if g["result"] == res]
        return sum(pts) / len(pts) if pts else None

    return {
        "games": games,
        "avg_win": _avg("Sieg"),
        "avg_draw": _avg("Unentschieden"),
        "avg_loss": _avg("Niederlage"),
    }


RELIABLE_RATIO = 0.6  # Ø-Punkte bei Niederlage >= 60% von Ø bei Sieg -> "verlässlich"


def punktetyp_label(profile):
    """
    Klartext-Einordnung des Punktetyps. Rückgabe (reliable, text):
    reliable=True -> Rohpunkte-Typ (punktet auch bei Niederlagen ordentlich),
    reliable=False -> Scorer-Typ (Punkte hängen stark am eigenen Sieg),
    (None, None) -> zu wenig Datenbasis (z.B. keine Niederlage in der
    Stichprobe der letzten Spiele).
    """
    if not profile or profile["avg_win"] is None or profile["avg_loss"] is None:
        return None, None
    if profile["avg_win"] <= 0:
        return None, None
    reliable = (profile["avg_loss"] / profile["avg_win"]) >= RELIABLE_RATIO
    label = "Rohpunkte-Typ" if reliable else "Scorer-Typ"
    text = (f"{label}: Ø {profile['avg_win']:.0f} P. bei Sieg, "
            f"Ø {profile['avg_loss']:.0f} P. bei Niederlage "
            f"({len(profile['games'])} Spiele, kleine Stichprobe)")
    return reliable, text


def explain(components, meta=None):
    labels = {
        "value_efficiency": "Preis-Leistung",
        "momentum": "MW-Momentum",
        "availability": "Einsatz-WK",
        "fixtures": "Spielplan",
        "form": "Form/Schnitt",
    }
    s = " | ".join(f"{labels[k]}: {v:.0%}" for k, v in components.items())
    if meta and not meta.get("data_complete", True):
        s += "  [⚠️ keine Punktehistorie - Form aus MW geschätzt]"
    return s
