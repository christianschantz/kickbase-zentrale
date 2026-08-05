"""
Transparentes Spieler-Scoring (0-100) mit Einzelkomponenten und
Datenlage-Erkennung.

Wichtig: Ein Spieler ohne Kickbase-Punktehistorie (Neuzugang aus anderer
Liga) wird NICHT abgestraft. Erkennung: ap und p fehlen/0. Dann:
- Form wird aus dem Marktwert geschätzt (die Community bepreist erwartete
  Leistung - ein 8-Mio-Neuzugang ist kein 20-Punkte-Spieler)
- Preis-Leistung wird neutral (0.5) gesetzt statt 0
- data_complete=False wird ausgewiesen, damit das Briefing es transparent macht

Zusätzlich wird ein "sporting_core" berechnet (Score OHNE Momentum):
Grundlage für die Kader-Verdikte, damit ein Stammspieler Stamm bleibt,
egal was sein Marktwert gerade macht.
"""

DEFAULT_WEIGHTS = {
    "value_efficiency": 0.22,
    "momentum": 0.26,
    "availability": 0.20,
    "fixtures": 0.14,
    "form": 0.18,
}

PROB_SCORE = {1: 1.0, 2: 0.75, 3: 0.5, 4: 0.25, 5: 0.0}
STATUS_PENALTY = {0: 1.0, 1: 0.6, 2: 0.25}

# Kickbase-Statusfarbe aus `prob` (SPEC_lineup_verified.md Punkt 4, 2026-08-05
# verifiziert per Kreuzprobe gegen 4 vom User genannte Spieler mit bekannter
# App-Farbe - 4/4 Treffer, nicht mehr Hypothese): prob 1 = blau (gesetzt),
# 2 = grün (wahrscheinlich Startelf), 3 = gelb (fraglich), 4 = rot (eher
# nicht), 5 = grau (keine Einschätzung/fällt aus - kein Gegenbeispiel
# gefunden, aber auch kein eigener Treffer, da kein prob=5-Spieler im
# Abgleich war). Ergänzt das eigene Verdikt (STAMM/HALTEN/...), ersetzt es
# NICHT - die Farbe sagt "spielt er?", das Verdikt sagt "was tue ich mit ihm?".
KICKBASE_COLOR = {1: "blau", 2: "grün", 3: "gelb", 4: "rot", 5: "grau"}


def kickbase_color(prob):
    return KICKBASE_COLOR.get(prob)


def is_min_price_player(mv, min_price, margin=0.10):
    """
    Zensierungs-Erkennung (SPEC_kalibrierung_fairvalue.md 3.1): Spieler am
    oder nahe dem Liga-Mindestmarktwert (2. BL 250k, La Liga 500k, s.
    config.LEAGUES) können nicht billiger sein - ihr Preis ist nach unten
    ABGESCHNITTEN, nicht marktbestimmt (zensierte Beobachtung, keine
    Bewertung). `margin` erweitert die Zone leicht nach oben (Standard 10%),
    damit auch knapp-über-Mindestpreis-Spieler noch als "neutral" statt
    fälschlich "überbewertet" gelten. Ohne bekannten `min_price` (z.B. ein
    Aufrufer ohne Liga-Konfiguration) liefert diese Funktion False - dann
    greift die normale Fair-Value-Bewertung, kein stiller Sonderfall.
    """
    if not min_price or mv <= 0:
        return False
    return mv <= min_price * (1 + margin)


def estimate_ap_from_peers(pos, team_strength, color, lookup):
    """
    Vergleichswert aus dem Umfeld (SPEC_kalibrierung_fairvalue.md 3.2) für
    Spieler OHNE eigene Punktehistorie (reine Ligawechsler/Neuzugänge, ap=0
    UND keine bestätigten Einsätze) - statt sie über den MW zu schätzen
    (`mv_implied_form`, deckelt bei 0.7 = strukturell unterbewertet ggü.
    echten Leistungsträgern) den MEDIAN von Spielern mit vergleichbarer
    Position + Teamstärke-Terzil + Kickbase-Farbe verwenden: "ein blauer
    Stammspieler eines Mittelfeldteams bekommt die typische Punktzahl genau
    dieser Gruppe, nicht 0".

    `lookup`: von `build_peer_lookup()` erzeugtes Dict
    {(pos, terzil, farbe): median_ap}. Fällt progressiv zurück, wenn der
    exakte Bucket leer ist: (pos, terzil, farbe) -> (pos, farbe) -> (pos) ->
    None (dann greift beim Aufrufer der alte MW-Schätzer als letzte Instanz).
    Liefert (median_ap, quelle) - quelle nur für Transparenz/Logging.
    """
    if not lookup:
        return None, None
    terzil = _team_strength_tercile(team_strength)
    for key, label in (((pos, terzil, color), "pos+teamstaerke+farbe"),
                       ((pos, None, color), "pos+farbe"),
                       ((pos, None, None), "pos")):
        val = lookup.get(key)
        if val is not None:
            return val, label
    return None, None


