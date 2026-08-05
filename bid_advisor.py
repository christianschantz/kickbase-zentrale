"""
Gebots-Berater.

Kickbase-Mechanik (recherchiert):
- Transfers werden beim 22:00-Update abgewickelt. Liegt dein Gebot dann
  UNTER dem neuen Marktwert, ist es ungültig.
- Deshalb: erwarteten 22-Uhr-MW schätzen (aktueller MW + heutiger Trend)
  und mit Puffer darüber bieten.
- Gebote >10% unter MW sind nicht möglich; Angebote der Mitspieler
  konkurrieren zusätzlich.
"""

DEFAULT_BUFFER = 0.03  # 3% über erwartetem 22-Uhr-MW

# SPEC_spieltagsmodell_v2.md 3.5: bei hohem Fair Value UND gutem Wachstum
# darf mehr geboten werden - Aufschlag = Basis-Aufschlag × (1 + 0,5 ×
# relative Unterbewertung), gedeckelt bei +50% (FAIR_VALUE_BOOST_CAP).
# Die Wertobergrenze aus 1.2 (max_gebot = max(trading_ceiling, fair_value))
# bleibt die harte Bremse - dieser Boost wirkt nur INNERHALB dieser Grenze,
# er kann sie nicht überschreiben (Erhöhung des Wunschgebots, nicht der
# Obergrenze).
FAIR_VALUE_BOOST_FACTOR = 0.5
FAIR_VALUE_BOOST_CAP = 1.5

# Bei stark & nachhaltig steigenden Spielern bietet nicht nur einer auf den
# nächsten 22-Uhr-MW, sondern die ganze Liga auf die absehbare Entwicklung der
# nächsten Tage (User-Erfahrung). PROJECTION_DAYS ist die Zielspanne dafür.
PROJECTION_DAYS = 4.5
STRONG_RISE_PCT = 0.008  # deckt sich mit squad_analysis.STRONG_RISE (0.8%/Tag)
WEAK_SPORTING_CORE = 0.4  # darunter gilt "punktet schlecht" -> Projektion NICHT ansetzen

# Star-Ausnahme: laut User-Erfahrung mit der Liga werden Topspieler vereinzelt
# (nicht die Regel!) deutlich über den regulären Puffer hinaus überboten.
# Kein aus dem Activity-Feed gelernter Wert (der Feed ist unverifiziert),
# sondern eine bewusst konservative Ausnahme-Obergrenze als Zusatzinfo.
STAR_THRESHOLD = 0.72  # deckt sich mit der Banger-Schwelle in main.py
STAR_OVERPAY_CEILING = 3_000_000


def dynamic_aggressiveness(score):
    """
    Aggressivität skaliert mit der Stärke des Transfers (Score 0-100):
    - Score 40  -> ~1.0 (normal: ok wenn er platzt)
    - Score 60  -> ~1.2
    - Score 80+ -> ~1.4+ (unbedingt haben wollen)
    """
    return round(0.55 + (score / 100) * 1.1, 2)


def recommend_bid(mv, tfhmvt, aggressiveness=1.0, league_overpay=None,
                  sporting_core=None, star=0.0, mv_history=None, fair_value_mv=None):
    """
    mv:       aktueller Marktwert
    tfhmvt:   24h-MW-Änderung (aus Spieler-Detail) - Fallback-Basis, wenn
              keine mv_history vorliegt
    aggressiveness: 0.5 = defensiv, 1.0 = normal, 1.5 = unbedingt haben wollen
    league_overpay: gelernter Ø-Overpay der Liga in % (optional, aus Feed)
    sporting_core: sportlicher Kern (0..1, momentum-frei) - dämpft die
                   Projektions-Anhebung bei sportlich schwachen Spielern
                   (nur im Legacy-Pfad ohne mv_history relevant)
    star: Star-Power (0..1, aus main.py) - schaltet die Star-Ausnahme-Info frei
    mv_history: optionale chronologische MW-Historie (mv_forecast.
                clean_mv_series()) - wenn vorhanden, läuft die Gebotslogik
                über das regime-basierte Prognosemodell. Ohne Historie (z.B.
                league_board.py/B5, wo 449 Spieler/Liga eine 92-Tage-Historie
                pro Spieler zu teuer machen) fällt es auf die alte lineare
                Näherung zurück.
    fair_value_mv: optional (coach.fair_value()) - "was ist er sportlich
                  wert". Zusammen mit der Trading-Obergrenze die zweite
                  Bremse gegen Überzahlung (SPEC_gebote_ki_team_KOMPLETT.md
                  Punkt 1.2 - vorher entstand das Gebot fast nur aus der
                  MW-Prognose, die Bewertungstiefe floss nur indirekt über
                  den Score-Aufschlag ein).
    """
    if mv_history is not None:
        return _recommend_bid_forecast(mv, mv_history, aggressiveness, league_overpay,
                                       star, fair_value_mv)
    return _recommend_bid_legacy(mv, tfhmvt, aggressiveness, league_overpay, sporting_core, star)


