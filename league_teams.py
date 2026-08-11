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

from fixtures import fixture_ease_for_team, team_strength_for
from odds import fixture_ease_odds, next_match_context
from scoring import kickbase_color, estimate_ap_from_peers
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
                    liga_avg_win_prob=0.5, peer_lookup=None, match_context=None):
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

    **Peer-Vergleichswert jetzt verdrahtet (SPEC_punkteformel_final.md)**:
    war bewusst ausgeklammert, weil `peer_lookup` (aus league_board.py) erst
    NACH der vollen Populations-Analyse verfügbar war. Seit main.py den
    Liga-Bestenlisten-Aufbau vor die Kader-Klassifizierung vorgezogen hat
    (own_ids kommen jetzt aus dem rohen Kader statt dem fertig
    klassifizierten), liegt `peer_lookup` bereits vor, BEVOR
    `build_league_teams()`/`analyze_manager()` überhaupt laufen - kein
    Zusatz-Call, nur ein durchgereichter Parameter. Ohne ihn kollabierte
    `_punktebasis()`s MW-Sockel-Fallback für Mitspieler ohne Punktehistorie
    auf denselben gedeckelten Wert (91,0) unabhängig von Position/Team/Farbe
    - live belegt (Wahl, Pieringer, Taz, Ofli, El Kadiri identisch "Basis
    91,0" trotz drei verschiedener Positionen).
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
        prob = _estimate_prob(p)
        peer_est = None
        if peer_lookup is not None:
            team_strength = (team_strength_for(team_name, strength_map)
                            if fixture_mode != "odds"
                            else strength_map.get(matcher(team_name, list(strength_map.keys())), 0.5))
            peer_est, _ = estimate_ap_from_peers(pos, team_strength, kickbase_color(prob), peer_lookup)
        # SPEC_spielertyp_matchkontext.md 1.2: dieselbe Tordifferenz-/Tore-
        # Herleitung wie main.py's Kader-/Marktloop - kostenlos, da
        # match_context bereits einmal pro Lauf aufgebaut wird.
        erw_tore, erw_tordiff = next_match_context(team_name, match_context, matcher)
        ep, factors = coach.expected_points(
            pos, p.get("ap", 0), None, p.get("st", 0), prob, ease,
            team_name=team_name, mv=p.get("mv", 0), liga_avg_win_prob=liga_avg_win_prob,
            peer_estimate=peer_est, erwartete_tordifferenz=erw_tordiff, erwartete_tore=erw_tore)
        # SPEC_ranking_faktoren_llm.md 2.3: Plausibilitätsprüfung - eine
        # Erwartung <25 P bei einem blauen/grünen (voraussichtlichen
        # Startelf-)Spieler ist fast immer ein Datenproblem, kein echtes
        # Signal (live so gefunden: Reese 14,3 P trotz 25-Mio-MW).
        if prob <= 2 and ep < 25 and p.get("lo") is not None:
            print(f"   ⚠️ Plausibilität: {p.get('pn', '?')} ({name}) E[Punkte]={ep} "
                 f"trotz Farbe {'blau' if prob == 1 else 'grün'} und gesetzter Startelf")
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

    # Bugfix ("Effizienz" >100% live gefunden bei realen 4-2-4-Formationen):
    # die echte gesetzte Formation muss immer Teil des Suchraums sein.
    real_formation = coach.derive_formation(xi) if xi else None
    lineup_opt = (coach.optimize_lineup(players, also_try=real_formation)
                 if players else None)
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
                       fixture_mode, matcher, sleep=0.15, liga_avg_win_prob=0.5,
                       own_uid=None, own_entry=None, peer_lookup=None, match_context=None):
    """
    Analysiert ALLE Manager der Liga (aus `ranking.us`, bereits geladen -
    kein Zusatz-Call). Liefert eine nach Prognose sortierte Liste von
    analyze_manager()-Ergebnissen plus Vorsaison-Baseline (Punkt 6.2) je
    Manager als Kontext.

    **Ein Rechenweg statt zwei (SPEC_ranking_faktoren_llm.md Abschnitt 1)**:
    für den EIGENEN Manager (`own_uid`) wird `analyze_manager()` NICHT
    aufgerufen - der Aufrufer (main.py) hat die eigene Prognose bereits über
    `get_player_details()` (mit echtem `prob`-Feld, präziser als die
    `managers/{uid}/squad`-Näherung `_estimate_prob()`, die für alle
    ANDEREN Manager mangels `prob`-Feld nötig bleibt) berechnet und liefert
    sie als `own_entry` fertig zusammengebaut. Live gefunden: ohne diesen
    Fix zeigte die eigene Detailansicht und das Ranking für DENSELBEN Kader/
    Spieltag zwei unterschiedliche Zahlen (916 vs. 840 P) - zwei getrennte
    Rechenwege für dieselbe Größe. `own_entry=None` (z.B. kein Kader
    geladen) fällt auf den alten Weg zurück, kein Absturz.
    """
    tid_to_name = fetch_team_map(kb, cid)
    time.sleep(sleep)

    managers = []
    for u in ranking.get("us", []) or []:
        uid, name = u.get("i"), (u.get("n") or "").strip()
        if not uid:
            continue
        if own_uid is not None and own_entry is not None and str(uid) == str(own_uid):
            analysis = dict(own_entry)
        else:
            analysis = analyze_manager(kb, cid, league_id, uid, name, tid_to_name,
                                       strength_map, upcoming, fixture_mode, matcher, sleep,
                                       liga_avg_win_prob=liga_avg_win_prob, peer_lookup=peer_lookup,
                                       match_context=match_context)
        analysis["vorsaison"] = {
            "platz": u.get("psp"), "punkte": u.get("pspts"), "siege": u.get("pswc"),
        }
        managers.append(analysis)

    # SPEC_lernzyklus.md 5.2c: eindeutiger Zweitschlüssel bei Punktgleichheit
    # - sonst ist die Reihenfolge bei exakt gleicher Prognose zufällig.
    managers.sort(key=lambda m: (-m["prognose"], str(m["uid"])))
    return managers
