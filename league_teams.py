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


def _estimate_prob(p):
    """
    **Bugfix, live über die neue Diagnose-Kaskade gefunden (SPEC_
    spieltagsmodell_v2.md 1.2)**: `managers/{uid}/squad` liefert KEIN
    `prob`-Feld (anders als `get_player_details`, das für den eigenen Kader
    genutzt wird - s. kickbase_api.get_manager_squad()-Docstring, die 17
    dokumentierten Felder enthalten kein `prob`). Ohne diesen Fix defaultete
    `p.get("prob", 3)` für JEDEN Mitspieler-Spieler auf gelb (Einsatzfaktor
    0,60) - live verifiziert: die Diagnose-Kaskade zeigte "Einsatz ×0.6" für
    praktisch jeden Manager, EINSCHLIESSLICH des eigenen Teams (dessen
    `squad_classified`-Pfad über get_player_details() korrekt überwiegend
    blau/grün zeigt) - der Unterschied bewies, dass es an der Datenquelle
    lag, nicht am Modell.

    Näherung: der Manager hat den Spieler bereits real in die Startelf
    gestellt (`lo` gesetzt) - das ist ein STÄRKERES Signal als Kickbases
    eigene Prognose. Fit (st=0) + in der Startelf -> prob~2 (grün).
    Verletzt/angeschlagen (st=2) -> prob~4 (rot), unabhängig vom Lineup-
    Status (ein Manager stellt einen Verletzten manchmal trotzdem optimistisch
    auf). Bankspieler/unklarer Status -> prob=3 (gelb, neutral) wie bisher.
    """
    st = p.get("st", 0)
    if st == 2:
        return 4
    if p.get("lo") is not None and st == 0:
        return 2
    return 3


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
            pos, p.get("ap", 0), None, p.get("st", 0), _estimate_prob(p), ease,
            team_name=team_name, mv=p.get("mv", 0), liga_avg_win_prob=liga_avg_win_prob)
        players.append({
            "id": str(p.get("pi")), "name": p.get("pn", "?"), "pos": pos,
            "team": team_name, "lo": p.get("lo"), "mv": p.get("mv", 0) or 0,
            "ap": p.get("ap", 0) or 0, "tfhmvt": p.get("tfhmvt", 0) or 0,
            "sdmvt": p.get("sdmvt"), "st": p.get("st", 0),
            "expected_points": ep, "ep_factors": factors, "opponents": opponents,
        })

    xi = sorted((p for p in players if p["lo"] is not None), key=lambda p: p["lo"])
    bench = [p for p in players if p["lo"] is None]

    # SPEC_spieltagsmodell_v2.md 1.1 + 2.1 + 2.3: echte Sigma-Bandbreite +
    # Direktduell-Dämpfung NUR innerhalb der Startelf (nicht mehr über den
    # ganzen Kader gesucht - Bankspieler sind irrelevant, deren Punkte
    # zählen nicht) statt einer festen ±15%-Näherung.
    xi_result = coach.xi_prognose(xi, matcher)
    prognose = xi_result["total"]
    prognose_range = xi_result["bandbreite"]
    duel_hints = coach.duel_hints_for_xi(xi, matcher)

    lineup_opt = coach.optimize_lineup(players) if players else None
    kaderstaerke_reason = None
    if lineup_opt and lineup_opt["best"]:
        kaderstaerke = lineup_opt["best_total"]
    else:
        kaderstaerke = None
        # 1.3-Bugfix: kein stummes "?" mehr - Grund benennen (zu wenige
        # Spieler einer Position für JEDE der 7 Formationen).
        kaderstaerke_reason = coach.formation_gap_reason(players) if players else "kein Kader geladen"
    effizienz = round(prognose / kaderstaerke * 100, 1) if kaderstaerke else None
    # 1.3: Effizienz fließt NIRGENDS in die Punkteerwartung ein, ist eine rein
    # abgeleitete Kennzahl (Prognose ÷ Kaderstärke) - Klartext statt nackter %.
    effizienz_text = (f"mit optimaler Aufstellung wären +{kaderstaerke - prognose:.0f} "
                      f"Punkte möglich" if kaderstaerke and kaderstaerke > prognose
                      else "spielt bereits die stärkste Elf" if kaderstaerke else None)

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
    # Formationsdynamik-Textbaustein (SPEC 2.4) - rein informativ, NUR wenn
    # er in diesem Fall etwas bedeutet (nicht wortgleich für jeden Manager,
    # s. SPEC 1.4 - deshalb prüft formation_hint() die Zu-Null-Erwartung
    # statt pauschal bei "drei Stürmer" zu triggern).
    hint = coach.formation_hint(xi)

    return {
        "uid": uid, "name": name,
        "squad_size": len(players), "bench_size": len(bench),
        "xi": xi, "bench": bench,
        "prognose": prognose, "prognose_range": prognose_range,
        "duel_hints": duel_hints,
        "kaderstaerke": kaderstaerke, "kaderstaerke_reason": kaderstaerke_reason,
        "effizienz": effizienz, "effizienz_text": effizienz_text,
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

    # SPEC_lernzyklus.md 5.2c: eindeutiger Zweitschlüssel bei Punktgleichheit
    # - sonst ist die Reihenfolge bei exakt gleicher Prognose zufällig.
    managers.sort(key=lambda m: (-m["prognose"], str(m["uid"])))
    return managers
