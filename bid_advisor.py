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
                  sporting_core=None, star=0.0):
    """
    mv:       aktueller Marktwert
    tfhmvt:   24h-MW-Änderung (aus Spieler-Detail)
    aggressiveness: 0.5 = defensiv, 1.0 = normal, 1.5 = unbedingt haben wollen
    league_overpay: gelernter Ø-Overpay der Liga in % (optional, aus Feed)
    sporting_core: sportlicher Kern (0..1, momentum-frei) - dämpft die
                   Projektions-Anhebung bei sportlich schwachen Spielern
    star: Star-Power (0..1, aus main.py) - schaltet die Star-Ausnahme-Info frei
    """
    daily_pct = (tfhmvt / mv) if mv > 0 else 0
    expected_mv_22h = mv + max(tfhmvt, 0)  # fallender MW: kein Aufschlag nötig

    # Projektions-Floor: bei stark & nachhaltig steigenden Spielern bieten
    # erfahrungsgemäß alle auf die absehbare Entwicklung der nächsten Tage,
    # nicht nur auf morgen - AUSSER der Spieler punktet sportlich schlecht.
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
        # Liga zahlt im Schnitt mehr? Dann müssen wir mindestens mithalten.
        buffer = max(buffer, league_overpay * aggressiveness)

    bid = int(expected_mv * (1 + buffer))

    # Wahrscheinlichkeits-Heuristik (grob, transparent):
    # - Gebot unter erwarteter Basis -> praktisch chancenlos
    # - je mehr Puffer über Liga-Overpay, desto sicherer
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
