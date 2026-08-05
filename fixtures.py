"""
Spielplan & Gegnerstärke.

Problem in der Saisonvorbereitung: Kickbase' mdsum enthält nur beendete
Spiele der Vorsaison und die aktuelle Tabelle ist leer. Lösung:
- Kommende Spiele direkt aus dem OpenLigaDB-Spielplan (steht vor Saisonstart)
- Gegnerstärke: aktuelle Tabelle, Fallback = Abschlusstabelle der Vorsaison
"""

import requests
from difflib import SequenceMatcher

OL_TABLE = "https://api.openligadb.de/getbltabelle/{shortcut}/{season}"
OL_MATCHES = "https://api.openligadb.de/getmatchdata/{shortcut}/{season}"
FD_STANDINGS = "https://api.football-data.org/v4/competitions/{comp}/standings"
FD_MATCHES = "https://api.football-data.org/v4/competitions/{comp}/matches?status=SCHEDULED"


def _get_json(url, headers=None):
    try:
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return None


# ---------- OpenLigaDB (2. Bundesliga) ----------

def get_table(season="2026", shortcut="bl2"):
    """Tabelle mit Vorsaison-Fallback (wichtig in der Saisonvorbereitung)."""
    table = _get_json(OL_TABLE.format(shortcut=shortcut, season=season)) or []
    if not table:
        prev = str(int(season) - 1)
        table = _get_json(OL_TABLE.format(shortcut=shortcut, season=prev)) or []
        if table:
            print(f"ℹ️ Tabelle {season} leer -> nutze Abschlusstabelle {prev} als Gegnerstärke.")
    return table


def get_season_start_date(season="2026", shortcut="bl2"):
    """
    Frühestes Datum aus den noch nicht gespielten Partien des Saison-
    Spielplans - in der Saisonvorbereitung (noch kein Spiel gelaufen)
    entspricht das Spieltag 1. Live verifiziert (2026-08-05): 2. Bundesliga
    Spieltag 1 = 2026-08-07 18:30 UTC. Liefert ein UTC-datetime oder None
    (z.B. wenn `shortcut` keine OpenLigaDB-Liga ist - nur für die 2.
    Bundesliga verifiziert, La Liga läuft über football-data.org ohne
    Datumsfeld in unserer bisherigen Anbindung).
    """
    from datetime import datetime
    matches = _get_json(OL_MATCHES.format(shortcut=shortcut, season=season)) or []
    dates = []
    for m in matches:
        if m.get("matchIsFinished"):
            continue
        raw = m.get("matchDateTimeUTC")
        if not raw:
            continue
        try:
            dates.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            continue
    return min(dates) if dates else None


def get_upcoming_by_team(season="2026", shortcut="bl2", next_n=3):
    """
    Kompletten Saison-Spielplan laden und pro Team die nächsten n noch nicht
    gespielten Partien (Gegnernamen) zurückgeben: {teamName: [gegner1, ...]}
    """
    matches = _get_json(OL_MATCHES.format(shortcut=shortcut, season=season)) or []
    upcoming = {}
    for m in matches:
        if m.get("matchIsFinished"):
            continue
        t1 = m.get("team1", {}).get("teamName", "")
        t2 = m.get("team2", {}).get("teamName", "")
        if t1 and t2:
            upcoming.setdefault(t1, []).append(t2)
            upcoming.setdefault(t2, []).append(t1)
    return {t: opps[:next_n] for t, opps in upcoming.items()}


# ---------- football-data.org (La Liga) ----------

def get_table_football_data(competition="PD", api_key=""):
    if not api_key:
        return []
    data = _get_json(FD_STANDINGS.format(comp=competition),
                     headers={"X-Auth-Token": api_key})
    if data:
        total = next((s for s in data.get("standings", [])
                      if s.get("type") == "TOTAL"), None)
        if total:
            return [{"teamName": r["team"]["name"]} for r in total.get("table", [])]
    return []


def get_upcoming_by_team_football_data(competition="PD", api_key="", next_n=3):
    if not api_key:
        return {}
    data = _get_json(FD_MATCHES.format(comp=competition),
                     headers={"X-Auth-Token": api_key})
    upcoming = {}
    for m in (data or {}).get("matches", []):
        t1 = m.get("homeTeam", {}).get("name", "")
        t2 = m.get("awayTeam", {}).get("name", "")
        if t1 and t2:
            upcoming.setdefault(t1, []).append(t2)
            upcoming.setdefault(t2, []).append(t1)
    return {t: opps[:next_n] for t, opps in upcoming.items()}


# ---------- Stärke & Matching ----------

def build_strength_map(table):
    n = len(table)
    if n < 2:
        return {}
    return {row["teamName"]: 1 - (i / (n - 1)) for i, row in enumerate(table)}


def _best_match(name, candidates):
    """Fuzzy-Match Kickbase-Kurzname ('Kiel') -> OpenLigaDB ('Holstein Kiel')."""
    best, best_score = None, 0.0
    for c in candidates:
        score = SequenceMatcher(None, name.lower(), c.lower()).ratio()
        if name.lower() in c.lower() or c.lower() in name.lower():
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = c, score
    return (best, best_score) if best_score >= 0.5 else (None, 0.0)


