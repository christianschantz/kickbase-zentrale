"""
Rückkopplungs-Protokollierung (SPEC_spieltagsmodell_v2.md 4.4) - "der
einzige zeitkritische Punkt dieser Spec": ab dem 07.08.2026 liegen erstmals
echte Ist-Werte vor, was bis dahin nicht geschrieben wird, ist dauerhaft
verloren. Deshalb VOR dem ersten Anpfiff scharfgeschaltet, nicht erst danach.

Drei Dateien, alle im Repo gehalten (KEIN Löschen - die Historie ist die
Datengrundlage für jede künftige Kalibrierung, s. `.github/workflows/
briefing.yml`, das `data/` inkl. dieser neuen Unterordner zurückcommittet):

- `data/predictions/<liga>_md<N>.json` (Datei A): einmal je Spieltag,
  geschrieben bei JEDEM Lauf VOR Anpfiff (überschreibt sich selbst, solange
  noch nicht angepfiffen ist - der letzte Lauf vor Kickoff gewinnt
  automatisch, kein manuelles "ist das der letzte Lauf?"-Tracking nötig).
  Danach eingefroren (kein weiteres Schreiben), damit sie als echte
  Prognose-Momentaufnahme für den späteren Vergleich taugt.
- `data/predictions/bids_<datum>.json` (Datei C): täglich, alle
  Gebotsempfehlungen des Tagesmarkts über beide Ligen in EINER Datei.
- `data/actuals/<liga>_md<N>.json` (Datei B): nach Spieltagsende.
  **Scope dieser ersten Fassung: NUR Manager-Ebene**
  (`ranking.us[].mdp` - Spieltagspunkte je Manager, bereits verifizierter
  Endpoint, KEIN Zusatz-Call). Die im Spec-Beispiel gezeigte Player-Level-
  Abweichungszerlegung ("-48 Einsatz Guedes 14 Min statt 90 erwartet")
  bräuchte je Mitspieler-Kader zusätzliche `ph`-Abrufe - hier bewusst NICHT
  gebaut, weil das vor dem ersten echten Spieltag ohnehin nicht validierbar
  gewesen wäre. Die Manager-Ebene deckt die Pflicht-Akzeptanzkriterien
  (Modellgüte, Korridor-Trefferquote) bereits vollständig ab.
"""

import json
import os

PRED_DIR = os.path.join("data", "predictions")
ACTUALS_DIR = os.path.join("data", "actuals")


def _slug(league_name):
    return league_name.lower().replace(" ", "_").replace("!", "").replace("ü", "ue")


def save_matchday_prediction(league_name, matchday, kickoff_first, league_teams,
                             anchor_expected, generated_at):
    """
    Datei A. Schreibt NUR, solange `generated_at < kickoff_first` - danach
    bleibt die Datei beim letzten Vor-Anpfiff-Stand eingefroren. Ohne
    bekanntes `kickoff_first` (z.B. La Liga, football-data.org liefert bei
    uns kein Datumsfeld) wird nichts geschrieben statt zu raten.
    """
    if kickoff_first is None or generated_at >= kickoff_first or not league_teams:
        return None
    os.makedirs(PRED_DIR, exist_ok=True)
    path = os.path.join(PRED_DIR, f"{_slug(league_name)}_md{matchday}.json")
    managers = []
    for m in league_teams:
        managers.append({
            "uid": str(m["uid"]), "name": m["name"],
            "predicted": m["prognose"], "range": list(m["prognose_range"]),
            "squad_strength": m.get("kaderstaerke"), "efficiency": m.get("effizienz"),
            "formation": m.get("formation"),
            "lineup": [
                {"pid": p["id"], "name": p["name"], "pos": p["pos"], "slot": p.get("lo"),
                 "expected": p["expected_points"], "team": p.get("team"),
                 "factors": p.get("ep_factors", {})}
                for p in m["xi"]
            ],
        })
    data = {
        "league": league_name, "matchday": matchday,
        "kickoff_first": kickoff_first.isoformat(),
        "generated_at": generated_at.isoformat(),
        "anchor_expected": anchor_expected,
        "managers": managers,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def save_daily_bids(league_name, date_str, bid_entries, generated_at):
    """
    Datei C. Eine Datei für ALLE Ligen pro Tag (Einträge tragen 'league').
    Bestehende Einträge derselben Liga+Datum werden ersetzt (idempotent bei
    mehrfachem Lauf am selben Tag), andere Ligen bleiben erhalten.
    """
    os.makedirs(PRED_DIR, exist_ok=True)
    path = os.path.join(PRED_DIR, f"bids_{date_str}.json")
    existing = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                existing = json.load(f).get("bids", [])
        except (json.JSONDecodeError, OSError):
            existing = []
    existing = [e for e in existing if e.get("league") != league_name]
    for e in bid_entries:
        existing.append({**e, "league": league_name})
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": date_str, "generated_at": generated_at.isoformat(),
                   "bids": existing}, f, ensure_ascii=False, indent=2)
    return path


