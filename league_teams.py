"""
Team-Analyse aller Liga-Manager (SPEC_gebote_ki_team_KOMPLETT.md Abschnitt 3
+ 6). "Das ist laut Nutzer das wichtigste Modul" - erst im Vergleich zu den
anderen Managern wird der eigentliche Handlungsbedarf sichtbar.

Datenbeschaffung (12-23 Requests/Liga/Lauf, s. Abschnitt 6.4.2): Manager-
Liste aus `/ranking` (bereits für die KPIs geladen), je Manager
`GET managers/{uid}/squad` - liefert Punkte/MW/Momentum UND die ECHTE
gesetzte Aufstellung über `lo` (Startelf-Slot). Keine Bestmöglich-Annahme,
keine Einzel-Spielerabrufe nötig (Kader-Response enthält `ap`, `mv`,
`tfhmvt`, `sdmvt`, `st` bereits).

**Scope dieser ersten Fassung**: 3.1 (erwartete Punkte je Spieler, reuse
coach.py) + 3.3 (Manager-Kennzahlen, ohne Streuung/Risikoprofil) + 3.5
(Übersichtstabelle). BEWUSST NICHT enthalten:
- 3.2 (KI-Einsatzminuten-Faktor) - eigener Umbauschritt der llm_insights.py-
  Schicht, hier nicht angefasst
- 3.4 (Abweichungszerlegung letzter Spieltag) - technisch nicht baubar/
  testbar vor dem ersten echten Spieltag (Saisonstart laut `ranking.nss`
  zum Zeitpunkt dieser Änderung 2 Tage entfernt) - es gibt schlicht noch
  keine Ist-Werte zum Vergleich
- Risikoprofil (Streuung aus `ph`) - `ph` zeigt in der Saisonvorbereitung
  nur zukünftige, nicht gespielte Spieltage (`hp: false` durchgängig,
  s. coach.py-Erfahrung), eine Streuung ist daraus nicht berechenbar
"""

import time
from collections import Counter

from fixtures import fixture_ease_for_team
from odds import fixture_ease_odds
import coach

POS_NAMES = {1: "TW", 2: "ABW", 3: "MF", 4: "ANG"}
DEPTH_THRESHOLD = 60  # Erstkalibrierung: "Tiefe" = Spieler ab dieser erwarteten Punktzahl


def fetch_team_map(kb, cid):
    """tid -> Teamname aus der Competition-Tabelle - für die Umrechnung von
    `tid` (einziges Team-Feld im Manager-Kader-Response) in einen Namen,
    den fixture_ease_for_team/fixture_ease_odds matchen können."""
    table = kb.get_competition_table(cid)
    return {str(t["tid"]): t.get("tn", "") for t in table.get("it", [])}


