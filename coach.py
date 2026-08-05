"""
Trainer-/Aufstellungsmodul - GRUNDGERÜST (SPEC_forecast_coach_scoring.md
Punkt 6), erweitert um Fair Value (SPEC_gebote_ki_team_KOMPLETT.md 1.2) und
**grundlegend rekalibriert** (SPEC_kalibrierung_fairvalue.md, 2026-08-05).

E[Punkte] = Basis × Einsatzfaktor × Gegnerfaktor × Formfaktor × Spielverlaufsfaktor
            + Zu-Null-Bonus (nur TW/ABW)

**Grundregel (SPEC_kalibrierung_fairvalue.md Abschnitt 0, zentraler Bugfix)**:
Ein durchschnittlicher Spieler in einer durchschnittlichen Situation MUSS
Faktor 1,0 bekommen - Faktoren verschieben nach oben und unten, sie dämpfen
nicht grundsätzlich. Die alte Fassung verletzte das: STATUS_PENALTY×PROB_SCORE
lag selbst für einen fitten "grün"-Spieler bei 0,75, für "grau" bei exakt 0,0
(PROB_SCORE[5]=0.0) - kombiniert mit einem zusätzlichen Gegnerfaktor führte
das dazu, dass die Spieltagsprognose systematisch bei ~250-450 statt den aus
`ranking.us[].pspts`/Spieltagszahl ableitbaren realen ~900 lag, UND dass in
Fair Value (die dieselbe Punktebasis in die Preiskurve invertiert) praktisch
jeder Spieler als "überbewertet" erschien. Einzige legitime Ausnahme von der
1,0-Regel: der Einsatzfaktor (wer weniger spielt, punktet real weniger) -
aber ein blauer Stammspieler bekommt dort jetzt exakt 1,0, nicht 0,75.

- Basis: reiner Anker (`ap` bzw. MW-Schätzung/Peer-Vergleichswert für
  Spieler ohne Historie) - KEINE Vermischung mit aktueller Form mehr (das
  war früher `scoring.form_raw()`s current_season_days-Blend, hier bewusst
  entfernt, um Doppelzählung mit dem neuen eigenständigen Formfaktor zu
  vermeiden, sobald current_season_days > 0 aktiviert wird)
- Einsatzfaktor: direkt an der Kickbase-Farbe verankert (EINSATZ_FACTOR)
- Gegnerfaktor: `1 + k·(Sieg-WK − Liga-Ø-Sieg-WK)`, positionsabhängiges k,
  zentriert auf die ECHTE Liga-Ø-Sieg-WK (~35-40% wegen Unentschieden als
  drittem Ausgang, NICHT 0,5 - s. fixtures.league_avg_win_prob), nicht mehr
  auf einen willkürlichen Range um den absoluten win_prob-Wert
- Formfaktor: aktuelle Form relativ zum eigenen Saisonschnitt, 0,80-1,25,
  neutral 1,0 ohne genug Spieltage (Vorsaison/<3 gespielte)
- Zu-Null-Bonus: additiver Term für TW/ABW aus der Zu-Null-Wahrscheinlichkeit
  (grobe Sieg-WK-Näherung, Erstkalibrierung)
- Spielverlaufsfaktor: aus llm_insights.matchday_outlook, sonst neutral 1,0

**Direkte Duelle (2.1)**: `adjust_for_self_play_duels()` erkennt eigene
Spieler in derselben Partie auf verschiedenen Seiten und dämpft den
ergebnisabhängigen Anteil (Gegnerfaktor-Abweichung + Zu-Null-Bonus) für
BEIDE auf die Hälfte - grobe, transparente Näherung an "der Ergebnis-Topf
wird einmal vergeben, nicht zweimal" (eine echte gemeinsame Verteilung
bräuchte eine Kovarianzrechnung, die hier bewusst nicht gebaut wird) und
weitet die ausgewiesene Bandbreite.

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
"""

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