def _recommend_bid_forecast(mv, mv_history, aggressiveness, league_overpay, star, fair_value_mv=None):
    from mv_forecast import forecast, INITIALISIERUNG

    f = forecast(mv_history)
    regime = f["regime"]

    # SPEC_spieltagsmodell_v2.md 3.6: die Regime-Detailzeile ("Regime STABIL
    # (92 Datenpunkte, Dämpfung 1.0) - ...") ist bei INITIALISIERUNG/INSTABIL
    # methodisch irreführend (es GIBT keine tragfähige Prognose) - Hauptsicht
    # bekommt nur die ehrliche Kurzform, die volle Methodik wandert in
    # `projection_note` (Detailbereich, s. html_report.py).
    if regime == INITIALISIERUNG or not f["projections"]:
        # Spec 1.2: keine Trendprojektion - Gebot = aktueller MW + kleiner
        # Fixaufschlag (der reguläre Puffer unten reicht dafür).
        untergrenze = mv
        trading_decke = mv
        jump_txt = f", Tagessprung {f['last_jump_pct']:+.0%}" if f.get("last_jump_pct") else ""
        projection_note = (f"Regime INITIALISIERUNG ({f['n_points']} Datenpunkte{jump_txt}) - "
                           "keine Trendprojektion (Neueinsteiger/Ausreißertag), "
                           "Gebot nah am aktuellen MW")
        projection_short = "zu wenig Historie für eine Prognose"
    else:
        untergrenze = f["projections"][1]["basis"]
        horizon = max(f["projections"])
        # Spec-Fix 1.1/1.2: die Obergrenze beruht auf dem BASIS-, nicht dem
        # optimistischen Szenario - sonst rechtfertigt ein hohes Gebot sich
        # über den besten Fall selbst.
        trading_decke = f["projections"][horizon]["basis"]
        projection_note = (f"Regime {regime} ({f['n_points']} Datenpunkte, "
                           f"Dämpfung {f['damping']}) - Basis-Prognose morgen 22:00 "
                           f"{untergrenze:,.0f} €, Trading-Obergrenze in {horizon} Tagen "
                           f"{trading_decke:,.0f} €")
        projection_short = None if regime == "STABIL" else "zu wenig Historie für eine Prognose"

    buffer = DEFAULT_BUFFER * aggressiveness
    if league_overpay is not None:
        buffer = max(buffer, league_overpay * aggressiveness)

    # SPEC 3.5: Fair Value erhöht den Aufschlag, wenn der Spieler deutlich
    # unterbewertet ist ("bei hohem Fair Value und gutem Wachstum darf mehr
    # geboten werden") - gedeckelt, damit es keine Einladung zum grenzenlosen
    # Überbieten wird. Wirkt nur nach OBEN (Unterbewertung), nie als Rabatt
    # bei Überbewertung - das regelt bereits die Wertobergrenze unten.
    if fair_value_mv is not None and mv > 0 and fair_value_mv > mv:
        underval = (fair_value_mv - mv) / mv
        boost = min(FAIR_VALUE_BOOST_CAP, 1 + FAIR_VALUE_BOOST_FACTOR * underval)
        buffer *= boost

    wunsch = untergrenze * (1 + buffer)
    # max_gebot = max(wert_decke, trading_decke) - Punkt 1.2c: entweder die
    # sportliche Rechtfertigung (Fair Value) oder der Wiederverkaufswert
    # (Trading) reicht, um ein Gebot zu tragen.
    max_gebot = max(trading_decke, fair_value_mv) if fair_value_mv is not None else trading_decke

    verdict = "bieten"
    reason = None
    if wunsch > max_gebot:
        verdict = "nicht_bieten"
        parts = [f"Trading-Obergrenze {trading_decke:,.0f} €"]
        if fair_value_mv is not None:
            parts.append(f"Fair Value {fair_value_mv:,.0f} €")
        reason = f"Wunschgebot {wunsch:,.0f} € übersteigt " + " UND ".join(parts)
        bid = int(max_gebot)
    else:
        bid = int(wunsch)

    margin = (bid - untergrenze) / untergrenze if untergrenze else 0
    ref = league_overpay if league_overpay is not None else DEFAULT_BUFFER
    win_prob = 0.05 if margin <= 0 else max(0.15, min(0.95, 0.5 + (margin - ref) * 8))

    star_ceiling = None
    if star and star >= STAR_THRESHOLD:
        candidate = int(mv + STAR_OVERPAY_CEILING)
        if candidate > bid:
            star_ceiling = candidate

    return {
        "expected_mv_22h": int(untergrenze),
        "recommended_bid": bid,
        "buffer_pct": round(buffer * 100, 1),
        "win_probability": round(win_prob, 2),
        "projection_note": projection_note,
        "projection_short": projection_short,
        "star_ceiling": star_ceiling,
        "regime": regime,
        "verdict": verdict,
        "verdict_reason": reason,
        "trading_ceiling": int(trading_decke),
        "fair_value": int(fair_value_mv) if fair_value_mv is not None else None,
    }


