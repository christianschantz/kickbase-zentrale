"""
B5: Liga-weite Bestenliste / Transferziel-Board - Zwei-Listen-Ranking.

Datenbeschaffung (verifiziert, ~38 Requests pro Liga und Lauf):
1. GET /competitions/{cid}/table -> alle 18 Team-IDs
2. Je Team: GET /competitions/{cid}/teams/{tid}/teamprofile -> kompletter
   Kader mit sdmvt (7-Tage-MW-Differenz, verifiziert exakt gegen die volle
   MW-Historie), ap, st, prob, pos, mv (18 Requests)
3. GET /competitions/{cid}/players/search, paginiert -> Besitzstatus über
   `onm` (zu 100% befüllt, ~19 Requests)

Merge über die Spieler-ID (teamprofile 'i' == search 'pi'). EIGEN wird NICHT
über Namensvergleich erkannt (der eigene Anzeigename steht nirgends direkt
in /me, und `onm` hat eine Whitespace-Tücke - kommt mit angehängtem
Leerzeichen zurück), sondern robust über die ID-Menge des eigenen Kaders
(kb.get_squad()) - eindeutig und ohne Zusatzaufwand.

SCORING (2026-07-31 grundlegend überarbeitet, Anlass: Pedri (Ø 158 P, 50 Mio)
fehlte in den La-Liga-Top-10, während Campos (Ø 100 P, 1 Mio) auf Platz 1
stand - die alten absoluten Skalen (Preis-Leistung/Form/mv_implied_form)
waren auf die 2. Liga kalibriert und für teure Spieler strukturell
unerreichbar). Jetzt: ALLES ligarelativ über Perzentile der kompletten
Competition-Population (scoring.percentile_rank), Preis-Rechtfertigung über
ein Residuum aus einer pro Liga gefitteten Log-Preiskurve
(scoring.fit_price_curve/value_residual) statt einem linearen Punkte/Mio-
Verhältnis. ZWEI getrennte Ranglisten lösen den Zielkonflikt "objektiv
bester Spieler" vs. "bester Deal" auf, statt ihn in einer Zahl zu verwischen:

- Liste A "Beste Spieler der Liga" (Qualität, preisunabhängig): Form,
  Verfügbarkeit, Teamstärke, Spielplan. Kein Preis, kein Momentum.
- Liste B "Beste Deals" (Preis-Leistung/Trading): Preis-Residuum, Momentum,
  Verfügbarkeit, Spielplan. Kein Formanteil.

Spieler, die in beiden Top-N-Listen einer Position stehen, werden markiert
(`in_both`) - das sind die wirklich interessanten Ziele. "Liga-Bangers" =
Top 5 in BEIDEN Listen derselben Position.

Punktetyp/Fitness-Text (bräuchten get_player_details je Spieler) werden hier
bewusst NICHT für alle ~449 Spieler gezogen - nur die Kaderplätze und der
Tagesmarkt bekommen die volle Tiefenanalyse (main.py, bestehender Flow).
Diese Rescale-Fixes fassen NUR die B5-Bestenliste an, nicht das Kader-/
Tagesmarkt-Scoring (`scoring.score_player`, an dessen Skala die STAMM/
HALTEN/BEOBACHTEN-Schwellen über viele User-Feedback-Runden kalibriert
wurden) - der dortige Star-Power-Patch in main.py bleibt deshalb bestehen.
"""

import time

from scoring import (STATUS_PENALTY, PROB_SCORE, percentile_rank,
                     fit_price_curve, value_residual, form_raw,
                     price_curve_diagnostics)
from bid_advisor import recommend_bid, dynamic_aggressiveness
from coach import fair_value

POS_NAMES = {1: "TW", 2: "ABW", 3: "MF", 4: "ANG"}

