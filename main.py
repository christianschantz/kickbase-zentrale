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
                      fixture_ease_for_team, get_season_start_date)
from scoring import score_player, explain, player_reliability_profile, punktetyp_label, kickbase_color
from bid_advisor import learn_league_overpay
from mv_forecast import clean_mv_series
from odds import load_fixture_odds, load_fixture_odds_api, fixture_ease_odds
from fixtures import _best_match
from squad_analysis import (classify_own_player, market_vs_squad,
                            finalize_headline_recommendations,
                            flag_formation_risk, POS_NAMES, DEBT_RATIO)
from league_board import build_league_lists
from report_builder import (compute_kpis, build_actions, build_squad_action_items,
                            build_targets, build_mitspieler_appendix, build_risks,
                            season_phase, save_report, load_previous_snapshot, diff_reports)
from llm_insights import generate_insights
import coach
from league_teams import build_league_teams

LEAGUE_BOARD_TOP_N = 10  # Top N je Position in der Liga-Bestenliste (B5)


def _match_name(name, candidates):
    m, _ = _best_match(name, candidates)
    return m


def load_fixture_data(cfg):
    # 1) Primär: Buchmacher-Quoten (football-data.co.uk fixtures.csv, kostenlos & keylos)
    odds_div = cfg.get("odds_div")
    if odds_div:
        upcoming_odds, power = load_fixture_odds(odds_div)
        if upcoming_odds:
            print(f"📊 Quoten geladen ({odds_div}): {len(power)} Teams, "
                  f"Top 3 laut Buchmachern: "
                  + ", ".join(sorted(power, key=power.get, reverse=True)[:3]))
            return power, upcoming_odds, "odds"
        print("ℹ️ Keine Quoten in fixtures.csv (Sommerpause?) -> Fallback the-odds-api.")

    # 1b) Fallback: the-odds-api.com (kostet Kontingent, nur wenn 1) leer ist)
    odds_api_sport = cfg.get("odds_api_sport")
    if odds_api_sport and ODDS_API_KEY:
        upcoming_api, power_api = load_fixture_odds_api(odds_api_sport, ODDS_API_KEY)
        if upcoming_api:
            print(f"📊 Quoten von the-odds-api geladen ({odds_api_sport}): "
                  f"{len(power_api)} Teams, Top 3 laut Buchmachern: "
                  + ", ".join(sorted(power_api, key=power_api.get, reverse=True)[:3]))
            return power_api, upcoming_api, "odds"
        print("ℹ️ Auch the-odds-api ohne Spiele -> Fallback Tabelle.")

    # 2) Fallback: Tabellen-basiert wie bisher
    src = cfg.get("fixture_source")
    if src == "openligadb":
        season, sc = cfg.get("season", "2026"), cfg.get("openligadb_shortcut", "bl2")
        return build_strength_map(get_table(season, sc)), get_upcoming_by_team(season, sc), "table"
    if src == "football-data":
        comp = cfg.get("football_data_competition", "PD")
        return (build_strength_map(get_table_football_data(comp, FOOTBALL_DATA_API_KEY)),
                get_upcoming_by_team_football_data(comp, FOOTBALL_DATA_API_KEY), "table")
    if src == "thesportsdb":
        lid = cfg.get("tsdb_league_id", "4335")
        return (build_strength_map(get_table_tsdb(lid, cfg.get("tsdb_season", "2026-2027"))),
                get_upcoming_by_team_tsdb(lid), "table")
    return {}, {}, "none"


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
    strength_map, upcoming, fixture_mode = load_fixture_data(cfg)
    if not upcoming:
        print("ℹ️ Kein Spielplan verfügbar -> Spielplan-Komponente neutral.")

    # Saisonstart live aus dem OpenLigaDB-Spielplan (verifiziert 2026-08-05,
    # nicht mehr geschätzt) - nur für openligadb-Quellen (2. Bundesliga)
    # verfügbar, football-data.org liefert bei uns kein Datumsfeld dafür.
    season_start_date = None
    if cfg.get("fixture_source") == "openligadb":
        season_start_date = get_season_start_date(
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
    squad_classified = []
    self_play_conflicts = []
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
            # Punkt 6 (Grundgerüst): erwartete Punkte für die Aufstellungs-
            # optimierung (coach.py) - `ease` (Sieg-WK-Näherung) und `ph`
            # (letzte Spieltage) liegen aus diesem Loop-Durchlauf schon vor,
            # kein Zusatz-Call nötig.
            ep, ep_factors = coach.expected_points(
                c["pos"], p.get("ap", 0), d.get("ph"), d.get("st", 0),
                d.get("prob", 3), ease, team_name=d.get("tn", ""), mv=c["mv"])
            c["expected_points"] = ep
            c["ep_factors"] = ep_factors
            squad_classified.append(c)
        flag_formation_risk(squad_classified)
        # Punkt 2.2 (SPEC_gebote_ki_team_KOMPLETT.md): "eigene Spieler, die
        # gegeneinander spielen" ist algorithmisch erkennbar - gehört der KI
        # ausdrücklich benannt vorgesetzt statt selbst erraten.
        self_play_conflicts = coach.detect_self_play_conflicts(squad_classified, _match_name)
        if self_play_conflicts:
            print("\n⚔️ EIGENE SPIELER GEGENEINANDER:")
            for txt in self_play_conflicts:
                print(f"   {txt}")

        order = {"VERKAUFEN": 0, "BEOBACHTEN": 1, "STAMM": 2, "HALTEN (Trading)": 3}
        for c in sorted(squad_classified, key=lambda x: order.get(x["verdict"], 9)):
            icon = {"VERKAUFEN": "🔻", "BEOBACHTEN": "👀",
                    "STAMM": "⭐", "HALTEN (Trading)": "📈"}[c["verdict"]]
            color_txt = f" [{c['kickbase_color']}]" if c["kickbase_color"] else ""
            print(f"\n{icon} {c['name']} ({c['pos']}){color_txt} - {c['verdict']} "
                  f"| Score {c['score']} | MW {c['mv']:,.0f} ({c['tfhmvt']:+,.0f}/Tag)")
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
    lineup_opt = coach.optimize_lineup(squad_classified) if squad_classified else None
    lineup_status = (coach.current_lineup_status(lineup_raw, squad_classified)
                     if squad_classified else None)
    swaps = coach.suggest_swaps(lineup_status) if lineup_status else []
    missing_pos = (coach.missing_positions(lineup_status, lineup_opt)
                   if lineup_status and lineup_opt else {})

    if lineup_opt and lineup_opt["best"]:
        best = lineup_opt["formations"][lineup_opt["best"]]
        print(f"\n🧠 AUFSTELLUNGSEMPFEHLUNG: {lineup_opt['best']} "
              f"({lineup_opt['best_total']} erwartete Punkte) · Deadline 20:29 Uhr")
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

    if lineup_status:
        print(f"\n📋 AKTUELL GESETZTE ELF: {len(lineup_status['xi'])}/11 Slots belegt")
        if lineup_status["empty_slots"]:
            gap_txt = ", ".join(f"{n}× {pos}" for pos, n in missing_pos.items()) or "Position unklar"
            print(f"   ⚠️ {lineup_status['empty_slots']} freie(r) Slot(s) - fehlt: {gap_txt}")
        for s in swaps:
            print(f"   ↳ Slot {s['slot']}: {s['out']['name']} raus, {s['in']['name']} rein "
                  f"- erwartet {s['diff']:+.1f} Punkte")

    # Kaufkraft-Kennzahlen fürs Dashboard (identische Formel wie in
    # squad_analysis.market_vs_squad, dort nicht nach außen gereicht).
    squad_value = sum(s["mv"] for s in squad_classified)
    net_value = squad_value + budget
    max_debt = DEBT_RATIO * net_value
    capacity = max_debt + budget

    # Liga-Bestenliste (B5) und Overpay-Lernen VORGEZOGEN (Spec-Fix 2026-08-05
    # Punkt 1.2): der Tagesmarkt-Loop unten braucht die volle Liga-Preiskurve
    # für Fair Value - das Drucken der B5-Sektionen bleibt an seiner alten
    # Stelle weiter unten, hier wird nur berechnet.
    league_overpay = learn_league_overpay(kb.get_activities(league_id))
    own_ids = {c["id"] for c in squad_classified}
    board = build_league_lists(kb, cid, league_id, own_ids, strength_map,
                               upcoming, fixture_mode, _match_name,
                               weights_quality=WEIGHTS_QUALITY,
                               weights_value=WEIGHTS_VALUE,
                               top_n=LEAGUE_BOARD_TOP_N,
                               league_overpay=league_overpay)
    price_curve = (board.get("price_curve") or {}).get("curve")

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
        # Fair Value (Punkt 1.2): "was ist er sportlich wert" statt "was
        # wird er kosten" - über die Liga-Preiskurve (price_curve, oben
        # vorgezogen) aus Form × Einsatz × Gegner × Teamstärke.
        fair_value_mv, bereinigte_erwartung = coach.fair_value(
            POS_NAMES.get(p.get("pos"), "?"), mv_now, p.get("ap", 0), d.get("ph"),
            d.get("st", 0), d.get("prob", 3), ease, team_strength, price_curve)
        market_scored.append({
            "id": str(p.get("i")),
            "name": f"{p.get('fn', '')} {p.get('n', '')}".strip(),
            "pos": POS_NAMES.get(p.get("pos"), "?"),
            "tid": str(d.get("tid", p.get("tid", "")) or ""),
            "mv": mv_now,
            "ap": p.get("ap", 0),
            "tfhmvt": d.get("tfhmvt", 0) or 0,
            "mv_history": mv_history,
            "score": total, "components": comps, "meta": meta, "opponents": opps,
            "fitness": d.get("stxt", ""), "expiry_s": p.get("exs", 0),
            "team": d.get("tn", ""), "st": d.get("st", 0),
            "prob": d.get("prob", 3),
            "kickbase_color": kickbase_color(d.get("prob", 3)),
            "team_strength": team_strength,
            "fair_value": fair_value_mv,
            "fair_value_bereinigt": bereinigte_erwartung,
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
                  f"| Score {m2['score']} | MW {m2['mv']:,.0f} ({m2['tfhmvt']:+,.0f}/Tag)")
            print(f"  🎯 {m2['team_verdict']}")
            print(f"  💶 Gebot {b['recommended_bid']:,.0f} € "
                  f"(WK ~{b['win_probability']:.0%}) {tick} {m2['financing']}")
            _print_bid_extra(b)

    for m in compared[:6]:
        color_txt = f" [{m['kickbase_color']}]" if m.get("kickbase_color") else ""
        print(f"\n• {m['name']} ({m['pos']}){color_txt} Score {m['score']} "
              f"| MW {m['mv']:,.0f} ({m['tfhmvt']:+,.0f}/Tag) | Ø {m['ap']} P "
              f"| ⏳ {m['expiry_s']/3600:.0f}h")
        if m.get("fair_value") is not None:
            diff_pct = (m["fair_value"] - m["mv"]) / m["mv"] if m["mv"] else 0
            urteil = "unterbewertet" if diff_pct > 0.05 else ("überbewertet" if diff_pct < -0.05 else "fair bewertet")
            print(f"  💰 Fair Value {m['fair_value']:,.0f} € ({diff_pct:+.0%}) - {urteil}")
        print(f"  {explain(m['components'], m.get('meta'))}")
        if m["opponents"]:
            print(f"  Nächste Gegner: {', '.join(m['opponents'])}")
        if m["fitness"]:
            print(f"  ⚠️ {m['fitness']}")
        print(f"  🎯 {m['team_verdict']}")
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

    def _print_board_entry(e, score_key):
        icon = status_icon.get(e["status"], "❓")
        owner_txt = f" ({e['owner']})" if e["owner"] else ""
        both = " ⭐ (auch in der anderen Liste)" if e.get("in_both") else ""
        print(f"  {icon} {e['name']} ({e['team']}) Score {e[score_key]} | "
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
                                      upcoming, fixture_mode, _match_name)
    print(f"\n👥 SPIELTAGSPROGNOSE - alle {len(league_teams)} Manager "
          f"(echte gesetzte Elf, keine Bestmöglich-Annahme):")
    print(f"   {'#':>2} {'Manager':<18} {'Prognose':>18} {'Kaderstärke':>12} "
          f"{'Effizienz':>10} {'Formation':>10} {'Slots':>6}")
    for i, m in enumerate(league_teams, 1):
        marker = " ⭐ (ich)" if str(m["uid"]) == str(kb.user_id) else ""
        rng = f"({m['prognose_range'][0]:.0f}-{m['prognose_range'][1]:.0f})"
        eff = f"{m['effizienz']:.0f}%" if m["effizienz"] is not None else "?"
        ks = f"{m['kaderstaerke']:.0f}" if m["kaderstaerke"] is not None else "?"
        slot_txt = f"{11 - m['empty_slots']}/11"
        print(f"   {i:>2} {m['name']:<18} {m['prognose']:>7.1f} {rng:>10} "
              f"{ks:>12} {eff:>10} {(m['formation'] or '?'):>10} {slot_txt:>6}{marker}")
        if m["empty_slots"]:
            print(f"      ⚠️ {m['empty_slots']} unbesetzte Slot(s)")
        if m["klumpenrisiko"] and m["klumpenrisiko"] >= 30:
            print(f"      ⚠️ Klumpenrisiko: {m['klumpenrisiko']:.0f}% der Prognose aus {m['top_team']}")
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
        "league_teams": league_teams,
        "my_uid": kb.user_id,
        "lineup_status": lineup_status,
        "lineup_swaps": swaps,
        "lineup_missing": missing_pos,
        "self_play_conflicts": self_play_conflicts,
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
    insights = generate_insights(report, strength_map, fixture_mode, _match_name,
                                 run_timestamp, GEMINI_API_KEY,
                                 season_start_date=season_start_date)
    report["llm_insights"] = insights
    # Punkt 2.1 (Spec-Fix 2026-08-05): stilles Verschwinden ist der
    # schlechteste Fall - der Report muss unterscheiden, OB die KI-Schicht
    # gar nicht konfiguriert ist (kein Key) oder heute nur fehlgeschlagen
    # ist (Kontingent/Fehler), statt beides gleich "nichts anzeigen".
    report["llm_status"] = "ok" if insights else ("no_key" if not GEMINI_API_KEY else "failed")
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
