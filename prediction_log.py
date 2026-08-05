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

import glob
import json
import os
import re

PRED_DIR = os.path.join("data", "predictions")
ACTUALS_DIR = os.path.join("data", "actuals")
WEIGHTS_DIR = os.path.join("data", "weights")


def active_weights_version():
    """
    SPEC_lernzyklus.md 1.1: "ohne Versionierung lässt sich später nicht
    unterscheiden, ob eine Verbesserung von der Anpassung kam oder vom
    Zufall" - jede Prognosedatei referenziert die zum Zeitpunkt aktive
    Gewichtsversion (höchste `data/weights/v<N>.json`). `coach.py` lädt
    diese Werte noch NICHT dynamisch (v1 ist 1:1 identisch zu den
    hartkodierten Modulkonstanten, s. `data/weights/v1.json`) - das lohnt
    sich erst, sobald Stufe 2 (Regression ab Spieltag 5) eine echte v2
    liefert. Liefert 0, wenn keine Datei existiert (kein Absturz).
    """
    versions = []
    for path in glob.glob(os.path.join(WEIGHTS_DIR, "v*.json")):
        m = re.match(r"v(\d+)\.json$", os.path.basename(path))
        if m:
            versions.append(int(m.group(1)))
    return max(versions) if versions else 0


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
        "weights_version": active_weights_version(),
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


def _biggest_factor_change(prev_lineup, curr_xi, total_delta):
    """
    SPEC_lernzyklus.md 5.3: sucht je Manager die Ursache mit dem größten
    Anteil am Prognose-Delta - liefert einen Klartext-Grund ("Sieg-WK-Faktor
    0.44 → 0.52 bei Robin04 (+5.2 P)") statt nur die Differenz zu zeigen.

    **Arbeitet auf PUNKT-Deltas je Spieler, nicht auf rohen Faktorwerten**
    (Bugfix, live beim ersten echten Testlauf gefunden): die erste Fassung
    suchte die größte FAKTOR-Wert-Änderung unabhängig von ihrem tatsächlichen
    Punkte-Einfluss - live beobachtet, PaulBowas Team fiel um 40 Punkte,
    gemeldete "Ursache" war eine Faktoränderung, die den Punktwert des
    betroffenen Spielers nur um ~2 veränderte. Jetzt: pro Spieler der echte
    Punkte-Delta (`expected_points` neu − alt), dominiert kein einzelner
    Spieler mindestens die Hälfte des Team-Deltas, wird das EHRLICH als
    "verteilt, keine dominante Einzelursache" gemeldet statt eine
    irreführend kleine Teilursache als DIE Erklärung auszugeben.
    """
    prev_by_pid = {p["pid"]: p for p in prev_lineup}
    curr_by_id = {p["id"]: p for p in curr_xi}
    left = [prev_by_pid[pid]["name"] for pid in prev_by_pid if pid not in curr_by_id]
    joined = [p["name"] for p in curr_xi if p["id"] not in prev_by_pid]
    if left or joined:
        parts = [f"{n} neu in der Startelf" for n in joined] + \
                [f"{n} nicht mehr in der Startelf" for n in left]
        return parts[0] if len(parts) == 1 else f"{len(parts)} Startelf-Wechsel ({parts[0]}, ...)"

    labels = {"gegnerfaktor": "Sieg-WK-Faktor", "einsatzfaktor": "Statusfarbe",
             "basis": "Punktebasis", "formfaktor": "Form"}
    player_deltas = []
    for p in curr_xi:
        prev = prev_by_pid.get(p["id"])
        if not prev:
            continue
        pts_delta = p["expected_points"] - prev.get("expected", p["expected_points"])
        if abs(pts_delta) < 0.5:
            continue
        pf, cf = prev.get("factors", {}), p.get("ep_factors", {})
        biggest = None
        for key, label in labels.items():
            pv, cv = pf.get(key), cf.get(key)
            if pv is None or cv is None or pv == cv:
                continue
            d = abs(cv - pv)
            if biggest is None or d > biggest[0]:
                biggest = (d, f"{label} {pv} → {cv}")
        player_deltas.append((pts_delta, p["name"], biggest[1] if biggest else "Faktor unklar"))

    if not player_deltas:
        return None
    player_deltas.sort(key=lambda x: -abs(x[0]))
    top_pts, top_name, top_factor = player_deltas[0]
    if total_delta and abs(top_pts) >= 0.5 * abs(total_delta):
        return f"{top_factor} bei {top_name} ({top_pts:+.1f} P)"
    return f"verteilt auf {len(player_deltas)} Spieler, keine dominante Einzelursache"