# Saisonvorbereitung 2026/27 (Stand 2026-07-31) - Gewicht der aktuellen Form
# in form_raw() ist damit 0, `ph` wird nicht gebraucht. TODO: vor Saisonstart
# (~7./15.8.) durch echte Spieltagszahl ersetzen, s. scoring.form_raw-Docstring.
CURRENT_SEASON_DAYS = 0

# Für die Preiskurve (Spec 3.3): ap=0-Spieler nur im teuren Segment per
# get_player_details auf echte Historie prüfen - live gemessen (2026-07-31):
# 28-46% aller Ligaspieler haben ap=0, das für ALLE zu prüfen wäre zu teuer
# (~450 Zusatz-Calls/Liga). Im oberen Preisdrittel sind es nur 30-55 Spieler -
# genau dort, wo die Kurve laut Diagnose verzerrt war.
ZERO_AP_CHECK_PERCENTILE = 0.66


def fetch_league_universe(kb, cid, league_id, sleep=0.15):
    """Lädt Tabelle + alle Teamprofile + volle Spieler-Suche für eine Competition."""
    table = kb.get_competition_table(cid)
    team_ids = [t["tid"] for t in table.get("it", [])]

    profiles = {}  # Spieler-ID -> Teamprofile-Eintrag + Teamname
    for tid in team_ids:
        tp = kb.get_team_profile(cid, tid, league_id)
        time.sleep(sleep)
        team_name = tp.get("tn", "")
        for pl in tp.get("it", []):
            profiles[str(pl["i"])] = {**pl, "tn": team_name}

    search_items = kb.search_all_players(cid, league_id)
    search_by_id = {str(s["pi"]): s for s in search_items}

    return profiles, search_by_id


def resolve_ownership(pid, search_by_id, own_ids):
    """EIGEN/MITSPIELER/MARKT/FREI - s. Modul-Docstring für die Herleitung."""
    if pid in own_ids:
        return "EIGEN", None
    s = search_by_id.get(pid)
    if not s:
        return "UNBEKANNT", None  # in search fehlend (Teamprofile-Diskrepanz)
    onm = (s.get("onm") or "").strip()
    if onm == "Kickbase":
        return ("MARKT", None) if s.get("iotm") else ("FREI", None)
    return "MITSPIELER", onm


def _resolve_zero_ap_history(kb, league_id, profiles, sleep=0.2):
    """
    Für ap=0-Spieler im teuren Preissegment (>= ZERO_AP_CHECK_PERCENTILE):
    get_player_details prüfen, ob `ph` echte Einsätze zeigt (hp=true) ->
    dann ECHTE Nullleistung (gehört in den Preiskurven-Fit, s. scoring.
    fit_price_curve-Docstring). Ohne jeden Eintrag -> Ligawechsler ohne
    Historie, bleibt unbewertet (Residuum None, nicht "positiv").
    """
    mvs = sorted((p.get("mv") or 0) for p in profiles.values())
    if not mvs:
        return set()
    threshold = mvs[int(len(mvs) * ZERO_AP_CHECK_PERCENTILE)]
    candidates = [pid for pid, e in profiles.items()
                 if (e.get("ap") or 0) == 0 and (e.get("mv") or 0) >= threshold]
    confirmed = set()
    for pid in candidates:
        d = kb.get_player_details(league_id, pid)
        time.sleep(sleep)
        if any(entry.get("hp") for entry in (d.get("ph") or [])):
            confirmed.add(pid)
    return confirmed


def _team_score_for(team_name, strength_map, matcher, fixture_mode):
    if fixture_mode == "odds":
        matched = matcher(team_name, list(strength_map.keys()))
        return strength_map.get(matched, 0.5)
    from fixtures import team_strength_for
    return team_strength_for(team_name, strength_map)


