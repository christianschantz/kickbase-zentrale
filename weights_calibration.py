"""
Dynamische Rekalibrierung von `coach.OPPONENT_K` (Sensitivität des
Gegnerfaktors je Position) - User-Wunsch (2026-08-21, im Anschluss an
AUSWERTUNG_spieltag2.md 2/4): "so einstellen, dass die Anpassung nach jedem
Spieltag dynamisch fortgeführt wird", nicht nur ein einmaliger Handwert.

Bewusst NUR OPPONENT_K (4 Freiheitsgrade, ein Skalar je Position) - eine
volle Regression über ALLE Faktoren (SPEC_lernzyklus.md Stufe 2) bräuchte
laut jener Spec ≥600 Beobachtungen. Mit nur vier zu schätzenden Werten
reicht auch eine kleinere, aber wachsende Datenbasis für einen VORSICHTIGEN,
vertrauensgewichteten Nudge - dasselbe n/(n+n0)-Prinzip, das
`coach.PUNKTEBASIS_N0` schon für die Punktebasis nutzt (SPEC_punkteformel_
final.md 8.3): die empirische Schätzung gewinnt erst mit wachsender
Beobachtungszahl an Gewicht, ein einzelner Spieltag kann den Wert nicht
umwerfen.

Methode: OLS-Steigung durch den Ursprung je Position -
    y = k * x
    x = win_prob_ist - liga_avg_win_prob (realisierter Ausgang, zentriert)
    y = (punkte_ist - zu_null_ist) / (basis * einsatz_ist * form) - 1
      (die "ausgang-bedingte" relative Punkteabweichung von der Basis,
      Zu-Null und Einsatz/Form-Effekte vorher herausgerechnet, damit NUR
      der Ausgang-Effekt in die Schätzung von k eingeht)
    k_empirisch = Σ(x·y) / Σ(x²)   (Ursprungsgerade, da bei x=0 - Sieg-WK
    exakt am Liga-Schnitt - der Faktor per Definition 1,0 sein muss)

Nur Beobachtungen aus NICHT-implausiblen Managern (retrospective.py
PLAUSIBILITY_THRESHOLD, s. AUSWERTUNG_spieltag2.md 1.4) - bei einer stark
abweichenden Aufstellung ist `punkte_ist` einem Spieler zugeordnet, der
wahrscheinlich gar nicht wirklich gespielt hat, das würde jede Regression
verfälschen.

Zustand (kumulierte Σ(x·y)/Σ(x²)/n je Position) liegt in
`data/weights/opponent_k_state.json` - additiv über Spieltage, ein
Spieltag wird über `matchdays_processed` nur EINMAL gezählt (idempotent
bei mehrfachem Tageslauf).
"""
import json
import os

WEIGHTS_DIR = os.path.join("data", "weights")
STATE_PATH = os.path.join(WEIGHTS_DIR, "opponent_k_state.json")

# Ausgangswerte = coach.py-Erstkalibrierung (SPEC 2.2, nie gegen echte
# Spieltage geprüft) - hier als eigenständige Kopie, damit dieses Modul
# NICHT `coach` importieren muss (coach.py importiert umgekehrt DIESES
# Modul - zirkulärer Import sonst unausweichlich).
OPPONENT_K_PRIOR = {"TW": 0.90, "ABW": 1.00, "MF": 0.50, "ANG": 0.75}

# Vertrauensgewichtung n/(n+n0): bei n=CALIBRATION_N0 Beobachtungen zählt
# die empirische Schätzung zur Hälfte, erst deutlich darüber überwiegt sie.
# **Live am ersten echten Lauf nachjustiert (2026-08-21)**: mit dem
# ursprünglichen n0=40 verschob ein EINZIGER Spieltag (n=5-20/Position) den
# Blend bereits um 34-44% - und die rohe empirische Schätzung war für
# ABW/MF/ANG NEGATIV (siegreiche Teams sollen ihren Spielern laut Modell-
# Prämisse nie WENIGER Punkte bringen als unterlegene - eine negative
# Steigung ist bei n=1 Spieltag praktisch immer Rauschen, kein Signal,
# s. `_signed_blend()`). n0=150 macht denselben Spieltag zu einer
# Bewegung von <15% - deutlich vorsichtiger, dafür braucht ein WIRKLICHER
# struktureller Kalibrierungsfehler mehrere Spieltage, um durchzuschlagen.
CALIBRATION_N0 = 150

