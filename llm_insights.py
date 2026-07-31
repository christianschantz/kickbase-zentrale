"""
KI-Einordnungsschicht (Gemini REST API, kein SDK nötig - Muster aus
test_gemini.py übernommen und produktionsreif gemacht).

Grundsatz (SPEC_llm_prompt_v2.md, nach erstem Live-Test verschärft): Das
Modell ordnet ein, es rechnet NICHT neu. Verdikte/Scores/Finanzkennzahlen
kommen fertig aus dem Code - die KI prüft nur, ob externe Informationen
(Verletzung, Trainerwechsel, Bericht) etwas ändern würden, und schreibt einen
kurzen Tageskommentar. Erster Testlauf zeigte: ohne genug Kontext rechnet das
Modell selbst neu und widerspricht dabei bereits korrekten Verdikten (Beispiel
aus dem Test: "Pieringer zum Zenit verkaufen" - das Modell verwechselte
BEOBACHTEN, das sich NUR auf MW-Momentum bezieht, mit einem sportlichen
Gesamturteil). Deshalb: fester Domänen-Textblock + explizite verdict_scope/
team_context je Spieler + hartes Herausfiltern nicht finanzierbarer Ziele.

Läuft nur, wenn config.GEMINI_API_KEY gesetzt ist - sonst wird die Schicht
übersprungen, die restliche Pipeline läuft unverändert weiter (kein
Hard-Dependency, kein Absturzrisiko für main.py).
"""

import json
from datetime import date, timezone

import requests

BASE = "https://generativelanguage.googleapis.com/v1beta"

# Produktivmodelle - explizit KEINE Preview-Modelle (SPEC v2 Abschnitt 7: der
# erste Testlauf wählte automatisch ein Preview-Modell, das jederzeit
# verschwinden kann). "preview" wird beim Picken zusätzlich hart ausgeschlossen.
PREFERRED = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite"]

# Grobe Schätzung (CLAUDE.md: Saisonstart ~7./15.08.2026) - unverifiziert,
# nur für die "Tage bis Saisonstart"-Einordnung im Prompt-Kontext relevant.
SEASON_START_ESTIMATE = date(2026, 8, 8)

VERDICT_SCOPE = {
    "STAMM": "sportliches Gesamturteil - gilt unabhängig vom aktuellen MW-Trend, "
             "kein Verkaufskandidat aus MW-Gründen",
    "HALTEN (Trading)": "reines Wert-Investment - kein Stammplatz-Argument, wird "
                        "gehalten solange der MW-Anstieg trägt",
    "BEOBACHTEN": "bezieht sich NUR auf die MW-Momentum-Komponente (Anstieg flacht "
                  "nachweisbar ab) - sagt NICHTS über die sportliche Qualität aus, "
                  "ein BEOBACHTEN-Spieler kann trotzdem Stammspieler sein",
    "VERKAUFEN": "MW fällt/stagniert UND sportlich schwach - beides muss zutreffen",
}

DOMAIN_BLOCK = """Du bist Analyst für einen Kickbase-Manager (Fantasy-Fußball).

SPIELPRINZIP:
- Marktwert-Update täglich um 22:00 Uhr, Treiber ist Community-Verhalten, nicht direkt Punkte.
- Verkauf bringt exakt den aktuellen Marktwert, kein Abschlag.
- Kreditregel: bis zu 33% des NETTO-Teamwerts (Kaderwert + Budget) ins Minus möglich.
- Ein einzelner Tageswert kippt selten abrupt - MW-Trends kündigen sich über mehrere Tage an.

BEDEUTUNG DER VERDIKTE (wörtlich, nicht neu interpretieren):
STAMM: Sportlich gesetzter Spieler. Gilt unabhängig vom aktuellen MW-Trend. Kein Verkaufskandidat aus MW-Gründen.
HALTEN (Trading): Kein Stammplatz-Argument, aber MW steigt kräftig (>0,8%/Tag). Reines Wert-Investment. Wird gehalten, solange der Anstieg trägt.
BEOBACHTEN: Bezieht sich NUR auf die MW-Momentum-Komponente (Anstieg flacht nachweisbar ab, gemessen über tfhmvt/sdmvt). Sagt NICHTS über die sportliche Qualität des Spielers aus - ein BEOBACHTEN-Spieler kann trotzdem Stammspieler sein, das Signal betrifft ausschließlich den richtigen Verkaufszeitpunkt.
VERKAUFEN: MW fällt/stagniert UND sportlich schwach. Beides muss zutreffen.

KENNZAHLEN-GLOSSAR:
tfhmvt: MW-Änderung der letzten 24 Stunden
sdmvt: MW-Änderung der letzten 7 Tage
momentum_ratio = (tfhmvt * 7) / sdmvt: >1 beschleunigt, 0,6-0,9 lässt nach, <0,6 deutliche Abflachung - ABER: eine Momentaufnahme, kein über mehrere Tage bestätigter Trend. Ein Wert von 0,3 heißt "Verkaufsfenster prüfen", nicht "morgen fällt der Kurs".
sporting_core: Score-Anteil OHNE Momentum - reine sportliche Einschätzung
team_score: Klassestärke des Vereins aus Wettquoten

VERHALTENSREGELN:
- Verwende die gelieferten Verdikte und Begründungen als gesetzt. Deine Aufgabe ist NICHT, sie neu zu berechnen, sondern zu prüfen, ob externe Informationen (Verletzung, Trainerwechsel, Bericht) etwas ändern.
- Ein einzelner niedriger Tageswert bei einem sonst stark steigenden Spieler ist ein Beobachtungs-, kein Alarmsignal - relativiere das im Report.
- Erwähne Marktziele, die als "nicht finanzierbar" markiert sind, NUR wenn explizit danach gefragt wird. Sonst weglassen (sie wurden hier bereits herausgefiltert).
- Bewerte die Budgetlage IMMER im Kontext des täglichen Kader-Ertrags und der Tage bis zum Saisonstart, nicht als isolierten Kontostand.
- Wenn nichts Neues/Externes zu sagen ist, sag das kurz - keine Zahlenwiederholung als Füllmaterial."""

