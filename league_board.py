"""
B5: Liga-weite Bestenliste / Transferziel-Board.

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

Scoring OHNE Zusatz-Detail-Calls: sdmvt/7 dient als Tages-Proxy für tfhmvt,
der Rest läuft durch das bestehende, transparente scoring.score_player().
Punktetyp/Fitness-Text (bräuchten get_player_details je Spieler) werden hier
bewusst NICHT für alle ~449 Spieler gezogen - nur die Kaderplätze und der
Tagesmarkt bekommen die volle Tiefenanalyse (main.py, bestehender Flow).
"""

import time

from scoring import score_player
from bid_advisor import recommend_bid, dynamic_aggressiveness

POS_NAMES = {1: "TW", 2: "ABW", 3: "MF", 4: "ANG"}


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
        return "UNBEKANNT", None  # in search fehlend (449 vs. 447 Teamprofile-Diskrepanz)
    onm = (s.get("onm") or "").strip()
    if onm == "Kickbase":
        return ("MARKT", None) if s.get("iotm") else ("FREI", None)
    return "MITSPIELER", onm


def _score_from_teamprofile(entry, ease, weights):
    """score_player() ohne Detail-Call: sdmvt/7 als Tages-Proxy für tfhmvt."""
    tfhmvt_proxy = (entry.get("sdmvt") or 0) / 7
    pseudo_market_entry = {"ap": entry.get("ap", 0), "p": entry.get("ap", 0)}
    pseudo_details = {
        "mv": entry.get("mv", 0),
        "tfhmvt": tfhmvt_proxy,
        "st": entry.get("st", 0),
        "prob": entry.get("prob", 3),
    }
    return score_player(pseudo_market_entry, pseudo_details, ease, weights)


def build_league_board(kb, cid, league_id, own_ids, strength_map, upcoming,
                       fixture_mode, matcher, weights=None, top_n=10,
                       league_overpay=None):
    """
    {pos_name: [ranked_entries]} - die stärksten Spieler je Position über die
    GESAMTE Competition (nicht nur Kader+Tagesmarkt), mit Besitzstatus und für
    MARKT-Spieler einer Gebotsempfehlung.
    """
    from fixtures import fixture_ease_for_team
    from odds import fixture_ease_odds

    profiles, search_by_id = fetch_league_universe(kb, cid, league_id)

    ranked = {pos: [] for pos in POS_NAMES.values()}
    for pid, entry in profiles.items():
        pos = POS_NAMES.get(entry.get("pos"))
        if pos is None:
            continue
        team_name = entry.get("tn", "")
        if fixture_mode == "odds":
            ease, opponents = fixture_ease_odds(team_name, upcoming, matcher)
        else:
            ease, opponents = fixture_ease_for_team(team_name, upcoming, strength_map)
        total, comps, meta = _score_from_teamprofile(entry, ease, weights)
        status, owner = resolve_ownership(pid, search_by_id, own_ids)

        bid = None
        if status == "MARKT":
            tfhmvt_proxy = (entry.get("sdmvt") or 0) / 7
            aggr = dynamic_aggressiveness(total)
            bid = recommend_bid(entry.get("mv", 0), tfhmvt_proxy, aggressiveness=aggr,
                                league_overpay=league_overpay,
                                sporting_core=meta["sporting_core"])

        ranked[pos].append({
            "id": pid, "name": entry.get("n", "?"), "pos": pos, "team": team_name,
            "mv": entry.get("mv", 0) or 0, "ap": entry.get("ap", 0) or 0,
            "sdmvt": entry.get("sdmvt"), "st": entry.get("st", 0),
            "prob": entry.get("prob", 3), "score": total, "components": comps,
            "meta": meta, "opponents": opponents, "status": status,
            "owner": owner, "bid": bid,
        })

    for pos in ranked:
        ranked[pos].sort(key=lambda x: -x["score"])
        ranked[pos] = ranked[pos][:top_n]

    return ranked
