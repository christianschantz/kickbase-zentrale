"""
Kader-Analyse mit klarer Verdikt-Hierarchie:

1. STAMM hat Vorrang: Wer sportlich überzeugt (sporting_core hoch, fit,
   Startelf-Kandidat), ist Stamm - egal was der Marktwert gerade macht.
   Der MW-Trend wird nur als Zusatzinfo genannt.
2. HALTEN (Trading) nur, wenn der Fall primär ein Wertpapier ist:
   sportlich (noch) kein Stamm-Argument, aber der MW steigt spürbar.
3. BEOBACHTEN: weder klares Stamm- noch klares Trading-Argument,
   Momentum flacht ab -> Verkaufsfenster im Blick behalten.
4. VERKAUFEN: sportlich schwach UND MW-Motor aus (fällt/stagniert).

Bei unvollständiger Datenlage (Neuzugang ohne Kickbase-Punkte) werden die
Schwellen zugunsten des Spielers ausgelegt - hoher MW = hohe Erwartung.
"""

from scoring import score_player
from bid_advisor import recommend_bid, dynamic_aggressiveness

POS_NAMES = {1: "TW", 2: "ABW", 3: "MF", 4: "ANG"}

STAMM_CORE = 0.55       # sporting_core-Schwelle für Stamm
STAMM_CORE_NODATA = 0.60  # ohne Punktehistorie etwas strenger (MW-Proxy)
STRONG_RISE = 0.008     # >0.8%/Tag
FLATTENING = 0.002


def classify_own_player(p, details, ease, weights=None):
    total, comps, meta = score_player(p, details, ease, weights)
    mv = details.get("mv", p.get("mv", 0)) or 0
    tfhmvt = details.get("tfhmvt", 0) or 0
    daily_pct = meta["daily_pct"]
    core = meta["sporting_core"]
    fit = details.get("st", 0) == 0
    likely_starter = details.get("prob", 3) <= 2

    reasons = []
    core_threshold = STAMM_CORE if meta["data_complete"] else STAMM_CORE_NODATA

    # 1) STAMM zuerst - sportliche Substanz schlägt Momentum
    if core >= core_threshold and fit and likely_starter:
        verdict = "STAMM"
        reasons.append(f"sportlicher Kern {core:.0%} - fitter Startelf-Kandidat")
        if not meta["data_complete"]:
            reasons.append("keine Kickbase-Punktehistorie - Einstufung über MW/Status")
        if daily_pct >= FLATTENING:
            reasons.append(f"MW-Trend zusätzlich positiv ({tfhmvt:+,.0f}/Tag)")
        elif tfhmvt < 0:
            reasons.append(f"MW aktuell rückläufig ({tfhmvt:+,.0f}/Tag) - "
                           "bei Stammspielern kein Verkaufsgrund")
    # 2) Trading-Hold: primär Wertpapier
    elif daily_pct >= STRONG_RISE:
        verdict = "HALTEN (Trading)"
        reasons.append(f"MW steigt {tfhmvt:+,.0f} €/Tag ({daily_pct:+.1%})")
        if not (fit and likely_starter):
            reasons.append("sportlich (noch) kein Stamm-Argument - reines Wert-Investment")
    # 3) Beobachten
    elif daily_pct >= FLATTENING or (core >= 0.45 and fit):
        verdict = "BEOBACHTEN"
        if daily_pct >= FLATTENING:
            reasons.append(f"MW-Anstieg flacht ab ({daily_pct:+.1%}/Tag) - "
                           "Verkaufsfenster im Blick behalten")
        else:
            reasons.append(f"sportlich Mittelfeld (Kern {core:.0%}), MW stagniert - "
                           "Entwicklung abwarten")
    # 4) Verkaufen
    else:
        verdict = "VERKAUFEN"
        if tfhmvt < 0:
            reasons.append(f"MW fällt ({tfhmvt:+,.0f} €/Tag) - Peak überschritten")
        else:
            reasons.append("MW stagniert - bindet nur Kapital")
        if not fit:
            reasons.append(f"Fitness: {details.get('stxt', 'angeschlagen')}")
        if core < 0.4:
            reasons.append(f"sportlich schwach (Kern {core:.0%})")

    return {
        "id": str(p.get("i")),
        "name": f"{p.get('fn', '')} {p.get('n', '')}".strip(),
        "pos": POS_NAMES.get(p.get("pos"), "?"),
        "mv": mv,
        "ap": p.get("ap", 0),
        "score": total,
        "components": comps,
        "meta": meta,
        "tfhmvt": tfhmvt,
        "daily_pct": daily_pct,
        "verdict": verdict,
        "reasons": reasons,
    }