def analyze_manager(kb, cid, league_id, uid, name, tid_to_name, strength_map,
                    upcoming, fixture_mode, matcher, sleep=0.15,
                    liga_avg_win_prob=0.5):
    """
    Liefert die vollständige Analyse EINES Managers: Kader mit erwarteten
    Punkten (coach.expected_points, ohne Spielverlaufsfaktor - kein KI-
    Kontext pro Mitspieler-Kader vorgesehen, s. Modul-Docstring), echte
    Startelf/Bank, Kennzahlen (Prognose/Kaderstärke/Effizienz/Tiefe/
    Klumpenrisiko), Formation aus der Positionsverteilung.

    `liga_avg_win_prob` (SPEC_kalibrierung_fairvalue.md Abschnitt 0/2.2)
    zentriert den Gegnerfaktor auf die echte Liga-Ø-Sieg-WK - ohne diesen
    Fix lag die Spieltagsprognose systematisch bei ~250-450 statt den aus
    `pspts`/Spieltagszahl ableitbaren realen ~900 (s. CLAUDE.md).
    **Kein Peer-Vergleichswert (3.2) hier** - bewusst ausgeklammert, würde
    zusätzlich Teamstärke+Farbe je Spieler und einen ligaweiten Peer-Lookup
    brauchen (aus league_board.py, dort aber erst nach der vollen
    Populations-Analyse verfügbar); Mitspieler-Kader ohne Punktehistorie
    fallen hier auf die MW-Schätzung zurück wie schon vor diesem Fix.
    """
    squad_data = kb.get_manager_squad(league_id, uid)
    time.sleep(sleep)
    items = squad_data.get("it", []) or []

    players = []
    for p in items:
        pos = POS_NAMES.get(p.get("pos"))
        if pos is None:
            continue
        team_name = tid_to_name.get(str(p.get("tid")), "")
        if fixture_mode == "odds":
            ease, opponents = fixture_ease_odds(team_name, upcoming, matcher)
        else:
            ease, opponents = fixture_ease_for_team(team_name, upcoming, strength_map)
        ep, factors = coach.expected_points(
            pos, p.get("ap", 0), None, p.get("st", 0), p.get("prob", 3), ease,
            team_name=team_name, mv=p.get("mv", 0), liga_avg_win_prob=liga_avg_win_prob)
        players.append({
            "id": str(p.get("pi")), "name": p.get("pn", "?"), "pos": pos,
            "team": team_name, "lo": p.get("lo"), "mv": p.get("mv", 0) or 0,
            "ap": p.get("ap", 0) or 0, "tfhmvt": p.get("tfhmvt", 0) or 0,
            "sdmvt": p.get("sdmvt"), "st": p.get("st", 0),
            "expected_points": ep, "ep_factors": factors, "opponents": opponents,
        })

    # SPEC_kalibrierung_fairvalue.md 2.1: gilt laut Spec ausdrücklich auch
    # für die Bewertung der Mitspieler-Teams, nicht nur die eigene Elf.
    coach.adjust_for_self_play_duels(players, matcher)

    xi = sorted((p for p in players if p["lo"] is not None), key=lambda p: p["lo"])
    bench = [p for p in players if p["lo"] is None]

    prognose = round(sum(p["expected_points"] for p in xi), 1)
    # Bandbreite grob aus den Einzel-Einsatzfaktoren - ohne Streuungsdaten
    # (s. Modul-Docstring) eine einfache +/-15%-Näherung, klar als solche
    # ausgewiesen statt eine Scheingenauigkeit vorzutäuschen.
    prognose_range = (round(prognose * 0.85, 1), round(prognose * 1.15, 1))

    lineup_opt = coach.optimize_lineup(players) if players else None
    kaderstaerke = lineup_opt["best_total"] if lineup_opt and lineup_opt["best"] else None
    effizienz = round(prognose / kaderstaerke * 100, 1) if kaderstaerke else None

    tiefe = sum(1 for p in players if p["expected_points"] >= DEPTH_THRESHOLD)

    team_points = Counter()
    for p in xi:
        if p["team"]:
            team_points[p["team"]] += p["expected_points"]
    klumpenrisiko = None
    top_team = None
    if prognose > 0 and team_points:
        top_team, top_pts = team_points.most_common(1)[0]
        klumpenrisiko = round(top_pts / prognose * 100, 1)

    formation_counts = Counter(p["pos"] for p in xi)
    formation = (f"{formation_counts.get('ABW', 0)}-{formation_counts.get('MF', 0)}-"
                f"{formation_counts.get('ANG', 0)}") if xi else None
    # Formationsdynamik-Textbaustein (SPEC 2.4) - rein informativ.
    hint = coach.formation_hint(xi)

    return {
        "uid": uid, "name": name,
        "squad_size": len(players), "bench_size": len(bench),
        "xi": xi, "bench": bench,
        "prognose": prognose, "prognose_range": prognose_range,
        "kaderstaerke": kaderstaerke, "effizienz": effizienz,
        "tiefe": tiefe, "klumpenrisiko": klumpenrisiko, "top_team": top_team,
        "formation": formation, "formation_hint": hint,
        "empty_slots": max(0, 11 - len(xi)),
    }


def build_league_teams(kb, cid, league_id, ranking, strength_map, upcoming,
                       fixture_mode, matcher, sleep=0.15, liga_avg_win_prob=0.5):
    """
    Analysiert ALLE Manager der Liga (aus `ranking.us`, bereits geladen -
    kein Zusatz-Call). Liefert eine nach Prognose sortierte Liste von
    analyze_manager()-Ergebnissen plus Vorsaison-Baseline (Punkt 6.2) je
    Manager als Kontext.
    """
    tid_to_name = fetch_team_map(kb, cid)
    time.sleep(sleep)

    managers = []
    for u in ranking.get("us", []) or []:
        uid, name = u.get("i"), (u.get("n") or "").strip()
        if not uid:
            continue
        analysis = analyze_manager(kb, cid, league_id, uid, name, tid_to_name,
                                   strength_map, upcoming, fixture_mode, matcher, sleep,
                                   liga_avg_win_prob=liga_avg_win_prob)
        analysis["vorsaison"] = {
            "platz": u.get("psp"), "punkte": u.get("pspts"), "siege": u.get("pswc"),
        }
        managers.append(analysis)

    managers.sort(key=lambda m: -m["prognose"])
    return managers
