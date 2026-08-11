"""
AUSWERTUNG_spieltag1.md: echte Wasserfall-Zerlegung gegen die tatsächlichen
Ist-Werte von Spieltag 1 (2. Bundesliga). Einmaliger Analyselauf, kein Teil
der täglichen main.py-Pipeline (die Ist-Daten ändern sich nur einmal je
abgeschlossenem Spieltag, ein täglicher Re-Fetch wäre reine Verschwendung -
retrospective_data.fetch_player_actuals() cached ohnehin idempotent).

Herleitung der Ist-Faktoren (M_ist/G_ist/Z_ist) aus den real verfügbaren
Feldern:
- M_ist: SPEC-sanktionierte Vergröberung, da echte Einsatzminuten nicht
  abrufbar sind - `hp`-Flag (gespielt ja/nein) aus `ph`, binär 1,0/0,0.
- G_ist: dieselbe opponent_factor()-Formel wie die Prognose, aber mit dem
  REALISIERTEN Ausgang (Sieg=1,0/Unentschieden=0,5/Niederlage=0,0) statt der
  vorherigen Sieg-WK. `punktetyp_idx` kommt aus der gespeicherten Prognose
  (dort bereits pro Spieler mitgespeichert), `liga_avg_win_prob` wird frisch
  berechnet (strukturelle Größe, ändert sich kaum von Spieltag zu Spieltag).
- Z_ist: TW/ABW - volle Prämie, wenn das Team zu Null gespielt hat (aus
  mdsum t1g/t2g), sonst 0 - das ist jetzt ein bekannter Fakt, keine
  Wahrscheinlichkeit mehr.

Prüfsumme (retrospective.waterfall_player) bestätigt die Korrektheit der
Zerlegung selbst je Spieler.
"""
import json
import statistics

from config import LEAGUES
from kickbase_api import KickbaseAPI
from fixtures import league_avg_win_prob, build_strength_map, get_table, get_upcoming_by_team
from odds import load_fixture_odds
import coach
import retrospective
from retrospective_data import fetch_player_actuals, player_actuals_path

MATCHDAY = 1
LEAGUE_CFG = LEAGUES[0]  # 1899, 2. Bundesliga - einzige Liga mit gespeicherter Prognosedatei


def _win_prob_ist(result):
    return {"Sieg": 1.0, "Unentschieden": 0.5, "Niederlage": 0.0}.get(result)


def _liga_avg_win_prob_now(cfg):
    """Frische Schätzung derselben Größe, die main.py pro Lauf berechnet -
    strukturell (3-Ausgänge-Ø), ändert sich kaum von Spieltag zu Spieltag."""
    odds_div = cfg.get("odds_div")
    if odds_div:
        upcoming, power, _ = load_fixture_odds(odds_div)
        if upcoming:
            return league_avg_win_prob("odds", power, upcoming)
    season, sc = cfg.get("season", "2026"), cfg.get("openligadb_shortcut", "bl2")
    strength_map = build_strength_map(get_table(season, sc))
    upcoming = get_upcoming_by_team(season, sc)
    return league_avg_win_prob("table", strength_map, upcoming)


def build_ist(player_actual, pos, punktetyp_idx, liga_avg_win_prob_now):
    """Übersetzt einen retrospective_data-Eintrag in das ist-Dict, das
    retrospective.waterfall_player() erwartet."""
    m_ist = 1.0 if player_actual["played"] else 0.0
    win_prob_ist = _win_prob_ist(player_actual.get("team_result"))
    if win_prob_ist is not None:
        g_ist = coach.opponent_factor(pos, win_prob_ist, liga_avg_win_prob_now, punktetyp_idx)
    else:
        g_ist = None  # Ergebnis unbekannt (z.B. Partie nicht gefunden) -> fällt auf Prognose zurück
    z_ist = None
    if pos in coach.ZU_NULL_PRAEMIE and player_actual.get("team_conceded") is not None:
        z_ist = coach.ZU_NULL_PRAEMIE[pos] if player_actual["team_conceded"] == 0 else 0.0
    ist = {"einsatzfaktor": m_ist, "punkte": player_actual["punkte_ist"]}
    if g_ist is not None:
        ist["gegnerfaktor"] = g_ist
    if z_ist is not None:
        ist["zu_null_bonus"] = z_ist
    return ist