# ---------- Einsatzfaktor (SPEC_kalibrierung_fairvalue.md 1.2) ----------
# Eigene Tabelle für die Punkteprognose - NICHT identisch mit
# scoring.STATUS_PENALTY/PROB_SCORE (die bleiben für score_player()'s
# Kader-/Tagesmarkt-Skala unangetastet, über viele Feedback-Runden
# kalibriert, s. CLAUDE.md). Direkt an der Kickbase-Farbe verankert: ein
# blauer Stammspieler MUSS Faktor 1,0 bekommen (Grundregel oben).
EINSATZ_FACTOR = {1: 1.00, 2: 0.95, 3: 0.60, 4: 0.25, 5: 0.10}
VERLETZT_CAP = 0.10  # st==2 (angeschlagen/verletzt) deckelt unabhängig von der Farbe


def einsatzfaktor(st, prob):
    factor = EINSATZ_FACTOR.get(prob, 0.5)
    if st == 2:
        factor = min(factor, VERLETZT_CAP)
    return factor


# ---------- Gegnerfaktor ----------
# k positionsabhängig (SPEC 2.2): TW/ABW reagieren am stärksten (Zu-Null-
# Prämie hängt am Sieg), MF am schwächsten (profitiert eher von
# Spielkontrolle als vom Ergebnis), ANG mittel-hoch (Torwahrscheinlichkeit
# steigt gegen schwache Gegner). Erstkalibrierung, noch nicht gegen echte
# Spieltage geprüft.
OPPONENT_K = {"TW": 0.90, "ABW": 1.00, "MF": 0.50, "ANG": 0.75}
OPPONENT_FACTOR_BOUNDS = (0.80, 1.25)


def opponent_factor(pos, win_prob, liga_avg_win_prob=0.5):
    """
    `1 + k·(Sieg-WK − Liga-Ø-Sieg-WK)`, geclippt auf OPPONENT_FACTOR_BOUNDS.
    Zentriert auf die ECHTE Liga-Ø-Sieg-WK (s. fixtures.league_avg_win_prob)
    statt auf 0,5 - Fußball hat 3 Ausgänge, die reale Ø-Sieg-WK pro Team
    liegt bei ~35-40%, nicht bei 50%.
    """
    if win_prob is None:
        return 1.0
    k = OPPONENT_K.get(pos, 0.7)
    lo, hi = OPPONENT_FACTOR_BOUNDS
    factor = 1 + k * (win_prob - liga_avg_win_prob)
    return max(lo, min(hi, factor))


# ---------- Formfaktor (neu, SPEC 1.2) ----------
FORM_FACTOR_RANGE = (0.80, 1.25)


def form_factor(ap, ph):
    """
    Aktuelle Form relativ zum eigenen Saisonschnitt - EIGENSTÄNDIGER
    Multiplikator, getrennt von der Basis (die bleibt der reine Anker).
    Ohne genug gespielte Spieltage (Vorsaison oder <3 in `ph` mit hp=true)
    neutral 1,0 - es gibt noch keine "aktuelle Form", die vom Saisonschnitt
    abweichen könnte.
    """
    if not ap or ap <= 0:
        return 1.0
    recent = [e["p"] for e in (ph or []) if e.get("hp") and e.get("p") is not None][-5:]
    if len(recent) < 3:
        return 1.0
    recent_avg = sum(recent) / len(recent)
    lo, hi = FORM_FACTOR_RANGE
    return max(lo, min(hi, recent_avg / ap))


def matchday_factor(pos, team_name, matchday_outlook):
    """Spielverlaufsfaktor aus llm_insights.matchday_outlook - neutral (1.0,
    kein Grund) ohne Treffer (kein KI-Kontext oder nichts Passendes)."""
    for o in (matchday_outlook or []):
        match_text = o.get("match", "")
        if team_name and team_name in match_text and pos in (o.get("beneficiary_positions") or []):
            return MATCHDAY_BOOST, o.get("reason")
    return 1.0, None


MATCHDAY_BOOST = 1.10  # SPEC-Tabelle: Spielverlauf 0,90-1,10