TASK_INSTRUCTIONS = """Du bekommst den fertigen Kickbase-Report meines Analyse-Skripts als JSON. Die
Berechnungen (Verdikte, Scores, Finanzkennzahlen) sind bereits erledigt und
korrekt - übernimm sie als Fakten.

Deine Aufgabe:
1. Prüfe gegen aktuelle Berichte, ob es EXTERNE Informationen gibt, die die
   Einschätzungen ändern würden (Verletzung, Trainerwechsel, Aufstellung,
   Formkrise/-hoch) - nur zu Spielern aus Kader und Transferzielen.
2. Schreibe einen Kurzreport (max. 150 Wörter): was ist HEUTE das wirklich
   relevante Thema, unter Berücksichtigung der Finanzlage im Zeitverlauf
   (nicht nur der Momentaufnahme) und der Zeit bis zum Saisonstart.
3. Wenn eine Einschätzung aus deiner Sicht falsch ist, sag es klar und mit
   Begründung/Quelle - aber widersprich nicht ohne externen Anlass.

Ignoriere Marktziele, die als nicht finanzierbar markiert sind, komplett
(sie sind hier bereits nicht enthalten).

Antworte ausschließlich im vorgegebenen JSON-Format."""

FLAG_SCHEMA = {
    "type": "object",
    "properties": {
        "report": {"type": "string"},
        "player_flags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "player_name": {"type": "string"},
                    "flag": {
                        "type": "string",
                        # momentum_* sind ausdrücklich MW-bezogen, in_form/
                        # out_of_form ausdrücklich sportlich - nicht vermischen
                        # (Trading-Spur vs. sportliche Spur, s. squad_analysis.py).
                        # description direkt am Enum-Feld, weil im ersten Test
                        # ein reiner MW-Tagesgewinn trotz Domänen-Block noch als
                        # "in_form" (sportlich!) geflaggt wurde - Prosa allein
                        # reichte nicht, das Schema muss es selbst erzwingen.
                        "description": (
                            "out/doubtful/rotation_risk/returning/in_form/out_of_form "
                            "beziehen sich AUSSCHLIESSLICH auf sportliche Leistung/Einsatz "
                            "(Verletzung, Aufstellung, Punkte). momentum_fading/"
                            "momentum_accelerating beziehen sich AUSSCHLIESSLICH auf "
                            "Marktwert-Bewegung (tfhmvt/sdmvt/momentum_ratio). Ein hoher "
                            "MW-Tagesgewinn ist momentum_accelerating, NIEMALS in_form."
                        ),
                        "enum": ["out", "doubtful", "rotation_risk", "returning",
                                "in_form", "out_of_form",
                                "momentum_fading", "momentum_accelerating"],
                    },
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "note": {"type": "string"},
                },
                "required": ["player_name", "flag", "confidence", "note"],
            },
        },
    },
    "required": ["report", "player_flags"],
}


def _team_context(player, strength_map, matcher, fixture_mode):
    team = player.get("team", "")
    if not team:
        return ""
    if fixture_mode == "odds":
        matched = matcher(team, list(strength_map.keys()))
        strength = strength_map.get(matched)
    else:
        from fixtures import team_strength_for
        strength = team_strength_for(team, strength_map)
    if strength is None:
        return team
    return f"{team}, Team-Stärke {strength:.0%} (Buchmacher-Einschätzung)"