def diff_predictions(previous, current_league_teams, threshold=5):
    """
    SPEC_lernzyklus.md 5.3 "Änderungsnachweis statt Vertrauen": vergleicht
    die aktuellen Manager-Prognosen gegen die zuletzt gespeicherten (VOR dem
    Überschreiben durch `save_matchday_prediction()` geladen, s. main.py).
    Änderungen unter `threshold` Punkten gelten als Rauschen und werden
    nicht einzeln aufgeführt. Für jede nennenswerte Änderung wird versucht,
    die dominante Ursache zu benennen (`_biggest_factor_change`) - bleibt
    keine dominante Ursache auffindbar, wird das EXPLIZIT gemeldet statt die
    Instabilität zu verstecken. None ohne Vorlauf (erster Lauf des Tages/
    der Saison, kein Fehler).
    """
    if not previous:
        return None
    prev_by_uid = {str(m["uid"]): m for m in previous.get("managers", [])}
    changes, unexplained = [], []
    for m in current_league_teams:
        prev = prev_by_uid.get(str(m["uid"]))
        if not prev:
            continue
        delta = m["prognose"] - prev["predicted"]
        if abs(delta) < threshold:
            continue
        cause = _biggest_factor_change(prev.get("lineup", []), m["xi"], delta)
        changes.append({"name": m["name"], "from": prev["predicted"],
                        "to": m["prognose"], "delta": round(delta, 1), "cause": cause})
        if not cause or "keine dominante" in cause:
            unexplained.append(m["name"])
    if not changes:
        return {"changes": [], "unexplained": []}
    return {"changes": changes, "unexplained": unexplained}


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
    rows, anomalies = detect_anomalies(rows)
    return {
        "matchday": matchday, "rows": rows,
        "mean_error_pct": round(sum(errors) / len(errors) * 100, 1) if errors else None,
        "in_corridor": in_corridor, "n": len(rows),
        "anomalies": anomalies,
    }


def detect_anomalies(rows):
    """
    SPEC_lernzyklus.md Abschnitt 3, Stufe A (statistisch, keine KI nötig):
    eine Beobachtung gilt als Ausreißer, wenn ihr Fehler mehr als das
    DREIFACHE des typischen Fehlers beträgt - robust über den Median
    (nicht das Mittel, das selbst von Ausreißern verzerrt würde). Markiert
    NUR (`row["anomaly"] = True`), löscht nichts - Stufe B (KI-Klassifikation
    einmalig/systematisch/unklar, s. SPEC Abschnitt 3) ist bewusst NICHT
    hier gebaut: sie bräuchte reale, klassifizierbare Abweichungsfälle, die
    es vor dem ersten echten Spieltag naturgemäß nicht gibt. **Scope-
    Hinweis**: arbeitet auf der Manager-Ebene (s. Modul-Docstring) - eine
    Spieler-Ebene-Erkennung (wie im Spec-Beispiel "pid 4034") bräuchte die
    hier bewusst nicht gebaute Player-Level-Ist-Erfassung.
    """
    errors = sorted(abs(r["diff"]) for r in rows)
    n = len(errors)
    if n < 3:
        for r in rows:
            r["anomaly"] = False
        return rows, []
    median_error = errors[n // 2] if n % 2 else (errors[n // 2 - 1] + errors[n // 2]) / 2
    threshold = 3 * median_error if median_error else None
    anomalies = []
    for r in rows:
        r["anomaly"] = bool(threshold and abs(r["diff"]) > threshold)
        if r["anomaly"]:
            anomalies.append(r)
    return rows, anomalies
