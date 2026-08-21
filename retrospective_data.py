"""
Player-Level-Ist-Werte für die Wasserfall-Zerlegung (AUSWERTUNG_spieltag1.md).

Holt für jeden Spieler in einer gespeicherten Prognosedatei (Datei A,
prediction_log.save_matchday_prediction()) den tatsächlichen Spieltags-
Ausgang über get_player_details() (ph+mdsum) - dieselbe Quelle, die
scoring.player_reliability_profile() für die Sieg/Niederlage-Auswertung
schon nutzt. Player-Level-Fetches sind teuer (~1 Request je Spieler, bei elf
11er-Elfs ~121 Requests) - deshalb NUR einmal je Spieltag geholt und in
data/actuals/<liga>_md<N>_players.json zwischengespeichert (idempotent - ein
zweiter Aufruf für denselben Spieltag liest nur noch die Datei).
"""
import json
import os
import re
import time

PLAYER_ACTUALS_DIR = os.path.join("data", "actuals")


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def player_actuals_path(league_name, matchday):
    return os.path.join(PLAYER_ACTUALS_DIR, f"{_slug(league_name)}_md{matchday}_players.json")


def _extract_actual(detail, matchday):
    """
    Liefert {"played", "punkte_ist", "team_result", "team_conceded"} für
    EINEN Spieler aus seinem get_player_details()-Response. `ph` ist
    chronologisch bis `day` - derselbe by_day-Aufbau wie
    scoring.player_reliability_profile(), hier auf einen einzelnen
    Spieltag statt einer ganzen Historie angewendet.
    """
    ph = detail.get("ph") or []
    mdsum = detail.get("mdsum") or []
    day = detail.get("day")
    n = len(ph)
    by_day_ph = ({day - (n - 1 - idx): entry for idx, entry in enumerate(ph)}
                if day is not None else {})
    ph_entry = by_day_ph.get(matchday)
    played = bool(ph_entry and ph_entry.get("hp"))
    punkte_ist = float(ph_entry["p"]) if played and ph_entry.get("p") is not None else 0.0

    md_entry = next((m for m in mdsum if m.get("day") == matchday and m.get("mdst") == 2), None)
    team_result, team_conceded = None, None
    if md_entry:
        tid = str(detail.get("tid", ""))
        t1, t2 = str(md_entry.get("t1")), str(md_entry.get("t2"))
        t1g, t2g = md_entry.get("t1g", 0) or 0, md_entry.get("t2g", 0) or 0
        gf = ga = None
        if tid == t1:
            gf, ga = t1g, t2g
        elif tid == t2:
            gf, ga = t2g, t1g
        if gf is not None:
            team_result = "Sieg" if gf > ga else "Niederlage" if gf < ga else "Unentschieden"
            team_conceded = ga

    return {"played": played, "punkte_ist": punkte_ist,
           "team_result": team_result, "team_conceded": team_conceded,
           "ap_now": detail.get("ap"), "pos": detail.get("pos")}


def _win_prob_ist(result):
    return {"Sieg": 1.0, "Unentschieden": 0.5, "Niederlage": 0.0}.get(result)


def build_ist(player_actual, pos, punktetyp_idx, liga_avg_win_prob_now):
    """
    Übersetzt einen _extract_actual()-Eintrag in das ist-Dict, das
    retrospective.waterfall_player() erwartet (M_ist/G_ist/Z_ist/Punkte).
    Identische Herleitung wie im ursprünglichen analyze_matchday1.py-
    Einmallauf (AUSWERTUNG_spieltag1.md): M_ist grob binär (gespielt ja/
    nein, echte Einsatzminuten nicht abrufbar), G_ist über dieselbe
    opponent_factor()-Formel wie die Prognose, aber mit dem REALISIERTEN
    Ausgang statt der Sieg-WK, Z_ist ein bekannter Fakt (Team zu Null
    gespielt oder nicht) statt einer Wahrscheinlichkeit.
    """
    import coach
    m_ist = 1.0 if player_actual["played"] else 0.0
    win_prob_ist = _win_prob_ist(player_actual.get("team_result"))
    if win_prob_ist is not None:
        g_ist = coach.opponent_factor(pos, win_prob_ist, liga_avg_win_prob_now, punktetyp_idx)
    else:
        g_ist = None  # Ergebnis unbekannt -> fällt in der Kaskade auf die Prognose zurück
    z_ist = None
    if pos in coach.ZU_NULL_PRAEMIE and player_actual.get("team_conceded") is not None:
        z_ist = coach.ZU_NULL_PRAEMIE[pos] if player_actual["team_conceded"] == 0 else 0.0
    ist = {"einsatzfaktor": m_ist, "punkte": player_actual["punkte_ist"]}
    if g_ist is not None:
        ist["gegnerfaktor"] = g_ist
    if z_ist is not None:
        ist["zu_null_bonus"] = z_ist
    # Für weights_calibration.py (OPPONENT_K-Nachjustierung): der ROHE
    # realisierte Ausgang, unabhängig vom aktuell geltenden K (g_ist ist
    # bereits mit K multipliziert, dafür ungeeignet als Regressions-Input).
    if win_prob_ist is not None:
        ist["win_prob_ist"] = win_prob_ist
    return ist