def build_context(report, strength_map, fixture_mode, matcher, generated_at):
    """
    Baut die zusätzlichen Eingangsdaten für den Prompt (SPEC v2 Abschnitt 4) -
    verdichtete Finanzkennzahlen im Zeitverlauf statt nur dem nackten Budget,
    verdict_scope je Kaderspieler (was das Verdikt NICHT aussagt), gefilterte
    (nur finanzierbare) Marktziele.
    """
    kpis = report.get("kpis", {})
    squad = report.get("squad_classified", [])

    budget = kpis.get("budget", 0)
    daily_squad_yield = kpis.get("team_value_delta_24h", 0)
    trading_holds_yield = sum(s["tfhmvt"] for s in squad
                              if s["verdict"] == "HALTEN (Trading)")

    days_to_positive = None
    if budget < 0 and daily_squad_yield > 0:
        days_to_positive = round(abs(budget) / daily_squad_yield, 1)

    days_until_season = (SEASON_START_ESTIMATE - generated_at.date()).days

    team_financials = {
        "budget": budget,
        "squad_value": kpis.get("team_value", 0),
        "buying_power_33pct": kpis.get("capacity", 0),
        "daily_squad_yield": daily_squad_yield,
        "trading_holds_daily_yield": trading_holds_yield,
        "days_to_positive_budget_est": days_to_positive,
        "days_until_season_start_est": days_until_season,
    }

    squad_ctx = []
    for s in squad:
        squad_ctx.append({
            "name": s["name"], "team": s.get("team", ""), "pos": s["pos"],
            "verdict": s["verdict"],
            "verdict_reason": s["reasons"][0] if s["reasons"] else "",
            "verdict_scope": VERDICT_SCOPE.get(s["verdict"], ""),
            "sporting_core": s.get("sporting_core"),
            "team_context": _team_context(s, strength_map, matcher, fixture_mode),
            "tfhmvt": s["tfhmvt"],
            "momentum_ratio": s.get("momentum_ratio"),
        })

    # Nicht finanzierbare Ziele werden HIER herausgefiltert, nicht erst im
    # Prompt-Text erwähnt (SPEC v2: hartes Filterkriterium, keine Fußnote).
    market_targets = [
        {"name": m["name"], "team": m.get("team", ""), "pos": m["pos"],
         "score": m["score"], "headline": m["team_verdict"].split(" | ")[0]}
        for m in report.get("market", [])
        if m.get("affordable") and "KEIN BEDARF" not in m["team_verdict"]
    ]
    market_targets.sort(key=lambda x: -x["score"])

    return {
        "team_financials": team_financials,
        "squad": squad_ctx,
        "market_targets": market_targets[:10],
    }


def build_prompt(context):
    context_json = json.dumps(context, ensure_ascii=False, indent=2)
    return f"{DOMAIN_BLOCK}\n\nAKTUELLER REPORT (JSON):\n{context_json}\n\n{TASK_INSTRUCTIONS}"


# ---------- Gemini REST API (Muster aus test_gemini.py) ----------

def _list_models(api_key):
    r = requests.get(f"{BASE}/models", headers={"X-goog-api-key": api_key}, timeout=30)
    if r.status_code != 200:
        return []
    out = []
    for m in r.json().get("models", []):
        name = m.get("name", "").replace("models/", "")
        if "generateContent" in m.get("supportedGenerationMethods", []):
            out.append(name)
    return out


def _pick_model(available):
    # Preview-Modelle explizit ausschließen (SPEC v2 Abschnitt 7).
    stable = [n for n in available if "preview" not in n.lower()]
    for want in PREFERRED:
        for name in stable:
            if name.startswith(want):
                return name
    for name in stable:
        if "flash" in name and "image" not in name:
            return name
    return stable[0] if stable else None


def _call_gemini(prompt, api_key, model, retries=1):
    """
    Ein Retry bei 5xx (Live-Test zeigte wiederholte, transiente 503 Service
    Unavailable - Google-seitig, kein Fehler in unserer Anfrage). 4xx wird
    NICHT wiederholt (z.B. falscher Key/Schema-Fehler - würde beim Retry
    genauso scheitern).
    """
    import time as _time
    url = f"{BASE}/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": FLAG_SCHEMA,
            "temperature": 0.3,
        },
    }
    for attempt in range(retries + 1):
        r = requests.post(url, headers={"X-goog-api-key": api_key}, json=payload, timeout=60)
        if r.status_code >= 500 and attempt < retries:
            _time.sleep(2)
            continue
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)


def generate_insights(report, strength_map, fixture_mode, matcher, generated_at, api_key):
    """
    Liefert {"report": str, "player_flags": [...]} oder None (kein Key, kein
    Modell verfügbar, Netzwerk-/Parse-Fehler) - schlägt NIE hart fehl, die
    Pipeline in main.py läuft ohne KI-Schicht unverändert weiter.
    """
    if not api_key:
        return None
    try:
        models = _list_models(api_key)
        model = _pick_model(models)
        if not model:
            print("   ⚠️ KI-Schicht: kein passendes Gemini-Modell gefunden - übersprungen.")
            return None
        context = build_context(report, strength_map, fixture_mode, matcher, generated_at)
        prompt = build_prompt(context)
        result = _call_gemini(prompt, api_key, model)
        if "report" not in result or "player_flags" not in result:
            print("   ⚠️ KI-Schicht: unerwartete Antwortstruktur - übersprungen.")
            return None
        return result
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        print(f"   ⚠️ KI-Schicht übersprungen ({type(e).__name__}: {e})")
        return None