# Plausibilitäts-Clamp für den GEBLENDETEN Wert (nicht die Rohschätzung) -
# k muss positiv bleiben (eine höhere Sieg-WK darf die Erwartung nie
# SENKEN) und nicht absurd ausschlagen, solange die Datenbasis klein ist.
K_CLAMP = (0.1, 2.5)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"positions": {}, "matchdays_processed": []}


def save_state(state):
    os.makedirs(WEIGHTS_DIR, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _observations(prediction_entry, ist_by_player, liga_avg_win_prob_now):
    """Liefert (pos, x, y)-Tupel für EINEN Manager - s. Moduldocstring."""
    obs = []
    for p in prediction_entry.get("lineup", []):
        pa_ist = ist_by_player.get(p["pid"])
        factors = p.get("factors") or {}
        if not pa_ist or not factors:
            continue
        win_prob_ist = pa_ist.get("win_prob_ist")
        punkte_ist = pa_ist.get("punkte")
        einsatz_ist = pa_ist.get("einsatzfaktor")
        basis = factors.get("basis")
        if win_prob_ist is None or punkte_ist is None or not basis or einsatz_ist is None:
            continue
        if einsatz_ist <= 0:
            continue  # nicht gespielt - keine Aussage über den Ausgang-Effekt möglich
        form = factors.get("formfaktor", 1.0) or 1.0
        zu_null_ist = pa_ist.get("zu_null_bonus", factors.get("zu_null_bonus", 0.0)) or 0.0
        baseline = basis * einsatz_ist * form
        if baseline <= 0:
            continue
        x = win_prob_ist - liga_avg_win_prob_now
        if abs(x) < 1e-6:
            continue  # x=0 trägt nichts zur Steigungsschätzung bei
        y = (punkte_ist - zu_null_ist) / baseline - 1
        obs.append((p["pos"], x, y))
    return obs


def update_from_matchday(league_name, matchday, prediction, retrospective_teams,
                         liga_avg_win_prob_now):
    """
    Akkumuliert Beobachtungen aus EINEM abgeschlossenen Spieltag in den
    persistierten Zustand. `retrospective_teams`: das Ergebnis von
    retrospective_data.build_waterfall_report() (trägt `uid`, `implausible`,
    `ist_by_player` je Manager). Kein Effekt, wenn dieser Spieltag/diese
    Liga schon einmal verarbeitet wurde (idempotent) oder keine
    Beobachtungen liefert (z.B. alle Manager implausibel).

    Liefert (state, n_neu) - n_neu = Anzahl NEU hinzugekommener
    Beobachtungen in diesem Aufruf (0 = nichts Neues, z.B. schon verarbeitet).
    """
    key = f"{league_name}_md{matchday}"
    state = load_state()
    if key in state["matchdays_processed"]:
        return state, 0

    by_uid = {str(m["uid"]): m for m in prediction.get("managers", [])}
    n_neu = 0
    for t in retrospective_teams:
        if t.get("implausible"):
            continue  # AUSWERTUNG_spieltag2.md 1.4: Ist-Werte hier nicht vertrauenswürdig
        m = by_uid.get(str(t["uid"]))
        ist_by_player = t.get("ist_by_player")
        if not m or not ist_by_player:
            continue
        for pos, x, y in _observations(m, ist_by_player, liga_avg_win_prob_now):
            pd = state["positions"].setdefault(pos, {"sum_xy": 0.0, "sum_xx": 0.0, "n": 0})
            pd["sum_xy"] += x * y
            pd["sum_xx"] += x * x
            pd["n"] += 1
            n_neu += 1
    state["matchdays_processed"].append(key)
    save_state(state)
    return state, n_neu


def _position_blend(pos, prior, pd):
    """
    Liefert (blended_k, empirical_oder_None, weight, grund) für EINE
    Position. `grund` erklärt, warum (nicht) geblendet wurde - "kein
    Blend" ist der Normalfall in den ersten Spieltagen, kein Fehler.

    **Vorzeichen-Constraint (live gefunden, 2026-08-21, erster echter
    Kalibrierungslauf)**: eine höhere realisierte Sieg-WK darf die
    Punkteerwartung laut Modellprämisse NIE senken - `k` muss positiv
    bleiben. Live beobachtet: nach nur EINEM Spieltag lieferte die rohe
    OLS-Schätzung für ABW/MF/ANG NEGATIVE Werte (-0.2 bis -0.5), die
    trotz Vertrauensgewichtung den Blend bereits 34-44% Richtung eines
    domänen-widersprüchlichen Werts gezogen hätten. Eine negative
    empirische Schätzung wird jetzt NIE geblendet (Prior bleibt exakt
    erhalten) - sie ist bei so kleinen Stichproben praktisch immer
    Rauschen (ein paar Ausreißer-Einzelleistungen auf der Verliererseite),
    kein Signal. Wird trotzdem geloggt (Transparenz), nur nicht angewendet.
    """
    if not pd or pd["sum_xx"] <= 0 or pd["n"] < 5:
        return prior, None, 0.0, "zu wenig Daten für einen Nudge"
    empirical = pd["sum_xy"] / pd["sum_xx"]
    n = pd["n"]
    weight = n / (n + CALIBRATION_N0)
    if empirical <= 0:
        return prior, empirical, weight, "empirisch NEGATIV - Domänen-Constraint (k>0) verletzt, Prior beibehalten"
    blended = weight * empirical + (1 - weight) * prior
    lo, hi = K_CLAMP
    return round(max(lo, min(hi, blended)), 3), empirical, weight, "geblendet"


def blended_opponent_k(state=None):
    """
    Liefert {pos: k} - vertrauensgewichteter Blend aus Prior und
    empirischer OLS-Steigung (s. Moduldocstring + `_position_blend()` für
    den Vorzeichen-Schutz). Ohne Zustandsdatei/ohne ausreichend
    Beobachtungen (n<5) oder bei negativer empirischer Schätzung: reiner
    Prior, kein Rateversuch. `coach.py` ruft das beim Modulimport einmal
    auf (kein Zusatzaufwand pro Spieler-Berechnung).
    """
    if state is None:
        state = load_state()
    result = {}
    for pos, prior in OPPONENT_K_PRIOR.items():
        pd = state.get("positions", {}).get(pos)
        k, _, _, _ = _position_blend(pos, prior, pd)
        result[pos] = k
    return result


def calibration_summary(state=None):
    """Klartext-Übersicht je Position (Konsole/Transparenz) - Prior vs.
    aktueller Blend vs. rohe empirische Schätzung, plus Beobachtungszahl."""
    if state is None:
        state = load_state()
    lines = []
    for pos, prior in OPPONENT_K_PRIOR.items():
        pd = state.get("positions", {}).get(pos, {"n": 0})
        n = pd.get("n", 0)
        k, empirical, weight, grund = _position_blend(pos, prior, pd)
        emp_txt = f"{empirical:.3f}" if empirical is not None else "-"
        if k == prior:
            lines.append(f"{pos}: Prior {prior} behalten (n={n}, empirisch {emp_txt} - {grund})")
        else:
            lines.append(f"{pos}: Prior {prior} -> Blend {k} "
                         f"(empirisch {emp_txt}, n={n}, Vertrauensgewicht {weight:.0%})")
    return lines