def _team_strength_tercile(team_strength):
    ts = 0.5 if team_strength is None else max(0.0, min(1.0, team_strength))
    if ts < 1 / 3:
        return "schwach"
    if ts < 2 / 3:
        return "mittel"
    return "stark"


def build_peer_lookup(players):
    """
    Baut das Bucket-Dict für `estimate_ap_from_peers()`: Median-`ap` je
    (pos, teamstaerke-terzil, kickbase-farbe) UND den gröberen Fallback-Ebenen
    (pos, None, farbe) und (pos, None, None) - über alle Spieler MIT echter
    Punktehistorie (`ap>0` oder bestätigte Nullleistung, s. Aufrufer). Jeder
    Spieler-Eintrag braucht 'pos', 'ap', 'team_strength', 'color'.
    """
    from collections import defaultdict
    buckets = defaultdict(list)
    for p in players:
        pos, ap = p.get("pos"), p.get("ap")
        if pos is None or ap is None:
            continue
        color = p.get("color")
        terzil = _team_strength_tercile(p.get("team_strength"))
        buckets[(pos, terzil, color)].append(ap)
        buckets[(pos, None, color)].append(ap)
        buckets[(pos, None, None)].append(ap)
    lookup = {}
    for key, vals in buckets.items():
        if len(vals) >= 3:  # Mindestgröße, sonst kein tragfähiger Median
            lookup[key] = _median(sorted(vals))
    return lookup


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def mv_implied_form(mv):
    """
    MW-basierte Leistungserwartung: 1M -> schwach, 10M+ -> gedeckelt bei 0.7.
    Deckel (Spec-Fix 2026-07-31): ein Spieler OHNE Punktehistorie darf nie
    dieselbe Formbewertung erreichen wie ein belegter Topscorer (vorher
    erreichten alle >10M-Neuzugänge 100% - dadurch standen Spieler mit
    Ø 0 P wie Hjulmand/Aubameyang fälschlich in Top-Ranglisten).
    """
    return min(0.70, _clamp((mv - 1_000_000) / 9_000_000))


def percentile_rank(value, sorted_values):
    """
    Ligarelatives Perzentil (0..1): Anteil der Population mit kleinerem Wert.
    Ersetzt absolute Skalen, die auf eine Liga (2. Bundesliga) kalibriert
    waren und in anderen Ligen (La Liga, höheres MW-/Punkteniveau) versagten.
    """
    from bisect import bisect_left
    if not sorted_values:
        return 0.5
    return bisect_left(sorted_values, value) / len(sorted_values)


def _median(sorted_values):
    n = len(sorted_values)
    mid = n // 2
    if n % 2:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2


