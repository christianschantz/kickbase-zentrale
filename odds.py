"""
Wettquoten als primäre Quelle für Team-Stärke & Spiel-Schwierigkeit.

Quelle: football-data.co.uk/fixtures.csv - kostenlos, kein Key.
Enthält die anstehenden Spiele mit echten Buchmacher-Quoten (Bet365 u.a.).
Div-Codes: D2 = 2. Bundesliga, SP1 = La Liga (Primera División).

Daraus berechnen wir:
- pro Spiel die impliziten Siegwahrscheinlichkeiten (Quoten-Marge entfernt)
- pro Team die "Power" = Ø Siegwahrscheinlichkeit über kommende Spiele
  -> das ist die Buchmacher-Definition von "Topteam", nicht die Vorsaison-Tabelle
"""

import csv
import io
import requests

FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"

# Quoten-Spalten in Präferenz-Reihenfolge (Heim/Unentschieden/Auswärts)
ODDS_COLS = [("B365H", "B365D", "B365A"),
             ("AvgH", "AvgD", "AvgA"),
             ("MaxH", "MaxD", "MaxA"),
             ("PSH", "PSD", "PSA")]


def _implied_probs(oh, od, oa):
    """Quoten -> Wahrscheinlichkeiten, Buchmacher-Marge herausnormiert."""
    try:
        ih, idr, ia = 1 / float(oh), 1 / float(od), 1 / float(oa)
    except (ValueError, ZeroDivisionError, TypeError):
        return None
    s = ih + idr + ia
    return ih / s, idr / s, ia / s


def _implied_probs_2way(o_a, o_b):
    """Zwei-Wege-Quoten (z.B. Über/Unter 2,5) -> Wahrscheinlichkeiten, Marge
    entfernt. Liefert (p_a, p_b) oder None."""
    try:
        ia, ib = 1 / float(o_a), 1 / float(o_b)
    except (ValueError, ZeroDivisionError, TypeError):
        return None
    s = ia + ib
    return ia / s, ib / s


# SPEC_spielertyp_matchkontext.md 1.2: Über/Unter-2,5- und Asian-Handicap-
# Spalten aus derselben fixtures.csv, Präferenz-Reihenfolge wie ODDS_COLS.
OU_COLS = [("B365>2.5", "B365<2.5"), ("Avg>2.5", "Avg<2.5"), ("Max>2.5", "Max<2.5")]


def _match_context(row):
    """
    Erwartete Gesamttore (aus Über/Unter-2,5-Quoten) und erwartete
    Tordifferenz aus HEIMSICHT (aus der Asian-Handicap-Linie `AHh` - der
    Markt-Konsens für die erwartete Tordifferenz, negativ = Heimteam
    favorisiert um diese Anzahl Tore). Beide Werte sind grobe, lineare
    Näherungen (Erstkalibrierung, wie an anderer Stelle im Projekt üblich -
    kein Poisson-Modell, bewusst einfach und nachvollziehbar, noch nicht
    gegen echte Spieltage geprüft). Liefert (erwartete_tore_gesamt,
    erwartete_tordifferenz_heim) - jeweils None ohne passende Spalte.
    """
    tore = None
    for c_over, c_under in OU_COLS:
        if row.get(c_over) and row.get(c_under):
            probs = _implied_probs_2way(row[c_over], row[c_under])
            if probs:
                p_over, _ = probs
                # 2,5 Tore ist die Über/Unter-Schwelle selbst - bei
                # p_over=0,5 (Buchmacher neutral) ist das der Erwartungswert.
                # Lineare Näherung: ±0,5 Buchmacher-Ausschlag verschiebt die
                # Erwartung um ±1,5 Tore, geclippt auf eine plausible
                # Bandbreite (torarme Partie ~1,8, torreiche ~3,5).
                tore = max(1.5, min(4.0, 2.5 + (p_over - 0.5) * 3.0))
                break
    tordiff = None
    ahh = row.get("AHh")
    if ahh not in (None, ""):
        try:
            tordiff = -float(ahh)
        except ValueError:
            pass
    return tore, tordiff


def load_fixture_odds(div):
    """
    Liefert für eine Division:
      upcoming:      {team: [(gegner, siegwahrscheinlichkeit), ...]}
      power:         {team: 0..1}  (Ø Sieg-WK, min-max-normiert)
      match_context: {team: [(gegner, erwartete_tore_gesamt,
                      erwartete_tordifferenz_team), ...]} - SPEC_
                      spielertyp_matchkontext.md 1.2, aus Über/Unter- und
                      Handicap-Spalten (s. _match_context()). Leer, wenn
                      diese Spalten für die Division nicht gefüllt sind
                      (bei kleineren Ligen manchmal lückenhaft) - dann
                      fallen Aufrufer auf die reine Sieg-WK zurück.
    Leere Dicts, wenn die CSV (noch) keine Spiele für die Division enthält
    (z.B. tief in der Sommerpause).
    """
    try:
        r = requests.get(FIXTURES_URL, timeout=20)
        r.raise_for_status()
    except requests.RequestException:
        return {}, {}, {}

    upcoming, win_probs, match_context = {}, {}, {}
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        if (row.get("Div") or "").strip() != div:
            continue
        home, away = (row.get("HomeTeam") or "").strip(), (row.get("AwayTeam") or "").strip()
        if not home or not away:
            continue
        probs = None
        for ch, cd, ca in ODDS_COLS:
            if row.get(ch) and row.get(cd) and row.get(ca):
                probs = _implied_probs(row[ch], row[cd], row[ca])
                if probs:
                    break
        if not probs:
            continue
        ph, _, pa = probs
        upcoming.setdefault(home, []).append((away, ph))
        upcoming.setdefault(away, []).append((home, pa))
        win_probs.setdefault(home, []).append(ph)
        win_probs.setdefault(away, []).append(pa)

        tore, tordiff_heim = _match_context(row)
        if tore is not None or tordiff_heim is not None:
            tordiff_away = -tordiff_heim if tordiff_heim is not None else None
            match_context.setdefault(home, []).append((away, tore, tordiff_heim))
            match_context.setdefault(away, []).append((home, tore, tordiff_away))

    if not win_probs:
        return {}, {}, {}

    avg = {t: sum(v) / len(v) for t, v in win_probs.items()}
    lo, hi = min(avg.values()), max(avg.values())
    span = (hi - lo) or 1.0
    power = {t: (v - lo) / span for t, v in avg.items()}
    return upcoming, power, match_context


ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"