def build_league_lists(kb, cid, league_id, own_ids, strength_map, upcoming,
                       fixture_mode, matcher, weights_quality=None,
                       weights_value=None, top_n=10, league_overpay=None):
    """
    {"quality": {pos: [...]}, "value": {pos: [...]}, "bangers": [...]} -
    zwei komplett unabhängige Ranglisten je Position über die GESAMTE
    Competition (s. Modul-Docstring), plus Schnittmenge (Top 5 in beiden).
    """
    from fixtures import fixture_ease_for_team
    from odds import fixture_ease_odds

    wq = weights_quality or {"form": 0.40, "availability": 0.25, "team": 0.20, "fixtures": 0.15}
    wv = weights_value or {"value": 0.45, "momentum": 0.30, "availability": 0.15, "fixtures": 0.10}

    profiles, search_by_id = fetch_league_universe(kb, cid, league_id)

    # ---- Preiskurve: ap=0 im teuren Segment auf echte Historie prüfen ----
    # (Spec 3.3 - reine Ligawechsler dürfen NICHT in den Fit, bestätigte
    # Nullleistungen MÜSSEN rein, sonst bleibt die Kurve am teuren Ende
    # verzerrt, s. scoring.fit_price_curve-Docstring).
    confirmed_zero_ap = _resolve_zero_ap_history(kb, league_id, profiles)

    fit_players = []
    for pid, e in profiles.items():
        ap, mv = e.get("ap", 0) or 0, e.get("mv", 0) or 0
        if mv <= 0:
            continue
        if ap > 0:
            fit_players.append({"mv": mv, "ap": ap})
        elif pid in confirmed_zero_ap:
            fit_players.append({"mv": mv, "ap": 0})
    curve = fit_price_curve(fit_players)

    # ---- Perzentil-Grundlagen einmal pro Liga ----
    residuals, expected_pts, form_raws, momentum_raws = {}, {}, {}, {}
    for pid, e in profiles.items():
        ap_raw, mv = e.get("ap", 0) or 0, e.get("mv", 0) or 0
        # ap=None (statt 0) für nicht bewertbare Ligawechsler -> value_residual
        # liefert bewusst None ("unbekannt"), nicht "positiv" (Spec 3.3).
        scoreable_ap = ap_raw if (ap_raw > 0 or pid in confirmed_zero_ap) else None
        residual, expected = value_residual(mv, scoreable_ap, curve)
        if residual is not None:
            residuals[pid] = residual
            expected_pts[pid] = expected
        form_raws[pid] = form_raw(ap_raw, None, CURRENT_SEASON_DAYS)
        sdmvt = e.get("sdmvt") or 0
        momentum_raws[pid] = (sdmvt / 7) / mv if mv > 0 else 0.0

    sorted_residuals = sorted(residuals.values())
    sorted_form = sorted(form_raws.values())
    sorted_momentum = sorted(momentum_raws.values())
    price_diag = price_curve_diagnostics(curve, list(residuals.values()))

    entries = {}
    for pid, e in profiles.items():
        pos = POS_NAMES.get(e.get("pos"))
        if pos is None:
            continue
        team_name = e.get("tn", "")
        if fixture_mode == "odds":
            ease, opponents = fixture_ease_odds(team_name, upcoming, matcher)
        else:
            ease, opponents = fixture_ease_for_team(team_name, upcoming, strength_map)
        team_score = _team_score_for(team_name, strength_map, matcher, fixture_mode)

        st, prob = e.get("st", 0), e.get("prob", 3)
        availability = STATUS_PENALTY.get(st, 0.5) * PROB_SCORE.get(prob, 0.5)

        ap, mv = e.get("ap", 0) or 0, e.get("mv", 0) or 0
        value_score = percentile_rank(residuals[pid], sorted_residuals) if pid in residuals else 0.5
        form_score = percentile_rank(form_raws[pid], sorted_form)
        momentum_score = percentile_rank(momentum_raws[pid], sorted_momentum)

        quality_total = (wq["form"] * form_score + wq["availability"] * availability
                         + wq["team"] * team_score + wq["fixtures"] * ease)
        value_total = (wv["value"] * value_score + wv["momentum"] * momentum_score
                       + wv["availability"] * availability + wv["fixtures"] * ease)

        status, owner = resolve_ownership(pid, search_by_id, own_ids)

        # Fair Value (Punkt 1.2) für alle Spieler - dieselbe Liga-Preiskurve
        # wie überall in diesem Modul, kein Zusatz-Call nötig. `ph` liegt für
        # die volle 449-Population nicht vor (Kostengrund) - fair_value()
        # fällt dann auf die MW-Schätzung zurück wie schon expected_points().
        fv_mv, _ = fair_value(pos, mv, ap, None, st, prob, ease, team_score, curve)

        bid = None
        if status == "MARKT":
            tfhmvt_proxy = (e.get("sdmvt") or 0) / 7
            aggr = dynamic_aggressiveness(round(value_total * 100, 1))
            bid = recommend_bid(mv, tfhmvt_proxy, aggressiveness=aggr,
                                league_overpay=league_overpay,
                                sporting_core=quality_total,
                                fair_value_mv=fv_mv)

        residual = residuals.get(pid)
        expected_ap = expected_pts.get(pid)
        # Absolute Punktedifferenz zusätzlich zum relativen Residuum (Spec 3.4
        # scort relativ, aber die absolute Zahl bleibt für den Report intuitiv
        # lesbar: "+44 P (+39%) ggü. Preiserwartung").
        abs_diff = (ap - expected_ap) if expected_ap is not None else None

        entries[pid] = {
            "id": pid, "name": e.get("n", "?"), "pos": pos, "team": team_name,
            "mv": mv, "ap": ap, "sdmvt": e.get("sdmvt"), "st": st, "prob": prob,
            "quality_score": round(quality_total * 100, 1),
            "value_score": round(value_total * 100, 1),
            "residual_pct": round(residual * 100, 1) if residual is not None else None,
            "residual_abs": round(abs_diff, 1) if abs_diff is not None else None,
            "expected_ap": round(expected_ap, 1) if expected_ap is not None else None,
            "has_points": ap > 0 or pid in confirmed_zero_ap,
            "fair_value": fv_mv,
            "opponents": opponents, "status": status, "owner": owner, "bid": bid,
        }

    quality_lists, value_lists = {}, {}
    for pos in POS_NAMES.values():
        pool = [e for e in entries.values() if e["pos"] == pos]
        quality_lists[pos] = sorted(pool, key=lambda x: -x["quality_score"])[:top_n]
        value_lists[pos] = sorted(pool, key=lambda x: -x["value_score"])[:top_n]
        a_ids = {e["id"] for e in quality_lists[pos]}
        b_ids = {e["id"] for e in value_lists[pos]}
        for e in quality_lists[pos]:
            e["in_both"] = e["id"] in b_ids
        for e in value_lists[pos]:
            e["in_both"] = e["id"] in a_ids

    bangers = []
    for pos in POS_NAMES.values():
        top5_quality = {e["id"]: e for e in quality_lists[pos][:5]}
        top5_value_ids = {e["id"] for e in value_lists[pos][:5]}
        bangers.extend(e for pid, e in top5_quality.items() if pid in top5_value_ids)

    # Stärkste MW-Steiger der Liga (Dashboard-Spec 2026-07-31, Abschnitt 5.2
    # verkleinert 2026-07-31: nur noch Top 5 GESAMT statt je Position - in
    # der Saisonvorbereitung ist die Formkomponente 0-gewichtet, MW-Bewegung
    # die einzige echte Signalquelle, aber 4x10 Karten sind für die
    # Startseite zu viel. Einzeilige Liste statt großer Karten.
    climbers = sorted(entries.values(), key=lambda x: -(x["sdmvt"] or 0))[:5]

    return {"quality": quality_lists, "value": value_lists, "bangers": bangers,
           "climbers": climbers, "price_curve": price_diag}