def _recommend_bid_legacy(mv, tfhmvt, aggressiveness, league_overpay, sporting_core, star):
    """Alte lineare Fortschreibung - nur noch für Aufrufer ohne mv_history
    (aktuell league_board.py/B5, s. recommend_bid-Docstring)."""
    daily_pct = (tfhmvt / mv) if mv > 0 else 0
    expected_mv_22h = mv + max(tfhmvt, 0)  # fallender MW: kein Aufschlag nötig

    expected_mv = expected_mv_22h
    projection_note = None
    if daily_pct >= STRONG_RISE_PCT:
        sportlich_ok = sporting_core is None or sporting_core >= WEAK_SPORTING_CORE
        if sportlich_ok:
            expected_mv = mv + tfhmvt * PROJECTION_DAYS
            projection_note = (f"stark & nachhaltig steigend ({daily_pct:+.1%}/Tag) - "
                               f"Gebotsbasis auf {PROJECTION_DAYS:.0f}-Tage-Projektion "
                               f"({expected_mv:,.0f} €) angehoben, das bieten erfahrungsgemäß alle")
        else:
            projection_note = ("steigt stark, punktet aber sportlich schwach - "
                               "Projektions-Aufschlag bewusst NICHT angesetzt")

    buffer = DEFAULT_BUFFER * aggressiveness
    if league_overpay is not None:
        buffer = max(buffer, league_overpay * aggressiveness)

    bid = int(expected_mv * (1 + buffer))

    margin = (bid - expected_mv) / expected_mv if expected_mv else 0
    ref = league_overpay if league_overpay is not None else DEFAULT_BUFFER
    if margin <= 0:
        win_prob = 0.05
    else:
        win_prob = min(0.95, 0.5 + (margin - ref) * 8)
        win_prob = max(0.15, win_prob)

    star_ceiling = None
    if star and star >= STAR_THRESHOLD:
        candidate = int(mv + STAR_OVERPAY_CEILING)
        if candidate > bid:
            star_ceiling = candidate

    return {
        "expected_mv_22h": int(expected_mv_22h),
        "recommended_bid": bid,
        "buffer_pct": round(buffer * 100, 1),
        "win_probability": round(win_prob, 2),
        "projection_note": projection_note,
        "projection_short": None,
        "star_ceiling": star_ceiling,
    }


def learn_league_overpay(activities):
    """
    Versucht aus dem Activity-Feed abgeschlossene Käufe zu extrahieren und
    den Ø-Overpay der Liga zu berechnen ((Preis - MW) / MW).
    Struktur des Feeds ist UNVERIFIZIERT -> defensiv geschrieben, gibt
    None zurück wenn nichts Brauchbares gefunden wird.
    """
    if not activities:
        return None

    items = activities.get("af", []) or activities.get("it", []) or []
    overpays = []
    for item in items:
        data = item.get("data", item)
        price = data.get("trp") or data.get("prc") or data.get("p")
        mv = data.get("mv")
        if price and mv and mv > 0 and price > mv * 0.5:
            overpays.append((price - mv) / mv)

    if len(overpays) >= 3:
        overpays.sort()
        mid = len(overpays) // 2  # Median, robust gegen Ausreisser
        return overpays[mid]
    return None