def main():
    with open(f"data/predictions/1899_md{MATCHDAY}.json", encoding="utf-8") as f:
        prediction = json.load(f)

    kb = KickbaseAPI()
    assert kb.login(), "Login fehlgeschlagen"
    league_id = kb.get_league_id(LEAGUE_CFG["name"])

    cached = __import__("os").path.exists(player_actuals_path("1899", MATCHDAY))
    print(f"Lade Player-Level-Ist-Werte ({'aus Cache' if cached else 'live, ~121 Requests, dauert'})...")
    actuals = fetch_player_actuals(kb, league_id, "1899", MATCHDAY, prediction)

    liga_avg_win_prob_now = _liga_avg_win_prob_now(LEAGUE_CFG)
    print(f"liga_avg_win_prob (frisch berechnet): {liga_avg_win_prob_now:.3f}\n")

    team_results, all_player_rows = [], []
    for m in prediction["managers"]:
        ist_by_player = {}
        for p in m["lineup"]:
            pa = actuals.get(p["pid"])
            if not pa:
                continue
            punktetyp_idx = p.get("factors", {}).get("punktetyp_idx")
            ist_by_player[p["pid"]] = build_ist(pa, p["pos"], punktetyp_idx, liga_avg_win_prob_now)
        team = retrospective.waterfall_manager(m, ist_by_player)
        team["manager"] = m["name"]
        team_results.append(team)
        for r in team["rows"]:
            if "ist" in r:
                r["manager"] = m["name"]
                all_player_rows.append(r)

    # ---------- Team-Ebene ----------
    print("=" * 70)
    print("TEAM-EBENE (11 Manager)")
    print("=" * 70)
    diffs = []
    for t in sorted(team_results, key=lambda x: -x.get("differenz", 0)):
        if "differenz" not in t:
            print(f"{t['manager']}: keine Ist-Zuordnung möglich")
            continue
        diffs.append(t["differenz"])
        flag = "" if t.get("checksum_ok") else "  ⚠️ PRÜFSUMME VERLETZT"
        print(f"{t['manager']:20s} Prognose {t['prognose']:7.1f}  Ist {t['ist']:7.1f}  "
              f"Diff {t['differenz']:+7.1f}{flag}")
        print(f"    Einsatz {t['delta_einsatz']:+6.1f}  Ausgang {t['delta_ausgang']:+6.1f}  "
              f"ZuNull {t['delta_zunull']:+6.1f}  Leistung {t['delta_leistung']:+6.1f}")

    n_over = sum(1 for d in diffs if d < 0)  # Prognose > Ist = Prognose war zu hoch
    print(f"\nManager mit Prognose > Ist (überschätzt): {n_over}/{len(diffs)}")
    print(f"Team-Ebene: Ø Fehler {statistics.mean(diffs):+.1f} P, Median {statistics.median(diffs):+.1f} P")
    if len(diffs) > 1:
        print(f"           Ø Fehler % {statistics.mean(diffs)/statistics.mean(t['prognose'] for t in team_results if 'prognose' in t)*100:+.1f}%")

    # ---------- Spieler-Ebene ----------
    print("\n" + "=" * 70)
    print(f"SPIELER-EBENE ({len(all_player_rows)} Startelfspieler mit Ist-Wert)")
    print("=" * 70)
    p_diffs = [r["ist"] - r["e0"] for r in all_player_rows]
    p_diffs_sorted = sorted(p_diffs)
    n = len(p_diffs_sorted)
    median_p = statistics.median(p_diffs_sorted)
    mean_p = statistics.mean(p_diffs_sorted)
    over_p = sum(1 for d in p_diffs if d < 0)
    print(f"Ø Fehler: {mean_p:+.1f} P · Median: {median_p:+.1f} P")
    print(f"Spieler mit Ist < Prognose (überschätzt): {over_p}/{n} ({over_p/n:.0%})")

    # Quantilverteilung: in welchem Fünftel der eigenen Prognoseverteilung
    # (E0 +/- sigma aus den Faktoren) lag der Ist-Wert? Grobe Prüfung: Anteil
    # innerhalb der gespeicherten 80%-Bandbreite (aus dem Prognose-Snapshot,
    # falls vorhanden) wird separat unten ausgewiesen.
    quintile_counts = [0] * 5
    for r in all_player_rows:
        rank = sorted(p_diffs).index(r["ist"] - r["e0"])
        quintile_counts[min(4, rank * 5 // n)] += 1
    print(f"Verteilung über 5 Fünftel (sollte ~gleichmäßig sein, je ~{n/5:.0f}): {quintile_counts}")

    biggest = sorted(all_player_rows, key=lambda r: r["ist"] - r["e0"])
    print("\nGrößte Unterschätzungen:")
    for r in biggest[-5:][::-1]:
        print(f"  {r['name']} ({r['manager']}): Prognose {r['e0']:.1f} -> Ist {r['ist']:.1f} ({r['ist']-r['e0']:+.1f})")
    print("Größte Überschätzungen:")
    for r in biggest[:5]:
        print(f"  {r['name']} ({r['manager']}): Prognose {r['e0']:.1f} -> Ist {r['ist']:.1f} ({r['ist']-r['e0']:+.1f})")

    checksum_fails = [t["manager"] for t in team_results if "differenz" in t and not t.get("checksum_ok")]
    print(f"\nPrüfsumme verletzt bei: {checksum_fails or 'keinem Manager - Mechanik korrekt'}")

    # ---------- 3.2: ap-Definition ----------
    print("\n" + "=" * 70)
    print("3.2: ap-DEFINITION (Stichprobe)")
    print("=" * 70)
    dnp_with_ap = [(pid, a) for pid, a in actuals.items() if not a["played"] and a.get("ap_now")]
    print(f"Spieler ohne Einsatz an Spieltag 1, aber mit ap>0 (aus VORSAISON, "
          f"noch nicht überschrieben): {len(dnp_with_ap)}")
    played_ap = [(pid, a) for pid, a in actuals.items() if a["played"]]
    matches_p = sum(1 for pid, a in played_ap if a.get("ap_now") == a["punkte_ist"])
    print(f"Gespielte Spieler, deren ap JETZT exakt dem Spieltag-1-Ergebnis entspricht: "
          f"{matches_p}/{len(played_ap)} (erwartbar, da bisher nur 1 Spieltag Datenbasis -"
          f" ap ist also der Schnitt ÜBER EINSÄTZE, wird erst ab Spieltag 2 unterscheidbar,"
          f" wenn ein Spieler einen Spieltag aussetzt)")

    with open("data/retrospective_md1.json", "w", encoding="utf-8") as f:
        json.dump({
            "matchday": MATCHDAY, "team_results": team_results,
            "n_managers_overestimated": n_over, "n_managers_total": len(diffs),
            "team_mean_error": statistics.mean(diffs), "team_median_error": statistics.median(diffs),
            "player_mean_error": mean_p, "player_median_error": median_p,
            "player_over_pct": over_p / n, "liga_avg_win_prob_used": liga_avg_win_prob_now,
        }, f, ensure_ascii=False, indent=2)
    print("\n💾 data/retrospective_md1.json geschrieben")


if __name__ == "__main__":
    main()
