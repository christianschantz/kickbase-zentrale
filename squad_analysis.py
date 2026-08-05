"""
Kader-Analyse mit klarer Verdikt-Hierarchie:

1. STAMM hat Vorrang: Wer sportlich überzeugt (sporting_core hoch, fit,
   Startelf-Kandidat), ist Stamm - egal was der Marktwert gerade macht.
   Der MW-Trend wird nur als Zusatzinfo genannt.
2. HALTEN (Trading) nur, wenn der Fall primär ein Wertpapier ist:
   sportlich (noch) kein Stamm-Argument, aber der MW steigt spürbar.
3. BEOBACHTEN: weder klares Stamm- noch klares Trading-Argument,
   Momentum flacht laut Momentum-Ratio deutlich ab (s.u.) -> Verkaufsfenster
   im Blick behalten.
4. VERKAUFEN: sportlich schwach UND MW-Motor aus (fällt/stagniert).

Bei unvollständiger Datenlage (Neuzugang ohne Kickbase-Punkte) werden die
Schwellen zugunsten des Spielers ausgelegt - hoher MW = hohe Erwartung.

A1-Fix (2026-07-31, überarbeitet): "flacht ab" wurde ursprünglich aus einem
einzelnen 24h-Wert (`tfhmvt`) geraten - mathematisch keine Abflachung (zweite
Ableitung), nur ein niedriger Tageswert. Erst über eine eigene 92-Tage-
Historie gelöst, dann durch den Fund von `sdmvt` (7-Tage-MW-Differenz, kommt
kostenlos im Kader-Response mit, kein Zusatz-Call) stark vereinfacht:
`classify_own_player` bekommt optional `sdmvt` und bildet daraus
`scoring.momentum_ratio()` = (tfhmvt×7)/sdmvt. Ratio <0.6 = "flacht deutlich
ab" -> BEOBACHTEN. Ohne sdmvt wird das explizit ausgewiesen statt zu raten.
Einschränkung: Ratio ist eine Momentaufnahme, kein bestätigter Mehrtages-Trend
(dafür bräuchte es Tagessnapshots, noch nicht gebaut, s. CLAUDE.md B4).
"""

from scoring import score_player, momentum_ratio
from bid_advisor import recommend_bid, dynamic_aggressiveness

POS_NAMES = {1: "TW", 2: "ABW", 3: "MF", 4: "ANG"}

STAMM_CORE = 0.55       # sporting_core-Schwelle für Stamm
STAMM_CORE_NODATA = 0.60  # ohne Punktehistorie etwas strenger (MW-Proxy)
STRONG_RISE = 0.008     # >0.8%/Tag
FLATTENING = 0.002
RATIO_FLATTENING = 0.6  # Momentum-Ratio darunter = "flacht deutlich ab"


