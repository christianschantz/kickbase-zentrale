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


def dynamic_aggressiveness(score):
    """
    Aggressivität skaliert mit der Stärke des Transfers (Score 0-100):
    - Score 40  -> ~1.0 (normal: ok wenn er platzt)
    - Score 60  -> ~1.2
    - Score 80+ -> ~1.4+ (unbedingt haben wollen)
    """
    return round(0.55 + (score / 100) * 1.1, 2)


def recommend_bid(mv, tfhmvt, aggressiveness=1.0, league_overpay=None):
    """
    mv:       aktueller Marktwert
    tfhmvt:   24h-MW-Änderung (aus Spieler-Detail)
    aggressiveness: 0.5 = defensiv, 1.0 = normal, 1.5 = unbedingt haben wollen
    league_overpay: gelernter Ø-Overpay der Liga in % (optional, aus Feed)
    """
    expected_mv_22h = mv + max(tfhmvt, 0)  # fallender MW: kein Aufschlag nötig

    buffer = DEFAULT_BUFFER * aggressiveness
    if league_overpay is not None:
        # Liga zahlt im Schnitt mehr? Dann müssen wir mindestens mithalten.
        buffer = max(buffer, league_overpay * aggressiveness)

    bid = int(expected_mv_22h * (1 + buffer))

    # Wahrscheinlichkeits-Heuristik (grob, transparent):
    # - Gebot unter erwartetem 22h-MW -> praktisch chancenlos
    # - je mehr Puffer über Liga-Overpay, desto sicherer
    margin = (bid - expected_mv_22h) / expected_mv_22h if expected_mv_22h else 0
    ref = league_overpay if league_overpay is not None else DEFAULT_BUFFER
    if margin <= 0:
        win_prob = 0.05
    else:
        win_prob = min(0.95, 0.5 + (margin - ref) * 8)
        win_prob = max(0.15, win_prob)

    return {
        "expected_mv_22h": int(expected_mv_22h),
        "recommended_bid": bid,
        "buffer_pct": round(buffer * 100, 1),
        "win_probability": round(win_prob, 2),
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
