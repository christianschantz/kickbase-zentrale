"""
Trainer-/Aufstellungsmodul - GRUNDGERÜST (SPEC_forecast_coach_scoring.md
Punkt 6, Priorität 6 - "soll perspektivisch der Hauptteil der App werden",
bis zum ersten Spieltag laut Spec selbst "realistisch nur im Grundgerüst
machbar"). Deckt Abschnitt 6.1 (erwartete Punkte je Spieler) und 6.3
(Aufstellungsoptimierung) ab. NICHT enthalten (explizit "Woche 2" laut Spec,
brauchen echte Spieltagsdaten zur Kalibrierung): 6.2 (Punktetyp-Streuung für
situationsabhängige Aufstellung), 6.4 (Team-KPI + ligaweite Prognosetabelle),
6.5 (Rückkopplungs-/Lern-Protokollierung).

**Lücke geschlossen (2026-08-05, SPEC_lineup_verified.md)**: `GET /v4/leagues
/{id}/lineup` liefert den kompletten Kader inkl. `lo` (Slot 0-10 in der
Startelf, fehlt bei Bankspielern) - live verifiziert. `current_lineup_status()`
und `suggest_swaps()` nutzen das für echte Wechselvorschläge ggü. der
tatsächlich gesetzten Elf. **Kein POST**: `/lineup` (setzt die Aufstellung)
wird bewusst nicht implementiert - würde den echten Kickbase-Kader verändern,
gehört laut Spec explizit nicht in den automatischen Lauf.

**Formationen unverifiziert**: die 7 Formationen unten sind allgemeines
Fantasy-Football-Wissen, nicht gegen einen Kickbase-Endpoint geprüft (kein
bekannter Kandidat dafür) - bei Bedarf anpassen.

E[Punkte] = Basisniveau × Einsatzfaktor × Gegnerfaktor × Spielverlaufsfaktor
- Basisniveau: scoring.form_raw() (aktuell = reiner Saisonschnitt, da
  CURRENT_SEASON_DAYS=0 in der Saisonvorbereitung)
- Einsatzfaktor: Kickbase-Status × Einsatz-Indikator (STATUS_PENALTY × PROB_SCORE)
- Gegnerfaktor: POSITIONSABHÄNGIG aus der Sieg-Wahrscheinlichkeit (`ease` aus
  main.py, bereits vorhanden) - ein Sieg nützt Abwehrspielern (Zu-Null-
  Prämien) stärker als Angreifern, Torhüter punkten auch im Underdog-Spiel
  über Paraden (Erstkalibrierung, noch nicht gegen echte Spieltage geprüft)
- Spielverlaufsfaktor: aus llm_insights.matchday_outlook (beneficiary_
  positions) falls vorhanden, sonst neutral (1.0) - "später über
  Rückkopplung verfeinert" (6.5, hier noch nicht gebaut)
"""

from scoring import form_raw, STATUS_PENALTY, PROB_SCORE

POS_NAMES = {1: "TW", 2: "ABW", 3: "MF", 4: "ANG"}

FORMATIONS = {
    "3-4-3": {"TW": 1, "ABW": 3, "MF": 4, "ANG": 3},
    "3-5-2": {"TW": 1, "ABW": 3, "MF": 5, "ANG": 2},
    "4-3-3": {"TW": 1, "ABW": 4, "MF": 3, "ANG": 3},
    "4-4-2": {"TW": 1, "ABW": 4, "MF": 4, "ANG": 2},
    "4-5-1": {"TW": 1, "ABW": 4, "MF": 5, "ANG": 1},
    "5-3-2": {"TW": 1, "ABW": 5, "MF": 3, "ANG": 2},
    "5-4-1": {"TW": 1, "ABW": 5, "MF": 4, "ANG": 1},
}

# Gegnerfaktor-Spannweite je Position bei win_prob 0..1 (Erstkalibrierung -
# s. Modul-Docstring). Torhüter: flache Kurve (Paraden zählen auch im
# Underdog-Spiel). Abwehr: steilste Kurve (Zu-Null-Prämie hängt am Sieg).
OPPONENT_FACTOR_RANGE = {
    "TW": (0.90, 1.10),
    "ABW": (0.70, 1.30),
    "MF": (0.80, 1.20),
    "ANG": (0.75, 1.25),
}

