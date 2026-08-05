"""
Marktwert-Prognose mit Regime-Erkennung und gedämpftem Wachstumsmodell
(SPEC_forecast_coach_scoring.md Abschnitt 1). Ersetzt die bisherige lineare
Fortschreibung `mv + tfhmvt * PROJECTION_DAYS` aus bid_advisor.py, die für
Neueinsteiger (Sprung von 0 auf mehrere Mio in 24h) absurde Gebote erzeugte -
ein 3,7-Mio-Neuzugang bekam eine ~14-Mio-Empfehlung, weil der Einstiegssprung
als fortsetzbare Tagesrate mit 4,5 Tagen hochgerechnet wurde. Das ist keine
Wachstumsphase, sondern die Einpreisung eines Startwerts.

Kickbase-Marktwerte wachsen nicht linear, sondern mit abklingender Rate - das
Modell bildet das über einen Dämpfungsfaktor ab (inhaltlich dieselbe Größe
wie die bereits eingeführte Momentum-Ratio, hier als Fortschreibungsfaktor
statt als Signal genutzt).

Konstanten (0,55/1,00-Grenzen, 0,7/1,15-Szenariofaktoren, Regime-Schwellen)
sind Erstkalibrierung - `backtest_mv_forecast.py` prüft sie rückwirkend
gegen die vorhandene bis zu 92-Tage-Historie, statt sie zu erraten.
"""

import statistics

INITIALISIERUNG = "INITIALISIERUNG"
INSTABIL = "INSTABIL"
STABIL = "STABIL"

REGIME_HORIZON = {INITIALISIERUNG: 0, INSTABIL: 2, STABIL: 5}

NEWCOMER_JUMP_PCT = 0.25   # Tagessprung > 25% -> keine fortsetzbare Trendphase
INSTABIL_MIN_POINTS = 5
STABIL_MIN_POINTS = 14
INSTABIL_SWING_PCT = 0.08  # Streuung (Stdabw.) der Tagesraten über dem Wert -> instabil

D_MIN, D_MAX = 0.55, 1.00

# Szenario-Bandbreite - EMPIRISCH kalibriert per backtest_mv_forecast.py
# (2026-07-31), am 2026-08-05 KORRIGIERT (SPEC_gebote_ki_team_KOMPLETT.md
# Punkt 1.1 - Live-Bugreport: Allgeier bekam eine 8,77-Mio-Obergrenze statt
# der rechnerisch korrekten ~6,9 Mio).
#
# URSACHE (selbst verursacht): die erste Nachkalibrierung hatte OPT_CAP=1.5
# gesetzt, um die Korridor-Trefferquote von ~40% auf ~78% zu heben. Das war
# falsch - d ist ein DÄMPFUNGSfaktor, der über mv_{t+k} = mv_t * Π(1 + g0*d^i)
# eingeht. Für d ≤ 1.0 fällt d^i mit wachsendem i monoton (Dämpfung wirkt wie
# vorgesehen). Für d > 1.0 wächst d^i dagegen EXPONENTIELL mit dem Horizont -
# aus einer täglichen +3%-Rate wurde bei 5 Tagen Horizont keine gedämpfte,
# sondern eine sich selbst verstärkende Kurve. Das erklärt exakt den
# gemeldeten Fehler (8,1%/Tag implizit statt der echten 3%/Tag).
#
# FIX: OPT_CAP zurück auf 1.0 (wie ursprünglich in der Spec) - keine
# Szenario-Dämpfung darf 1.0 übersteigen, sonst ist die Formel für mehrtägige
# Horizonte nicht mehr wohldefiniert. Ehrliche Trefferquote nach dem Fix
# (backtest_mv_forecast.py, 2026-08-05, 11.134 Vergleiche): 57-64% statt der
# vorherigen (fehlerhaften) 78-81% - schmaler, aber nie mehr explosiv. Allgeier-
# Gegenprobe bestanden: Obergrenze in 5 Tagen jetzt 6,72 Mio (Spec-Erwartung
# ~6,88 Mio), vorher 8,77 Mio.
PESS_MULT = 0.0
OPT_MULT = 2.5
OPT_CAP = 1.0