# ---------- Zu-Null-Term (neu, SPEC 2.3) ----------
# Startwerte als Stützpunkte (Sieg-WK -> P(zu Null)), linear interpoliert,
# an den Rändern abgeflacht - "Favorit daheim ~40%, ausgeglichen ~25%,
# Außenseiter auswärts ~12%". Danach über Rückkopplung kalibrieren, das ist
# hier ausdrücklich eine Schätzung mit Startwerten, kein gemessener Wert.
ZU_NULL_ANCHORS = [(0.15, 0.12), (0.40, 0.25), (0.65, 0.40)]
ZU_NULL_PRAEMIE = {"TW": 25, "ABW": 20}
# TW hat einen "Paraden-Sockel" (SPEC 2.2: "punktet auch bei Unterlegenheit
# über Paraden, ein Sockel, der nicht mit der Sieg-WK fällt") - die
# TW-Wahrscheinlichkeit fällt deshalb nie unter diesen Boden, auch als
# krasser Außenseiter.
ZU_NULL_TW_FLOOR = 0.15


def zu_null_probability(win_prob, pos):
    if win_prob is None:
        win_prob = 0.35  # grobe Liga-Ø-Näherung ohne Spielplandaten
    pts = ZU_NULL_ANCHORS
    if win_prob <= pts[0][0]:
        p = pts[0][1]
    elif win_prob >= pts[-1][0]:
        p = pts[-1][1]
    else:
        for (wp_lo, p_lo), (wp_hi, p_hi) in zip(pts, pts[1:]):
            if wp_lo <= win_prob <= wp_hi:
                t = (win_prob - wp_lo) / (wp_hi - wp_lo)
                p = p_lo + t * (p_hi - p_lo)
                break
        else:
            p = pts[-1][1]
    if pos == "TW":
        p = max(p, ZU_NULL_TW_FLOOR)
    return p


def zu_null_bonus(pos, win_prob):
    """(bonus_punkte, p_zu_null) - 0.0/None für Positionen ohne Zu-Null-Bezug."""
    if pos not in ZU_NULL_PRAEMIE:
        return 0.0, None
    p = zu_null_probability(win_prob, pos)
    return p * ZU_NULL_PRAEMIE[pos], p


# ---------- Teamfaktor (Fair Value, SPEC_gebote_ki_team_KOMPLETT.md 1.2) ----------
TEAM_FACTOR_RANGE = (0.80, 1.20)


def team_factor(team_strength):
    lo, hi = TEAM_FACTOR_RANGE
    team_strength = 0.5 if team_strength is None else max(0.0, min(1.0, team_strength))
    return lo + (hi - lo) * team_strength


# ---------- Bandbreite (Transparenz, SPEC 2.5) ----------
# Erstkalibrierung ohne echte Streuungsdaten (bräuchte Ist-Werte je
# Spieltag, die es vor dem ersten Spieltag naturgemäß nicht gibt) - grobe,
# klar als Näherung ausgewiesene Spanne um die erwarteten Punkte.
BANDWIDTH_RANGE = (0.65, 1.45)
BANDWIDTH_RANGE_DUEL = (0.55, 1.60)  # weiter bei direkten Duellen (2.1: "Streuung steigt")


def _bandwidth(erwartung, widened=False):
    lo, hi = BANDWIDTH_RANGE_DUEL if widened else BANDWIDTH_RANGE
    return (round(erwartung * lo, 1), round(erwartung * hi, 1))


def _punktebasis(ap, ph, mv, peer_estimate=None):
    """
    Reiner Anker (SPEC_kalibrierung_fairvalue.md: "Basis unverändert - das
    ist der Anker"). Aktuelle Form ist ein EIGENER Multiplikator
    (form_factor()), nicht mehr in die Basis eingemischt.

    Ohne eigene Punktehistorie (ap=0 UND keine ph-Einträge mit hp=true):
    1. `peer_estimate` (SPEC 3.2, Median aus Position+Teamstärke+Farbe via
       scoring.estimate_ap_from_peers) wenn vorhanden - realistischerer
       Vergleichswert als eine reine MW-Schätzung
    2. sonst MW-Schätzung (`mv_implied_form`, gedeckelt bei 0,7) als letzte
       Instanz - verhindert "Basis 0" für Ligawechsler ohne jeden Kontext
       (verstößt sonst gegen "fehlende Daten ≠ schlechter Spieler")

    Liefert (basis, quelle) mit quelle in {"real", "peer", "mv_estimate"}.
    """
    from scoring import mv_implied_form
    has_data = bool(ap) or any(e.get("hp") and e.get("p") is not None for e in (ph or []))
    if has_data:
        return (ap or 0), "real"
    if peer_estimate is not None:
        return peer_estimate, "peer"
    return mv_implied_form(mv or 0) * 130, "mv_estimate"