def classify_own_player(p, details, ease, weights=None, sdmvt=None):
    total, comps, meta = score_player(p, details, ease, weights)
    mv = details.get("mv", p.get("mv", 0)) or 0
    tfhmvt = details.get("tfhmvt", 0) or 0
    daily_pct = meta["daily_pct"]
    core = meta["sporting_core"]
    fit = details.get("st", 0) == 0
    likely_starter = details.get("prob", 3) <= 2

    reasons = []
    core_threshold = STAMM_CORE if meta["data_complete"] else STAMM_CORE_NODATA

    # A1: "flacht ab" nur bei echtem Beleg aus der Momentum-Ratio - ohne
    # sdmvt (ratio=None) NICHT raten.
    ratio = momentum_ratio(tfhmvt, sdmvt)
    truly_flattening = ratio is not None and daily_pct > 0 and ratio < RATIO_FLATTENING

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
    elif truly_flattening or (core >= 0.45 and fit):
        verdict = "BEOBACHTEN"
        if truly_flattening:
            reasons.append(f"MW-Anstieg flacht ab (Momentum-Ratio {ratio:.2f} - 24h-Wert nur "
                           f"noch {ratio:.0%} des 7-Tage-Schnitts) - Verkaufsfenster im Blick behalten "
                           "(Momentaufnahme, kein bestätigter Mehrtages-Trend)")
        else:
            reasons.append(f"sportlich Mittelfeld (Kern {core:.0%}), MW stagniert - "
                           "Entwicklung abwarten")
    # 4) Verkaufen
    else:
        verdict = "VERKAUFEN"
        if ratio is None:
            reasons.append("MW stagniert/fällt (24h-Wert - keine Momentum-Ratio berechenbar, sdmvt fehlt)")
        elif tfhmvt < 0:
            reasons.append(f"MW fällt ({tfhmvt:+,.0f} €/Tag) - Peak überschritten")
        else:
            reasons.append("MW stagniert - bindet nur Kapital")
        if not fit:
            reasons.append(f"Fitness: {details.get('stxt', 'angeschlagen')}")
        if core < 0.4:
            reasons.append(f"sportlich schwach (Kern {core:.0%})")

    if ratio is not None:
        if sdmvt < 0 and ratio > 1:
            reasons.append(f"⚠️ Absturz beschleunigt sich (Momentum-Ratio {ratio:.2f})")
        elif verdict in ("STAMM", "HALTEN (Trading)"):
            reasons.append(f"Momentum-Ratio {ratio:.2f} (24h vs. Ø 7-Tage)")

    if not meta.get("status_known", True):
        reasons.append(f"⚠️ Status-Code unbekannt (st={details.get('st')}) - "
                       "Verfügbarkeit neutral angenommen, nicht verlässlich")

    return {
        "id": str(p.get("i")),
        "name": f"{p.get('fn', '')} {p.get('n', '')}".strip(),
        "pos": POS_NAMES.get(p.get("pos"), "?"),
        "tid": str(details.get("tid", p.get("tid", "")) or ""),
        "team": details.get("tn", ""),
        "mv": mv,
        "ap": p.get("ap", 0),
        "score": total,
        "components": comps,
        "meta": meta,
        "tfhmvt": tfhmvt,
        "daily_pct": daily_pct,
        "verdict": verdict,
        "reasons": reasons,
        "fit": fit,  # für Rückkehrer-/Statuswechsel-Diff (Tagesvergleich)
        "st": details.get("st", 0),
        "sporting_core": core,  # momentum-frei, für die KI-Schicht (llm_insights.py)
        "momentum_ratio": ratio,
    }


# Fair-Value-Verkaufskandidat/Halte-Argument (SPEC_kalibrierung_fairvalue.md
# 4.1) - Erstkalibrierung.
FAIR_VALUE_SELL_THRESHOLD = 0.25
FAIR_VALUE_HOLD_THRESHOLD = -0.25


def apply_fair_value_note(c, fair_value_mv, min_price_player=False):
    """
    SPEC_kalibrierung_fairvalue.md 4.1: Fair Value ERGÄNZT die bestehenden
    Verdikte, ersetzt sie nicht - die Farbregel (STAMM bei blau/grün, s.
    Punkt 1.3) bleibt vorrangig, ein Verkauf wird hier NIE ausgelöst, nur
    eine zusätzliche Reason angehängt. Mindestpreis-Spieler (s. scoring.
    is_min_price_player) werden nie als über-/unterbewertet ausgewiesen -
    ihr Preis ist zensiert (kann nicht billiger sein), nicht marktbestimmt.
    Mutiert `c` (fügt 'fair_value'/'fair_value_sell_flag' hinzu, ggf. eine
    reasons-Zeile) und gibt es zurück.
    """
    c["fair_value"] = fair_value_mv
    c["fair_value_sell_flag"] = False
    if fair_value_mv is None or min_price_player or not c.get("mv") or fair_value_mv <= 0:
        return c
    diff_pct = (c["mv"] - fair_value_mv) / fair_value_mv
    if diff_pct >= FAIR_VALUE_SELL_THRESHOLD:
        c["fair_value_sell_flag"] = True
        if c["verdict"] == "STAMM":
            c["reasons"].append(
                f"💰 Marktwert {diff_pct:+.0%} über sportlichem Wert (Fair Value "
                f"{fair_value_mv:,.0f} €) - bei Stammspielern kein automatischer "
                "Verkaufsgrund, nur Beobachtungshinweis")
        else:
            c["reasons"].append(
                f"💰 Marktwert {diff_pct:+.0%} über sportlichem Wert (Fair Value "
                f"{fair_value_mv:,.0f} €) - Verkaufskandidat")
    elif diff_pct <= FAIR_VALUE_HOLD_THRESHOLD:
        c["reasons"].append(
            f"💰 Marktwert {diff_pct:+.0%} unter sportlichem Wert (Fair Value "
            f"{fair_value_mv:,.0f} €) - Halte-Argument, auch wenn das MW-Momentum abflacht")
    return c


DEBT_RATIO = 0.33  # Kickbase erlaubt bis -33% des Kaderwerts

