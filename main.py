"""
Kickbase-Zentrale - tägliches Briefing über alle konfigurierten Ligen.

Aufbau pro Liga:
1. KADER-STATUS: jeder eigene Spieler mit Verdikt
   (HALTEN Trading / STAMM / BEOBACHTEN / VERKAUFEN) + Begründung
2. MARKT vs. KADER: Kaufkandidaten immer im Vergleich zum Status quo -
   freier Kaderplatz? Upgrade für wen? Oder kein Bedarf?
3. Spielplan: kommende Gegner aus OpenLigaDB-Spielplan (funktioniert auch
   in der Saisonvorbereitung), Gegnerstärke mit Vorsaison-Fallback
"""

import sys
import time
from config import (LEAGUES, WEIGHTS, FOOTBALL_DATA_API_KEY, ODDS_API_KEY,
                    WEIGHTS_QUALITY, WEIGHTS_VALUE, GEMINI_API_KEY)

CLUB_LIMIT = 3  # max. Spieler desselben Vereins im Kader (User-bestätigt)
from kickbase_api import KickbaseAPI
from fixtures import (get_table, get_table_football_data, build_strength_map,
                      team_strength_for,
                      get_upcoming_by_team, get_upcoming_by_team_football_data,
                      get_table_tsdb, get_upcoming_by_team_tsdb,
                      fixture_ease_for_team, get_season_info, league_avg_win_prob)
from scoring import (score_player, explain, player_reliability_profile, punktetyp_label,
                     punktetyp_index, reliability_score,
                     kickbase_color, is_min_price_player, estimate_ap_from_peers,
                     expected_points as scoring_expected_points, clamp_fair_value)
from bid_advisor import learn_league_overpay
from mv_forecast import clean_mv_series
from odds import load_fixture_odds, load_fixture_odds_api, fixture_ease_odds, next_match_context
from fixtures import _best_match
from squad_analysis import (classify_own_player, market_vs_squad,
                            finalize_headline_recommendations,
                            flag_formation_risk, POS_NAMES, DEBT_RATIO,
                            apply_fair_value_note)
from league_board import build_league_lists
from report_builder import (compute_kpis, build_actions, build_squad_action_items,
                            build_targets, build_mitspieler_appendix, build_risks,
                            season_phase, save_report, load_previous_snapshot, diff_reports)
from llm_insights import generate_insights, call_summary as llm_call_summary
import coach
from league_teams import build_league_teams
from prediction_log import (save_matchday_prediction, save_daily_bids, save_matchday_actuals,
                            deviation_report, load_matchday_prediction, diff_predictions)
from retrospective_data import build_waterfall_report

LEAGUE_BOARD_TOP_N = 10  # Top N je Position in der Liga-Bestenliste (B5)


def _match_name(name, candidates):
    m, _ = _best_match(name, candidates)
    return m