def expected_points(pos, ap, ph, st, prob, win_prob, team_name=None,
                    matchday_outlook=None, mv=0, liga_avg_win_prob=0.5,
                    peer_estimate=None):
    """Liefert (erwartete_punkte, {faktoren-aufschlüsselung})."""
    basis, quelle = _punktebasis(ap, ph, mv, peer_estimate)
    einsatz = einsatzfaktor(st, prob)
    gegner = opponent_factor(pos, win_prob, liga_avg_win_prob)
    form = form_factor(ap, ph)
    verlauf, verlauf_grund = matchday_factor(pos, team_name, matchday_outlook)
    zu_null, p_zu_null = zu_null_bonus(pos, win_prob)

    erwartung = basis * einsatz * gegner * form * verlauf + zu_null
    return round(erwartung, 1), {
        "basis": round(basis, 1), "basis_quelle": quelle,
        "basis_geschaetzt": quelle != "real",
        "einsatzfaktor": round(einsatz, 2),
        "gegnerfaktor": round(gegner, 2), "formfaktor": round(form, 2),
        "spielverlaufsfaktor": round(verlauf, 2), "spielverlauf_grund": verlauf_grund,
        "zu_null_bonus": round(zu_null, 1), "p_zu_null": round(p_zu_null, 2) if p_zu_null else None,
        "bandbreite": _bandwidth(erwartung),
    }


def fair_value(pos, mv, ap, ph, st, prob, win_prob, team_strength, curve,
               liga_avg_win_prob=0.5, peer_estimate=None):
    """
    "Was ist der Spieler wert" statt "was wird er kosten"
    (SPEC_gebote_ki_team_KOMPLETT.md 1.2). Bereinigte Punkteerwartung aus
    Form × Einsatz × Gegner × Teamstärke - bewusst OHNE Spielverlaufsfaktor
    (KI-abhängig/optional, Fair Value soll auch ohne KI-Schicht robust
    laufen) und OHNE Zu-Null-Bonus (der ist matchday-spezifisch/volatil,
    Fair Value soll die stabilere, saisonweite Einordnung sein).

    **Bugfix (2026-08-05, beim ersten echten Testlauf gefunden)**: Einsatz/
    Gegner/Team werden NACH der Kurven-Inversion multipliziert, nicht davor
    - die Preiskurve ist auf der ROHEN Punktebasis kalibriert, ein Malus-
    lastiger Kontextfaktor VOR der Inversion lief fast immer in den
    Kurven-Boden (s. Git-Historie für das konkrete Fallbeispiel).

    Liefert (fair_value_mv, {"basis":…, "quelle":…}) - (None, None) ohne
    Kurve oder ohne Kurventreffer.
    """
    if not curve:
        return None, None
    basis, quelle = _punktebasis(ap, ph, mv, peer_estimate)

    from scoring import invert_price_curve
    base_fv = invert_price_curve(basis, curve)
    if base_fv is None:
        return None, None

    einsatz = einsatzfaktor(st, prob)
    gegner = opponent_factor(pos, win_prob, liga_avg_win_prob)
    team = team_factor(team_strength)
    fv = base_fv * einsatz * gegner * team
    return round(fv), {"basis": round(basis, 1), "quelle": quelle}


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
            "team": extra.get("team"),
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


def formation_hint(xi):
    """
    Textbaustein zur Formationsdynamik (SPEC 2.4) für die Manager-Analyse:
    Fünferkette + hohe Zu-Null-Erwartung verstärkt sich (Bonus fünffach
    anfällig), Fünferkette gegen starke Gegner = mehrfach kein Bonus + hohes
    Klumpenrisiko, drei Stürmer gegen schwache Abwehrreihen = hohe Erwartung
    UND hohe Streuung. Rein informativ, kein Bestandteil der Punkteberechnung.
    """
    abw = [p for p in xi if p.get("pos") == "ABW"]
    ang = [p for p in xi if p.get("pos") == "ANG"]
    if len(abw) >= 5:
        avg_p = (sum(p.get("ep_factors", {}).get("p_zu_null") or 0 for p in abw) / len(abw))
        if avg_p >= 0.30:
            return "Fünferkette mit hoher Zu-Null-Erwartung - der Bonus fällt fünffach an, verstärkter Effekt"
        if avg_p <= 0.15:
            return "Fünferkette gegen starke Gegner - mehrfach kein Zu-Null-Bonus zu erwarten, hohes Klumpenrisiko"
    if len(ang) >= 3:
        return "Drei Stürmer aufgeboten - hohe Erwartung, aber auch hohe Streuung"
    return None