DEBT_RATIO = 0.33  # Kickbase erlaubt bis -33% des Kaderwerts

# Sportlicher Vergleich: ab wann gilt ein Marktspieler als klare Verbesserung
# gegenüber einem Kaderspieler? (sporting_core ist 0..1, ap ist Punkteschnitt)
MIN_CORE_GAP = 0.08
MIN_AP_GAP = 8.0

# Positionsübergreifender Vergleich nur bei wirklich großem Punkte-Mehrwert -
# sonst bringt "stärkerer ANG statt schwächstem TW" nichts (Formation!).
BIG_UPSIDE_CORE = 0.65
BIG_UPSIDE_AP_GAP = 15.0


def _trading_assessment(m, squad_classified):
    """
    Ist der Marktspieler als Investitionsobjekt geeignet? Vergleich AUSSCHLIESSLICH
    gegen die eigenen Trading-Holds (nicht gegen Stamm/Startelf-Kandidaten) -
    konkret gegen den am schwächsten steigenden Trading-Hold im Kader. Gibt es
    keinen Trading-Hold, zählt die Standard-Trading-Schwelle als Referenz.
    """
    trading_holds = [s for s in squad_classified if s["verdict"] == "HALTEN (Trading)"]
    m_daily_pct = (m["tfhmvt"] / m["mv"]) if m["mv"] else 0
    if trading_holds:
        weakest = min(trading_holds, key=lambda s: s["daily_pct"])
        return {"qualifies": m_daily_pct > weakest["daily_pct"],
                "daily_pct": m_daily_pct, "target": weakest}
    return {"qualifies": m_daily_pct >= STRONG_RISE,
            "daily_pct": m_daily_pct, "target": None}


def _sporting_assessment(m, squad_classified):
    """
    Sportlicher Vergleich für einen potenziellen Startelf-Spieler: primär
    gegen den Kader auf DERSELBEN Position (sportlicher Kern + Punkteschnitt),
    positionsübergreifend nur bei großem Punkte-Mehrwert. Stamm- und
    Trading-Hold-Spieler sind nicht ersetzbar, tauchen aber als "kein Zwang"
    auf, wenn der Marktspieler trotzdem stärker wäre.
    """
    same_pos = [s for s in squad_classified if s["pos"] == m["pos"]]
    replaceable = [s for s in same_pos if s["verdict"] in ("VERKAUFEN", "BEOBACHTEN")]
    m_core = m["meta"]["sporting_core"]
    m_ap = m["ap"]

    def is_upgrade(target):
        return (m_core - target["meta"]["sporting_core"] >= MIN_CORE_GAP
                or m_ap - target["ap"] >= MIN_AP_GAP)

    weakest = min(replaceable, key=lambda s: s["meta"]["sporting_core"]) if replaceable else None
    if weakest and is_upgrade(weakest):
        return {"kind": "same_pos_upgrade", "target": weakest}

    weakest_any = min(same_pos, key=lambda s: s["meta"]["sporting_core"]) if same_pos else None
    if weakest_any and is_upgrade(weakest_any):
        return {"kind": "same_pos_blocked", "target": weakest_any}

    if m_core >= BIG_UPSIDE_CORE and squad_classified:
        weakest_overall = min(squad_classified, key=lambda s: s["meta"]["sporting_core"])
        if (m_ap - weakest_overall["ap"] >= BIG_UPSIDE_AP_GAP
                and m_core - weakest_overall["meta"]["sporting_core"] >= MIN_CORE_GAP):
            return {"kind": "cross_pos_upgrade", "target": weakest_overall}

    return {"kind": "none", "target": None}