def fit_price_curve(players, min_price=None, censor_margin=0.10):
    """
    Referenzkurve über Preis-Dezile (Median-MW/Median-Punkte je Dezil) statt
    einer globalen Log-Regression (Spec-Fix 2026-07-31, SPEC_forecast_coach_
    scoring.md Abschnitt 3: die Regression lag am teuren Ende systematisch zu
    tief - fast jeder teure Spieler stand "über Erwartung", statistisch
    unmöglich bei korrektem Fit. Ursache: der Ausschluss von ap=0-Spielern
    verengte die Stichprobe im oberen Preissegment auf reine Leistungsträger,
    da dort viele Neuzugänge ohne Kickbase-Historie liegen - die Kurve wurde
    von einer nach oben verzerrten Stichprobe gelernt).

    `players` MUSS bereits gefiltert sein: nur Spieler mit ap>0 ODER
    bestätigter echter Nullleistung (Historie vorhanden, aber 0 Punkte -
    s. league_board._resolve_zero_ap_history). Reine Ligawechsler ohne
    Historie dürfen hier NICHT rein (das war der Bug).

    **Zensierung am Mindestpreis (SPEC_kalibrierung_fairvalue.md 3.1)**:
    Spieler am/nahe dem Liga-Mindestmarktwert (`min_price`, z.B. 250k in der
    2. BL) können nicht billiger sein als der Mindestpreis - ihr Preis ist
    ABGESCHNITTEN (zensierte Beobachtung), nicht frei marktbestimmt. Werden
    sie mitgefittet, verzerren sie das untere Kurvenende (viele verschiedene
    Leistungsniveaus auf demselben Mindestpreis gestapelt). Werden per
    `scoring.is_min_price_player()` ausgeschlossen, wenn `min_price` gesetzt
    ist - ohne `min_price` (Aufrufer ohne Liga-Konfiguration) bleibt das
    alte Verhalten (keine Zensierung) erhalten.

    Vorteil ggü. Regression: robust gegen Ausreißer, kein Modellrisiko, direkt
    als Tabelle darstellbar ("für 12 Mio bringt der Durchschnittsspieler
    92 Punkte" - wörtlich das Dezil-Ergebnis). Liefert eine sortierte Liste
    von 10 (median_mv, median_ap)-Stützpunkten oder None bei <30 Punkten
    (Mindestgröße für 10 einigermaßen tragfähige Dezile).
    """
    pts = sorted(((p["mv"], p["ap"]) for p in players if p.get("mv", 0) > 0
                 and not is_min_price_player(p.get("mv", 0), min_price, censor_margin)),
                key=lambda x: x[0])
    n = len(pts)
    if n < 30:
        return None
    deciles = 10
    size = n / deciles
    curve = []
    for i in range(deciles):
        chunk = pts[int(i * size):int((i + 1) * size)] or [pts[-1]]
        curve.append((_median(sorted(x[0] for x in chunk)),
                      _median(sorted(x[1] for x in chunk))))
    return curve


def expected_points(mv, curve):
    """
    Erwartete Punkte für einen Marktwert laut Dezil-Kurve - linear zwischen
    den Stützpunkten interpoliert. Außerhalb der Randdezile wird NICHT
    extrapoliert, sondern der Randwert übernommen (Spec 3.2 Schritt 5).
    """
    if not curve or mv <= 0:
        return None
    if mv <= curve[0][0]:
        return curve[0][1]
    if mv >= curve[-1][0]:
        return curve[-1][1]
    for (mv_lo, ap_lo), (mv_hi, ap_hi) in zip(curve, curve[1:]):
        if mv_lo <= mv <= mv_hi:
            if mv_hi == mv_lo:
                return ap_lo
            t = (mv - mv_lo) / (mv_hi - mv_lo)
            return ap_lo + t * (ap_hi - ap_lo)
    return curve[-1][1]


def invert_price_curve(target_points, curve):
    """
    Inverse von expected_points() (SPEC_gebote_ki_team_KOMPLETT.md Punkt 1.2,
    "Fair Value"): für eine bereinigte Punkteerwartung, welcher Marktwert ist
    in dieser Liga dafür üblich? "Ein Spieler, der so viel liefert, kostet
    hier normalerweise X." Dieselbe Dezil-Kurve, nur mv/Punkte-Achsen
    vertauscht - piecewise-linear, an den Rändern abgeflacht statt
    extrapoliert. Die Kurve ist als Median je Preis-Dezil nicht zwingend
    streng monoton in ap - bei einem lokalen Ausreißer-Dezil wird trotzdem
    der erste passende Abschnitt genommen (stabil genug für die Dezil-
    Granularität). None ohne Kurve.
    """
    if not curve:
        return None
    aps = [p for _, p in curve]
    lo_ap, hi_ap = min(aps), max(aps)
    if target_points <= lo_ap:
        return curve[aps.index(lo_ap)][0]
    if target_points >= hi_ap:
        return curve[aps.index(hi_ap)][0]
    for (mv_lo, ap_lo), (mv_hi, ap_hi) in zip(curve, curve[1:]):
        seg_lo, seg_hi = min(ap_lo, ap_hi), max(ap_lo, ap_hi)
        if seg_lo <= target_points <= seg_hi:
            if ap_hi == ap_lo:
                return mv_lo
            t = (target_points - ap_lo) / (ap_hi - ap_lo)
            return mv_lo + t * (mv_hi - mv_lo)
    return curve[-1][0]