def fixture_ease_for_team(kb_team_name, upcoming_by_team, strength_map, next_n=3):
    """
    0..1 (1 = leichte Gegner) + Gegnerliste für ein Kickbase-Team.
    Matcht den Kickbase-Teamnamen fuzzy auf die OpenLigaDB-Namen.
    """
    if not kb_team_name or not upcoming_by_team:
        return 0.5, []
    matched, _ = _best_match(kb_team_name, list(upcoming_by_team.keys()))
    if not matched:
        return 0.5, []
    opponents = upcoming_by_team[matched][:next_n]
    if not opponents:
        return 0.5, []
    eases = []
    for opp in opponents:
        strength = strength_map.get(opp)
        if strength is None:
            m2, _ = _best_match(opp, list(strength_map.keys()))
            strength = strength_map.get(m2, 0.5)
        eases.append(1 - strength)
    return sum(eases) / len(eases), opponents


# ---------- TheSportsDB (La Liga, kostenlos ohne Key) ----------
# Liga-ID 4335 = Spanish La Liga. Season-Format: "2026-2027".
TSDB_TABLE = "https://www.thesportsdb.com/api/v1/json/123/lookuptable.php?l={league_id}&s={season}"
TSDB_NEXT = "https://www.thesportsdb.com/api/v1/json/123/eventsnextleague.php?id={league_id}"


def get_table_tsdb(league_id="4335", season="2026-2027"):
    """Tabelle via TheSportsDB, mit Vorsaison-Fallback."""
    for s in (season, _prev_tsdb_season(season)):
        data = _get_json(TSDB_TABLE.format(league_id=league_id, season=s))
        rows = (data or {}).get("table") or []
        if rows:
            if s != season:
                print(f"ℹ️ TSDB-Tabelle {season} leer -> nutze {s}.")
            return [{"teamName": r.get("strTeam", "")} for r in rows]
    return []


def _prev_tsdb_season(season):
    try:
        a, b = season.split("-")
        return f"{int(a)-1}-{int(b)-1}"
    except (ValueError, AttributeError):
        return season


def get_upcoming_by_team_tsdb(league_id="4335", next_n=3):
    """Kommende Partien via TheSportsDB -> {teamName: [gegner, ...]}."""
    data = _get_json(TSDB_NEXT.format(league_id=league_id))
    upcoming = {}
    for ev in (data or {}).get("events") or []:
        t1, t2 = ev.get("strHomeTeam", ""), ev.get("strAwayTeam", "")
        if t1 and t2:
            upcoming.setdefault(t1, []).append(t2)
            upcoming.setdefault(t2, []).append(t1)
    return {t: opps[:next_n] for t, opps in upcoming.items()}


def team_strength_for(kb_team_name, strength_map, default=0.5):
    """Tabellen-Stärke (0..1) für einen Kickbase-Teamnamen (fuzzy)."""
    if not kb_team_name or not strength_map:
        return default
    matched, _ = _best_match(kb_team_name, list(strength_map.keys()))
    return strength_map.get(matched, default)


def league_avg_win_prob(fixture_mode, strength_map, upcoming):
    """
    Liga-Ø-Sieg-WK für die Gegnerfaktor-Zentrierung (SPEC_kalibrierung_
    fairvalue.md Abschnitt 0/1.2/2.2, verengt SPEC_spieltagsmodell_v2.md 1.2):
    Fußball hat 3 Ausgänge, die reale Ø-Siegwahrscheinlichkeit pro Team liegt
    bei ~35-40%, nicht bei 50% - ein Gegnerfaktor, der bei 0,5 zentriert,
    unterschätzt daher systematisch fast jeden Spieler.

    **Verengt auf `next_n=1`** (2026-08-05, SPEC_spieltagsmodell_v2.md 1.2:
    "Mittelwert der Sieg-WK über alle Partien DES Spieltags", nicht über
    mehrere künftige Spieltage gemittelt) - vorher wurden je Team die
    nächsten 3 Spiele gemittelt, was den Nullpunkt leicht in Richtung des
    generellen Liga-Durchschnitts über mehrere Spieltage verwässern konnte
    statt exakt auf DIESEN Spieltag zu zentrieren.

    odds-Modus (`upcoming` = {team: [(gegner, echte Sieg-WK), ...]} aus
    odds.py, chronologisch): Mittel der eigenen Sieg-WK des JEWEILS NÄCHSTEN
    Spiels über alle Teams - echte Buchmacher-Quoten, typischerweise klar
    unter 0,5. table/openligadb-Modus: `strength_map` ist über
    `build_strength_map()` uniform von 1 (Erster) bis 0 (Letzter) verteilt
    (`1 - i/(n-1)`) - das Mittel ist rechnerisch IMMER exakt 0,5, hier
    trotzdem generisch über die Daten berechnet statt hartkodiert. 0,5 als
    letzter Fallback ohne Datengrundlage.
    """
    if fixture_mode == "odds" and upcoming:
        team_next = [games[0][1] for games in upcoming.values() if games]
        if team_next:
            return sum(team_next) / len(team_next)
    if strength_map:
        vals = list(strength_map.values())
        return 1 - (sum(vals) / len(vals))
    return 0.5
