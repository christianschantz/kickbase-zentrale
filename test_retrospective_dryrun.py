"""
Trockenlauf der Wasserfall-Zerlegung (SPEC_punkteformel_final.md 4.1):
"Snapshot schreiben, eine Ist-Datei mit ausgedachten Werten erzeugen,
Retrospektive laufen lassen. Kommt die Zerlegung heraus und summieren sich
die Einzelbeiträge auf die Gesamtdifferenz, funktioniert die Mechanik."

Nutzt die ECHTE, bereits gespeicherte Prognosedatei (data/predictions/
1899_md1.json, Basis+Faktoren real) und erfindet dazu Ist-Werte - das prüft
den Rechenmechanismus (retrospective.py), nicht ob echte Ist-Daten verfügbar
sind (sind sie noch nicht, s. retrospective.py-Docstring). Kein Netzwerk-
zugriff, kein KI-Kontingent - direkt ausführbar: `python test_retrospective_dryrun.py`.
"""
import json
import os
import random

from retrospective import waterfall_manager, format_manager_report

PRED_PATH = os.path.join("data", "predictions", "1899_md1.json")


def _invent_ist(lineup):
    """Erfindet plausible, aber absichtlich VOM PROGNOSEWERT ABWEICHENDE
    Ist-Werte je Spieler - deckt alle drei Kaskadenschritte ab (manche
    Spieler nur Einsatz, manche zusätzlich Gegner/Zu-Null geändert), plus
    einen zufälligen "Leistungs"-Ausschlag oben drauf."""
    rng = random.Random(42)  # deterministisch, damit der Trockenlauf reproduzierbar ist
    ist = {}
    for p in lineup:
        f = p.get("factors", {})
        m_ist = max(0.0, f.get("einsatzfaktor", 1.0) + rng.uniform(-0.3, 0.1))
        g_ist = max(0.6, min(1.4, f.get("gegnerfaktor", 1.0) + rng.uniform(-0.2, 0.2)))
        z_ist = max(0.0, f.get("zu_null_bonus", 0.0) + rng.choice([-1, 0, 1]) * rng.uniform(0, 15))
        basis = f.get("basis", 0)
        form = f.get("formfaktor", 1.0)
        verlauf = f.get("spielverlaufsfaktor", 1.0)
        e3 = basis * m_ist * g_ist * form * verlauf + z_ist
        leistungs_ausschlag = rng.uniform(-20, 20)
        punkte = max(0.0, e3 + leistungs_ausschlag)
        ist[p["pid"]] = {"einsatzfaktor": m_ist, "gegnerfaktor": g_ist,
                         "zu_null_bonus": z_ist, "punkte": punkte}
    return ist


def main():
    if not os.path.exists(PRED_PATH):
        print(f"❌ {PRED_PATH} nicht gefunden - erst main.py laufen lassen, "
              "das schreibt die Prognosedatei.")
        return False

    with open(PRED_PATH, encoding="utf-8") as f:
        data = json.load(f)

    managers = data.get("managers", [])
    if not managers:
        print("❌ Keine Manager in der Prognosedatei - Trockenlauf nicht möglich.")
        return False

    print(f"Trockenlauf gegen {PRED_PATH} ({len(managers)} Manager, "
          f"Spieltag {data.get('matchday')}, Gewichtsversion {data.get('weights_version')})\n")

    all_ok = True
    for m in managers:
        lineup = m.get("lineup", [])
        if not lineup:
            continue
        ist_by_player = _invent_ist(lineup)
        team = waterfall_manager(m, ist_by_player)
        ok = team.get("checksum_ok", False)
        all_ok = all_ok and ok
        status = "✅ Prüfsumme OK" if ok else "❌ PRÜFSUMME VERLETZT"
        print(f"--- {m['name']} · {status} ---")
        if m is managers[0]:
            # volle Klartext-Ausgabe nur für den ersten Manager (Beispiel,
            # s. SPEC-Format) - für die übrigen reicht der Prüfsummen-Status.
            print(format_manager_report(m["name"], team))
            print()

    print(f"\n{'✅ Alle Prüfsummen stimmen - Mechanik korrekt.' if all_ok else '❌ Mindestens eine Prüfsumme verletzt - Fehler in retrospective.py.'}")
    return all_ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