def _punktetyp_note(m):
    """Punktetyp-Hinweis inkl. Einordnung ggü. dem kommenden Spielplan -
    beantwortet 'lohnt er sich trotz schwacher Sieg-WK am Wochenende?'."""
    text = m.get("punktetyp_text")
    if not text:
        return None
    fix_ease = m["components"].get("fixtures", 0.5)
    if fix_ease < 0.45:
        if m.get("reliable_type"):
            return (text + f" -> auch bei schwachem Spielplan (Spielplan-Score nur "
                    f"{fix_ease:.0%}) ein Kandidat, Punkte hängen nicht am Sieg")
        return (text + f" -> Vorsicht: schwacher Spielplan (nur {fix_ease:.0%}) UND "
                "punktet primär bei eigenen Siegen")
    return text


def market_vs_squad(market_scored, squad_classified, budget, max_squad,
                    league_overpay=None):
    free_slots = max(0, max_squad - len(squad_classified))
    squad_value = sum(s["mv"] for s in squad_classified)
    # Kaufkraft-Regel (korrekt): Basis für die 33% ist der NETTO-Teamwert
    # (Kaderwert + Budget; ein negatives Budget reduziert die Basis).
    # Beispiel: Kader 136M, Budget -20M -> Basis 116M -> max. Schulden 38.3M,
    # davon 20M schon verbraucht -> echte Kaufkraft 18.3M.
    net_value = squad_value + budget
    max_debt = DEBT_RATIO * net_value
    capacity = max_debt + budget  # = 33%*(Kader+Budget) + Budget
    results = []

    def _sell_and_recompute(target, bid):
        proceeds = int(target["mv"] * 0.95)
        b_after = budget + proceeds
        sv_after = squad_value - target["mv"]
        cap_after = DEBT_RATIO * (sv_after + b_after) + b_after
        affordable = cap_after >= bid["recommended_bid"]
        financing = (f"Kaufkraft nach Verkauf {cap_after:,.0f} "
                     f"(Budget {budget:+,.0f} + Erlös ~{proceeds:,.0f}, "
                     f"33% vom neuen Netto-Teamwert)")
        return affordable, financing

    for m in sorted(market_scored, key=lambda x: -x["score"]):
        trading = _trading_assessment(m, squad_classified)
        sporting = _sporting_assessment(m, squad_classified)

        angles = []
        if trading["qualifies"]:
            if trading["target"]:
                t = trading["target"]
                angles.append(f"📈 TRADING: MW-Momentum {trading['daily_pct']:+.1%}/Tag > "
                              f"schwächster Trading-Hold {t['name']} ({t['daily_pct']:+.1%}/Tag)")
            else:
                angles.append(f"📈 TRADING: MW-Momentum {trading['daily_pct']:+.1%}/Tag - "
                              "kein Trading-Hold im Kader zum Vergleich, Standardschwelle übertroffen")

        if sporting["kind"] == "same_pos_upgrade":
            t = sporting["target"]
            angles.append(f"⚽ SPORTLICH: Ø {m['ap']:.0f} vs {t['ap']:.0f} Punkte, "
                          f"sportlicher Kern {m['meta']['sporting_core']:.0%} vs "
                          f"{t['meta']['sporting_core']:.0%} ggü. {t['name']} ({t['pos']}), "
                          f"Team-Stärke {m['team_strength']:.0%}")
        elif sporting["kind"] == "cross_pos_upgrade":
            t = sporting["target"]
            angles.append(f"⚽ SPORTLICH (positionsübergreifend, großer Mehrwert): "
                          f"Ø {m['ap']:.0f} vs {t['ap']:.0f} Punkte ggü. schwächstem "
                          f"Kaderspieler {t['name']} ({t['pos']}), Team-Stärke {m['team_strength']:.0%}")
        elif sporting["kind"] == "same_pos_blocked":
            t = sporting["target"]
            angles.append(f"⚽ stärker als {t['name']} ({t['pos']}), der ist aber "
                          f"{t['verdict']} -> kein sportlicher Zwang")

        note = _punktetyp_note(m)
        if note:
            angles.append(f"📊 {note}")

        aggr = dynamic_aggressiveness(m["score"])
        bid = recommend_bid(m["mv"], m["tfhmvt"], aggressiveness=aggr,
                            league_overpay=league_overpay,
                            sporting_core=m["meta"]["sporting_core"],
                            star=m.get("star", 0))

        if free_slots > 0:
            headline = "KAUFEN (freier Kaderplatz)"
            affordable = capacity >= bid["recommended_bid"]
            financing = (f"Kaufkraft {capacity:,.0f} "
                         f"(33% von Netto-Teamwert {net_value:,.0f} = max. Schulden "
                         f"{max_debt:,.0f}, Budget {budget:+,.0f})")
        elif sporting["kind"] in ("same_pos_upgrade", "cross_pos_upgrade"):
            t = sporting["target"]
            headline = f"UPGRADE für {t['name']} ({t['pos']}, sportlich)"
            affordable, financing = _sell_and_recompute(t, bid)
        elif trading["qualifies"] and trading["target"]:
            t = trading["target"]
            headline = f"TRADING-UPGRADE für {t['name']} ({t['daily_pct']:+.1%}/Tag)"
            affordable, financing = _sell_and_recompute(t, bid)
        elif angles:
            headline = "interessant, aber kein Kaderzwang (Stamm/Trading-Holds nicht ersetzbar)"
            affordable = capacity >= bid["recommended_bid"]
            financing = f"Kaufkraft {capacity:,.0f}"
        else:
            headline = "KEIN BEDARF - weder Trading- noch sportlicher Mehrwert erkennbar"
            affordable = False
            financing = ""

        team_verdict = headline + ((" | " + " | ".join(angles)) if angles else "")

        results.append({**m, "team_verdict": team_verdict, "bid": bid,
                        "aggressiveness": aggr, "affordable": affordable,
                        "financing": financing,
                        "trading_angle": trading, "sporting_angle": sporting})
    return results, free_slots


