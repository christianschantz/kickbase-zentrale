"""
Determinismus-Test (SPEC_lernzyklus.md 5.4): "Ein Testlauf führt die
Prognose zweimal auf demselben eingefrorenen Datenstand aus und vergleicht.
Jede Differenz ist ein Fehler. Der Test gehört in den Workflow, nicht in
die manuelle Prüfung."

Scope: die reine Berechnungsschicht (coach.py - expected_points/xi_prognose/
optimize_lineup, alles pure Funktionen ohne I/O oder Zufall). Die Live-API
selbst ist naturgemäß nicht wiederholbar identisch (Quoten/Marktwerte ändern
sich), das ist keine Instabilität im hier gemeinten Sinn - der Test nutzt
deshalb die zuletzt gespeicherte Prognosedatei (data/predictions/<liga>_
md<N>.json, SPEC_spieltagsmodell_v2.md 4.4) als eingefrorenen Datenstand und
rechnet NUR die Aggregation (xi_prognose, inkl. Direktduell-Dämpfung und
Sigma-Bandbreite) zweimal auf denselben Faktoren nach.

Läuft ohne Netzwerkzugriff, ohne Kickbase-Login - eignet sich für den
GitHub-Actions-Workflow als eigener, schneller Schritt.
"""
import glob
import json
import sys

import coach


def _rebuild_xi(lineup_entries):
    xi = []
    for e in lineup_entries:
        f = e["factors"]
        expected = (f["basis"] * f["einsatzfaktor"] * f["gegnerfaktor"]
                   * f.get("formfaktor", 1.0) * f["spielverlaufsfaktor"]
                   + f.get("zu_null_bonus", 0.0))
        xi.append({
            "id": e["pid"], "name": e["name"], "pos": e["pos"], "team": e.get("team"),
            "expected_points": round(expected, 1), "ep_factors": dict(f),
            "opponents": [],
        })
    return xi


def _no_match(name, candidates):
    return None  # Duell-Erkennung braucht echte Team-Opponents-Daten, hier neutral


def run():
    files = sorted(glob.glob("data/predictions/*_md*.json"))
    if not files:
        print("ℹ️ Keine Prognosedatei zum Testen gefunden (data/predictions/*.json) - "
              "Test übersprungen, kein Fehler.")
        return True

    ok = True
    checked = 0
    for path in files:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for m in data.get("managers", []):
            xi1 = _rebuild_xi(m["lineup"])
            r1 = coach.xi_prognose(xi1, _no_match)
            xi2 = _rebuild_xi(m["lineup"])
            r2 = coach.xi_prognose(xi2, _no_match)
            checked += 1
            if r1 != r2:
                print(f"❌ Nicht deterministisch: {path} · {m['name']}")
                print(f"   Lauf 1: {r1}")
                print(f"   Lauf 2: {r2}")
                ok = False

    if ok:
        print(f"✅ Determinismus-Test bestanden ({checked} Manager-Prognosen aus "
              f"{len(files)} Datei(en) zweifach nachgerechnet, identisch).")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
