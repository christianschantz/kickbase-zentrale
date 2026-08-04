"""
Backtest für mv_forecast.py (SPEC_forecast_coach_scoring.md Abschnitt 1.6):
prüft das Prognosemodell rückwirkend gegen die vorhandene bis zu 92-Tage-MW-
Historie, statt auf neue Daten zu warten. KEIN Teil der täglichen Pipeline -
einmalig/gelegentlich von Hand laufen lassen (`python backtest_mv_forecast.py`),
um die Konstanten in mv_forecast.py zu prüfen/nachzukalibrieren, bevor das
Modell produktiv Gebote beeinflusst.

Für jeden Spieler und jeden Zeitpunkt t (mit genug Vorlauf-Historie): Prognose
für t+1 und t+4 NUR aus Daten bis t, dann Vergleich mit dem tatsächlichen
Wert. Metriken: mittlerer absoluter prozentualer Fehler (MAPE) und
Korridor-Trefferquote (wie oft lag der echte Wert zwischen pessimistischem
und optimistischem Szenario?), je Regime und Horizont aufgeschlüsselt.

Datenbasis: Kader + Tagesmarkt beider Ligen (nicht die volle ~450-Spieler-
Population - ein Backtest-Lauf ist kein täglicher Vorgang, aber 92-Tage-
Historie für 450 Spieler wäre trotzdem unnötig teuer für einen Kalibrierungs-
Check).
"""

import sys
import time

from config import LEAGUES
from kickbase_api import KickbaseAPI
from mv_forecast import clean_mv_series, forecast, detect_regime, INITIALISIERUNG

HORIZONS = (1, 4)


def backtest_player(mvs):
    """Liste von Ergebnis-Dicts je getestetem (Zeitpunkt, Horizont)."""
    results = []
    n = len(mvs)
    max_h = max(HORIZONS)
    for t in range(5, n - max_h):
        history_up_to_t = mvs[:t + 1]
        regime, _, _ = detect_regime(history_up_to_t)
        if regime == INITIALISIERUNG:
            continue
        f = forecast(history_up_to_t, horizon_days=max_h)
        for h in HORIZONS:
            proj = f["projections"].get(h)
            if not proj:
                continue
            actual = mvs[t + h]
            if not actual:
                continue
            err_pct = abs(proj["basis"] - actual) / actual
            hit = proj["pessimistisch"] <= actual <= proj["optimistisch"]
            results.append({"regime": regime, "horizon": h,
                            "abs_pct_error": err_pct, "corridor_hit": hit})
    return results


def summarize(results):
    if not results:
        print("Keine auswertbaren Prognose/Ist-Paare gefunden.")
        return
    by_key = {}
    for r in results:
        by_key.setdefault((r["regime"], r["horizon"]), []).append(r)

    print(f"{'Regime':<16}{'Horizont':<12}{'n':>6}{'MAPE':>10}{'Korridor-Trefferquote':>24}")
    for (regime, h), rs in sorted(by_key.items()):
        errs = [r["abs_pct_error"] for r in rs]
        mape = sum(errs) / len(errs)
        hit_rate = sum(1 for r in rs if r["corridor_hit"]) / len(rs)
        print(f"{regime:<16}{str(h) + ' Tag(e)':<12}{len(rs):>6}{mape:>9.1%}{hit_rate:>23.0%}")


def main():
    kb = KickbaseAPI()
    print("Verbinde mit Kickbase...")
    if not kb.login():
        print("❌ Login fehlgeschlagen.")
        sys.exit(1)

    all_results = []
    for cfg in LEAGUES:
        if "DEIN-" in cfg["name"]:
            continue
        league_id = kb.get_league_id(cfg["name"])
        if not league_id:
            continue
        me = kb.get_me(league_id)
        cid = int(me.get("cpi") or cfg["competition_id"])

        squad = kb.get_squad(league_id).get("it", [])
        market = kb.get_transfer_market(league_id)
        players = {p["i"]: p for p in squad + market}
        print(f"\n{cfg['name']}: {len(players)} Spieler (Kader+Tagesmarkt) im Backtest")

        league_results = []
        for pid in players:
            hist_raw = kb.get_mv_history(cid, pid, league_id)
            time.sleep(0.25)
            mvs = clean_mv_series(hist_raw)
            if len(mvs) < 10:
                continue
            league_results.extend(backtest_player(mvs))
        print(f"   {len(league_results)} Prognose/Ist-Vergleiche")
        all_results.extend(league_results)

    print(f"\n=== Gesamtergebnis über {len(all_results)} Prognose/Ist-Vergleiche ===")
    summarize(all_results)
    print("\nHinweis: MAPE = mittlerer absoluter prozentualer Fehler des Basis-"
         "Szenarios. Korridor-Trefferquote = Anteil, bei dem der echte Wert "
         "zwischen pessimistischem und optimistischem Szenario lag.")


if __name__ == "__main__":
    main()
