"""
Testet den Gemini-Zugang fuer die KI-Schicht.

Prueft in einem Durchlauf:
1. Ist der API-Key gueltig?
2. Welche Modelle stehen zur Verfuegung? (Namen aendern sich - nicht raten)
3. Funktioniert erzwungenes JSON (responseSchema)?
4. Wie gut ist die Antwort bei einem realistischen Kickbase-Prompt?

VORHER: pip install requests
DANN:   python test_gemini.py

Key oben eintragen. Kein SDK noetig, laeuft ueber die REST-API.
"""

import json
import requests
from config import GEMINI_API_KEY as API_KEY

BASE = "https://generativelanguage.googleapis.com/v1beta"

# Wird unten automatisch aus der Modellliste gewaehlt, falls vorhanden.
PREFERRED = ["gemini-3-flash", "gemini-3.5-flash", "gemini-2.5-flash",
             "gemini-2.5-flash-lite"]


# ---------- 1 + 2: Key pruefen und Modelle listen ----------
def list_models():
    r = requests.get(f"{BASE}/models",
                     headers={"X-goog-api-key": API_KEY}, timeout=30)
    if r.status_code != 200:
        print(f"FEHLER {r.status_code}: {r.text[:400]}")
        return []
    models = []
    for m in r.json().get("models", []):
        name = m.get("name", "").replace("models/", "")
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" in methods:
            models.append(name)
    return models


def pick_model(available):
    for want in PREFERRED:
        for name in available:
            if name.startswith(want):
                return name
    # Fallback: irgendein Flash-Modell
    for name in available:
        if "flash" in name and "image" not in name:
            return name
    return available[0] if available else None


# ---------- 3 + 4: echter Call mit erzwungenem JSON ----------
SCHEMA = {
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
                        "enum": ["out", "doubtful", "rotation_risk",
                                 "returning", "in_form", "out_of_form"],
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "note": {"type": "string"},
                },
                "required": ["player_name", "flag", "confidence", "note"],
            },
        },
    },
    "required": ["report", "player_flags"],
}

PROMPT = """Du bist Analyst fuer einen Kickbase-Manager (Fantasy-Fussball, 2. Bundesliga).

Hier der aktuelle Stand aus dem Analyse-Skript:

KADER (Auszug):
- Wahl (Wolfsburg, ABW) - Verdikt STAMM, MW 7,5 Mio, +15k/Tag, fit, Einsatz sicher
- Pieringer (Karlsruhe, ANG) - Verdikt BEOBACHTEN, MW 4,2 Mio, Anstieg flacht ab (Ratio 0,32)
- Klein (Bielefeld, MF) - Verdikt VERKAUFEN, MW 2,0 Mio, -43k/Tag, angeschlagen

TRANSFERZIELE:
- Reese (Wolfsburg, ANG) - frei auf dem Markt, MW 25,1 Mio, Score 84
- Zukowski (Magdeburg, ABW) - gehoert Mitspieler "Luke", MW 19,1 Mio

BUDGET: -20,2 Mio, Kaufkraft 18,3 Mio

Aufgabe:
1. Schreibe einen Kurzreport (MAXIMAL 150 Woerter, knapp und direkt, kein Aufsatz,
   keine Wiederholung der Zahlen von oben) - was ist heute das eigentliche Thema?
2. Markiere auffaellige Spieler mit einem Flag.

Antworte ausschliesslich im vorgegebenen JSON-Format."""


def generate(model):
    url = f"{BASE}/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": PROMPT}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": SCHEMA,
            "temperature": 0.3,
        },
    }
    r = requests.post(url, headers={"X-goog-api-key": API_KEY},
                      json=payload, timeout=60)
    if r.status_code != 200:
        print(f"FEHLER {r.status_code}: {r.text[:600]}")
        return None
    data = r.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        print("Unerwartete Antwortstruktur:")
        print(json.dumps(data, ensure_ascii=False, indent=2)[:1500])
        return None
    usage = data.get("usageMetadata", {})
    return text, usage


if __name__ == "__main__":
    if not API_KEY:
        print("GEMINI_API_KEY in config.py leer (aistudio.google.com -> Get API key).")
        raise SystemExit

    print("=== 1./2. Verfuegbare Modelle ===")
    models = list_models()
    if not models:
        print("Keine Modelle erhalten - Key pruefen.")
        raise SystemExit
    for name in models[:25]:
        print(f"   {name}")
    if len(models) > 25:
        print(f"   ... und {len(models) - 25} weitere")

    model = pick_model(models)
    print(f"\n   -> verwendet: {model}")

    print("\n=== 3./4. Test-Call mit erzwungenem JSON ===")
    result = generate(model)
    if not result:
        raise SystemExit
    text, usage = result

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        print("Antwort war kein gueltiges JSON:")
        print(text[:1000])
        raise SystemExit

    print("\n--- KURZREPORT ---")
    report = parsed.get("report", "")
    print(report)
    print(f"\n   ({len(report.split())} Woerter - Ziel: unter 150)")

    print("\n--- FLAGS ---")
    for f in parsed.get("player_flags", []):
        print(f"   {f.get('player_name'):<14} {f.get('flag'):<14} "
              f"{f.get('confidence'):<7} {f.get('note')}")

    print("\n--- VERBRAUCH ---")
    print(f"   Input:  {usage.get('promptTokenCount')} Token")
    print(f"   Output: {usage.get('candidatesTokenCount')} Token")
    print(f"   Gesamt: {usage.get('totalTokenCount')} Token")

    print("\nFertig. Wenn der Report brauchbar aussieht, kann llm_insights.py "
          "auf dieses Muster aufsetzen.")