def load_fixture_odds_api(sport_key, api_key, region="eu"):
    """
    Fallback, wenn football-data.co.uk/fixtures.csv (noch) leer ist (tiefe
    Sommerpause). the-odds-api.com liefert dann trotzdem reguläre Spielquoten
    (h2h) fuer viele Ligen - gleiche Auswertung wie load_fixture_odds, nur
    ueber mehrere Buchmacher gemittelt statt einer bevorzugten Quotenspalte.
    Kostet Kontingent (Free Tier: 500 Requests/Monat) -> nur als Fallback nutzen,
    nicht pro Spieler aufrufen. Outright-/Meisterquoten sind fuer 2. Bundesliga
    und La Liga auf diesem Tier NICHT verfuegbar (INVALID_MARKET_COMBO, geprueft).
    """
    if not sport_key or not api_key:
        return {}, {}
    try:
        r = requests.get(
            ODDS_API_URL.format(sport=sport_key),
            params={"apiKey": api_key, "regions": region, "markets": "h2h",
                    "oddsFormat": "decimal"},
            timeout=20,
        )
        remaining = r.headers.get("x-requests-remaining")
        if remaining is not None:
            print(f"   ℹ️ the-odds-api Kontingent verbleibend: {remaining}")
        r.raise_for_status()
        games = r.json()
    except requests.RequestException:
        return {}, {}

    upcoming, win_probs = {}, {}
    for g in games:
        home, away = g.get("home_team", ""), g.get("away_team", "")
        if not home or not away:
            continue
        ph_list, pa_list = [], []
        for bm in g.get("bookmakers", []):
            for m in bm.get("markets", []):
                if m.get("key") != "h2h":
                    continue
                outcomes = {o["name"]: o["price"] for o in m.get("outcomes", [])}
                oh, oa, od = outcomes.get(home), outcomes.get(away), outcomes.get("Draw")
                if oh and oa and od:
                    probs = _implied_probs(oh, od, oa)
                    if probs:
                        ph_list.append(probs[0])
                        pa_list.append(probs[2])
        if not ph_list:
            continue
        ph, pa = sum(ph_list) / len(ph_list), sum(pa_list) / len(pa_list)
        upcoming.setdefault(home, []).append((away, ph))
        upcoming.setdefault(away, []).append((home, pa))
        win_probs.setdefault(home, []).append(ph)
        win_probs.setdefault(away, []).append(pa)

    if not win_probs:
        return {}, {}
    avg = {t: sum(v) / len(v) for t, v in win_probs.items()}
    lo, hi = min(avg.values()), max(avg.values())
    span = (hi - lo) or 1.0
    power = {t: (v - lo) / span for t, v in avg.items()}
    return upcoming, power


def fixture_ease_odds(kb_team_name, upcoming, matcher, next_n=3):
    """
    Spielplan-Leichtigkeit direkt aus Sieg-WKs: Ø Siegwahrscheinlichkeit
    des Teams in den nächsten n Spielen. Rückgabe: (ease 0..1, Anzeige-Liste
    ['vs Schalke (58%)', ...]).
    """
    if not kb_team_name or not upcoming:
        return 0.5, []
    matched = matcher(kb_team_name, list(upcoming.keys()))
    if not matched:
        return 0.5, []
    games = upcoming[matched][:next_n]
    if not games:
        return 0.5, []
    ease = sum(p for _, p in games) / len(games)
    display = [f"{opp} ({p:.0%} Sieg-WK)" for opp, p in games]
    return ease, display


def next_match_context(kb_team_name, match_context, matcher):
    """
    SPEC_spielertyp_matchkontext.md 1.2: erwartete Gesamttore + erwartete
    Tordifferenz (aus TEAM-Sicht) für die NÄCHSTE Partie des Teams - anders
    als fixture_ease_odds() (Ø über mehrere kommende Spiele) wird hier nur
    das nächste Spiel betrachtet, weil die Zu-Null-Frage matchday-spezifisch
    ist, nicht saisonweit gemittelt werden soll. Liefert (erwartete_tore,
    erwartete_tordifferenz) - (None, None) ohne Treffer/ohne match_context
    (z.B. La Liga über the-odds-api/Tabellen-Fallback, die keine Handicap-/
    Über-Unter-Daten liefern - Aufrufer fallen dann auf die reine Sieg-WK
    zurück, s. coach.zu_null_bonus()).
    """
    if not kb_team_name or not match_context:
        return None, None
    matched = matcher(kb_team_name, list(match_context.keys()))
    if not matched or not match_context[matched]:
        return None, None
    _, tore, tordiff = match_context[matched][0]
    return tore, tordiff