def clean_mv_series(history_response):
    """
    Chronologische (älteste zuerst) reine MW-Werte aus kickbase_api.
    get_mv_history() - führende mv=0.0-Einträge (vor Tracking-Beginn)
    gefiltert. Identische Filterlogik wie scoring.analyze_mv_history().
    """
    items = [it for it in (history_response.get("it") or []) if (it.get("mv") or 0) > 0]
    items = sorted(items, key=lambda x: x.get("dt", 0))
    return [it["mv"] for it in items]


def _median(values):
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _daily_rates(mvs):
    """g_t = mv_t/mv_{t-1} - 1 für jeden Tag, älteste zuerst."""
    return [(mvs[i] / mvs[i - 1] - 1) for i in range(1, len(mvs)) if mvs[i - 1] > 0]


def detect_regime(mvs):
    """
    mvs: clean_mv_series()-Ausgabe (chronologisch, kein Padding).
    Liefert (regime, n_points, letzter_tagessprung_pct_oder_None).

    INITIALISIERUNG: < 5 Datenpunkte ODER der letzte Tagessprung > 25% des MW
      (der eigentliche Neueinsteiger-Detektor - Wachstum dieser Größenordnung
      ist nie ein fortsetzbarer Trend, unabhängig davon wie viel Historie
      sonst vorliegt).
    INSTABIL: 5-13 Datenpunkte, oder Streuung der jüngsten Tagesraten > 8%.
    STABIL: >= 14 Datenpunkte, Streuung < 8%.
    """
    n = len(mvs)
    if n < 2:
        return INITIALISIERUNG, n, None

    rates = _daily_rates(mvs)
    last_jump = rates[-1] if rates else None

    if n < INSTABIL_MIN_POINTS or (last_jump is not None and abs(last_jump) > NEWCOMER_JUMP_PCT):
        return INITIALISIERUNG, n, last_jump

    window = rates[-14:] if len(rates) >= 14 else rates
    swing = statistics.pstdev(window) if len(window) >= 2 else 0.0

    if n < STABIL_MIN_POINTS or swing > INSTABIL_SWING_PCT:
        return INSTABIL, n, last_jump
    return STABIL, n, last_jump


def _damping_factor(rates):
    """d = (Ø Rate letzte 3 Tage) / (Ø Rate der 4 Tage davor), auf [0.55, 1.00]
    begrenzt (verhindert Selbstverstärkung einer Beschleunigung). Neutraler
    Fallback (Mittelwert der Grenzen), wenn zu wenig Historie für den
    Vorher/Nachher-Vergleich vorliegt (<7 Tagesraten)."""
    if len(rates) < 7:
        return round((D_MIN + D_MAX) / 2, 2)
    recent3 = rates[-3:]
    prior4 = rates[-7:-3]
    avg_recent = sum(recent3) / 3
    avg_prior = sum(prior4) / 4
    if avg_prior == 0:
        return round((D_MIN + D_MAX) / 2, 2)
    return max(D_MIN, min(D_MAX, avg_recent / avg_prior))


def _project(mv_t, g0, d, days):
    """mv_{t+k} = mv_t * Π(1 + g0 * d^i) für i=1..days."""
    out = []
    mv = mv_t
    for i in range(1, days + 1):
        mv = mv * (1 + g0 * (d ** i))
        out.append(mv)
    return out


PLAUSIBILITY_FACTOR = 1.5  # Spec 1.1: Prognose verwerfen/kappen, wenn die
                           # implizite Tagesrate mehr als das 1.5-fache der
                           # aktuellen Tagesrate (g0) beträgt.


def _implied_daily_rate(mv_t, mv_future, days):
    if mv_t <= 0 or days <= 0:
        return None
    ratio = mv_future / mv_t
    if ratio <= 0:
        return None
    return ratio ** (1 / days) - 1


def _plausibility_clamp(mv_t, mv_future, days, g0):
    """
    Plausibilitätstest (Spec 1.1, zusätzlich zum OPT_CAP-Fix): fängt
    Ausreißer ab, die trotz gedeckelter Dämpfung durch eine ungewöhnliche
    g0-Schätzung entstehen. Kappt die implizite Tagesrate auf das
    1.5-fache von g0 und meldet das über den zweiten Rückgabewert.
    """
    implied = _implied_daily_rate(mv_t, mv_future, days)
    if implied is None or g0 == 0:
        return mv_future, False
    limit = abs(g0) * PLAUSIBILITY_FACTOR
    if abs(implied) <= limit:
        return mv_future, False
    capped_rate = limit if implied > 0 else -limit
    return mv_t * ((1 + capped_rate) ** days), True


