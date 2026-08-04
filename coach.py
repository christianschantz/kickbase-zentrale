"""
Trainer-/Aufstellungsmodul - GRUNDGERÜST (SPEC_forecast_coach_scoring.md
Punkt 6, Priorität 6 - "soll perspektivisch der Hauptteil der App werden",
bis zum ersten Spieltag laut Spec selbst "realistisch nur im Grundgerüst
machbar"). Deckt Abschnitt 6.1 (erwartete Punkte je Spieler) und 6.3
(Aufstellungsoptimierung) ab. NICHT enthalten (explizit "Woche 2" laut Spec,
brauchen echte Spieltagsdaten zur Kalibrierung): 6.2 (Punktetyp-Streuung für
situationsabhängige Aufstellung), 6.4 (Team-KPI + ligaweite Prognosetabelle),
6.5 (Rückkopplungs-/Lern-Protokollierung).

**Bekannte Lücke**: kein Vergleich mit der AKTUELL GESETZTEN Aufstellung
(Spec 6.3 "Tausch X gegen Y bringt +6,4 Punkte") - es gibt keinen
verifizierten Kickbase-Endpoint für die im Spiel hinterlegte Startelf (der
Community-Kandidat `/lineupex` lieferte beim `get_squad`-Verifizieren leer
zurück, s. CLAUDE.md). Der Optimierer liefert nur die BESTE Elf je Formation.

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