def build_waterfall_report(kb, league_id, league_name, matchday, liga_avg_win_prob_now,
                           prediction=None, official_actuals=None):
    """
    Echte Wasserfall-Zerlegung (Einsatz/Ausgang/Zu-Null/Leistung) für ALLE
    Manager eines ABGESCHLOSSENEN Spieltags - main.py-taugliche
    Zusammenfassung von fetch_player_actuals()+build_ist()+
    retrospective.waterfall_manager(), erstmals als analyze_matchday1.py-
    Einmallauf gebaut (AUSWERTUNG_spieltag1.md), hier für den TÄGLICHEN
    Report nutzbar gemacht - User-Feedback "mir fehlt komplett die
    Transparenz über die Abweichungen", die bisherige `deviation_report()`-
    Zeile zeigte nur eine nackte Ø-Fehler-% ohne jede Erklärung, obwohl der
    Zerlegungsmechanismus in retrospective.py bereits fertig und per
    Trockenlauf verifiziert bereitlag.

    Kostet ~1 API-Call je EINZIGARTIGEM Startelf-Spieler über alle Manager
    (typisch ~100-120/Liga bei 11 Managern, analog league_board.py) - aber
    NUR beim ERSTEN Aufruf für einen Spieltag (fetch_player_actuals() cached
    nach data/actuals/<liga>_md<N>_players.json, jeder weitere Tageslauf
    liest nur noch die Datei, kein Zusatz-Call).

    None ohne gespeicherte Prognosedatei (Datei A) für diesen Spieltag -
    z.B. La Liga vor 2026-08-05 oder wenn kickoff_first nie bekannt war.

    official_actuals: optional {uid: offizielle Spieltagspunkte} (aus
    `ranking.us[].mdp`, main.py hat das für deviation_report() ohnehin schon
    geladen) - **Bugfix, live vom User gemeldet**: die aus Einzelspielern
    summierte "Ist"-Punktzahl kann von der offiziellen Zahl abweichen (für
    Spieltag 1 nachweislich, s. retrospective.waterfall_manager()-
    Docstring) - wird `official_actuals` mitgegeben, gewinnt der offizielle
    Wert, die Differenz landet transparent in `delta_datenluecke` statt
    unkommentiert falsch angezeigt zu werden.
    """
    import retrospective
    if prediction is None:
        from prediction_log import load_matchday_prediction
        prediction = load_matchday_prediction(league_name, matchday)
    if not prediction:
        return None
    actuals = fetch_player_actuals(kb, league_id, league_name, matchday, prediction)
    teams = []
    for m in prediction.get("managers", []):
        ist_by_player = {}
        for p in m.get("lineup", []):
            pa = actuals.get(p["pid"])
            if not pa:
                continue
            punktetyp_idx = p.get("factors", {}).get("punktetyp_idx")
            ist_by_player[p["pid"]] = build_ist(pa, p["pos"], punktetyp_idx, liga_avg_win_prob_now)
        official = (official_actuals or {}).get(str(m["uid"]))
        team = retrospective.waterfall_manager(m, ist_by_player, official_actual=official)
        team["uid"], team["name"] = m["uid"], m["name"]
        # weights_calibration.py braucht die rohen Ist-Werte je Spieler
        # (win_prob_ist etc.) für die OPPONENT_K-Regression - hier schon
        # einmal berechnet, kein Grund, sie in main.py erneut zu bauen.
        team["ist_by_player"] = ist_by_player
        teams.append(team)
    return teams


def fetch_player_actuals(kb, league_id, league_name, matchday, prediction, sleep=0.25):
    """
    Liefert {pid: {...}} für ALLE Spieler in
    `prediction["managers"][].lineup[]`. Cached in player_actuals_path() -
    ein zweiter Aufruf für denselben Spieltag/dieselbe Liga liest nur noch
    die Datei, kein erneuter API-Call.
    """
    path = player_actuals_path(league_name, matchday)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    actuals = {}
    seen = set()
    for m in prediction.get("managers", []):
        for p in m.get("lineup", []):
            pid = p["pid"]
            if pid in seen:
                continue
            seen.add(pid)
            detail = kb.get_player_details(league_id, pid)
            time.sleep(sleep)
            actuals[pid] = _extract_actual(detail, matchday)

    os.makedirs(PLAYER_ACTUALS_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(actuals, f, ensure_ascii=False, indent=2)
    return actuals