def value_residual(mv, ap, curve):
    """
    RELATIVES Residuum ggü. der Preiserwartung (Spec-Fix 3.4):
    (ap - erwartung) / erwartung statt absolut - +10 Punkte sind bei einem
    2-Mio-Spieler (Erwartung ~40) stark, bei einem 30-Mio-Spieler (Erwartung
    ~110) marginal. `ap` darf explizit 0 sein (bestätigte Nullleistung mit
    Historie) - nur `ap=None` (keine Historie, nicht bewertbar) liefert None.
    Liefert (relatives_residuum, erwartete_punkte) - (None, None) ohne Kurve/
    Bewertbarkeit.
    """
    if not curve or mv <= 0 or ap is None:
        return None, None
    expected = expected_points(mv, curve)
    if not expected:
        return None, None
    return (ap - expected) / expected, expected


def price_curve_diagnostics(curve, scored_residuals):
    """
    Selbstprüfung (Spec 3.5, Pflicht): bei korrektem Fit sollten ~50% der
    bewerteten Spieler über der Preiserwartung liegen. Deutliche Abweichung
    (außerhalb 40-60%) heißt: Fit ist verzerrt, Warnung statt stillschweigend
    weiterrechnen.
    """
    if not curve or not scored_residuals:
        return {"curve": curve or [], "over_pct": None, "n": 0, "plausible": None}
    over = sum(1 for r in scored_residuals if r > 0)
    over_pct = over / len(scored_residuals)
    return {
        "curve": curve,
        "over_pct": over_pct,
        "n": len(scored_residuals),
        "plausible": 0.40 <= over_pct <= 0.60,
    }


def form_raw(ap, ph, current_season_days):
    """
    Aktuelle Form (Ø letzte 5 gespielte Spieltage aus `ph`) gemischt mit dem
    Saisonschnitt (`ap`), Gewicht der aktuellen Form wächst mit der
    Spieltagszahl (Deckel 70%, verhindert dass 2 schwache Spiele einen
    belegten Topspieler aus der Liste kippen).

    current_season_days=0 (Saisonvorbereitung, Stand 2026-07-31) -> Gewicht
    immer 0, `ph` wird dann gar nicht gebraucht (Kostengrund: würde sonst
    einen get_player_details-Call je Spieler der gesamten Liga-Population
    brauchen). TODO: vor Saisonstart (~7./15.8.) echte Spieltagszahl aus
    /v4/competitions/{cid}/players (Felder day/sn/mdsn/nsn) ableiten - noch
    nicht verifiziert, s. CLAUDE.md.
    """
    w = min(0.70, current_season_days / 12) if current_season_days else 0.0
    if w == 0:
        return ap or 0
    recent = [e["p"] for e in (ph or []) if e.get("hp") and e.get("p") is not None][-5:]
    recent_avg = sum(recent) / len(recent) if recent else None
    if recent_avg is None:
        return ap or 0
    return w * recent_avg + (1 - w) * (ap or 0)


def score_player(market_entry, details, fix_ease, weights=None):
    w = weights or DEFAULT_WEIGHTS

    mv = details.get("mv") or market_entry.get("mv", 0)
    ap = market_entry.get("ap") or details.get("ap") or 0
    total_p = market_entry.get("p") or details.get("p") or 0
    tfhmvt = details.get("tfhmvt", 0) or 0

    # Datenlage: keine Punktehistorie vorhanden?
    data_complete = bool(ap or total_p)

    if data_complete:
        ppm = (ap / (mv / 1_000_000)) if mv > 0 else 0
        value_eff = _clamp((ppm - 3) / 12)
        form = _clamp((ap - 20) / 60)
    else:
        value_eff = 0.5                # neutral, nicht bestrafen
        form = mv_implied_form(mv)     # Community-Preis als Leistungs-Proxy

    daily_pct = (tfhmvt / mv) if mv > 0 else 0
    momentum = _clamp((daily_pct + 0.015) / 0.03)

    st = details.get("st", 0)
    prob = details.get("prob", 3)
    # 'st' ist laut Live-Stichprobe (2. Liga) eine Bitmaske - reale Werte
    # 0/1/2/4/16/256 beobachtet, nur 0/1/2 sind bisher geklärt (User prüft
    # 4/16/256 noch in der App). Unbekannte Werte NICHT still auf "neutral"
    # fallen lassen, sondern als solche kennzeichnen (status_known in meta).
    availability = STATUS_PENALTY.get(st, 0.5) * PROB_SCORE.get(prob, 0.5)

    components = {
        "value_efficiency": value_eff,
        "momentum": momentum,
        "availability": availability,
        "fixtures": fix_ease,
        "form": form,
    }
    total = sum(components[k] * w[k] for k in w) * 100

    # Sportlicher Kern: gleiche Komponenten OHNE Momentum, neu normiert.
    core_keys = [k for k in w if k != "momentum"]
    core_weight = sum(w[k] for k in core_keys)
    sporting_core = sum(components[k] * w[k] for k in core_keys) / core_weight

    return round(total, 1), components, {
        "sporting_core": round(sporting_core, 3),
        "data_complete": data_complete,
        "daily_pct": daily_pct,
        "status_known": st in STATUS_PENALTY,
    }


