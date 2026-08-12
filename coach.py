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

**Direkte Duelle (SPEC_spieltagsmodell_v2.md 2.1/2.3, partieweise gruppiert
seit SPEC_ranking_faktoren_llm.md Abschnitt 3)**: `xi_prognose()` erkennt
(über `find_self_play_matches()`) eigene Spieler in derselben Partie auf
verschiedenen Seiten - NUR wenn beide in der übergebenen Startelf stehen,
Bankspieler zählen nicht - und dämpft den ergebnisabhängigen Anteil
(Gegnerfaktor-Abweichung + Zu-Null-Bonus) für ALLE Beteiligten auf die
Hälfte, ohne die Spieler-Dicts zu mutieren (dieselbe Elf kann in mehreren
Kontexten, z.B. Ideal- vs. Ist-Aufstellung, unterschiedlich zu werten sein).
Grobe, transparente Näherung an "der Ergebnis-Topf wird einmal vergeben,
nicht zweimal" (eine echte gemeinsame Verteilung bräuchte eine Kovarianz-
rechnung, bewusst nicht gebaut) und senkt die Sigma-Gewichtung in der
Team-Bandbreite. **Partieweise statt paarweise (Bugfix)**: die Vorfassung
gruppierte nach SpielerPAAR - bei 2+ betroffenen Spielern je Seite (z.B.
zwei Abwehrspieler von Team A gegen einen Stürmer von Team B) erschien
dieselbe reale Partie mehrfach als separate Zeile ("Keller vs. Otto" UND
"Keller vs. Zoma" für ein einziges Nürnberg-Dresden-Spiel). `find_self_play_
matches()` gruppiert jetzt nach Team-Paar, `duel_hints_for_xi()` liefert
Text-Hinweise dazu - max. 2 je Aufruf, Handlungsempfehlung ("X ist die
bessere Wahl") nur für das eigene Team (`own=True`), bei fremden Kadern nur
der Hinweis, dass sich der Ergebnisbonus gegenseitig aufhebt.

**Lücke geschlossen (2026-08-05, SPEC_lineup_verified.md)**: `GET /v4/leagues
/{id}/lineup` liefert den kompletten Kader inkl. `lo` (Slot 0-10 in der
Startelf, fehlt bei Bankspielern) - live verifiziert. `current_lineup_status()`
und `swaps_from_ideal()` (SPEC_spieltagsmodell_v2.md 2.1 - EINE Quelle statt
zwei unabhängig berechneter, die sich widersprechen konnten: die Ideal-Elf
ist die Wahrheit, Wechselvorschläge sind das Delta dazu) nutzen das für echte
Wechselvorschläge ggü. der tatsächlich gesetzten Elf. **Kein POST**:
`/lineup` (setzt die Aufstellung) wird bewusst nicht implementiert - würde
den echten Kickbase-Kader verändern, gehört laut Spec explizit nicht in den
automatischen Lauf.

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
# Werte auf SPEC_punkteformel_final.md Abschnitt 2 (M) exakt angeglichen
# (2026-08-07, geringfügige Abweichung 0,95/0,60/0,25 -> 0,92/0,55/0,20 -
# beides Erstkalibrierung ohne echte Ist-Minuten dahinter, die Spec ist die
# jetzt verbindliche Fassung). Ab Spieltag 5 werden diese Werte laut Spec
# durch gemessene Ist-Minuten je Farbe ersetzt - noch nicht gebaut (braucht
# echte Player-Level-Einsatzzeiten, die aktuell nicht erfasst werden, s.
# retrospective.py-Docstring).
EINSATZ_FACTOR = {1: 1.00, 2: 0.92, 3: 0.55, 4: 0.20, 5: 0.10}
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


def opponent_factor(pos, win_prob, liga_avg_win_prob=0.5, punktetyp_idx=None):
    """
    `1 + k_eff·(Sieg-WK − Liga-Ø-Sieg-WK)`, geclippt auf OPPONENT_FACTOR_BOUNDS.
    Zentriert auf die ECHTE Liga-Ø-Sieg-WK (s. fixtures.league_avg_win_prob)
    statt auf 0,5 - Fußball hat 3 Ausgänge, die reale Ø-Sieg-WK pro Team
    liegt bei ~35-40%, nicht bei 50%.

    **Punktetyp-Kopplung (SPEC_spielertyp_matchkontext.md 1.1)**: `k_eff =
    k_pos × (1 − 0,5 × (1 − punktetyp_idx))` - ein reiner Rohpunkte-Spieler
    (punktetyp_idx≈0, s. scoring.punktetyp_index) punktet weitgehend
    unabhängig vom Spielausgang und bekommt deshalb eine halbierte Gegner-
    Sensitivität; ein reiner Scorer (punktetyp_idx≈1, Punkte hängen am
    eigenen Sieg) bleibt bei der vollen `k_pos`. `punktetyp_idx=None`
    (Standardfall ohne ausreichende Sieg/Niederlage-Stichprobe) lässt
    `k_eff=k_pos` unverändert - keine Kopplung ohne Datenbasis.
    """
    if win_prob is None:
        return 1.0
    k = OPPONENT_K.get(pos, 0.7)
    if punktetyp_idx is not None:
        k = k * (1 - 0.5 * (1 - punktetyp_idx))
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


def zu_null_probability_from_context(pos, erwartete_tordifferenz, erwartete_tore):
    """
    SPEC_spielertyp_matchkontext.md 1.2: leitet P(zu Null) aus der erwarteten
    TORDIFFERENZ (Asian-Handicap-Linie) und den erwarteten GESAMTTOREN
    (Über/Unter-2,5-Quoten) her, statt nur aus der Sieg-Wahrscheinlichkeit.
    Grund: ein 1:0-Favoritensieg (kleine Tordifferenz, wenig Tore) ist für
    die Zu-Null-Chance wertvoller als ein 3:2-Sieg bei identischer Sieg-WK -
    die reine win_prob-Tabelle (zu_null_probability()) kann diese beiden
    Fälle nicht unterscheiden.

    erwartete_tordifferenz: aus TEAM-Sicht (positiv = Team im Erwartungswert
    vorne). erwartete_tore: erwartete Gesamttore der Partie.

    Lineare Näherung, dieselbe Bandbreite wie die bisherige Anker-Tabelle
    (ZU_NULL_ANCHORS, ~0,10-0,42) - Erstkalibrierung, noch nicht gegen echte
    Spieltage geprüft.
    """
    dominanz = max(-2.0, min(2.0, erwartete_tordifferenz))
    p = 0.25 + dominanz * 0.085
    if erwartete_tore is not None:
        # Torarme Partie erhöht P(zu Null) für BEIDE Seiten, torreiche senkt
        # sie - 2,5 Tore (Über/Unter-Schwelle) ist der neutrale Bezugspunkt.
        tore_delta = 2.5 - max(1.5, min(4.0, erwartete_tore))
        p += tore_delta * 0.06
    p = max(0.0, min(0.55, p))
    if pos == "TW":
        p = max(p, ZU_NULL_TW_FLOOR)
    return p


def zu_null_bonus(pos, win_prob, erwartete_tordifferenz=None, erwartete_tore=None):
    """(bonus_punkte, p_zu_null) - 0.0/None für Positionen ohne Zu-Null-Bezug.

    Nutzt `zu_null_probability_from_context()` (Tordifferenz+Gesamttore aus
    Über/Unter- und Handicap-Quoten), wenn `erwartete_tordifferenz` vorliegt
    - präziser als die reine Sieg-WK-Ankertabelle (s. dortige Docstring).
    Ohne Match-Kontext (z.B. La Liga über the-odds-api/Tabellen-Fallback,
    die beide keine Handicap-/Tore-Quoten liefern) fällt es unverändert auf
    `zu_null_probability(win_prob, pos)` zurück."""
    if pos not in ZU_NULL_PRAEMIE:
        return 0.0, None
    if erwartete_tordifferenz is not None:
        p = zu_null_probability_from_context(pos, erwartete_tordifferenz, erwartete_tore)
    else:
        p = zu_null_probability(win_prob, pos)
    return p * ZU_NULL_PRAEMIE[pos], p


# ---------- Teamfaktor (Fair Value, SPEC_gebote_ki_team_KOMPLETT.md 1.2) ----------
TEAM_FACTOR_RANGE = (0.80, 1.20)


def team_factor(team_strength):
    lo, hi = TEAM_FACTOR_RANGE
    team_strength = 0.5 if team_strength is None else max(0.0, min(1.0, team_strength))
    return lo + (hi - lo) * team_strength


# ---------- Bandbreite (SPEC_spieltagsmodell_v2.md 1.1 - echte Streuung
# statt fester ±15%) ----------
# Erstkalibrierung für Spieler OHNE `ph`-Historie (Neuzugänge/Vorbereitung):
# grobe Sigma-Schätzung als Anteil der Basis, je Position - Torhüter am
# stabilsten (Paraden/Gegentore schwanken wenig), Angreifer am volatilsten
# (Boom-or-Bust: Tor oder nichts).
SIGMA_ESTIMATE_FACTOR = {"TW": 0.25, "ABW": 0.35, "MF": 0.30, "ANG": 0.40}
MIN_SIGMA = 5.0


def player_sigma(ph, pos, basis):
    """
    Standardabweichung der Spieltagspunkte (SPEC 1.1): aus `ph` (Vorsaison-
    Spieltage mit hp=true), wenn mindestens 4 Datenpunkte vorliegen - sonst
    aus der Positions-/Preisklasse geschätzt (Basis × Erfahrungsfaktor,
    `SIGMA_ESTIMATE_FACTOR`) und als Schätzung gekennzeichnet.
    Liefert (sigma, ist_geschaetzt).
    """
    real = [e["p"] for e in (ph or []) if e.get("hp") and e.get("p") is not None]
    if len(real) >= 4:
        mean = sum(real) / len(real)
        var = sum((x - mean) ** 2 for x in real) / (len(real) - 1)
        return max(MIN_SIGMA, var ** 0.5), False
    factor = SIGMA_ESTIMATE_FACTOR.get(pos, 0.33)
    return max(MIN_SIGMA, basis * factor), True


# Ziel-Konfidenzniveau für die Bandbreiten (User-Vorgabe "80% WK"): 1,2816×σ
# ist der zweiseitige z-Wert für 80% Flächenanteil unter der Normalverteilung
# (nicht 1,0×σ ≈ 68%, wie hier vorher fälschlich verwendet - Bugfix
# 2026-08-12, live vom User bemängelt). Noch NICHT empirisch validiert (nur
# 1 Spieltag Ist-Daten bisher) - die tatsächliche Korridor-Trefferquote
# wird über prediction_log.deviation_report()/detect_anomalies() sichtbar
# gemacht, sobald genug Spieltage vorliegen; bis dahin ist dies der
# rechnerisch korrekte Ausgangspunkt für das vereinbarte Ziel, nicht das
# Endergebnis einer Kalibrierung.
BANDWIDTH_Z = 1.2816


def _bandwidth(erwartung, sigma):
    """
    Einzelspieler-Bandbreite: E ± BANDWIDTH_Z×σ (~80% der Fälle laut
    Normalnäherung - der vereinbarte Zielwert, SPEC_spieltagsmodell_v2.md
    1.1 nutzte ursprünglich 1,0×σ/~68%, was nie explizit gegen ein Ziel-
    Konfidenzniveau geprüft wurde). Die Team-Bandbreite (mit Direktduell-
    Dämpfung + Klumpen-Korrelation) baut `xi_prognose()` separat aus den
    Einzel-Sigmas auf, mit demselben `BANDWIDTH_Z`.

    **Bugfix (2026-08-12)**: der frühere `sigma=None`-Fallback-Zweig
    (grobe ±-Prozent-Näherung für Aufrufer ohne Sigma) referenzierte eine
    nirgends definierte Variable `widened` - ein latenter NameError, der nur
    deshalb nie auftrat, weil der einzige verbliebene Aufrufer (`expected_
    points()`) `sigma` immer über `player_sigma()` liefert (nie None, dort
    mit `MIN_SIGMA`-Untergrenze). Toter, kaputter Code entfernt statt repariert.
    """
    return (round(max(0.0, erwartung - BANDWIDTH_Z * sigma), 1),
           round(erwartung + BANDWIDTH_Z * sigma, 1))


# AUSWERTUNG_spieltag1.md: Vertrauensgewichtung für die Basis selbst (nicht
# nur für die Unsicherheitskalibrierung aus SPEC_punkteformel_final.md 8.3,
# deren n₀≈50 für eine andere, langsamere Größe gedacht ist - hier geht es
# darum, wie schnell EIN Spielers eigener `ap` das Vertrauen des Wertes
# "typisch für ihn" verdient). Kleiner gewählt als das globale n₀, weil ein
# einzelner Spieler viel schneller eine eigene Formkurve aufbaut als das
# Gesamtmodell eine neue Kalibrierung rechtfertigt.
PUNKTEBASIS_N0 = 8


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

    **Vertrauensgewichtung bei DÜNNER Historie (2026-08-10, AUSWERTUNG_
    spieltag1.md - live gefunden am Tag nach Spieltag 1)**: `ap` wurde
    bisher ab dem ERSTEN echten Spieltag (n=1) voll vertraut, egal wie
    extrem der Einzelwert war - live belegt: Wanitzek erzielte an Spieltag 1
    320 Punkte in einem einzigen Spiel (Ausreißer), `ap` sprang dadurch
    sofort auf 320 - und wurde für die Spieltag-2-Prognose UNGEBREMST als
    "typischer" Wert übernommen (Vorhersage in den Hunderten statt im
    üblichen Rahmen). Genau der vom User vermutete Mechanismus ("wurden die
    Punkte nochmal draufgerechnet") - keine Dopplung im Code, aber
    funktional identisch: ein Einzelspitzenwert wird zur neuen Normalität.
    Fix: `ap` wird jetzt mit `n/(n+n₀)` (SPEC_punkteformel_final.md 8.3,
    hier mit kleinerem `n₀`=`PUNKTEBASIS_N0` für die Einzelspieler-Ebene)
    gegen einen stabileren Referenzwert geblendet (`peer_estimate`, sonst
    der MW-Sockel) - bei n=1 zählt der reale Wert nur zu ~11%, bei n=8 zur
    Hälfte, erst danach überwiegt der eigene Schnitt zunehmend. Wächst mit
    jedem weiteren echten Spieltag automatisch, keine Wartefrist.

    Die alte MW-Sockel-Untergrenze (SPEC_ranking_faktoren_llm.md 2.3, schützt
    gegen negative/sehr niedrige `ap`) bleibt für dünne Stichproben (n <
    PUNKTEBASIS_N0) zusätzlich als Boden bestehen - der geblendete Wert darf
    dort nicht darunter fallen.

    Liefert (basis, quelle) mit quelle in
    {"real", "real_blend_n<N>", "peer", "mv_estimate"}.
    """
    from scoring import mv_implied_form
    n_real = sum(1 for e in (ph or []) if e.get("hp") and e.get("p") is not None)
    mv_floor = mv_implied_form(mv or 0) * 130
    if n_real == 0:
        if peer_estimate is not None:
            return max(peer_estimate, 0.0), "peer"
        return mv_floor, "mv_estimate"

    if n_real >= PUNKTEBASIS_N0:
        return (ap or 0), "real"

    reference = peer_estimate if peer_estimate is not None else mv_floor
    weight = n_real / (n_real + PUNKTEBASIS_N0)
    basis = weight * (ap or 0) + (1 - weight) * reference
    basis = max(basis, mv_floor)
    return basis, f"real_blend_n{n_real}"


def expected_points(pos, ap, ph, st, prob, win_prob, team_name=None,
                    matchday_outlook=None, mv=0, liga_avg_win_prob=0.5,
                    peer_estimate=None, punktetyp_idx=None,
                    erwartete_tordifferenz=None, erwartete_tore=None):
    """Liefert (erwartete_punkte, {faktoren-aufschlüsselung}).

    punktetyp_idx: optional (scoring.punktetyp_index) - koppelt die Gegner-
    Sensitivität an den Punktetyp (SPEC_spielertyp_matchkontext.md 1.1),
    s. opponent_factor().
    erwartete_tordifferenz/erwartete_tore: optional (odds.load_fixture_odds()s
    match_context, aus Über/Unter-2,5- und Asian-Handicap-Quoten) - präzisere
    Zu-Null-Herleitung als die reine Sieg-WK, s. zu_null_bonus()."""
    basis, quelle = _punktebasis(ap, ph, mv, peer_estimate)
    einsatz = einsatzfaktor(st, prob)
    gegner = opponent_factor(pos, win_prob, liga_avg_win_prob, punktetyp_idx)
    form = form_factor(ap, ph)
    verlauf, verlauf_grund = matchday_factor(pos, team_name, matchday_outlook)
    zu_null, p_zu_null = zu_null_bonus(pos, win_prob, erwartete_tordifferenz, erwartete_tore)

    erwartung = basis * einsatz * gegner * form * verlauf + zu_null
    sigma, sigma_geschaetzt = player_sigma(ph, pos, basis)
    return round(erwartung, 1), {
        "basis": round(basis, 1), "basis_quelle": quelle,
        "punktetyp_idx": round(punktetyp_idx, 2) if punktetyp_idx is not None else None,
        "basis_geschaetzt": quelle != "real",
        "einsatzfaktor": round(einsatz, 2),
        "gegnerfaktor": round(gegner, 2), "formfaktor": round(form, 2),
        "spielverlaufsfaktor": round(verlauf, 2), "spielverlauf_grund": verlauf_grund,
        "zu_null_bonus": round(zu_null, 1), "p_zu_null": round(p_zu_null, 2) if p_zu_null else None,
        "sigma": round(sigma, 1), "sigma_geschaetzt": sigma_geschaetzt,
        "bandbreite": _bandwidth(erwartung, sigma),
    }


def diagnose_prognose(xi):
    """
    Kaskadierte Diagnose-Tabelle (SPEC_spieltagsmodell_v2.md 1.2, Pflicht-
    Ausgabe bei >25% Abweichung vom pspts-Anker): zeigt Schritt für Schritt,
    wie viel Niveau jeder Faktor kostet/bringt - "welcher Faktor drückt die
    Prognose" ist damit direkt ablesbar statt vermutet.
    """
    basis_sum = sum(p["ep_factors"]["basis"] for p in xi)
    nach_einsatz = sum(p["ep_factors"]["basis"] * p["ep_factors"]["einsatzfaktor"] for p in xi)
    nach_gegner = sum(p["ep_factors"]["basis"] * p["ep_factors"]["einsatzfaktor"]
                      * p["ep_factors"]["gegnerfaktor"] for p in xi)
    nach_form = sum(p["ep_factors"]["basis"] * p["ep_factors"]["einsatzfaktor"]
                    * p["ep_factors"]["gegnerfaktor"] * p["ep_factors"].get("formfaktor", 1.0)
                    for p in xi)
    zu_null_sum = sum(p["ep_factors"].get("zu_null_bonus", 0.0) for p in xi)
    final = sum(p["expected_points"] for p in xi)

    def _eff(nach, vor):
        return round(nach / vor, 2) if vor else None

    return {
        "basis": round(basis_sum, 1),
        "nach_einsatz": round(nach_einsatz, 1), "einsatz_effektiv": _eff(nach_einsatz, basis_sum),
        "nach_gegner": round(nach_gegner, 1), "gegner_effektiv": _eff(nach_gegner, nach_einsatz),
        "nach_form": round(nach_form, 1), "form_effektiv": _eff(nach_form, nach_gegner),
        "zu_null": round(zu_null_sum, 1),
        "final": round(final, 1),
    }


def fair_value(pos, mv, ap, ph, st, prob, win_prob, team_strength, curve,
               liga_avg_win_prob=0.5, peer_estimate=None, punktetyp_idx=None):
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
    gegner = opponent_factor(pos, win_prob, liga_avg_win_prob, punktetyp_idx)
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
        # SPEC_lernzyklus.md 5.2c: eindeutiger Zweitschlüssel bei Punktgleichheit.
        pool = sorted(players_by_pos.get(pos, []), key=lambda p: (-p["expected_points"], p["id"]))
        chosen = pool[:count]
        if len(chosen) < count:
            return None  # Kader hat nicht genug Spieler dieser Position
        xi.extend(chosen)
        total += sum(p["expected_points"] for p in chosen)
    return {"formation": formation, "xi": xi, "total_points": round(total, 1)}


def derive_formation(xi):
    """
    Zählt die Positionen einer konkreten Elf (`pos`-Feld je Spieler) zu
    einem Formations-Dict wie in FORMATIONS. Für optimize_lineup()s
    `also_try` gedacht - die ECHTE gesetzte Formation eines Managers, auch
    wenn sie nicht in den 7 Standard-Formationen enthalten ist.
    """
    counts = {"TW": 0, "ABW": 0, "MF": 0, "ANG": 0}
    for p in xi:
        if p.get("pos") in counts:
            counts[p["pos"]] += 1
    return counts


def optimize_lineup(squad_with_points, also_try=None):
    """
    squad_with_points: Liste von Dicts mit mind. id/name/pos/expected_points.
    Liefert {"formations": {name: {...}}, "best": name, "best_total": float}
    - beste Formation nach Gesamtpunkten, Alternativen mit allen Ergebnissen
    zum Differenzvergleich.

    `also_try`: optionales zusätzliches Formations-Dict (typischerweise die
    ECHT gesetzte Formation, s. derive_formation()) - **Bugfix (live
    gefunden: "Effizienz" 106% bei PaulBowa, Formation 4-2-4)**. Ohne diesen
    Parameter sucht die Optimierung NUR unter den 7 Standard-Formationen -
    setzt ein Manager eine andere (z.B. 4-2-4, vier Stürmerslots statt
    maximal drei in jeder Standardformation), kann seine reale Elf mehr
    Punkte erzielen als das rechnerische "Optimum", weil dessen Suchraum
    unvollständig war. "Kaderstärke" soll aber per Definition das
    theoretische Maximum sein (Effizienz ≤ 100% immer) - `also_try` stellt
    sicher, dass die reale Formation IMMER Teil des Suchraums ist, mit der
    bestmöglichen Spielerauswahl für genau diese Slotverteilung (nicht der
    realen Spielerauswahl selbst, die kann weiterhin schwächer sein als das
    Optimum für dieselbe Formation).
    """
    by_pos = {}
    for p in squad_with_points:
        by_pos.setdefault(p["pos"], []).append(p)

    formations_to_try = dict(FORMATIONS)
    if also_try and also_try not in formations_to_try.values():
        label = "-".join(str(also_try.get(p, 0)) for p in ("ABW", "MF", "ANG"))
        formations_to_try[f"{label} (gesetzt)"] = also_try

    results = {}
    for name, formation in formations_to_try.items():
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


# Differenz unter ~8% der Erwartung = Abwägung kennzeichnen, keine klare
# Empfehlung (SPEC_spieltagsmodell_v2.md 2.2).
SWAP_MARGIN = 0.08


def swaps_from_ideal(lineup_status, lineup_opt, max_suggestions=3):
    """
    SPEC_spieltagsmodell_v2.md 2.1: EINE Quelle statt zwei getrennt
    berechneter Kennzahlen, die sich widersprechen konnten (Wechsel-
    vorschläge liefen bisher unabhängig über "stärkster Bankspieler ggü.
    jedem Starter" statt gegen die tatsächlich empfohlene Ideal-Elf). Jetzt:
    die Ideal-Elf (`lineup_opt`, `coach.optimize_lineup()`) ist die einzige
    Wahrheit, Wechselvorschläge sind das DELTA zur echten Ist-Aufstellung -
    für jeden Ideal-Elf-Spieler, der nicht in der echten Startelf steht,
    wird der schwächste echte Startelf-Spieler DERSELBEN Position (der
    selbst nicht Teil der Ideal-Elf ist) als Tausch-Kandidat vorgeschlagen.
    `knapp=True` (SPEC 2.2) markiert Differenzen <8% der Erwartung als
    Abwägung statt klare Empfehlung.
    """
    if not lineup_opt or not lineup_opt.get("best"):
        return []
    ideal_xi = lineup_opt["formations"][lineup_opt["best"]]["xi"]
    ideal_ids = {p["id"] for p in ideal_xi}
    real_ids = {p["id"] for p in lineup_status["xi"]}
    missing = [p for p in ideal_xi if p["id"] not in real_ids]
    if not missing:
        return []
    real_by_pos = {}
    for p in lineup_status["xi"]:
        real_by_pos.setdefault(p["pos"], []).append(p)

    suggestions, used_out = [], set()
    for ideal_p in sorted(missing, key=lambda x: -x["expected_points"]):
        candidates = [p for p in real_by_pos.get(ideal_p["pos"], [])
                     if p["id"] not in ideal_ids and p["id"] not in used_out]
        if not candidates:
            continue
        worst_real = min(candidates, key=lambda p: p["expected_points"])
        used_out.add(worst_real["id"])
        diff = ideal_p["expected_points"] - worst_real["expected_points"]
        knapp = abs(diff) < SWAP_MARGIN * max(worst_real["expected_points"], 1)
        suggestions.append({"slot": worst_real.get("lo"), "out": worst_real,
                           "in": ideal_p, "diff": round(diff, 1), "knapp": knapp})
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


def formation_gap_reason(players):
    """
    SPEC_spieltagsmodell_v2.md 1.3: kein stummes "?", wenn optimize_lineup()
    für KEINE der 7 Formationen genug Spieler einer Position findet - benennt
    konkret, welche Position(en) unter der Kickbase-Mindestbesetzung liegen
    (squad_analysis.MIN_POS_COUNT, gilt über alle Formationen hinweg).
    """
    from collections import Counter
    from squad_analysis import MIN_POS_COUNT
    counts = Counter(p["pos"] for p in players)
    gaps = [f"{MIN_POS_COUNT[pos] - counts.get(pos, 0)}x {pos}"
           for pos in MIN_POS_COUNT if counts.get(pos, 0) < MIN_POS_COUNT[pos]]
    if gaps:
        return "zu wenige Spieler für eine gültige Elf - fehlt: " + ", ".join(gaps)
    return "zu wenige Spieler für alternative Formationen (Kader zu klein/unausgewogen)"


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


def find_self_play_matches(xi, matcher):
    """
    SPEC_ranking_faktoren_llm.md Abschnitt 3 (ersetzt find_self_play_pairs -
    Bugfix "partieweise statt paarweise"): Duelle werden NUR ausgewertet,
    wenn BEIDE Seiten in der STARTELF stehen - Bankspieler sind irrelevant,
    deren Punkte zählen nicht. `xi` muss daher bereits die konkrete Elf sein
    (Ideal- oder Ist-Aufstellung), NICHT der volle Kader. Gruppiert nach
    TEAM-PAAR statt SPIELER-Paar - alle eigenen Spieler beider Seiten
    derselben realen Partie kommen in EINEN Eintrag, egal wie viele Spieler
    je Seite betroffen sind (vorher: eine Zeile pro Spielerpaar, dieselbe
    Partie erschien bei 2+ Spielern je Seite mehrfach). Reine Erkennung
    (Fuzzy-Match von `opponents[0]` gegen die eigenen Teamnamen in der Elf,
    derselbe `_match_name`-Matcher wie sonst im Projekt), keine Punkte-
    mutation - s. `xi_prognose()`/`duel_hints_for_xi()` für die Verwendung.
    Liefert [{"team_a", "players_a", "team_b", "players_b"}, ...].
    """
    team_players = {}
    for p in xi:
        if p.get("team"):
            team_players.setdefault(p["team"], []).append(p)
    team_names = list(team_players.keys())
    seen, matches = set(), []
    for team, players in team_players.items():
        opp_team = None
        for p in players:
            if not p.get("opponents"):
                continue
            opp_clean = p["opponents"][0].split(" (")[0].strip()
            matched = matcher(opp_clean, team_names)
            if matched and matched != team:
                opp_team = matched
                break
        if not opp_team or opp_team not in team_players:
            continue
        key = tuple(sorted((team, opp_team)))
        if key in seen:
            continue
        seen.add(key)
        matches.append({
            "team_a": key[0], "players_a": team_players[key[0]],
            "team_b": key[1], "players_b": team_players[key[1]],
        })
    return matches


def _damped_points(f):
    """Punkte eines Spielers, wenn sein ergebnisabhängiger Anteil (Gegner-
    faktor-Ausschlag + Zu-Null-Bonus) wegen eines Direktduells halbiert wird
    (SPEC_kalibrierung_fairvalue.md 2.1: "der Topf wird einmal vergeben,
    nicht zweimal") - grobe, transparente Näherung ohne echte Kovarianz."""
    gegner_dev = f.get("gegnerfaktor", 1.0) - 1.0
    damped_gegner = 1.0 + gegner_dev * 0.5
    damped_zu_null = f.get("zu_null_bonus", 0.0) * 0.5
    pts = (f.get("basis", 0) * f.get("einsatzfaktor", 1.0) * damped_gegner
          * f.get("formfaktor", 1.0) * f.get("spielverlaufsfaktor", 1.0)
          + damped_zu_null)
    return round(pts, 1)


def xi_prognose(xi, matcher):
    """
    Gesamtprognose + Bandbreite für eine KONKRETE Startelf (SPEC_
    spieltagsmodell_v2.md 1.1 + 2.1 + 2.3 zusammengeführt) - mutiert die
    übergebenen Spieler-Dicts NICHT (dieselbe Elf kann in mehreren Kontexten,
    z.B. Ideal- vs. Ist-Aufstellung, unterschiedlich zu werten sein).
    Direktduelle (beide Seiten IN DIESER Elf) dämpfen ihren ergebnis-
    abhängigen Punkteanteil UND senken ihre Sigma-Gewichtung (negativ
    korreliert). Team-Varianz sonst additiv aus `ep_factors['sigma']`
    + Klumpen-Korrelation (mehrere Spieler desselben Vereins).
    Liefert {"total", "bandbreite", "duels": [{"team_a", "players_a", ...}, ...]}
    (partieweise gruppiert, s. find_self_play_matches()).
    """
    if not xi:
        return {"total": 0.0, "bandbreite": (0.0, 0.0), "duels": []}
    matches = find_self_play_matches(xi, matcher)
    damped_ids = {p["id"] for m in matches for p in m["players_a"] + m["players_b"]}

    total, var_total = 0.0, 0.0
    by_team = {}
    for p in xi:
        f = p.get("ep_factors", {})
        sigma = f.get("sigma", MIN_SIGMA)
        if p["id"] in damped_ids:
            pts = _damped_points(f)
            sigma *= 0.75
        else:
            pts = p["expected_points"]
        total += pts
        var_total += sigma ** 2
        if p.get("team"):
            by_team.setdefault(p["team"], []).append(sigma)
    for sigmas in by_team.values():
        if len(sigmas) >= 2:
            avg = sum(sigmas) / len(sigmas)
            var_total += 0.4 * (len(sigmas) - 1) * avg ** 2
    sigma_team = var_total ** 0.5

    return {
        "total": round(total, 1),
        # BANDWIDTH_Z (~80% Konfidenz, s. _bandwidth()-Docstring) - vorher
        # 1,0×σ (~68%), Bugfix 2026-08-12.
        "bandbreite": (round(max(0.0, total - BANDWIDTH_Z * sigma_team), 1),
                       round(total + BANDWIDTH_Z * sigma_team, 1)),
        "duels": matches,
    }


def duel_hints_for_xi(xi, matcher, own=False, max_hints=2):
    """
    Text-Hinweise für Report/KI (SPEC_gebote_ki_team_KOMPLETT.md 2.2 +
    SPEC_spieltagsmodell_v2.md 2.3, **partieweise + begrenzt seit
    SPEC_ranking_faktoren_llm.md Abschnitt 3**) - NUR für Duelle innerhalb
    einer konkreten Startelf (`xi`), EIN Eintrag je realer Partie (nicht je
    Spielerpaar - vorher erschien z.B. "Keller vs. Otto" UND "Keller vs.
    Zoma" für dieselbe Partie Nürnberg-Dresden). Max. `max_hints` Einträge.
    Handlungsempfehlung ("X ist die bessere Wahl") gibt es NUR fürs eigene
    Team (`own=True`) - bei fremden Kadern ist nur relevant, dass sich der
    Ergebnisbonus gegenseitig aufhebt, keine Empfehlung für fremde
    Kaderentscheidungen.
    """
    hints = []
    for m in find_self_play_matches(xi, matcher)[:max_hints]:
        seite_a = ", ".join(f"{p['name']} ({p['expected_points']} P)" for p in m["players_a"])
        seite_b = ", ".join(f"{p['name']} ({p['expected_points']} P)" for p in m["players_b"])
        text = f"{m['team_a']} – {m['team_b']}: {seite_a} vs. {seite_b}"
        if own:
            beteiligte = m["players_a"] + m["players_b"]
            besser = max(beteiligte, key=lambda p: p["expected_points"])
            text += (f" - Ergebnisbonus fällt nur einmal an, alle gleichzeitig stark ist "
                     f"unwahrscheinlich. {besser['name']} ({besser['expected_points']} P) "
                     f"ist die beste Wahl.")
        else:
            text += " - Ergebnisbonus fällt nur einmal an, dämpft Erwartung/Streuung beider Seiten."
        hints.append(text)
    return hints
