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
from config import LEAGUES, WEIGHTS, FOOTBALL_DATA_API_KEY, ODDS_API_KEY
from kickbase_api import KickbaseAPI
from fixtures import (get_table, get_table_football_data, build_strength_map,
                      team_strength_for,
                      get_upcoming_by_team, get_upcoming_by_team_football_data,
                      get_table_tsdb, get_upcoming_by_team_tsdb,
                      fixture_ease_for_team)
from scoring import score_player, explain, player_reliability_profile, punktetyp_label
from bid_advisor import learn_league_overpay
from odds import load_fixture_odds, load_fixture_odds_api, fixture_ease_odds
from fixtures import _best_match
from squad_analysis import (classify_own_player, market_vs_squad,
                            finalize_headline_recommendations, POS_NAMES)


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


def run_league(kb, cfg):
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
    strength_map, upcoming, fixture_mode = load_fixture_data(cfg)
    if not upcoming:
        print("ℹ️ Kein Spielplan verfügbar -> Spielplan-Komponente neutral.")

    # ---------- 1) EIGENER KADER ----------
    squad_raw = kb.get_squad(league_id)
    squad_players = squad_raw.get("it", []) or squad_raw.get("players", [])
    squad_classified = []
    if squad_players:
        print(f"\n👥 KADER-STATUS ({len(squad_players)}/{max_squad} Plätze, "
              f"Budget {budget:+,.0f} €):")
        for p, d, ease, opps in enrich_players(kb, league_id, squad_players,
                                               strength_map, upcoming, fixture_mode):
            c = classify_own_player(p, d, ease, WEIGHTS)
            c["opponents"] = opps
            squad_classified.append(c)

        order = {"VERKAUFEN": 0, "BEOBACHTEN": 1, "STAMM": 2, "HALTEN (Trading)": 3}
        for c in sorted(squad_classified, key=lambda x: order.get(x["verdict"], 9)):
            icon = {"VERKAUFEN": "🔻", "BEOBACHTEN": "👀",
                    "STAMM": "⭐", "HALTEN (Trading)": "📈"}[c["verdict"]]
            print(f"\n{icon} {c['name']} ({c['pos']}) - {c['verdict']} "
                  f"| Score {c['score']} | MW {c['mv']:,.0f} ({c['tfhmvt']:+,.0f}/Tag)")
            print(f"   {'; '.join(c['reasons'])}")
            if c["opponents"]:
                print(f"   Nächste Gegner: {', '.join(c['opponents'])}")
    else:
        print("\nℹ️ Kader-Endpoint lieferte nichts - Team-Ansicht in der App mit "
              "HTTP Toolkit aufnehmen, dann verifiziere ich den Pfad.")
    squad_slots = len(squad_players)

    # ---------- 2) MARKT IM TEAM-KONTEXT ----------
    market = kb.get_transfer_market(league_id)
    print(f"\n🛒 TRANSFERMARKT ({len(market)} freie Spieler) - im Kader-Kontext:")
    market_scored = []
    for p, d, ease, opps in enrich_players(kb, league_id, market, strength_map, upcoming, fixture_mode):
        total, comps, meta = score_player(p, d, ease, WEIGHTS)
        profile = player_reliability_profile(d)
        reliable_type, punktetyp_text = punktetyp_label(profile)
        market_scored.append({
            "id": str(p.get("i")),
            "name": f"{p.get('fn', '')} {p.get('n', '')}".strip(),
            "pos": POS_NAMES.get(p.get("pos"), "?"),
            "mv": d.get("mv", p.get("mv", 0)),
            "ap": p.get("ap", 0),
            "tfhmvt": d.get("tfhmvt", 0) or 0,
            "score": total, "components": comps, "meta": meta, "opponents": opps,
            "fitness": d.get("stxt", ""), "expiry_s": p.get("exs", 0),
            "team": d.get("tn", ""), "st": d.get("st", 0),
            "prob": d.get("prob", 3),
            "team_strength": team_strength_for(d.get("tn", ""), strength_map)
                if fixture_mode != "odds"
                else strength_map.get(_match_name(d.get("tn", ""), list(strength_map.keys())), 0.5),
            "reliability": profile,
            "reliable_type": reliable_type,
            "punktetyp_text": punktetyp_text,
        })

    # --- Star-Power: Topspieler von Topteams dürfen nicht untergehen ---
    # mv_pct: wo steht der Spieler preislich im aktuellen Markt-Set?
    mvs = sorted(x["mv"] for x in market_scored) or [1]
    for x in market_scored:
        mv_pct = mvs.index(x["mv"]) / max(1, len(mvs) - 1)
        star = 0.55 * mv_pct + 0.45 * x["team_strength"]
        x["star"] = round(star, 2)
        x["banger"] = (star >= 0.72 and x["st"] == 0 and x["prob"] <= 2)
        # Star fließt moderat in den Score ein (max +10), transparent:
        if star > 0.5:
            x["score"] = round(min(100, x["score"] + (star - 0.5) * 20), 1)

    league_overpay = learn_league_overpay(kb.get_activities(league_id))
    compared, free_slots = market_vs_squad(market_scored, squad_classified,
                                           budget, max_squad,
                                           league_overpay=league_overpay)
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
        print(f"\n• {m['name']} ({m['pos']}) Score {m['score']} "
              f"| MW {m['mv']:,.0f} ({m['tfhmvt']:+,.0f}/Tag) | Ø {m['ap']} P "
              f"| ⏳ {m['expiry_s']/3600:.0f}h")
        print(f"  {explain(m['components'], m.get('meta'))}")
        if m["opponents"]:
            print(f"  Nächste Gegner: {', '.join(m['opponents'])}")
        if m["fitness"]:
            print(f"  ⚠️ {m['fitness']}")
        print(f"  🎯 {m['team_verdict']}")
        if "KEIN BEDARF" not in m["team_verdict"]:
            b = m["bid"]
            tick = "✅" if m["affordable"] else "❌ nicht finanzierbar"
            print(f"  💶 Gebot {b['recommended_bid']:,.0f} € "
                  f"(22h-MW ~{b['expected_mv_22h']:,.0f}, Puffer {b['buffer_pct']}%, "
                  f"WK ~{b['win_probability']:.0%}) {tick} {m['financing']}")
            _print_bid_extra(b)

    return {
        "name": name,
        "budget": budget,
        "max_squad": max_squad,
        "squad_slots": squad_slots,
        "squad_classified": squad_classified,
        "market": compared,
        "bangers": bangers,
        "free_slots": free_slots,
        "fixture_mode": fixture_mode,
        "has_fixtures": bool(upcoming),
    }


def main():
    kb = KickbaseAPI()
    print("Verbinde mit Kickbase...")
    if not kb.login():
        print("❌ Login fehlgeschlagen - EMAIL/PASSWORD prüfen (lokal in config.py, "
              "in CI in den GitHub Secrets KICKBASE_EMAIL/KICKBASE_PASSWORD).")
        sys.exit(1)
    reports = []
    for cfg in LEAGUES:
        if "DEIN-" in cfg["name"]:
            continue
        report = run_league(kb, cfg)
        if report:
            reports.append(report)

    write_html_report(reports)


def write_html_report(reports):
    import hashlib
    import os
    from datetime import datetime, timezone
    from config import PAGE_PASSWORD
    from html_report import render_html

    password_hash = (hashlib.sha256(PAGE_PASSWORD.encode()).hexdigest()
                     if PAGE_PASSWORD else None)
    generated_at = datetime.now(timezone.utc)
    html = render_html(reports, generated_at, password_hash)

    out_dir = "site"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n💾 HTML-Briefing geschrieben: {out_path}")


if __name__ == "__main__":
    main()
