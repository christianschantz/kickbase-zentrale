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


# Teamfaktor-Spannweite (Spec-Ergänzung 2026-08-05, Fair Value Punkt 1.2) -
# bewusst enger als der Gegnerfaktor: Klassestärke ist ein längerfristiges
# Signal, soll nicht so stark ausschlagen wie die Sieg-WK der nächsten Partie.
TEAM_FACTOR_RANGE = (0.80, 1.20)


def _punktebasis(ap, ph, mv, current_season_days=0):
    """
    Gemeinsame Basisniveau-Logik für expected_points() und fair_value():
    Form/Saisonschnitt, mit MW-Schätzung als Fallback für Spieler OHNE
    Kickbase-Punktehistorie (ap=0 und keine ph-Einträge mit hp=true) - sonst
    bekämen gesetzte Stammspieler ohne Historie (Ligawechsler) "Basis 0" und
    würden systematisch unterbewertet, verstößt gegen den Projekt-Grundsatz
    "fehlende Daten ≠ schlechter Spieler" (CLAUDE.md). Nutzt dieselbe
    MW-Schätzung wie scoring.py (mv_implied_form, 0..0.7), auf eine
    Punkte-Größenordnung skaliert (Faktor 130 ≈ Punkteschnitt eines starken
    Stammspielers). Liefert (basis, has_data).
    """
    from scoring import mv_implied_form
    has_data = bool(ap) or any(e.get("hp") and e.get("p") is not None for e in (ph or []))
    if has_data:
        return form_raw(ap or 0, ph, current_season_days), True
    return mv_implied_form(mv or 0) * 130, False


def team_factor(team_strength):
    lo, hi = TEAM_FACTOR_RANGE
    team_strength = 0.5 if team_strength is None else max(0.0, min(1.0, team_strength))
    return lo + (hi - lo) * team_strength


def expected_points(pos, ap, ph, st, prob, win_prob, team_name=None,
                    matchday_outlook=None, current_season_days=0, mv=0):
    """Liefert (erwartete_punkte, {faktoren-aufschlüsselung})."""
    basis, has_data = _punktebasis(ap, ph, mv, current_season_days)
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


def fair_value(pos, mv, ap, ph, st, prob, win_prob, team_strength, curve,
               current_season_days=0):
    """
    "Was ist der Spieler wert" statt "was wird er kosten" (SPEC_gebote_ki_
    team_KOMPLETT.md Punkt 1.2 - die bisherige Gebotslogik leitete den Preis
    fast nur aus der MW-Prognose ab, die ganze Bewertungstiefe floss nur
    indirekt über den Score in den Aufschlag ein).

    **Bugfix (2026-08-05, beim ersten echten Testlauf gefunden)**: die erste
    Fassung multiplizierte Einsatz/Gegner/Team VOR der Kurven-Inversion auf
    die Punktebasis - das vermischt zwei verschiedene Skalen. Die Preiskurve
    (scoring.fit_price_curve) ist auf den ROHEN Saisonschnitt `ap` kalibriert
    (Median-MW je Punkte-Dezil über die ganze Liga-Population). Der
    Einsatzfaktor (STATUS_PENALTY x PROB_SCORE) ist aber ein reiner
    Abwärts-Malus OHNE Gegenstück >1.0 (selbst ein fitter "blau"-Spieler
    erreicht bestenfalls 1.0) - im Schnitt über die Population liegt er klar
    unter 1.0. Wurde er VOR der Inversion mit reingerechnet, landete die
    Punktebasis fast immer unterhalb der niedrigsten Dezil-Stützstelle und
    lief in den Kurven-Boden - live beobachtet: ein Spieler mit Ø 89 Punkten
    und 100% Preis-Leistung bekam denselben Fair-Value-Bodenwert wie zwei
    Ligawechsler ohne jede Punktehistorie. Fix: die Kurve wird auf der
    ROHEN Punktebasis invertiert (dieselbe Skala, auf der sie gefittet wurde
    - "was kostet ein Spieler mit diesem Saisonschnitt üblicherweise"), erst
    DANACH wird der resultierende Marktwert mit Einsatz x Gegner x Team
    skaliert (linear auf den MW-Betrag statt nonlinear durch die Kurve
    verstärkt/verzerrt zu werden). Gegner/Team sind bei win_prob=0.5 bzw.
    team_strength=0.5 symmetrisch auf 1.0 zentriert (s. opponent_factor/
    team_factor) - nur der Einsatzfaktor bleibt bewusst ein reiner Malus,
    das ist inhaltlich richtig (ein angeschlagener Spieler ist gerade JETZT
    weniger wert als sein Saisonschnitt nahelegt).

    Liefert (fair_value_mv, punktebasis_vor_kontextanpassung) - (None, None)
    ohne Kurve oder ohne Kurventreffer.
    """
    if not curve:
        return None, None
    basis, _ = _punktebasis(ap, ph, mv, current_season_days)

    from scoring import invert_price_curve
    base_fv = invert_price_curve(basis, curve)
    if base_fv is None:
        return None, None

    einsatz = STATUS_PENALTY.get(st, 0.5) * PROB_SCORE.get(prob, 0.5)
    gegner = opponent_factor(pos, win_prob)
    team = team_factor(team_strength)
    fv = base_fv * einsatz * gegner * team
    return round(fv), round(basis, 1)


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


def detect_self_play_conflicts(squad, matcher):
    """
    SPEC_gebote_ki_team_KOMPLETT.md Punkt 2.2: "eigene Spieler, die
    gegeneinander spielen" - hier ist eine Aufstellungsentscheidung nötig,
    weil sich die Ergebnisse gegenseitig ausschließen, und das ist laut Spec
    "algorithmisch erkennbar" statt der KI überlassen zu werden. Vergleicht
    den nächsten Gegner jedes Kaderspielers (aus der bereits vorhandenen
    Fixture-/Quoten-Zuordnung `opponents`, mit demselben Fuzzy-Matcher, der
    im Projekt überall sonst für Teamnamen-Abgleich genutzt wird) gegen die
    eigenen Teamnamen der übrigen Kaderspieler - kein neuer API-Call nötig.
    Heuristik: die Fixture-Quelle liefert Anzeige-Strings ("Team (58% Sieg-
    WK)"), der Teamname selbst kommt aus einer anderen Quelle (Kickbase
    players/{id}.tn) - beide können leicht abweichen, deshalb Fuzzy-Match
    statt exaktem Stringvergleich.
    """
    teams = {s["team"]: s for s in squad if s.get("team")}
    team_names = list(teams.keys())
    seen, conflicts = set(), []
    for s in squad:
        if not s.get("team") or not s.get("opponents"):
            continue
        opp_clean = s["opponents"][0].split(" (")[0].strip()
        matched = matcher(opp_clean, team_names)
        if not matched or matched == s["team"]:
            continue
        other = teams.get(matched)
        if not other or other["name"] == s["name"]:
            continue
        pair = tuple(sorted((s["name"], other["name"])))
        if pair in seen:
            continue
        seen.add(pair)
        conflicts.append(
            f"{s['name']} ({s['team']}) trifft direkt auf {other['name']} ({other['team']}) "
            f"- beide eigene Spieler, nur einer kann von einem Sieg profitieren"
        )
    return conflicts


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