def forecast(mvs, horizon_days=None):
    """
    mvs: clean_mv_series()-Ausgabe. Liefert ein Dict mit Regime, Datenpunkt-
    zahl, Dämpfung und je Tag 1..Horizont eine (pessimistisch/basis/
    optimistisch)-Bandbreite. Bei INITIALISIERUNG bleibt "projections" leer -
    keine Trendprojektion (Spec 1.2), der Aufrufer (bid_advisor) setzt dann
    Gebot = aktueller MW + kleiner Fixaufschlag.
    """
    regime, n, last_jump = detect_regime(mvs)
    mv_t = mvs[-1] if mvs else 0
    result = {
        "regime": regime, "n_points": n, "current_mv": mv_t,
        "last_jump_pct": last_jump, "damping": None, "g0": None,
        "horizon_days": 0, "projections": {},
    }
    if regime == INITIALISIERUNG or mv_t <= 0:
        return result

    rates = _daily_rates(mvs)
    g0 = _median(rates[-3:] if len(rates) >= 3 else rates)
    d = _damping_factor(rates)
    result["damping"] = round(d, 2)
    result["g0"] = round(g0, 4)

    horizon = horizon_days if horizon_days is not None else REGIME_HORIZON[regime]
    result["horizon_days"] = horizon
    if horizon <= 0:
        return result

    scenarios = {"pessimistisch": d * PESS_MULT, "basis": d,
                "optimistisch": min(OPT_CAP, d * OPT_MULT)}
    proj_by_scenario = {name: _project(mv_t, g0, dd, horizon) for name, dd in scenarios.items()}

    clamped_any = False
    for day in range(1, horizon + 1):
        basis_raw, clamped = _plausibility_clamp(mv_t, proj_by_scenario["basis"][day - 1], day, g0)
        clamped_any = clamped_any or clamped
        bound_a_raw, c1 = _plausibility_clamp(mv_t, proj_by_scenario["pessimistisch"][day - 1], day, g0)
        bound_b_raw, c2 = _plausibility_clamp(mv_t, proj_by_scenario["optimistisch"][day - 1], day, g0)
        clamped_any = clamped_any or c1 or c2
        basis, bound_a, bound_b = round(basis_raw), round(bound_a_raw), round(bound_b_raw)
        # Bei negativem g0 (fallender Trend) wirkt dieselbe Dämpfung auf
        # beide Szenarien in umgekehrter Richtung - "pessimistisch" kann dann
        # rechnerisch ÜBER "optimistisch" liegen. Sortieren stellt sicher,
        # dass die Labels immer die tatsächliche untere/obere Korridorgrenze
        # meinen (sonst schlägt der Korridor-Trefferquote-Check in
        # backtest_mv_forecast.py bei fallenden Werten systematisch fehl).
        lo, hi = min(bound_a, bound_b), max(bound_a, bound_b)
        result["projections"][day] = {
            "pessimistisch": lo, "optimistisch": hi, "basis": basis,
        }
    result["plausibility_clamped"] = clamped_any
    return result


def format_forecast_line(name, mvs):
    """Report-Klartext wie in der Spec (Abschnitt 1.4)."""
    f = forecast(mvs)
    if f["regime"] == INITIALISIERUNG:
        return (f"{name} - MW heute {f['current_mv']:,.0f} - Regime: INITIALISIERUNG "
               f"({f['n_points']} Datenpunkte) - keine Trendprojektion, Neueinsteiger "
               f"oder Ausreißertag")
    lines = [f"{name} - MW heute {f['current_mv']:,.0f}"]
    for day, p in f["projections"].items():
        label = "morgen 22:00" if day == 1 else f"in {day} Tagen"
        lines.append(f"  Prognose {label}: {p['pessimistisch']:,.0f} - "
                     f"{p['optimistisch']:,.0f} (Basis {p['basis']:,.0f})")
    lines.append(f"  Regime: {f['regime']} · {f['n_points']} Datenpunkte · "
                f"Dämpfung {f['damping']}")
    return "\n".join(lines)