# A5: Mindestbesetzung je Position über alle 8 Kickbase-Formationen
# (3-4-3, 3-5-2, 4-3-3, 4-4-2, 4-5-1, 5-2-3, 5-3-2, 5-4-1) - Spielwissen,
# keine API-Quelle dafür (analog zur 33%-Kreditregel).
MIN_POS_COUNT = {"TW": 1, "ABW": 3, "MF": 2, "ANG": 1}


def flag_formation_risk(squad_classified):
    """
    Eine VERKAUFEN-Empfehlung darf den Kader nicht unter die Mindestbesetzung
    einer Position drücken, sonst ist keine gültige Kickbase-Elf mehr
    aufstellbar. Prüft pro VERKAUFEN-Kandidat den aktuellen Positions-Bestand
    gegen MIN_POS_COUNT und hängt bei Verstoß eine Warnung an die reasons an,
    statt die Empfehlung stillschweigend zu geben.
    """
    counts = {}
    for s in squad_classified:
        counts[s["pos"]] = counts.get(s["pos"], 0) + 1

    for s in squad_classified:
        if s["verdict"] != "VERKAUFEN":
            continue
        minimum = MIN_POS_COUNT.get(s["pos"])
        remaining = counts.get(s["pos"], 0) - 1
        if minimum is not None and remaining < minimum:
            s["reasons"].append(
                f"⚠️ Formations-Risiko: nur noch {remaining}x {s['pos']} im Kader nach "
                f"Verkauf (Minimum für eine gültige Elf: {minimum}) - vor Verkauf Ersatz holen")
    return squad_classified

# Sportlicher Vergleich: ab wann gilt ein Marktspieler als klare Verbesserung
# gegenüber einem Kaderspieler? (sporting_core ist 0..1, ap ist Punkteschnitt)
MIN_CORE_GAP = 0.08
MIN_AP_GAP = 8.0

# Positionsübergreifender Vergleich nur bei wirklich großem Punkte-Mehrwert -
# sonst bringt "stärkerer ANG statt schwächstem TW" nichts (Formation!).
BIG_UPSIDE_CORE = 0.65
BIG_UPSIDE_AP_GAP = 15.0

# Farbrang für den sportlichen Vergleich (Spec-Fix 2026-08-05, Punkt 1.3):
# blau/grün = sportlich relevant (wahrscheinlich Startelf), unabhängig vom
# Trading-Verdikt. Die alte Logik verglich nur sporting_core und ließ
# "kein sportlicher Zwang" auch bei grünen (!) Spielern erscheinen, obwohl
# deren Farbe bereits "wahrscheinlich Startelf" bedeutet.
COLOR_RANK = {"blau": 4, "grün": 3, "gelb": 2, "rot": 1, "grau": 0}