def adjust_for_self_play_duels(players, matcher):
    """
    SPEC_kalibrierung_fairvalue.md 2.1, vom Nutzer als wichtigster Mangel
    benannt: stehen zwei eigene Spieler in derselben Partie auf
    verschiedenen Seiten, sind die ergebnisabhängigen Anteile (Gegnerfaktor-
    Abweichung von 1,0 + Zu-Null-Bonus) wechselseitig ausschließend - nur
    eine Seite kann tatsächlich gewinnen. Beide unverändert mit ihrem vollen
    Ergebnisbonus zu bewerten überschätzt das Team systematisch.

    Grobe, transparente Näherung ("der Topf wird einmal vergeben, gewichtet
    nach Sieg-WK, nicht zweimal"): der Gegnerfaktor-Ausschlag UND der
    Zu-Null-Bonus werden für BEIDE Betroffenen halbiert (eine echte gemeinsame
    Verteilung bräuchte eine Kovarianzrechnung, die hier bewusst nicht gebaut
    wird), die ausgewiesene Bandbreite wird geweitet (Streuung steigt bei
    korrelierten Ergebnissen). Mutiert `players` (Feld `expected_points`/
    `ep_factors`) direkt und gibt die betroffenen Paare zurück
    ([(playerA, playerB), ...], Namen für die Report-Ausweisung).

    `players`: Liste von Dicts mit 'name', 'team', 'pos', 'opponents'
    (nächste Gegner-Namen), 'expected_points', 'ep_factors' - passt sowohl
    auf squad_classified (main.py) als auch league_teams.analyze_manager()'s
    Spielerliste.
    """
    teams = {p["team"]: p for p in players if p.get("team")}
    team_names = list(teams.keys())
    seen, pairs = set(), []
    for p in players:
        if not p.get("team") or not p.get("opponents") or "ep_factors" not in p:
            continue
        opp_clean = p["opponents"][0].split(" (")[0].strip()
        matched = matcher(opp_clean, team_names)
        if not matched or matched == p["team"]:
            continue
        other = teams.get(matched)
        if not other or other is p or "ep_factors" not in other:
            continue
        pair = tuple(sorted((p["name"], other["name"])))
        if pair in seen:
            continue
        seen.add(pair)
        pairs.append((p, other))

    for a, b in pairs:
        for pl in (a, b):
            f = pl["ep_factors"]
            gegner_dev = f["gegnerfaktor"] - 1.0
            f["gegnerfaktor"] = round(1.0 + gegner_dev * 0.5, 2)
            f["zu_null_bonus"] = round(f.get("zu_null_bonus", 0.0) * 0.5, 1)
            f["direktduell_gedaempft"] = True
            neu = (f["basis"] * f["einsatzfaktor"] * f["gegnerfaktor"]
                  * f.get("formfaktor", 1.0) * f["spielverlaufsfaktor"]
                  + f["zu_null_bonus"])
            pl["expected_points"] = round(neu, 1)
            f["bandbreite"] = _bandwidth(neu, widened=True)

    return pairs


def detect_self_play_conflicts(squad, matcher):
    """
    Text-Hinweise fürs Reporting/die KI (SPEC_gebote_ki_team_KOMPLETT.md
    2.2 + SPEC_kalibrierung_fairvalue.md 2.1) - nutzt dieselbe Paar-Findung
    wie `adjust_for_self_play_duels()` (identische Fuzzy-Match-Logik über
    `opponents[0]` gegen die eigenen Teamnamen), aber rein informativ, ohne
    die Punkte zu verändern (für Aufrufer, die nur den Hinweistext brauchen,
    z.B. den KI-Kontext).
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
            f"- beide eigene Spieler, Ergebnisbonus hebt sich weitgehend auf"
        )
    return conflicts