def momentum_ratio(tfhmvt, sdmvt):
    """
    A1 (überarbeitet 2026-07-31, verifiziert gegen analyze_mv_history/
    marketValue/92 - exakte Übereinstimmung): Verhältnis 24h-Änderung zu
    Ø-7-Tage-Änderung, beantwortet "flacht der Anstieg ab?" OHNE eigene
    Mehrtages-Historie zu ziehen. `sdmvt` (7-Tage-MW-Differenz) kommt direkt
    im Kader-/Teamprofil-Response mit - kein Zusatz-API-Call nötig.

    ratio = (tfhmvt * 7) / sdmvt

    >1.1 beschleunigt, 0.9-1.1 konstant, 0.6-0.9 lässt nach,
    <0.6 flacht deutlich ab -> Verkaufsfenster prüfen.
    Bei fallendem MW (sdmvt<0) kehrt sich die Interpretation um: ratio>1
    heißt der Absturz beschleunigt sich.
    None bei sdmvt fehlend/0 - dann NICHT raten.

    Einschränkung (aus der Spec übernommen): in der Saisonvorbereitung
    steigen MW schubweise, ein einzelner schwacher Tag ist noch kein Peak -
    für ein belastbares Signal bräuchte es mehrere Tage in Folge unter der
    Schwelle (Tagessnapshots, noch nicht gebaut). Bis dahin ein
    Momentaufnahme-Indikator, kein bestätigter Trend.
    """
    if not sdmvt:
        return None
    return (tfhmvt * 7) / sdmvt


def analyze_mv_history(history_response, current_mv):
    """
    Mehrtages-Trend aus der vollen MW-Historie - seit dem momentum_ratio()-
    Fund (sdmvt kommt kostenlos im Kader-/Teamprofil-Response mit) nur noch
    für Detailansichten gedacht, nicht mehr für die Massen-Trendberechnung
    im täglichen Lauf (spart einen API-Call pro Spieler). Quelle:
    kickbase_api.get_mv_history() - liefert bis zu 92 Tage MW-Historie
    ({"it": [{"dt": Tagesnummer, "mv": float}], "hmv": Allzeithoch}).
    Führende mv=0.0-Einträge (vor Tracking-Beginn) werden gefiltert.

    Liefert IMMER ein Dict (nie None), damit Aufrufer bei zu wenig Historie
    das explizit ausweisen können statt zu raten:
    - trend_3d/trend_7d: Ø-MW-Änderung/Tag über die letzten 3/7 Tage
    - accelerating: True = 3-Tage-Rate >= die 3 Tage davor (noch beschleunigend/
      stabil), False = echte Abflachung, None = zu wenig Punkte (< 7) für den
      Vergleich - dann NICHT raten
    - dist_to_hmv: Abstand zum Allzeithoch in % (Überhitzungs-Indikator)
    """
    items = [it for it in (history_response.get("it") or []) if (it.get("mv") or 0) > 0]
    items = sorted(items, key=lambda x: x.get("dt", 0))
    mvs = [it["mv"] for it in items]
    n = len(mvs)

    def _avg_daily_change(window):
        if n < 2:
            return None
        pts = mvs[-(window + 1):] if n > window else mvs
        if len(pts) < 2:
            return None
        return (pts[-1] - pts[0]) / (len(pts) - 1)

    trend_3d = _avg_daily_change(3)
    trend_7d = _avg_daily_change(7)

    accelerating = None
    if n >= 7:
        recent, prior = mvs[-4:], mvs[-7:-3]
        recent_rate = (recent[-1] - recent[0]) / (len(recent) - 1)
        prior_rate = (prior[-1] - prior[0]) / (len(prior) - 1)
        accelerating = recent_rate >= prior_rate

    hmv = history_response.get("hmv") or (max(mvs) if mvs else None)
    dist_to_hmv = ((hmv - current_mv) / hmv) if hmv else None

    return {
        "n_points": n,
        "insufficient": n < 3,
        "trend_3d": trend_3d,
        "trend_7d": trend_7d,
        "accelerating": accelerating,
        "hmv": hmv,
        "dist_to_hmv": dist_to_hmv,
    }