def _sportlich_vergleich(m_core, m_color, m_ap, t_core, t_color, t_ap):
    """Dreistufiger Vergleich (Spec 1.3) - Farbe UND sporting_core/ap
    zusammen, nicht das Trading-Verdikt. Liefert "staerker"/"vergleichbar"/
    "schwaecher"."""
    m_rank, t_rank = COLOR_RANK.get(m_color, 2), COLOR_RANK.get(t_color, 2)
    better = (m_core - t_core >= MIN_CORE_GAP) or (m_ap - t_ap >= MIN_AP_GAP)
    worse = (t_core - m_core >= MIN_CORE_GAP) or (t_ap - m_ap >= MIN_AP_GAP)
    if m_rank >= t_rank and better:
        return "staerker"
    if m_rank <= t_rank and worse:
        return "schwaecher"
    return "vergleichbar"


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
    gegen den Kader auf DERSELBEN Position, über `_sportlich_vergleich()`
    (Farbe + sporting_core/ap zusammen, Spec-Fix 1.3), positionsübergreifend
    nur bei großem Punkte-Mehrwert. Stamm- und Trading-Hold-Spieler sind
    nicht ersetzbar, tauchen aber weiterhin auf, wenn der Marktspieler
    stärker/vergleichbar wäre - mit dreistufiger, farbaware Einordnung statt
    binärem "kein Zwang" (das durfte bei blau/grün-Spielern nie erscheinen).
    """
    same_pos = [s for s in squad_classified if s["pos"] == m["pos"]]
    replaceable = [s for s in same_pos if s["verdict"] in ("VERKAUFEN", "BEOBACHTEN")]
    m_core = m["meta"]["sporting_core"]
    m_color = m.get("kickbase_color")
    m_ap = m["ap"]

    def tier(target):
        return _sportlich_vergleich(m_core, m_color, m_ap, target["meta"]["sporting_core"],
                                    target.get("kickbase_color"), target["ap"])

    weakest = min(replaceable, key=lambda s: s["meta"]["sporting_core"]) if replaceable else None
    if weakest and tier(weakest) == "staerker":
        return {"kind": "same_pos_upgrade", "target": weakest}

    weakest_any = min(same_pos, key=lambda s: s["meta"]["sporting_core"]) if same_pos else None
    if weakest_any:
        t = tier(weakest_any)
        if t == "staerker":
            return {"kind": "same_pos_blocked", "target": weakest_any}
        if t == "vergleichbar":
            return {"kind": "same_pos_comparable", "target": weakest_any}
        # "schwaecher" -> gar nicht als Ersatz vorschlagen (Spec-Regel 1.3)

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


# SPEC_spieltagsmodell_v2.md 3.2: "die eigentliche Brücke zwischen
# Transfermarkt und Trainer-Modul" - beantwortet direkt "bringt mir der
# Spieler Punkte?" statt den Spieler nur abstrakt zu bewerten.
def bridge_to_ideal_elf(ep_market, pos, lineup_opt, free_slots):
    if free_slots > 0:
        return {"kind": "free_slot", "gain": ep_market}
    if not lineup_opt or not lineup_opt.get("best"):
        return {"kind": "unknown"}
    xi = lineup_opt["formations"][lineup_opt["best"]]["xi"]
    same_pos = [p for p in xi if p["pos"] == pos]
    if not same_pos:
        return {"kind": "unknown"}
    weakest = min(same_pos, key=lambda p: p["expected_points"])
    gain = ep_market - weakest["expected_points"]
    if gain > 0:
        return {"kind": "verdraengt", "target": weakest, "gain": gain}
    return {"kind": "kein_platz", "target": weakest, "gap": -gain}


# SPEC 3.3: vierstufige Empfehlung statt binär, mit Ausschlusskriterien -
# baut auf dem bereits berechneten team_verdict/bid AUF (kein Ersatz für
# die bestehende, über viele Feedback-Runden kalibrierte market_vs_squad-
# Logik, nur eine gröbere Einordnung obendrauf).
def recommendation_tier(m, bridge):
    headline = m.get("team_verdict", "")
    color = m.get("kickbase_color")
    no_startelf_aussicht = color in ("rot", "grau") and bridge.get("kind") not in ("free_slot", "verdraengt")
    kein_beitrag = bridge.get("kind") in ("kein_platz", "unknown") and m.get("tfhmvt", 0) <= 0
    if no_startelf_aussicht or kein_beitrag or "KEIN BEDARF" in headline:
        return "KEIN BEDARF"
    if "KLARE KAUFEMPFEHLUNG" in headline:
        return "KLARE KAUFEMPFEHLUNG"
    if bridge.get("kind") in ("free_slot", "verdraengt") and m.get("affordable"):
        return "INTERESSANT"
    if m.get("trading_angle", {}).get("qualifies"):
        return "NUR TRADING"
    return "INTERESSANT" if m.get("affordable") and ("UPGRADE" in headline or "KAUFEN" in headline) else "KEIN BEDARF"


def market_vs_squad(market_scored, squad_classified, budget, max_squad,
                    league_overpay=None, club_limit=None):
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
        # Verkauf bringt den VOLLEN MW (kein Abschlag, User-bestätigt) -> der
        # Netto-Teamwert (Kaderwert+Budget) bleibt unverändert, die Kaufkraft
        # steigt um exakt proceeds: cap_after = capacity + proceeds.
        proceeds = int(target["mv"])
        b_after = budget + proceeds
        sv_after = squad_value - target["mv"]
        cap_after = DEBT_RATIO * (sv_after + b_after) + b_after
        affordable = cap_after >= bid["recommended_bid"]
        financing = (f"Kaufkraft nach Verkauf {cap_after:,.0f} "
                     f"(Budget {budget:+,.0f} + Erlös {proceeds:,.0f} = voller MW, "
                     f"33% vom neuen Netto-Teamwert)")
        return affordable, financing

    # SPEC_lernzyklus.md 5.2c: eindeutiger Zweitschlüssel bei Punktgleichheit.
    for m in sorted(market_scored, key=lambda x: (-x["score"], x["id"])):
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

        m_color = m.get("kickbase_color")
        color_txt = f" [{m_color}]" if m_color else ""
        if sporting["kind"] == "same_pos_upgrade":
            t = sporting["target"]
            t_color_txt = f" [{t.get('kickbase_color')}]" if t.get("kickbase_color") else ""
            angles.append(f"⚽ Echtes Upgrade für {t['name']}{t_color_txt} ({t['pos']}): "
                          f"Ø {m['ap']:.0f} vs {t['ap']:.0f} Punkte{color_txt}, "
                          f"sportlicher Kern {m['meta']['sporting_core']:.0%} vs "
                          f"{t['meta']['sporting_core']:.0%}, Team-Stärke {m['team_strength']:.0%}")
        elif sporting["kind"] == "cross_pos_upgrade":
            t = sporting["target"]
            angles.append(f"⚽ SPORTLICH (positionsübergreifend, großer Mehrwert): "
                          f"Ø {m['ap']:.0f} vs {t['ap']:.0f} Punkte ggü. schwächstem "
                          f"Kaderspieler {t['name']} ({t['pos']}), Team-Stärke {m['team_strength']:.0%}")
        elif sporting["kind"] == "same_pos_blocked":
            # Spec-Fix 1.3: "kein sportlicher Zwang" darf bei blau/grün NIE
            # erscheinen - die Farbe bedeutet bereits "sportlich relevant",
            # unabhängig vom Trading-Verdikt.
            t = sporting["target"]
            t_color = t.get("kickbase_color")
            if t_color in ("blau", "grün"):
                angles.append(f"⚽ stärker als {t['name']} ({t['pos']}, {t_color} - selbst "
                              f"sportlich relevant) - kein Ersatzbedarf, {t['name']} ist gesetzt")
            else:
                angles.append(f"⚽ stärker als {t['name']} ({t['pos']}), der ist aber "
                              f"{t['verdict']} -> kein sportlicher Zwang")
        elif sporting["kind"] == "same_pos_comparable":
            t = sporting["target"]
            angles.append(f"⚽ sportlich gleichwertig zu {t['name']} ({t['pos']}) - "
                          "Kauf nur bei freiem Kaderplatz sinnvoll")

        note = _punktetyp_note(m)
        if note:
            angles.append(f"📊 {note}")

        aggr = dynamic_aggressiveness(m["score"])
        bid = recommend_bid(m["mv"], m["tfhmvt"], aggressiveness=aggr,
                            league_overpay=league_overpay,
                            sporting_core=m["meta"]["sporting_core"],
                            star=m.get("star", 0),
                            mv_history=m.get("mv_history"),
                            fair_value_mv=m.get("fair_value"))

        sold_target = None
        is_purchase = False
        if free_slots > 0:
            headline = "KAUFEN (freier Kaderplatz)"
            affordable = capacity >= bid["recommended_bid"]
            financing = (f"Kaufkraft {capacity:,.0f} "
                         f"(33% von Netto-Teamwert {net_value:,.0f} = max. Schulden "
                         f"{max_debt:,.0f}, Budget {budget:+,.0f})")
            is_purchase = True
        elif sporting["kind"] in ("same_pos_upgrade", "cross_pos_upgrade"):
            t = sporting["target"]
            headline = f"UPGRADE für {t['name']} ({t['pos']}, sportlich)"
            affordable, financing = _sell_and_recompute(t, bid)
            sold_target = t
            is_purchase = True
        elif trading["qualifies"] and trading["target"]:
            t = trading["target"]
            headline = f"TRADING-UPGRADE für {t['name']} ({t['daily_pct']:+.1%}/Tag)"
            affordable, financing = _sell_and_recompute(t, bid)
            sold_target = t
            is_purchase = True
        elif angles:
            headline = "interessant, aber kein Kaderzwang (Stamm/Trading-Holds nicht ersetzbar)"
            affordable = capacity >= bid["recommended_bid"]
            financing = f"Kaufkraft {capacity:,.0f}"
        else:
            headline = "KEIN BEDARF - weder Trading- noch sportlicher Mehrwert erkennbar"
            affordable = False
            financing = ""

        # A4: Vereinslimit (tpc) - nur relevant, wenn tatsächlich ein Kauf
        # vorgeschlagen wird. Der ggf. verkaufte Spieler zählt nicht mehr mit.
        if is_purchase and club_limit and m.get("tid"):
            same_club = sum(1 for s in squad_classified if s.get("tid") == m["tid"])
            if sold_target and sold_target.get("tid") == m["tid"]:
                same_club -= 1
            if same_club >= club_limit:
                headline = (f"⚠️ VEREINSLIMIT erreicht ({same_club}/{club_limit} "
                           f"Spieler von {m.get('team', 'diesem Verein')}) - " + headline)
                affordable = False

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