def _check_pspts_anchor(ranking, matchdays, league_teams):
    """
    SPEC_kalibrierung_fairvalue.md 1.1: harter, von den eigenen Faktoren
    unabhängiger Referenzwert - Vorsaison-Gesamtpunkte je Manager (`pspts`)
    geteilt durch die Spieltagszahl ergibt den real erreichten Spieltags-
    schnitt. Weicht der Median der eigenen Prognosen um mehr als 25% davon
    ab, ist das ein Hinweis auf einen systematischen Modellfehler (genau das
    hätte den ursprünglichen Faktor-Bug sofort sichtbar gemacht). None ohne
    ausreichende Datengrundlage (kein pspts oder keine Prognosen).
    """
    anchor_values = [u["pspts"] / matchdays for u in (ranking.get("us") or [])
                     if u.get("pspts") and matchdays]
    if not anchor_values or not league_teams:
        return None
    anchor = sum(anchor_values) / len(anchor_values)
    if anchor <= 0:
        return None
    prognosen = sorted(m["prognose"] for m in league_teams)
    n = len(prognosen)
    median_prognose = (prognosen[n // 2] if n % 2
                       else (prognosen[n // 2 - 1] + prognosen[n // 2]) / 2)
    deviation = (median_prognose - anchor) / anchor
    return {"anchor": anchor, "median_prognose": median_prognose,
           "deviation": deviation, "plausible": abs(deviation) <= 0.25}


def load_fixture_data(cfg):
    # 1) Primär: Buchmacher-Quoten (football-data.co.uk fixtures.csv, kostenlos & keylos)
    odds_div = cfg.get("odds_div")
    if odds_div:
        upcoming_odds, power, match_context = load_fixture_odds(odds_div)
        if upcoming_odds:
            print(f"📊 Quoten geladen ({odds_div}): {len(power)} Teams, "
                  f"Top 3 laut Buchmachern: "
                  + ", ".join(sorted(power, key=power.get, reverse=True)[:3]))
            return power, upcoming_odds, "odds", match_context
        print("ℹ️ Keine Quoten in fixtures.csv (Sommerpause?) -> Fallback the-odds-api.")

    # 1b) Fallback: the-odds-api.com (kostet Kontingent, nur wenn 1) leer ist)
    # SPEC_spielertyp_matchkontext.md 1.2: the-odds-api liefert nur h2h-
    # Quoten, keine Über/Unter-/Handicap-Daten -> match_context bleibt leer,
    # Aufrufer fallen auf die reine Sieg-WK-Zu-Null-Herleitung zurück.
    odds_api_sport = cfg.get("odds_api_sport")
    if odds_api_sport and ODDS_API_KEY:
        upcoming_api, power_api = load_fixture_odds_api(odds_api_sport, ODDS_API_KEY)
        if upcoming_api:
            print(f"📊 Quoten von the-odds-api geladen ({odds_api_sport}): "
                  f"{len(power_api)} Teams, Top 3 laut Buchmachern: "
                  + ", ".join(sorted(power_api, key=power_api.get, reverse=True)[:3]))
            return power_api, upcoming_api, "odds", {}
        print("ℹ️ Auch the-odds-api ohne Spiele -> Fallback Tabelle.")

    # 2) Fallback: Tabellen-basiert wie bisher
    src = cfg.get("fixture_source")
    if src == "openligadb":
        season, sc = cfg.get("season", "2026"), cfg.get("openligadb_shortcut", "bl2")
        return build_strength_map(get_table(season, sc)), get_upcoming_by_team(season, sc), "table", {}
    if src == "football-data":
        comp = cfg.get("football_data_competition", "PD")
        return (build_strength_map(get_table_football_data(comp, FOOTBALL_DATA_API_KEY)),
                get_upcoming_by_team_football_data(comp, FOOTBALL_DATA_API_KEY), "table", {})
    if src == "thesportsdb":
        lid = cfg.get("tsdb_league_id", "4335")
        return (build_strength_map(get_table_tsdb(lid, cfg.get("tsdb_season", "2026-2027"))),
                get_upcoming_by_team_tsdb(lid), "table", {})
    return {}, {}, "none", {}


def enrich_players(kb, league_id, players, strength_map, upcoming_by_team, mode):
    """Details + Fixture-Ease für eine Spielerliste (Markt ODER Kader)."""
    out = []
    for p in players:
        d = kb.get_player_details(league_id, p.get("i"))
        time.sleep(0.25)
        team_name = d.get("tn", "")
        if mode == "odds":
            ease, opponents = fixture_ease_odds(team_name, upcoming_by_team, _match_name)
        else:
            ease, opponents = fixture_ease_for_team(team_name, upcoming_by_team, strength_map)
        out.append((p, d, ease, opponents))
    return out


def run_league(kb, cfg, run_timestamp):
    name = cfg["name"]
    print("\n" + "=" * 62)
    print(f"🏆 LIGA: {name}")
    print("=" * 62)

    league_id = kb.get_league_id(name)
    if not league_id:
        print(f"❌ Liga '{name}' nicht gefunden - übersprungen.")
        return {"name": name, "error": "Liga nicht gefunden"}

    me = kb.get_me(league_id)
    budget = me.get("b", 0)
    max_squad = me.get("mppu", 20)
    # "tpc" ist NICHT das Vereinslimit (frühere Fehlannahme, korrigiert) -
    # es ist eine Liste {tid, npt} = AKTUELLE Spieleranzahl je Verein.
    # Das echte Limit (User-bestätigt 2026-07-31): max. 3 Spieler pro Verein.
    club_limit = CLUB_LIMIT
    # cid robust aus /me ableiten statt dem hartkodierten (bei La Liga
    # unverifizierten) config-Wert zu vertrauen.
    cid = int(me.get("cpi") or cfg["competition_id"])
    strength_map, upcoming, fixture_mode, match_context = load_fixture_data(cfg)
    if not upcoming:
        print("ℹ️ Kein Spielplan verfügbar -> Spielplan-Komponente neutral.")

    # SPEC_kalibrierung_fairvalue.md Abschnitt 0/2.2: Gegnerfaktor-Zentrierung
    # auf die ECHTE Liga-Ø-Sieg-WK (~35-40% wegen Unentschieden), nicht 0,5 -
    # einmal pro Liga berechnet, unten an alle coach.expected_points()/
    # fair_value()-Aufrufe durchgereicht. min_price (Abschnitt 3.1) zensiert
    # Spieler am Mindestmarktwert aus der Preiskurve.
    liga_avg_win_prob = league_avg_win_prob(fixture_mode, strength_map, upcoming)
    min_price = cfg.get("min_price")

    # Saisonstart live aus dem OpenLigaDB-Spielplan (verifiziert 2026-08-05,
    # nicht mehr geschätzt) - nur für openligadb-Quellen (2. Bundesliga)
    # verfügbar, football-data.org liefert bei uns kein Datumsfeld dafür.
    season_start_date = None
    current_matchday = None
    if cfg.get("fixture_source") == "openligadb":
        # Bugfix (2026-08-12): season_start_date UND current_matchday kommen
        # jetzt aus EINEM gemeinsamen Fetch (get_season_info()) statt zwei
        # unabhängigen Calls - ein live gefundener Bot-Lauf hatte die beiden
        # Werte sonst inkonsistent gespeichert (matchday=1 vom Fallback,
        # kickoff_first aber korrekt vom nächsten echten Spieltag), s.
        # fixtures.get_season_info()-Docstring.
        season_start_date, current_matchday = get_season_info(
            cfg.get("season", "2026"), cfg.get("openligadb_shortcut", "bl2"))
        if season_start_date:
            print(f"📅 Saisonstart (verifiziert): {season_start_date:%d.%m.%Y %H:%M} UTC")

    # ---------- 1) EIGENER KADER ----------
    squad_raw = kb.get_squad(league_id)
    squad_players = squad_raw.get("it", []) or squad_raw.get("players", [])
    # Verifiziert 2026-08-05 (SPEC_lineup_verified.md): /lineup liefert `lo`
    # (Startelf-Slot) sowie `os`/`ht` (Gegner-Kürzel/Heimrecht) direkt - macht
    # Team-Fuzzy-Matching für die EIGENE Kader-Gegneranzeige überflüssig.
    lineup_raw = kb.get_lineup(league_id)
    lineup_by_id = {str(p.get("i")): p for p in (lineup_raw.get("it", []) or [])}

    # SPEC_punkteformel_final.md Abschnitt 2/5 ("Basis 91,0 für fünf
    # verschiedene Spieler"): Liga-Bestenliste (B5) + Peer-Lookup jetzt VOR
    # der Kader-Klassifizierung gebaut statt danach - `own_ids` braucht dafür
    # nur die IDs aus dem ROHEN Kader (bereits oben verfügbar), nicht den
    # fertig klassifizierten Kader. Grund: der eigene Kader-Loop unten soll
    # `peer_estimate` (Median aus Position×Teamstärke×Farbe,
    # scoring.estimate_ap_from_peers) direkt in `coach.expected_points()`
    # einspeisen können - vorher bekam nur `fair_value()` (in einem SEPARATEN,
    # späteren Durchlauf) den Peer-Vergleichswert, `expected_points()` (die
    # Zahl, die tatsächlich überall als "E[Punkte]" angezeigt wird) fiel für
    # Spieler ohne eigene Historie immer auf den MW-Sockel zurück
    # (`mv_implied_form(mv)×130`, gedeckelt bei 0,7×130=91,0) - der Deckel
    # kollabiert für jeden Spieler mit ausreichend hohem MW auf denselben
    # Wert, unabhängig von Position/Team/Farbe (live belegt: Wahl, Pieringer,
    # Taz, Ofli, El Kadiri alle exakt "Basis 91,0"). Der Peer-Vergleichswert
    # ist positions-/team-/farbspezifisch und damit die eigentlich vorgesehene
    # Vergleichsgruppen-Schätzung statt eines de-facto-globalen Konstantwerts.
    league_overpay = learn_league_overpay(kb.get_activities(league_id))
    own_ids = {str(p.get("i")) for p in squad_players}
    board = build_league_lists(kb, cid, league_id, own_ids, strength_map,
                               upcoming, fixture_mode, _match_name,
                               weights_quality=WEIGHTS_QUALITY,
                               weights_value=WEIGHTS_VALUE,
                               top_n=LEAGUE_BOARD_TOP_N,
                               league_overpay=league_overpay,
                               min_price=min_price,
                               liga_avg_win_prob=liga_avg_win_prob)
    price_curve = (board.get("price_curve") or {}).get("curve")
    peer_lookup = board.get("peer_lookup")
    fair_value_ok = board.get("fair_value_ok", True)
    if not fair_value_ok:
        print("   ⚠️ Preiskurve wirkt verzerrt (Selbstprüfung 40-60% verletzt) - "
              "Fair Value wird diesen Lauf unterdrückt statt falscher Zahlen.")

    squad_classified = []
    self_play_conflicts = []
    plausibility_warnings = []
    if squad_players:
        print(f"\n👥 KADER-STATUS ({len(squad_players)}/{max_squad} Plätze, "
              f"Budget {budget:+,.0f} €):")
        for p, d, ease, opps in enrich_players(kb, league_id, squad_players,
                                               strength_map, upcoming, fixture_mode):
            # A1: sdmvt (7-Tage-MW-Differenz) kommt im Kader-Response direkt
            # mit -> Momentum-Ratio ohne Zusatz-API-Call (repariert
            # "BEOBACHTEN" - s. squad_analysis-Docstring).
            c = classify_own_player(p, d, ease, WEIGHTS, sdmvt=p.get("sdmvt"))
            c["opponents"] = opps
            lu = lineup_by_id.get(c["id"])
            c["next_opponent_verified"] = (
                f"{lu['os']} ({'Heim' if lu.get('ht') else 'Auswärts'})"
                if lu and lu.get("os") else None)
            # Punkt 4 (verifiziert 4/4 Treffer 2026-08-05): Kickbase-Farbe aus
            # prob 1-5 - ergänzt das eigene Verdikt, ersetzt es nicht (s.
            # KICKBASE_COLOR-Docstring in scoring.py).
            c["kickbase_color"] = kickbase_color(d.get("prob", 3))
            # Für die spätere Fair-Value-Nachverarbeitung (nach dem Aufbau der
            # Liga-Preiskurve unten) - prob/ease/team_strength werden hier nur
            # gebraucht, aber erst dort verwendet.
            c["prob"] = d.get("prob", 3)
            c["ease"] = ease
            c["team_strength"] = (team_strength_for(d.get("tn", ""), strength_map)
                                  if fixture_mode != "odds"
                                  else strength_map.get(_match_name(d.get("tn", ""), list(strength_map.keys())), 0.5))
            # Punkt 6 (Grundgerüst): erwartete Punkte für die Aufstellungs-
            # optimierung (coach.py) - `ease` (Sieg-WK-Näherung) und `ph`
            # (letzte Spieltage) liegen aus diesem Loop-Durchlauf schon vor,
            # kein Zusatz-Call nötig. liga_avg_win_prob zentriert den
            # Gegnerfaktor (SPEC_kalibrierung_fairvalue.md Abschnitt 0).
            # SPEC_punkteformel_final.md: `peer_estimate` (Vergleichsgruppe
            # Position×Teamstärke×Farbe) jetzt an DIESER Stelle mitgegeben,
            # nicht mehr nur bei fair_value() - board/peer_lookup liegen seit
            # dem Umbau oben schon vor Beginn dieses Loops vor. Ohne
            # peer_estimate fiel `_punktebasis()` für Spieler ohne eigene
            # Historie auf den MW-Sockel zurück, der bei ausreichend hohem MW
            # für JEDEN Spieler auf denselben gedeckelten Wert (91,0)
            # kollabiert, unabhängig von Position/Team/Farbe.
            peer_est = None
            if peer_lookup is not None:
                peer_est, _ = estimate_ap_from_peers(c["pos"], c["team_strength"],
                                                     c["kickbase_color"], peer_lookup)
            # SPEC_spielertyp_matchkontext.md 1.1: Punktetyp-Index koppelt die
            # Gegner-Sensitivität (k_eff) an Rohpunkte- vs. Scorer-Typ -
            # dieselbe Datenbasis (ph+mdsum), die punktetyp_label() für den
            # Transfermarkt-Text schon nutzt, jetzt auch als Faktor.
            reliability_profile = player_reliability_profile(d)
            p_idx = punktetyp_index(reliability_profile)
            c["reliability_score"] = reliability_score(reliability_profile)
            # SPEC_spielertyp_matchkontext.md 1.2: erwartete Tordifferenz +
            # Gesamttore aus Über/Unter-/Handicap-Quoten für die NÄCHSTE
            # Partie - präzisere Zu-Null-Herleitung als die reine Sieg-WK.
            # Leer (None, None) außerhalb des odds-Modus oder ohne gefüllte
            # Spalten (z.B. La Liga) - fällt dann automatisch auf die alte
            # Sieg-WK-Ankertabelle zurück (s. coach.zu_null_bonus()).
            erw_tore, erw_tordiff = next_match_context(d.get("tn", ""), match_context, _match_name)
            ep, ep_factors = coach.expected_points(
                c["pos"], p.get("ap", 0), d.get("ph"), d.get("st", 0),
                d.get("prob", 3), ease, team_name=d.get("tn", ""), mv=c["mv"],
                liga_avg_win_prob=liga_avg_win_prob, peer_estimate=peer_est,
                punktetyp_idx=p_idx, erwartete_tordifferenz=erw_tordiff,
                erwartete_tore=erw_tore)
            c["expected_points"] = ep
            c["ep_factors"] = ep_factors
            # Plausibilitätslog je Spieler (SPEC_kalibrierung_fairvalue.md
            # 1.2): bei einem fitten Stammspieler ist eine Abweichung um mehr
            # als Faktor 2 vom Punkteschnitt fast immer ein Modellfehler,
            # kein echtes Signal.
            ap_check = p.get("ap", 0)
            if ap_check and (ep > 2 * ap_check or ep < 0.5 * ap_check):
                w = f"{c['name']} E[Punkte]={ep} weicht >Faktor 2 von Ø {ap_check} ab"
                print(f"   ⚠️ Plausibilität: {w}")
                plausibility_warnings.append(w)
            # REVIEW_architektur_KOMPLETT.md 2.6 ("Zec ist ein ungeklärter
            # Ausreißer"): die Faktor-2-Prüfung oben griff nicht in jedem
            # Fall (z.B. wenn schon die Anker-`ap` selbst niedrig/unsicher
            # ist) - dieselbe absolute Warnschwelle, die league_teams.py für
            # MITSPIELER-Kader bereits nutzt (E[Punkte]<25 bei blau/grün =
            # fast immer ein Datenproblem, kein echtes Signal), fehlte für
            # den EIGENEN Kader komplett. Nachgezogen, plus jetzt auch
            # sichtbar im HTML-Report (vorher nur Konsole - die Review
            # basierte auf dem Livereport und sah die Konsole nicht).
            if c["kickbase_color"] in ("blau", "grün") and ep < 25:
                w = f"{c['name']} E[Punkte]={ep} trotz Farbe {c['kickbase_color']} (Stammspieler-Ampel)"
                print(f"   ⚠️ Plausibilität: {w}")
                plausibility_warnings.append(w)
            squad_classified.append(c)
        flag_formation_risk(squad_classified)

        order = {"VERKAUFEN": 0, "BEOBACHTEN": 1, "STAMM": 2, "HALTEN (Trading)": 3}
        for c in sorted(squad_classified, key=lambda x: (order.get(x["verdict"], 9), x["id"])):
            icon = {"VERKAUFEN": "🔻", "BEOBACHTEN": "👀",
                    "STAMM": "⭐", "HALTEN (Trading)": "📈"}[c["verdict"]]
            color_txt = f" [{c['kickbase_color']}]" if c["kickbase_color"] else ""
            print(f"\n{icon} {c['name']} ({c['pos']}){color_txt} - {c['verdict']} "
                  f"| Kader-Score {c['score']} | MW {c['mv']:,.0f} ({c['tfhmvt']:+,.0f}/Tag)")
            print(f"   {'; '.join(c['reasons'])}")
            if c["next_opponent_verified"]:
                print(f"   Nächster Gegner: {c['next_opponent_verified']}")
            elif c["opponents"]:
                print(f"   Nächste Gegner: {', '.join(c['opponents'])}")
    else:
        print("\nℹ️ Kader-Endpoint lieferte nichts - Team-Ansicht in der App mit "
              "HTTP Toolkit aufnehmen, dann verifiziere ich den Pfad.")
    squad_slots = len(squad_players)

    # ---------- 1b) AUFSTELLUNG (Punkt 6, jetzt mit echter Startelf) ----------
    # SPEC_spieltagsmodell_v2.md 2.1: die Ideal-Elf ist die EINZIGE Quelle -
    # Wechselvorschläge (swaps_from_ideal) werden als Delta dazu abgeleitet,
    # nicht mehr unabhängig über "stärkster Bankspieler" berechnet (das
    # konnte sich widersprechen).
    lineup_status = (coach.current_lineup_status(lineup_raw, squad_classified)
                     if squad_classified else None)
    # Bugfix ("Effizienz" 106% live gefunden, PaulBowa Formation 4-2-4):
    # optimize_lineup() muss die ECHT gesetzte Formation immer mit
    # durchsuchen, sonst kann die reale Elf das rechnerische "Optimum"
    # schlagen, wenn ihre Formation nicht in den 7 Standardformationen steckt.
    real_formation = (coach.derive_formation(lineup_status["xi"])
                      if lineup_status and lineup_status["xi"] else None)
    lineup_opt = (coach.optimize_lineup(squad_classified, also_try=real_formation)
                 if squad_classified else None)
    swaps = (coach.swaps_from_ideal(lineup_status, lineup_opt)
            if lineup_status and lineup_opt else [])
    missing_pos = (coach.missing_positions(lineup_status, lineup_opt)
                   if lineup_status and lineup_opt else {})
    # 1.3: kein stummes "?" - wenn KEINE Formation passt, den Grund benennen.
    kaderstaerke_reason = (coach.formation_gap_reason(squad_classified)
                           if squad_classified and not (lineup_opt and lineup_opt["best"]) else None)

    # SPEC_spieltagsmodell_v2.md 2.3: Direktduelle NUR auswerten, wenn beide
    # Spieler in der Startelf stehen (nicht mehr im ganzen Kader gesucht) -
    # ausgewertet auf der ECHTEN Ist-Elf, weil die die "Prognose"-Kennzahlen
    # überall im Report trägt (Dashboard, Modul 3, KI-Kontext).
    ist_prognose = (coach.xi_prognose(lineup_status["xi"], _match_name)
                    if lineup_status and lineup_status["xi"] else None)
    self_play_conflicts = (coach.duel_hints_for_xi(lineup_status["xi"], _match_name, own=True)
                           if lineup_status and lineup_status["xi"] else [])
    ideal_prognose = (coach.xi_prognose(lineup_opt["formations"][lineup_opt["best"]]["xi"], _match_name)
                      if lineup_opt and lineup_opt["best"] else None)

    if lineup_opt and lineup_opt["best"]:
        best = lineup_opt["formations"][lineup_opt["best"]]
        print(f"\n🧠 AUFSTELLUNGSEMPFEHLUNG: {lineup_opt['best']} "
              f"({ideal_prognose['total']} erwartete Punkte, Bandbreite "
              f"{ideal_prognose['bandbreite'][0]:.0f}-{ideal_prognose['bandbreite'][1]:.0f}) · "
              f"Deadline 20:29 Uhr")
        for p in sorted(best["xi"], key=lambda x: -x["expected_points"]):
            print(f"   {p['pos']:<4} {p['name']:<20} {p['expected_points']:>5.1f} P "
                  f"(Basis {p['ep_factors']['basis']} × Einsatz {p['ep_factors']['einsatzfaktor']} "
                  f"× Gegner {p['ep_factors']['gegnerfaktor']} × Verlauf {p['ep_factors']['spielverlaufsfaktor']})")
        alternatives = sorted(
            ((n, r["total_points"]) for n, r in lineup_opt["formations"].items() if n != lineup_opt["best"]),
            key=lambda x: -x[1])
        if alternatives:
            alt_txt = ", ".join(f"{n} ({t:+.1f})" for n, t in
                                [(n, t - lineup_opt["best_total"]) for n, t in alternatives[:3]])
            print(f"   Alternativen: {alt_txt}")
    elif kaderstaerke_reason:
        print(f"\n🧠 AUFSTELLUNGSEMPFEHLUNG: nicht berechenbar - {kaderstaerke_reason}")

    if lineup_status:
        n_filled = len(lineup_status["xi"])
        prog_txt = (f" · Prognose {ist_prognose['total']:.0f} P "
                    f"({ist_prognose['bandbreite'][0]:.0f}-{ist_prognose['bandbreite'][1]:.0f})"
                    if ist_prognose else "")
        print(f"\n📋 AKTUELL GESETZTE ELF: {n_filled}/11 Slots belegt{prog_txt}")
        if lineup_status["empty_slots"]:
            gap_txt = ", ".join(f"{n}× {pos}" for pos, n in missing_pos.items()) or "Position unklar"
            print(f"   ⚠️ {lineup_status['empty_slots']} freie(r) Slot(s) - fehlt: {gap_txt}")
        for s in swaps:
            # SPEC 2.2: knappe Differenzen (<8% der Erwartung) als Abwägung
            # kennzeichnen statt als klare Empfehlung.
            tag = " (⚖️ knappe Entscheidung)" if s.get("knapp") else ""
            print(f"   ↳ Slot {s['slot']}: {s['out']['name']} raus, {s['in']['name']} rein "
                  f"- erwartet {s['diff']:+.1f} Punkte{tag}")
        if self_play_conflicts:
            print("   ⚔️ Direktes Duell in deiner Elf:")
            for txt in self_play_conflicts:
                print(f"      {txt}")

    # SPEC_ranking_faktoren_llm.md Abschnitt 1: "zwei Rechenwege für dieselbe
    # Zahl" - die eigene Detailansicht (oben, aus squad_classified/
    # lineup_status/ist_prognose) und league_teams.py (unten, Modul 3)
    # berechneten die EIGENE Prognose bisher unabhängig doppelt - über
    # unterschiedliche Datenquellen (get_player_details mit echtem `prob`
    # oben vs. managers/{uid}/squad OHNE `prob`, _estimate_prob()-Näherung
    # unten), mit unterschiedlichem Ergebnis für denselben Kader/Spieltag.
    # Fix: EIN Ergebnisobjekt - `own_entry` wird hier aus den bereits oben
    # berechneten (präziseren) Daten zusammengebaut und unten an
    # build_league_teams() durchgereicht, das für die eigene uid keinen
    # eigenen Rechenweg mehr aufmacht (spart nebenbei einen API-Call).
    own_entry = None
    if lineup_status and ist_prognose:
        from collections import Counter as _Counter
        tiefe = sum(1 for c in squad_classified if c["expected_points"] >= 60)
        team_points = {}
        for p in lineup_status["xi"]:
            if p.get("team"):
                team_points[p["team"]] = team_points.get(p["team"], 0) + p["expected_points"]
        klumpenrisiko, top_team = None, None
        if ist_prognose["total"] > 0 and team_points:
            top_team = max(team_points, key=team_points.get)
            klumpenrisiko = round(team_points[top_team] / ist_prognose["total"] * 100, 1)
        fc = _Counter(p["pos"] for p in lineup_status["xi"])
        formation = (f"{fc.get('ABW', 0)}-{fc.get('MF', 0)}-{fc.get('ANG', 0)}"
                    if lineup_status["xi"] else None)
        own_kaderstaerke = lineup_opt["best_total"] if lineup_opt and lineup_opt["best"] else None
        own_effizienz = (round(ist_prognose["total"] / own_kaderstaerke * 100, 1)
                         if own_kaderstaerke else None)
        own_effizienz_text = (f"mit optimaler Aufstellung wären +{own_kaderstaerke - ist_prognose['total']:.0f} "
                              f"Punkte möglich" if own_kaderstaerke and own_kaderstaerke > ist_prognose["total"]
                              else "spielt bereits die stärkste Elf" if own_kaderstaerke else None)
        own_entry = {
            "uid": kb.user_id, "name": (kb.user_name or name).strip(),
            "squad_size": len(squad_classified), "bench_size": len(lineup_status["bench"]),
            "xi": lineup_status["xi"], "bench": lineup_status["bench"],
            "prognose": ist_prognose["total"], "prognose_range": ist_prognose["bandbreite"],
            "duel_hints": self_play_conflicts,
            "kaderstaerke": own_kaderstaerke, "kaderstaerke_reason": kaderstaerke_reason,
            "effizienz": own_effizienz, "effizienz_text": own_effizienz_text,
            "tiefe": tiefe, "klumpenrisiko": klumpenrisiko, "top_team": top_team,
            "formation": formation, "formation_hint": coach.formation_hint(lineup_status["xi"]),
            "empty_slots": lineup_status["empty_slots"],
        }

    # Kaufkraft-Kennzahlen fürs Dashboard (identische Formel wie in
    # squad_analysis.market_vs_squad, dort nicht nach außen gereicht).
    squad_value = sum(s["mv"] for s in squad_classified)
    net_value = squad_value + budget
    max_debt = DEBT_RATIO * net_value
    capacity = max_debt + budget

    # league_overpay/own_ids/board/price_curve/peer_lookup/fair_value_ok
    # werden jetzt VOR der Kader-Klassifizierung gebaut (s. Kommentar dort,
    # SPEC_punkteformel_final.md) - hier nicht mehr nötig.

    # SPEC_kalibrierung_fairvalue.md 4.1: Fair Value je Kaderspieler nach der
    # Verdikt-Klassifizierung nachreichen (braucht c["pos"]/c["team_strength"]/
    # c["kickbase_color"], die erst im Kader-Loop entstehen - price_curve/
    # peer_lookup selbst liegen jetzt schon vorher vor, s.o.). Ergänzt die
    # bestehenden Verdikte nur um eine Reason - die Farbregel (STAMM bei
    # blau/grün) bleibt vorrangig, kein automatischer Verkauf.
    for c in squad_classified:
        fv_mv = None
        if fair_value_ok and price_curve:
            peer_est, _ = estimate_ap_from_peers(c["pos"], c["team_strength"],
                                                 c["kickbase_color"], peer_lookup)
            fv_mv, _ = coach.fair_value(
                c["pos"], c["mv"], c["ap"], None, c["st"], c["prob"], c["ease"],
                c["team_strength"], price_curve, liga_avg_win_prob=liga_avg_win_prob,
                peer_estimate=peer_est)
            fv_mv, fv_clamped = clamp_fair_value(fv_mv, c["mv"])
            if fv_clamped:
                print(f"   ⚠️ Fair Value für {c['name']} unplausibel (>Faktor 3 vom MW) - unterdrückt")
        apply_fair_value_note(c, fv_mv, is_min_price_player(c["mv"], min_price))

    # ---------- 2) MARKT IM TEAM-KONTEXT ----------
    market = kb.get_transfer_market(league_id)
    print(f"\n🛒 TRANSFERMARKT ({len(market)} freie Spieler) - im Kader-Kontext:")
    market_scored = []
    for p, d, ease, opps in enrich_players(kb, league_id, market, strength_map, upcoming, fixture_mode):
        total, comps, meta = score_player(p, d, ease, WEIGHTS)
        profile = player_reliability_profile(d)
        reliable_type, punktetyp_text = punktetyp_label(profile)
        # Regime-basierte MW-Prognose (Punkt 1) braucht die volle Historie,
        # nicht nur tfhmvt - nur für den Tagesmarkt geladen (bounded ~20-40
        # Spieler/Liga), nicht für die 449-Spieler-Liga-Bestenliste (B5
        # bleibt dort bewusst auf der günstigeren sdmvt-Näherung).
        hist_raw = kb.get_mv_history(cid, p.get("i"), league_id)
        time.sleep(0.25)
        mv_history = clean_mv_series(hist_raw)
        team_strength = (team_strength_for(d.get("tn", ""), strength_map)
                         if fixture_mode != "odds"
                         else strength_map.get(_match_name(d.get("tn", ""), list(strength_map.keys())), 0.5))
        mv_now = d.get("mv", p.get("mv", 0))
        pos_now = POS_NAMES.get(p.get("pos"), "?")
        color_now = kickbase_color(d.get("prob", 3))
        # Fair Value (Punkt 1.2): "was ist er sportlich wert" statt "was
        # wird er kosten" - über die Liga-Preiskurve (price_curve, oben
        # vorgezogen) aus Form × Einsatz × Gegner × Teamstärke. Peer-
        # Vergleichswert (3.2) für Spieler ohne eigene Historie, Mindestpreis-
        # Zensierung (3.1) und Selbstprüfungs-Unterdrückung (3.3) wie überall.
        fair_value_mv, expected_ap_for_mv = None, None
        min_price_now = is_min_price_player(mv_now, min_price)
        # SPEC_punkteformel_final.md: peer_est jetzt UNGATED von min_price_now
        # berechnet (reiner Lookup, kein Zusatz-Call) - wird unten sowohl für
        # fair_value() (dort weiterhin min_price-gegated) als auch für
        # expected_points() gebraucht, die für JEDEN Marktspieler läuft.
        peer_est = None
        if peer_lookup is not None:
            peer_est, _ = estimate_ap_from_peers(pos_now, team_strength, color_now, peer_lookup)
        # SPEC_spielertyp_matchkontext.md 1.1: `profile` (oben schon für
        # punktetyp_label() berechnet) liefert auch den numerischen Index
        # für k_eff - kein Zusatz-Call, dieselbe Datenbasis wiederverwendet.
        p_idx = punktetyp_index(profile)
        if fair_value_ok and price_curve:
            expected_ap_for_mv = scoring_expected_points(mv_now, price_curve)
            if not min_price_now:
                fair_value_mv, _ = coach.fair_value(
                    pos_now, mv_now, p.get("ap", 0), d.get("ph"), d.get("st", 0), d.get("prob", 3),
                    ease, team_strength, price_curve, liga_avg_win_prob=liga_avg_win_prob,
                    peer_estimate=peer_est, punktetyp_idx=p_idx)
                fair_value_mv, fv_clamped = clamp_fair_value(fair_value_mv, mv_now)
                if fv_clamped:
                    name_now = f"{p.get('fn', '')} {p.get('n', '')}".strip()
                    print(f"   ⚠️ Fair Value für {name_now} unplausibel (>Faktor 3 vom MW) - unterdrückt")
        # SPEC_spieltagsmodell_v2.md 3.2: erwartete Punkte des Marktspielers
        # IM EIGENEN Kontext (Gegner/Team der kommenden Partie) - Grundlage
        # für die Ideal-Elf-Brücke unten (wichtigste inhaltliche Ergänzung
        # laut Spec: "bringt mir der Spieler Punkte?").
        erw_tore, erw_tordiff = next_match_context(d.get("tn", ""), match_context, _match_name)
        ep_market, _ = coach.expected_points(
            pos_now, p.get("ap", 0), d.get("ph"), d.get("st", 0), d.get("prob", 3), ease,
            team_name=d.get("tn", ""), mv=mv_now, liga_avg_win_prob=liga_avg_win_prob,
            peer_estimate=peer_est, punktetyp_idx=p_idx,
            erwartete_tordifferenz=erw_tordiff, erwartete_tore=erw_tore)
        market_scored.append({
            "id": str(p.get("i")),
            "name": f"{p.get('fn', '')} {p.get('n', '')}".strip(),
            "pos": pos_now,
            "tid": str(d.get("tid", p.get("tid", "")) or ""),
            "mv": mv_now,
            "ap": p.get("ap", 0),
            "tfhmvt": d.get("tfhmvt", 0) or 0,
            "mv_history": mv_history,
            "score": total, "components": comps, "meta": meta, "opponents": opps,
            "fitness": d.get("stxt", ""), "expiry_s": p.get("exs", 0),
            "team": d.get("tn", ""), "st": d.get("st", 0),
            "prob": d.get("prob", 3),
            "kickbase_color": color_now,
            "team_strength": team_strength,
            "min_price_player": min_price_now,
            "expected_points_mine": ep_market,
            "fair_value": fair_value_mv,
            "expected_ap_for_mv": round(expected_ap_for_mv, 1) if expected_ap_for_mv is not None else None,
            "reliability": profile,
            "reliable_type": reliable_type,
            "punktetyp_text": punktetyp_text,
        })

    # --- Star-Power: Topspieler von Topteams dürfen nicht untergehen ---
    # mv_pct: wo steht der Spieler preislich in Kader+Markt zusammen? (A2-Fix:
    # gegen das reine Tages-Markt-Set von ~16 Spielern normiert war zu klein/
    # volatil - ein zufällig teurer Markt-Tag machte jeden zum "Star". Kader+
    # Markt zusammen ist breiter und tagesstabiler. Volle Liga-Sicht (alle
    # Mitspielerkader) bräuchte die noch nicht gebaute Mitspieler-Analyse.)
    mv_universe = [s["mv"] for s in squad_classified] + [x["mv"] for x in market_scored]
    mvs = sorted(mv_universe) or [1]
    for x in market_scored:
        mv_pct = mvs.index(x["mv"]) / max(1, len(mvs) - 1)
        star = 0.55 * mv_pct + 0.45 * x["team_strength"]
        x["star"] = round(star, 2)
        x["banger"] = (star >= 0.72 and x["st"] == 0 and x["prob"] <= 2)
        # Star fließt moderat in den Score ein (max +10), transparent:
        if star > 0.5:
            x["score"] = round(min(100, x["score"] + (star - 0.5) * 20), 1)

    # league_overpay bereits oben berechnet (für die vorgezogene B5-Preiskurve).
    compared, free_slots = market_vs_squad(market_scored, squad_classified,
                                           budget, max_squad,
                                           league_overpay=league_overpay,
                                           club_limit=club_limit)
    compared = finalize_headline_recommendations(compared)
    if free_slots:
        print(f"   ({free_slots} freie Kaderplätze!)")

    # SPEC_spieltagsmodell_v2.md 3.2/3.3: Ideal-Elf-Brücke + vierstufige
    # Empfehlung je Marktspieler - NACH free_slots/team_verdict, die beide
    # hier gebraucht werden.
    from squad_analysis import bridge_to_ideal_elf, recommendation_tier
    for m in compared:
        bridge = bridge_to_ideal_elf(m.get("expected_points_mine", 0), m["pos"], lineup_opt, free_slots)
        m["ideal_elf_bridge"] = bridge
        m["tier"] = recommendation_tier(m, bridge)

    def _print_bid_extra(b):
        if b.get("projection_note"):
            print(f"     ↳ {b['projection_note']}")
        if b.get("star_ceiling"):
            print(f"     ↳ Star-Ausnahme: in Einzelfällen bis "
                  f"{b['star_ceiling']:,.0f} € belegt (nicht die Regel)")

    bangers = sorted([m2 for m2 in compared if m2.get("banger")],
                     key=lambda x: -x["star"])
    if bangers:
        print("\n💎 BANGER-ZIELE (Starpower - teuerste Klasse + Topteam + fit):")
        for m2 in bangers[:3]:
            b = m2["bid"]
            tick = "✅" if m2["affordable"] else "❌"
            print(f"• {m2['name']} ({m2['team']}, {m2['pos']}) Star {m2['star']:.0%} "
                  f"| Kader-Score {m2['score']} | MW {m2['mv']:,.0f} ({m2['tfhmvt']:+,.0f}/Tag)")
            print(f"  🎯 {m2['team_verdict']}")
            print(f"  💶 Gebot {b['recommended_bid']:,.0f} € "
                  f"(WK ~{b['win_probability']:.0%}) {tick} {m2['financing']}")
            _print_bid_extra(b)

    # REVIEW_architektur_KOMPLETT.md 2.5: Banger-Kandidaten oben bereits
    # gezeigt - im normalen Kartenblock ausschließen, sonst dieselbe Karte
    # zweimal (identisch zum html_report._transfermarkt_section()-Fix).
    banger_ids_console = {b["id"] for b in bangers[:3]}
    for m in [m for m in compared if m["id"] not in banger_ids_console][:6]:
        color_txt = f" [{m['kickbase_color']}]" if m.get("kickbase_color") else ""
        print(f"\n• {m['name']} ({m['pos']}){color_txt} Kader-Score {m['score']} "
              f"| MW {m['mv']:,.0f} ({m['tfhmvt']:+,.0f}/Tag) | Ø {m['ap']} P "
              f"| ⏳ {m['expiry_s']/3600:.0f}h")
        if m.get("min_price_player"):
            print(f"  💰 Fair Value: neutral - Mindestpreis (kann nicht billiger sein)")
        elif m.get("fair_value") is not None:
            diff_pct = (m["fair_value"] - m["mv"]) / m["mv"] if m["mv"] else 0
            urteil = "unterbewertet" if diff_pct > 0.05 else ("überbewertet" if diff_pct < -0.05 else "fair bewertet")
            print(f"  💰 Fair Value {m['fair_value']:,.0f} € · aktuell {m['mv']:,.0f} € "
                  f"({diff_pct:+.0%}) - {urteil}")
            if m.get("expected_ap_for_mv") is not None:
                print(f"     liefert Ø {m['ap']:.0f} P, für {m['mv']:,.0f} € üblich "
                      f"wären {m['expected_ap_for_mv']:.0f} P")
        print(f"  {explain(m['components'], m.get('meta'))}")
        if m["opponents"]:
            print(f"  Nächste Gegner: {', '.join(m['opponents'])}")
        if m["fitness"]:
            print(f"  ⚠️ {m['fitness']}")
        # SPEC_spieltagsmodell_v2.md 3.2/3.3: Ideal-Elf-Brücke + Tier-Badge.
        bridge = m.get("ideal_elf_bridge") or {}
        if bridge.get("kind") == "free_slot":
            print(f"  ⚽ würde in deine Ideal-Elf einrücken (freier Kaderplatz) - "
                  f"{bridge['gain']:.0f} erwartete Punkte")
        elif bridge.get("kind") == "verdraengt":
            t = bridge["target"]
            print(f"  ⚽ würde deine Ideal-Elf verstärken: verdrängt {t['name']} "
                  f"({t['expected_points']:.0f} P) · +{bridge['gain']:.0f} P")
        elif bridge.get("kind") == "kein_platz":
            t = bridge["target"]
            print(f"  ⚽ käme nicht in deine Elf ({t['pos']} ist mit {t['name']} "
                  f"({t['expected_points']:.0f} P) besetzt, {bridge['gap']:.0f} P schwächer)")
        print(f"  🎯 [{m.get('tier', '?')}] {m['team_verdict']}")
        if "KEIN BEDARF" not in m["team_verdict"]:
            b = m["bid"]
            if b.get("verdict") == "nicht_bieten":
                print(f"  🚫 NICHT BIETEN - {b.get('verdict_reason', 'zu teuer')}")
            else:
                tick = "✅" if m["affordable"] else "❌ nicht finanzierbar"
                print(f"  💶 Gebot {b['recommended_bid']:,.0f} € "
                      f"(22h-MW ~{b['expected_mv_22h']:,.0f}, Puffer {b['buffer_pct']}%, "
                      f"WK ~{b['win_probability']:.0%}) {tick} {m['financing']}")
            _print_bid_extra(b)

    # ---------- 3) BESTE SPIELER DER LIGA (B5, Zwei-Listen-Ranking) ----------
    # board bereits oben berechnet (für die vorgezogene Preiskurve/Fair Value).

    # Pflicht-Selbstprüfung der Preiskurve (Spec 3.5): Anteil "über Erwartung"
    # muss nahe 50% liegen, sonst ist der Fit verzerrt.
    diag = board.get("price_curve") or {}
    if diag.get("curve"):
        curve_txt = "   ".join(f"{mv/1e6:.1f} Mio -> {ap:.0f} P" for mv, ap in diag["curve"][::2])
        plausible_txt = "PLAUSIBEL" if diag["plausible"] else "⚠️ VERZERRT"
        print(f"\n📐 Preis-Referenzkurve ({diag['n']} bewertete Spieler):")
        print(f"   {curve_txt}")
        print(f"   Über Erwartung: {diag['over_pct']:.0%} der Spieler   [{plausible_txt}]")

    status_icon = {"EIGEN": "🟢", "MITSPIELER": "👤", "MARKT": "🛒", "FREI": "⚪",
                  "UNBEKANNT": "❓"}

    # REVIEW_architektur_KOMPLETT.md Item 1: "Score" disambiguiert, s.
    # html_report.BOARD_SCORE_LABEL - dieselbe Zahl heißt hier "Liga-Score
    # (Qualität/Deal)", nicht der "Kader-Score" aus scoring.score_player().
    board_score_label = {"quality_score": "Liga-Score (Qualität)", "value_score": "Liga-Score (Deal)"}

    def _print_board_entry(e, score_key):
        icon = status_icon.get(e["status"], "❓")
        owner_txt = f" ({e['owner']})" if e["owner"] else ""
        both = " ⭐ (auch in der anderen Liste)" if e.get("in_both") else ""
        label = board_score_label.get(score_key, "Score")
        print(f"  {icon} {e['name']} ({e['team']}) {label} {e[score_key]} | "
              f"MW {e['mv']:,.0f} | Ø {e['ap']} P | {e['status']}{owner_txt}{both}")
        if e.get("residual_pct") is not None:
            sign = "+" if e["residual_abs"] >= 0 else ""
            print(f"      Ø {e['ap']} P - für {e['mv']/1e6:.0f} Mio erwartbar wären "
                  f"{e['expected_ap']:.0f} P - {sign}{e['residual_abs']:.0f} P "
                  f"({sign}{e['residual_pct']:.0f}%) ggü. Preiserwartung")
        if e["bid"]:
            tick_note = e["bid"].get("projection_note")
            print(f"      💶 Gebot {e['bid']['recommended_bid']:,.0f} € "
                  f"(WK ~{e['bid']['win_probability']:.0%})"
                  + (f" ↳ {tick_note}" if tick_note else ""))

    if board["bangers"]:
        print(f"\n💎 LIGA-BANGER (Top 5 in BEIDEN Listen derselben Position):")
        for e in board["bangers"]:
            _print_board_entry(e, "quality_score")

    print(f"\n🏆 BESTE SPIELER DER LIGA - Qualität, preisunabhängig "
          f"(Top {LEAGUE_BOARD_TOP_N} je Position):")
    for pos, entries in board["quality"].items():
        if not entries:
            continue
        print(f"\n  -- {pos} --")
        for e in entries[:5]:
            _print_board_entry(e, "quality_score")

    print(f"\n💰 BESTE DEALS DER LIGA - Preis-Leistung/Trading "
          f"(Top {LEAGUE_BOARD_TOP_N} je Position):")
    for pos, entries in board["value"].items():
        if not entries:
            continue
        print(f"\n  -- {pos} --")
        for e in entries[:5]:
            _print_board_entry(e, "value_score")

    # ---------- 4) DASHBOARD-BAUSTEINE (Report-Objekt statt nur Text) ----------
    ranking = kb.get_ranking(league_id)
    kpis = compute_kpis(budget, squad_classified, max_squad, squad_slots,
                        capacity, net_value, max_debt, ranking, kb.user_id)

    # ---------- Modul 3: Team-Analyse aller Liga-Manager ----------
    league_teams = build_league_teams(kb, cid, league_id, ranking, strength_map,
                                      upcoming, fixture_mode, _match_name,
                                      liga_avg_win_prob=liga_avg_win_prob,
                                      own_uid=kb.user_id, own_entry=own_entry,
                                      peer_lookup=peer_lookup, match_context=match_context)
    print(f"\n👥 SPIELTAGSPROGNOSE - alle {len(league_teams)} Manager "
          f"(echte gesetzte Elf, keine Bestmöglich-Annahme):")
    print(f"   {'#':>2} {'Manager':<18} {'Prognose':>18} {'Kaderstärke':>12} "
          f"{'Effizienz':>10} {'Formation':>10} {'Slots':>6}")
    # SPEC_spieltagsmodell_v2.md 1.2: Pflicht-Diagnosetabelle je Manager, der
    # >25% vom EIGENEN pspts-Anker abweicht - zeigt die Faktor-Kaskade statt
    # nur die Abweichung zu behaupten.
    pspts_by_uid = {str(u.get("i")): u.get("pspts") for u in (ranking.get("us") or []) if u.get("i")}
    matchdays_cfg = cfg.get("matchdays", 34)

    for i, m in enumerate(league_teams, 1):
        marker = " ⭐ (ich)" if str(m["uid"]) == str(kb.user_id) else ""
        rng = f"({m['prognose_range'][0]:.0f}-{m['prognose_range'][1]:.0f})"
        eff = f"{m['effizienz']:.0f}%" if m["effizienz"] is not None else "?"
        ks = f"{m['kaderstaerke']:.0f}" if m["kaderstaerke"] is not None else "?"
        slot_txt = f"{11 - m['empty_slots']}/11"
        print(f"   {i:>2} {m['name']:<18} {m['prognose']:>7.1f} {rng:>10} "
              f"{ks:>12} {eff:>10} {(m['formation'] or '?'):>10} {slot_txt:>6}{marker}")
        # 1.3: kein stummes "?" - fehlende Kaderstärke wird begründet.
        if m.get("kaderstaerke_reason"):
            print(f"      ℹ️ Kaderstärke: {m['kaderstaerke_reason']}")
        elif m.get("effizienz_text") and m["effizienz"] is not None and m["effizienz"] < 97:
            print(f"      ℹ️ Effizienz {m['effizienz']:.0f}% - {m['effizienz_text']}")
        # 1.4: höchstens 2 Kontext-Hinweise je Manager, nach Wirkung sortiert
        # (keine wortgleiche Wiederholung für jeden Manager).
        hints = []
        if m["empty_slots"]:
            hints.append((3, f"⚠️ {m['empty_slots']} unbesetzte Slot(s)"))
        if m["klumpenrisiko"] and m["klumpenrisiko"] >= 30:
            hints.append((2, f"⚠️ Klumpenrisiko: {m['klumpenrisiko']:.0f}% der Prognose aus {m['top_team']}"))
        if m.get("formation_hint"):
            hints.append((1, f"ℹ️ {m['formation_hint']}"))
        hints.sort(key=lambda x: -x[0])
        for _, txt in hints[:2]:
            print(f"      {txt}")
        # 2.3: Direktduelle NUR innerhalb der Startelf, nur wenn eins vorliegt.
        for txt in (m.get("duel_hints") or []):
            print(f"      ⚔️ {txt}")
        # 1.2 Diagnose-Kaskade bei >25% Abweichung vom EIGENEN pspts-Anker.
        pspts = pspts_by_uid.get(str(m["uid"]))
        if pspts and matchdays_cfg and m["xi"]:
            m_anchor = pspts / matchdays_cfg
            if m_anchor > 0:
                dev = (m["prognose"] - m_anchor) / m_anchor
                if abs(dev) > 0.25:
                    d = coach.diagnose_prognose(m["xi"])
                    print(f"      📐 Diagnose (Anker {m_anchor:.0f}, Abw. {dev:+.0%}): "
                          f"Basis {d['basis']:.0f} → Einsatz {d['nach_einsatz']:.0f} "
                          f"(×{d['einsatz_effektiv']}) → Gegner {d['nach_gegner']:.0f} "
                          f"(×{d['gegner_effektiv']}) → Form {d['nach_form']:.0f} "
                          f"(×{d['form_effektiv']}) + Zu-Null {d['zu_null']:+.0f} = {d['final']:.0f}")

    # SPEC_kalibrierung_fairvalue.md 1.1: harter Kalibrierungsanker aus
    # `ranking.us[].pspts`/Spieltagszahl - hätte den ursprünglichen Faktor-
    # Bug (Prognose ~250-450 statt real ~900) sofort sichtbar gemacht.
    # Pflicht-Selbstprüfung, kein optionales Debug-Feature.
    calibration = _check_pspts_anchor(ranking, matchdays_cfg, league_teams)
    if calibration:
        icon = "⚠️ KALIBRIERUNGS-WARNUNG" if not calibration["plausible"] else "✅ Kalibrierungsanker"
        print(f"\n{icon}: Median der Spieltagsprognosen {calibration['median_prognose']:.0f} P "
              f"vs. Vorsaison-Anker {calibration['anchor']:.0f} P/Spieltag "
              f"(aus pspts/{matchdays_cfg}) - Abweichung {calibration['deviation']:+.0%}"
              + ("" if calibration["plausible"] else " (Faktoren prüfen!)"))

    # ---------- SPEC_spieltagsmodell_v2.md 4.4: Rückkopplungs-Protokollierung
    # (zeitkritisch - vor dem ersten Anpfiff scharfgeschaltet) ----------
    # AUSWERTUNG_spieltag1.md: `matchday` kam bisher aus einer Konstante -
    # live gefunden, dass das nach Spieltag 1 zu STILLER Fehlbeschriftung
    # führt (kickoff_first rückte automatisch auf Spieltag 2 vor, der
    # Freeze öffnete sich erneut, `1899_md1.json` wurde mit Spieltag-2-
    # Prognosen überschrieben, aber weiter "matchday: 1" genannt - hat die
    # Retrospektive für Spieltag 1 im Nachhinein unbrauchbar gemacht).
    # `get_current_matchday()` (fixtures.py, dieselbe OpenLigaDB-Quelle wie
    # season_start_date) liefert jetzt die echte Nummer; Fallback 1 nur,
    # wenn die Quelle nichts liefert (z.B. La Liga, unverifiziert) oder die
    # Saison noch nicht begonnen hat.
    matchday = current_matchday or 1
    kickoff_first = season_start_date  # None für La Liga (kein Datumsfeld verfügbar)
    # SPEC_lernzyklus.md 5.3: die VORHERIGE Prognose vor dem Überschreiben
    # laden - Grundlage für den Änderungsnachweis unten.
    previous_pred = load_matchday_prediction(name, matchday)
    pred_path = save_matchday_prediction(name, matchday, kickoff_first, league_teams,
                                         calibration["anchor"] if calibration else None,
                                         run_timestamp)
    if pred_path:
        print(f"\n💾 Spieltagsprognose protokolliert: {pred_path}")
    pred_diff = diff_predictions(previous_pred, league_teams)
    if pred_diff and (pred_diff["changes"] or pred_diff["unexplained"]):
        print(f"\n📈 PROGNOSE-ÄNDERUNGEN seit letztem Lauf:")
        for c in pred_diff["changes"]:
            print(f"   {c['name']}: {c['from']:.0f} → {c['to']:.0f} ({c['delta']:+.0f})")
            print(f"      Ursache: {c['cause'] or '⚠️ nicht erklärbar'}")
    bid_entries = [
        {"player": m["name"], "pos": m["pos"], "team": m.get("team"),
         "mv_now": m.get("mv"), "recommended_bid": m["bid"].get("recommended_bid"),
         "fair_value": m.get("fair_value"), "expected_mv_22h": m["bid"].get("expected_mv_22h"),
         "regime": m["bid"].get("regime"), "verdict": m["bid"].get("verdict")}
        for m in compared if m.get("bid") and "KEIN BEDARF" not in m["team_verdict"]
    ]
    save_daily_bids(name, run_timestamp.strftime("%Y-%m-%d"), bid_entries, run_timestamp)
    # AUSWERTUNG_spieltag1.md-Folgefund: Datei B (Ist-Werte) und die
    # Abweichungszerlegung gehören zum zuletzt ABGESCHLOSSENEN Spieltag,
    # nicht zum kommenden (=`matchday`, das die Prognose oben verwendet) -
    # beide liefen bisher unter demselben `matchday`-Wert, was live gefunden
    # zu einem Fehlvergleich führte: sobald `matchday` nach Spieltag 1 auf 2
    # vorrückte, verglich deviation_report() die neue Spieltag-2-Prognose
    # gegen `ranking.us[].mdp`, das zu dem Zeitpunkt immer noch Spieltag 1s
    # echtes Ergebnis zeigte (Spieltag 2 war ja noch nicht gespielt) - ein
    # inhaltlich sinnloser, aber unauffällig falscher Vergleich.
    # `save_matchday_actuals()`s eigene `mdp>0`-Prüfung greift das nicht ab,
    # weil sie nur "hat IRGENDEIN Spieltag begonnen" prüft, nicht "ist GENAU
    # DIESER Spieltag beendet". Fix: separater Spieltagszähler für Datei B.
    last_completed_matchday = (current_matchday - 1) if current_matchday and current_matchday > 1 else None
    deviation = None
    retrospective_note = None
    retrospective_caveat = None
    if last_completed_matchday:
        actuals_path = save_matchday_actuals(name, last_completed_matchday, ranking, run_timestamp)
        if actuals_path:
            print(f"💾 Spieltags-Ist-Werte protokolliert: {actuals_path}")
        deviation = deviation_report(name, last_completed_matchday)

        # Datenqualitäts-Gate (2026-08-12, User-Feedback "PaulBowa hat 1500
        # Punkte als Prognose - das kann nicht die Vorab-Prognose sein"):
        # live gefunden, dass `data/predictions/1899_md1.json` durch einen
        # Bot-Lauf NACH dem ursprünglichen get_current_matchday()-Fix
        # trotzdem nochmal korrumpiert wurde (s. fixtures.get_season_info()-
        # Docstring - der eigentliche root cause, jetzt behoben). Eine
        # gespeicherte Prognosedatei für `last_completed_matchday` ist nur
        # verwertbar, wenn ihr `kickoff_first` VOR dem aktuellen, gerade neu
        # gefetchten `season_start_date` liegt (der VORHERIGE Spieltag muss
        # chronologisch vor dem AKTUELLEN angepfiffen haben) - verletzt das,
        # enthält die Datei nachweislich Daten eines SPÄTEREN Spieltags,
        # nur falsch beschriftet, und darf nicht als "Vorab-Prognose"
        # gezeigt werden.
        stale_prediction = False
        raw_pred = load_matchday_prediction(name, last_completed_matchday)
        if raw_pred and season_start_date and raw_pred.get("kickoff_first"):
            from datetime import datetime
            try:
                pred_kickoff = datetime.fromisoformat(raw_pred["kickoff_first"])
                stale_prediction = pred_kickoff >= season_start_date
            except ValueError:
                pass

        # Lineup-Aktualitäts-Hinweis (2026-08-12, User-Feedback "du hast
        # doch die ganzen Reports und so" - nach dem obigen Korruptions-Fund
        # wurde `1899_md1.json` aus dem git-Commit "Finaler Spieltagsprognose-
        # Snapshot vor Anpfiff Spieltag 1" wiederhergestellt, s. CLAUDE.md.
        # Diese Datei ist inhaltlich SAUBER (kein Bug), aber laut
        # AUSWERTUNG_spieltag1.md änderten ALLE 11 Manager ihre Aufstellung
        # noch NACH diesem letzten Lauf vor Anpfiff (13:11/hier 10:04 UTC vs.
        # echter ~18:29-Uhr-Deadline) - die Team-Summen bleiben aussagekräftig,
        # die Einsatz/Ausgang/Zu-Null-Zuordnung je EINZELNEM Spieler kann für
        # zwischenzeitlich getauschte Spieler abweichen. Genereller Hinweis
        # (nicht MD1-spezifisch hartkodiert) - greift künftig automatisch,
        # falls ein Lauf mal deutlich vor der echten Deadline eingefroren wird.
        retrospective_caveat = None
        if raw_pred and not stale_prediction and raw_pred.get("generated_at") and raw_pred.get("kickoff_first"):
            from datetime import datetime
            try:
                gap_h = (datetime.fromisoformat(raw_pred["kickoff_first"])
                        - datetime.fromisoformat(raw_pred["generated_at"])).total_seconds() / 3600
                if gap_h > 2:
                    retrospective_caveat = (
                        f"Basis: letzter gespeicherter Lauf {gap_h:.1f}h vor Anpfiff - "
                        f"einzelne Manager können ihre Aufstellung danach noch geändert haben. "
                        f"Team-Prognose/-Ist bleibt aussagekräftig, die Einsatz/Ausgang/Zu-Null-"
                        f"Zuordnung je Spieler kann in Einzelfällen abweichen.")
            except ValueError:
                pass

        # User-Feedback ("es ist immer noch inkonsistent... unklar welcher
        # der beiden Werte die finalen Spieltagspunkte sind"): früher gab es
        # HIER zusätzlich eine eigene "ABWEICHUNGSZERLEGUNG"-Kurzzeile mit
        # einer UNABHÄNGIG berechneten Prognose/Ist-Zahl (aus
        # deviation_report()) NEBEN der u.g. Wasserfall-Zerlegung (eigene
        # Prognose-Summe aus den Faktoren) - beide Zahlen wichen minimal
        # voneinander ab (Rundung) und zeigten teils eine andere Top-5-
        # Reihenfolge, was wie Willkür wirkte. Jetzt EINE Quelle der
        # Wahrheit: `deviation` wird nur noch für den offiziellen Ist-Wert
        # (Autorität, s.u.) genutzt, nicht mehr eigenständig gedruckt.
        printable = []
        if stale_prediction:
            retrospective_note = (
                f"Rückblick Spieltag {last_completed_matchday} nicht verwertbar: die "
                f"gespeicherte Vorab-Prognose ist nachweislich korrumpiert (kickoff_first "
                f"{raw_pred['kickoff_first']} liegt NACH dem aktuellen Saisonstart "
                f"{season_start_date.isoformat()} - enthält Daten eines späteren Spieltags, "
                f"falsch beschriftet). Bekannter, jetzt behobener Bug, s. CLAUDE.md.")
            print(f"\n⚠️ {retrospective_note}")
            retrospective_teams = None
        else:
            official_actuals = ({r["uid"]: r["actual"] for r in deviation["rows"]}
                                if deviation else None)
            try:
                retrospective_teams = build_waterfall_report(
                    kb, league_id, name, last_completed_matchday, liga_avg_win_prob,
                    official_actuals=official_actuals)
            except Exception as e:
                print(f"⚠️ Wasserfall-Zerlegung fehlgeschlagen: {e}")
                retrospective_teams = None
            if retrospective_teams:
                # AUSWERTUNG_spieltag2.md 3.2/5 (User-Feedback: "beantwortet
                # nicht die Frage, die man zuerst hat: Wie stehe ich?"):
                # sortiert jetzt nach dem ECHTEN erzielten Punktestand
                # (Platzierung) statt nach Abweichung - die Abweichung bleibt
                # als Zusatzinfo je Zeile erhalten, bestimmt aber nicht mehr
                # die Reihenfolge. Zeigt ALLE Manager (Tabellen-Charakter),
                # nicht nur die Top 5.
                printable = sorted(
                    (t for t in retrospective_teams if "differenz" in t),
                    key=lambda x: (-x["ist"], x["uid"]))
            elif deviation:
                # Wasserfall-Zerlegung nicht verfügbar (z.B. Player-Fetch
                # fehlgeschlagen) - wenigstens den offiziellen Wert zeigen,
                # explizit als Fallback gekennzeichnet, kein zweites System.
                printable = [{"uid": r["uid"], "name": r["name"], "prognose": r["predicted"],
                             "ist": r["actual"], "differenz": r["diff"], "checksum_ok": True}
                            for r in sorted(deviation["rows"], key=lambda x: (-x["actual"], x["uid"]))]
        if printable:
            print(f"\n📉 RÜCKBLICK · Spieltag {last_completed_matchday} - offizielle "
                  f"Platzierung, Prognoseabweichung als Zusatzinfo:")
            if retrospective_caveat:
                print(f"   ℹ️ {retrospective_caveat}")
            for i, t in enumerate(printable, 1):
                flag = "" if t.get("checksum_ok", True) else " ⚠️ Prüfsumme verletzt"
                print(f"   {i}. {t['name']}: {t['ist']:.0f} P "
                      f"(Vorab-Prognose {t['prognose']:.0f} P, Differenz {t['differenz']:+.0f}){flag}")
                if t.get("implausible"):
                    pct = abs(t["ist"] - t["ist_spielersumme"]) / max(abs(t["prognose"]), 1) * 100
                    print(f"      ⚠️ Zerlegung nicht möglich: Spieler-Summe "
                          f"({t['ist_spielersumme']:.0f} P) weicht um {pct:.0f}% der Prognose "
                          f"vom offiziellen Wert ({t['ist']:.0f} P) ab - Datenproblem "
                          f"(vermutlich abweichende Aufstellung), keine Modellabweichung.")
                elif "delta_einsatz" in t:
                    dl = f" · Datenlücke {t['delta_datenluecke']:+.0f}" if "delta_datenluecke" in t else ""
                    print(f"      davon erklärt: Einsatz {t['delta_einsatz']:+.0f} · "
                          f"Ausgang {t['delta_ausgang']:+.0f} · "
                          f"Zu-Null {t['delta_zunull']:+.0f} · "
                          f"Leistung (unerklärt) {t['delta_leistung']:+.0f}{dl}")
                    if "delta_datenluecke" in t:
                        print(f"      ℹ️ Datenlücke = Aufstellungs-Momentaufnahme war nicht "
                              f"die finale (offizieller Wert {t['ist']:.0f} P übernommen, "
                              f"Spieler-Summe war {t['ist_spielersumme']:.0f} P)")
    else:
        retrospective_teams = None

    actions = build_actions(compared, squad_classified)
    squad_action_items = build_squad_action_items(squad_classified)
    targets = build_targets(board)
    mitspieler_appendix = build_mitspieler_appendix(board)
    risks = build_risks(capacity, net_value, budget, compared, bool(upcoming))

    # Leere Aufstellungsslots sind dringend (Spec 5.2/6.3) - vorne in Risiken
    # UND Handlungsleiste, mit konkretem Positionsbezug wenn ableitbar.
    if lineup_status and lineup_status["empty_slots"]:
        gap_txt = ", ".join(f"{n}× {pos}" for pos, n in missing_pos.items()) or "Position unklar"
        risks.insert(0, {
            "level": "warn", "icon": "⚠️",
            "text": f"{lineup_status['empty_slots']} freie Aufstellungsslot(s) vor der 20:29-Deadline - fehlt: {gap_txt}",
        })
        actions.insert(0, {
            "icon": "🧩", "kind": "lineup_gap", "urgent": True,
            "text": f"{lineup_status['empty_slots']} Aufstellungsslot(s) unbesetzt",
            "amount": None, "deadline_hours": None,
            "reason": f"Fehlt: {gap_txt} - Deadline 20:29 Uhr",
        })
        actions[:] = actions[:5]

    report = {
        "league": {"name": name, "id": league_id, "cid": cid,
                  "generated_at": run_timestamp.isoformat()},
        "kpis": kpis,
        "actions": actions,
        "squad_action_items": squad_action_items,
        "squad_classified": squad_classified,
        "market": compared,
        "bangers": bangers,
        "free_slots": free_slots,
        "fixture_mode": fixture_mode,
        "has_fixtures": bool(upcoming),
        "league_board": board,
        "targets": targets,
        "mitspieler_appendix": mitspieler_appendix,
        "lineup": lineup_opt,
        "ideal_prognose": ideal_prognose,
        "ist_prognose": ist_prognose,
        "kaderstaerke_reason": kaderstaerke_reason,
        "league_teams": league_teams,
        "my_uid": kb.user_id,
        "lineup_status": lineup_status,
        "lineup_swaps": swaps,
        "lineup_missing": missing_pos,
        "self_play_conflicts": self_play_conflicts,
        "plausibility_warnings": plausibility_warnings,
        "calibration": calibration,
        "deviation_report": deviation,
        "retrospective": retrospective_teams,
        "retrospective_note": retrospective_note,
        "retrospective_caveat": retrospective_caveat,
        "last_completed_matchday": last_completed_matchday,
        "prediction_diff": pred_diff,
        "fair_value_ok": fair_value_ok,
        "risks": risks,
        "meta": {"season_phase": season_phase(kpis)},
        # Rückwärtskompatible Top-Level-Felder (html_report.py-Detailblöcke):
        "name": name, "budget": budget, "max_squad": max_squad,
        "squad_slots": squad_slots,
    }

    previous = load_previous_snapshot(name, run_timestamp.strftime("%Y-%m-%d"))
    report["changes"] = diff_reports(report, previous)
    saved_path = save_report(report)
    print(f"\n💾 Tages-Snapshot gespeichert: {saved_path}")

    # ---------- 5) KI-EINORDNUNG (optional, überspringt sich selbst ohne Key) ----------
    if GEMINI_API_KEY:
        print("\n🤖 KI-Einordnung (Gemini)...")
    insights, llm_diag = generate_insights(report, strength_map, fixture_mode, _match_name,
                                           run_timestamp, GEMINI_API_KEY,
                                           season_start_date=season_start_date)
    report["llm_insights"] = insights
    # Punkt 2.1 (Spec-Fix 2026-08-05): stilles Verschwinden ist der
    # schlechteste Fall - der Report muss unterscheiden, OB die KI-Schicht
    # gar nicht konfiguriert ist (kein Key) oder heute nur fehlgeschlagen
    # ist (Kontingent/Fehler), statt beides gleich "nichts anzeigen".
    # SPEC_lernzyklus.md 6.1/6.4: llm_diag trägt jetzt IMMER die konkrete
    # Ursache (Statuscode, Fehlertext, quota_kind, Modell/Token) statt eines
    # pauschalen "failed".
    report["llm_status"] = "ok" if insights else llm_diag.get("status", "failed")
    report["llm_diag"] = llm_diag
    if insights:
        print(f"   {insights['report']}")
        for f in insights.get("player_flags", []):
            print(f"   ⚑ {f['player_name']}: {f['flag']} ({f['confidence']}) - {f['note']}")
        for o in insights.get("matchday_outlook", []):
            print(f"   ⚽ {o['match']}: {o['expected_script']} "
                  f"(begünstigt {'/'.join(o.get('beneficiary_positions', []))}) - {o['reason']}")

    return report


def main():
    from datetime import datetime, timezone

    kb = KickbaseAPI()
    print("Verbinde mit Kickbase...")
    if not kb.login():
        print("❌ Login fehlgeschlagen - EMAIL/PASSWORD prüfen (lokal in config.py, "
              "in CI in den GitHub Secrets KICKBASE_EMAIL/KICKBASE_PASSWORD).")
        sys.exit(1)
    run_timestamp = datetime.now(timezone.utc)
    reports = []
    for cfg in LEAGUES:
        if "DEIN-" in cfg["name"]:
            continue
        report = run_league(kb, cfg, run_timestamp)
        if report:
            reports.append(report)

    # SPEC_ranking_faktoren_llm.md 6.1: "erste Maßnahme, vor jeder
    # Anbieterdiskussion: Aufrufe je Lauf zählen und protokollieren" - der
    # gemeldete 429 RPM trotz "ein gebündelter Call je Liga"-Architektur
    # war unbelegt, jetzt sichtbar (inkl. Retries, die einen einzelnen
    # logischen Call auf mehrere echte HTTP-Requests vervielfachen können).
    summary = llm_call_summary()
    if summary["total"]:
        by_kind = ", ".join(f"{k}: {v}" for k, v in summary["by_kind"].items())
        print(f"\n📊 Gemini-Aufrufe in diesem Lauf: {summary['total']} ({by_kind})")

    write_html_report(reports, run_timestamp)


def write_html_report(reports, generated_at):
    import hashlib
    import os
    from config import PAGE_PASSWORD
    from html_report import render_html

    password_hash = (hashlib.sha256(PAGE_PASSWORD.encode()).hexdigest()
                     if PAGE_PASSWORD else None)
    html = render_html(reports, generated_at, password_hash)

    out_dir = "site"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n💾 HTML-Briefing geschrieben: {out_path}")


if __name__ == "__main__":
    main()