ACTIONABLE_MARKERS = ("UPGRADE", "KAUFEN")
SKIP_STAR_THRESHOLD = 0.6


def finalize_headline_recommendations(compared):
    """
    Wählt aus den bewerteten Marktkandidaten bis zu zwei mit einer klaren
    Kaufen/Nicht-Kaufen-Entscheidung statt nur Pro/Contra + Gebot (User-
    Feedback: "du wägst sehr gut ab, aber gibst keine finale Handlungs-
    empfehlung"). Alle anderen Kandidaten bleiben unverändert eine Abwägung.

    - Kauf-Empfehlung: höchster Score mit echtem Handlungs-Ansatz (UPGRADE
      oder freier Kaderplatz) UND finanzierbar.
    - Nicht-Kaufen-Empfehlung: der auffälligste Star-Kandidat (Star-Power
      >= 0.6), der GERADE NICHT finanzierbar ist - "sieht gut aus, aber
      aktuell keine Jagd wert".
    Beides ist optional (0, 1 oder 2 Treffer je nach Marktlage).
    """
    buy_pick = None
    for m in sorted(compared, key=lambda x: -x["score"]):
        if m["affordable"] and any(k in m["team_verdict"] for k in ACTIONABLE_MARKERS):
            buy_pick = m
            break

    skip_pick = None
    for m in sorted(compared, key=lambda x: -x.get("star", 0)):
        if m is buy_pick:
            continue
        if m.get("star", 0) >= SKIP_STAR_THRESHOLD and not m["affordable"]:
            skip_pick = m
            break

    if buy_pick:
        buy_pick["team_verdict"] = "✅ KLARE KAUFEMPFEHLUNG — " + buy_pick["team_verdict"]
    if skip_pick:
        skip_pick["team_verdict"] = ("⛔ EHER NICHT (aktuell nicht finanzierbar) — "
                                     + skip_pick["team_verdict"])
    return compared
