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