def load_matchday_prediction(league_name, matchday):
    path = os.path.join(PRED_DIR, f"{_slug(league_name)}_md{matchday}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_matchday_actuals(league_name, matchday, ranking, generated_at):
    """
    Datei B, s. Modul-Docstring für den Scope (Manager-Ebene, aus
    `ranking.us[].mdp`). Schreibt nur, wenn der Spieltag laut `mdp>0` für
    mindestens einen Manager begonnen hat, und nur EINMAL (schreibt nicht
    erneut, wenn die Datei schon existiert - Ist-Werte sind endgültig).
    """
    us = ranking.get("us") or []
    if not any((u.get("mdp") or 0) > 0 for u in us):
        return None
    os.makedirs(ACTUALS_DIR, exist_ok=True)
    path = os.path.join(ACTUALS_DIR, f"{_slug(league_name)}_md{matchday}.json")
    if os.path.exists(path):
        return path
    managers = [{"uid": str(u.get("i")), "name": (u.get("n") or "").strip(),
                "actual": u.get("mdp"), "rank": u.get("mdpl")} for u in us]
    data = {"league": league_name, "matchday": matchday,
           "generated_at": generated_at.isoformat(), "managers": managers}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def deviation_report(league_name, matchday):
    """
    SPEC 4.4 Auswertung - vergleicht Datei A (Prognose) gegen Datei B (Ist)
    je Manager, sobald BEIDE vorliegen. Liefert Modellgüte (mittlerer
    Fehler, Korridor-Trefferquote) + je Manager Prognose/Ist/Differenz.
    None, wenn eine der beiden Dateien fehlt (vor Spieltagsende der
    Normalfall, kein Fehler).
    """
    pred = load_matchday_prediction(league_name, matchday)
    actuals_path = os.path.join(ACTUALS_DIR, f"{_slug(league_name)}_md{matchday}.json")
    if not pred or not os.path.exists(actuals_path):
        return None
    with open(actuals_path, encoding="utf-8") as f:
        actuals = json.load(f)
    actual_by_uid = {a["uid"]: a["actual"] for a in actuals.get("managers", [])}
    rows, errors, in_corridor = [], [], 0
    for m in pred.get("managers", []):
        actual = actual_by_uid.get(m["uid"])
        if actual is None:
            continue
        diff = actual - m["predicted"]
        if actual:
            errors.append(abs(diff) / actual)
        lo_hi = m.get("range") or [None, None]
        lo, hi = lo_hi[0], lo_hi[1]
        if lo is not None and hi is not None and lo <= actual <= hi:
            in_corridor += 1
        rows.append({"uid": m["uid"], "name": m["name"], "predicted": m["predicted"],
                    "actual": actual, "diff": round(diff, 1)})
    if not rows:
        return None
    return {
        "matchday": matchday, "rows": rows,
        "mean_error_pct": round(sum(errors) / len(errors) * 100, 1) if errors else None,
        "in_corridor": in_corridor, "n": len(rows),
    }