MATCHDAY_BOOST = 1.15


def opponent_factor(pos, win_prob):
    lo, hi = OPPONENT_FACTOR_RANGE.get(pos, (0.85, 1.15))
    win_prob = 0.5 if win_prob is None else max(0.0, min(1.0, win_prob))
    return lo + (hi - lo) * win_prob


def matchday_factor(pos, team_name, matchday_outlook):
    """Spielverlaufsfaktor aus llm_insights.matchday_outlook - neutral (1.0,
    kein Grund) ohne Treffer (kein KI-Kontext oder nichts Passendes)."""
    for o in (matchday_outlook or []):
        match_text = o.get("match", "")
        if team_name and team_name in match_text and pos in (o.get("beneficiary_positions") or []):
            return MATCHDAY_BOOST, o.get("reason")
    return 1.0, None


def expected_points(pos, ap, ph, st, prob, win_prob, team_name=None,
                    matchday_outlook=None, current_season_days=0, mv=0):
    """
    Liefert (erwartete_punkte, {faktoren-aufschlüsselung}).

    mv: Marktwert - Basisniveau-Fallback für Spieler OHNE Kickbase-
    Punktehistorie (ap=0 und keine ph-Einträge). Ohne diesen Fallback bekämen
    z.B. gesetzte Stammspieler ohne Historie (Ligawechsler) "Basis 0" und
    würden von der Aufstellungsoptimierung systematisch unterbewertet -
    verstößt gegen den Projekt-Grundsatz "fehlende Daten ≠ schlechter
    Spieler" (CLAUDE.md). Nutzt dieselbe MW-Schätzung wie scoring.py
    (mv_implied_form, 0..0.7), auf eine Punkte-Größenordnung skaliert
    (Faktor 130 ≈ Punkteschnitt eines starken Stammspielers).
    """
    from scoring import mv_implied_form

    # bool(ph) allein reicht nicht - ph kann nur zukünftige, noch nicht
    # gespielte Spieltage enthalten (hp=false); erst ein Eintrag mit hp=true
    # ist ein echter Beleg gespielter Historie.
    has_data = bool(ap) or any(e.get("hp") and e.get("p") is not None for e in (ph or []))
    if has_data:
        basis = form_raw(ap or 0, ph, current_season_days)
    else:
        basis = mv_implied_form(mv or 0) * 130

    einsatz = STATUS_PENALTY.get(st, 0.5) * PROB_SCORE.get(prob, 0.5)
    gegner = opponent_factor(pos, win_prob)
    verlauf, verlauf_grund = matchday_factor(pos, team_name, matchday_outlook)

    erwartung = basis * einsatz * gegner * verlauf
    return round(erwartung, 1), {
        "basis": round(basis, 1), "basis_geschaetzt": not has_data,
        "einsatzfaktor": round(einsatz, 2),
        "gegnerfaktor": round(gegner, 2), "spielverlaufsfaktor": round(verlauf, 2),
        "spielverlauf_grund": verlauf_grund,
    }


def _best_eleven_for_formation(players_by_pos, formation):
    """
    Greedy: pro Position die Top-N nach erwarteten Punkten. Bei fixen
    Positions-Slots (keine Punkte-Fungibilität über Positionen hinweg, jede
    Formation schreibt die Anzahl je Position fest vor) ist die Position-für-
    Position-Bestenauswahl zugleich das globale Optimum für die
    Formation - kein komplexerer Solver nötig.
    """
    xi, total = [], 0.0
    for pos, count in formation.items():
        pool = sorted(players_by_pos.get(pos, []), key=lambda p: -p["expected_points"])
        chosen = pool[:count]
        if len(chosen) < count:
            return None  # Kader hat nicht genug Spieler dieser Position
        xi.extend(chosen)
        total += sum(p["expected_points"] for p in chosen)
    return {"formation": formation, "xi": xi, "total_points": round(total, 1)}