def player_reliability_profile(details):
    """
    "Punktetyp": punktet der Spieler zuverlässig unabhängig vom Spielausgang
    seines Teams (Rohpunkte-Typ) oder vor allem dann, wenn sein Team gewinnt
    (Scorer-Typ)? Quelle: 'ph' (Punkte je Spieltag, chronologisch bis zum
    aktuellen 'day') + 'mdsum' (Ergebnisse je Spieltag, gleiche Tage) aus
    get_player_details - beide Felder verifiziert, aber nur ~3-5 Spiele
    Stichprobe (Basis-Signal, keine Statistik). Funktioniert auch in der
    Sommerpause, weil 'mdsum'/'ph' die letzten Spiele der VORSAISON zeigen.
    """
    tid = str(details.get("tid", "") or "")
    day = details.get("day")
    ph = details.get("ph") or []
    mdsum = details.get("mdsum") or []
    if not tid or day is None or not ph or not mdsum:
        return None

    n = len(ph)
    by_day = {day - (n - 1 - idx): entry for idx, entry in enumerate(ph)}

    games = []
    for md in mdsum:
        if md.get("mdst") != 2:  # nur beendete Spiele zählen
            continue
        entry = by_day.get(md.get("day"))
        if not entry or not entry.get("hp") or entry.get("p") is None:
            continue
        t1, t2 = str(md.get("t1")), str(md.get("t2"))
        t1g, t2g = md.get("t1g", 0) or 0, md.get("t2g", 0) or 0
        if tid == t1:
            gf, ga = t1g, t2g
        elif tid == t2:
            gf, ga = t2g, t1g
        else:
            continue
        result = "Sieg" if gf > ga else "Niederlage" if gf < ga else "Unentschieden"
        games.append({"day": md.get("day"), "result": result, "points": entry["p"]})

    if not games:
        return None

    def _avg(res):
        pts = [g["points"] for g in games if g["result"] == res]
        return sum(pts) / len(pts) if pts else None

    return {
        "games": games,
        "avg_win": _avg("Sieg"),
        "avg_draw": _avg("Unentschieden"),
        "avg_loss": _avg("Niederlage"),
    }


RELIABLE_RATIO = 0.6  # Ø-Punkte bei Niederlage >= 60% von Ø bei Sieg -> "verlässlich"


def punktetyp_label(profile):
    """
    Klartext-Einordnung des Punktetyps. Rückgabe (reliable, text):
    reliable=True -> Rohpunkte-Typ (punktet auch bei Niederlagen ordentlich),
    reliable=False -> Scorer-Typ (Punkte hängen stark am eigenen Sieg),
    (None, None) -> zu wenig Datenbasis (z.B. keine Niederlage in der
    Stichprobe der letzten Spiele).
    """
    if not profile or profile["avg_win"] is None or profile["avg_loss"] is None:
        return None, None
    if profile["avg_win"] <= 0:
        return None, None
    reliable = (profile["avg_loss"] / profile["avg_win"]) >= RELIABLE_RATIO
    label = "Rohpunkte-Typ" if reliable else "Scorer-Typ"
    text = (f"{label}: Ø {profile['avg_win']:.0f} P. bei Sieg, "
            f"Ø {profile['avg_loss']:.0f} P. bei Niederlage "
            f"({len(profile['games'])} Spiele, kleine Stichprobe)")
    return reliable, text


def explain(components, meta=None):
    labels = {
        "value_efficiency": "Preis-Leistung",
        "momentum": "MW-Momentum",
        "availability": "Einsatz-WK",
        "fixtures": "Spielplan",
        "form": "Form/Schnitt",
    }
    s = " | ".join(f"{labels[k]}: {v:.0%}" for k, v in components.items())
    if meta and not meta.get("data_complete", True):
        s += "  [⚠️ keine Punktehistorie - Form aus MW geschätzt]"
    if meta and not meta.get("status_known", True):
        s += "  [⚠️ Status-Code unbekannt - Einsatz-WK neutral angenommen]"
    return s