def optimize_lineup(squad_with_points):
    """
    squad_with_points: Liste von Dicts mit mind. id/name/pos/expected_points.
    Liefert {"formations": {name: {...}}, "best": name, "best_total": float}
    - beste Formation nach Gesamtpunkten, Alternativen mit allen Ergebnissen
    zum Differenzvergleich.
    """
    by_pos = {}
    for p in squad_with_points:
        by_pos.setdefault(p["pos"], []).append(p)

    results = {}
    for name, formation in FORMATIONS.items():
        r = _best_eleven_for_formation(by_pos, formation)
        if r:
            results[name] = r

    if not results:
        return {"formations": {}, "best": None, "best_total": None}

    best_name = max(results, key=lambda k: results[k]["total_points"])
    return {"formations": results, "best": best_name,
           "best_total": results[best_name]["total_points"]}


def current_lineup_status(lineup_raw, squad_with_points):
    """
    Ordnet GET /lineup (echte Startelf über `lo`) die bereits berechneten
    erwarteten Punkte zu (Merge über die Spieler-ID). Liefert
    {"xi": [...], "bench": [...], "empty_slots": int} - xi sortiert nach Slot.
    """
    points_by_id = {p["id"]: p for p in squad_with_points}
    items = lineup_raw.get("it", []) or []

    def _enrich(p):
        pid = str(p.get("i"))
        extra = points_by_id.get(pid, {})
        return {
            "id": pid, "name": p.get("n", "?"),
            "pos": POS_NAMES.get(p.get("pos"), "?"),
            "lo": p.get("lo"), "os": p.get("os"), "ht": p.get("ht"),
            "expected_points": extra.get("expected_points", 0.0),
            "ep_factors": extra.get("ep_factors", {}),
        }

    xi = sorted((_enrich(p) for p in items if p.get("lo") is not None),
               key=lambda p: p["lo"])
    bench = [_enrich(p) for p in items if p.get("lo") is None]
    return {"xi": xi, "bench": bench, "empty_slots": max(0, 11 - len(xi))}


def suggest_swaps(status, max_suggestions=3):
    """
    Vergleicht jeden Startelf-Spieler mit dem stärksten Bankspieler DERSELBEN
    Position (die Formation ist fix, kein Positionswechsel innerhalb eines
    Tauschs möglich) und schlägt Tausche mit positiver erwarteter
    Punktedifferenz vor, absteigend sortiert.
    """
    bench_by_pos = {}
    for b in status["bench"]:
        bench_by_pos.setdefault(b["pos"], []).append(b)
    for pos in bench_by_pos:
        bench_by_pos[pos].sort(key=lambda p: -p["expected_points"])

    suggestions = []
    for starter in status["xi"]:
        candidates = bench_by_pos.get(starter["pos"], [])
        if not candidates:
            continue
        best = candidates[0]
        diff = best["expected_points"] - starter["expected_points"]
        if diff > 0:
            suggestions.append({"slot": starter["lo"], "out": starter,
                               "in": best, "diff": round(diff, 1)})
    suggestions.sort(key=lambda s: -s["diff"])
    return suggestions[:max_suggestions]


def missing_positions(status, lineup_result):
    """
    Leere Slots (Spec 5.2/6.3): vergleicht die AKTUELLE Startelf-Besetzung je
    Position mit der vom Optimierer empfohlenen Formation (die tatsächlich in
    der App eingestellte Formation ist über GET /lineup nicht auslesbar) und
    liefert die Positionen, die dafür noch fehlen. Leer, wenn 11 Slots belegt
    sind oder keine Optimierer-Empfehlung vorliegt.
    """
    if status["empty_slots"] == 0 or not lineup_result.get("best"):
        return {}
    target = lineup_result["formations"][lineup_result["best"]]["formation"]
    have = {}
    for p in status["xi"]:
        have[p["pos"]] = have.get(p["pos"], 0) + 1
    missing = {}
    for pos, need in target.items():
        gap = need - have.get(pos, 0)
        if gap > 0:
            missing[pos] = gap
    return missing
