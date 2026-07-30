"""
Vorlage für config.py. Lokal: kopieren nach config.py und Werte eintragen
(config.py wird NIE committet, steht in .gitignore).
In GitHub Actions: config_template.py wird 1:1 nach config.py kopiert - die
Secrets kommen dann über Umgebungsvariablen (GitHub Secrets) rein, siehe
.github/workflows/briefing.yml.
"""

import os


def _env(name):
    # .strip(): GitHub-Secrets enthalten manchmal einen versehentlich
    # mitkopierten Zeilenumbruch/Leerzeichen - das lässt den Kickbase-Login
    # mit err:1 AccessDenied scheitern, obwohl der sichtbare Wert stimmt.
    return os.environ.get(name, "").strip()


EMAIL = _env("KICKBASE_EMAIL")
PASSWORD = _env("KICKBASE_PASSWORD")

# Mehrere Ligen: das Briefing läuft nacheinander über alle Einträge.
# Spielplan/Gegnerstärke-Kaskade (main.load_fixture_data):
#   1. odds_div          - football-data.co.uk fixtures.csv, kostenlos & keylos (primär)
#   2. odds_api_sport     - the-odds-api.com, kostet Kontingent (Fallback wenn 1 leer ist,
#                           z.B. tief in der Sommerpause)
#   3. fixture_source     - Tabellen-basierter Fallback (openligadb/football-data/thesportsdb)
LEAGUES = [
    {
        "name": "1899",            # Liga-Name wie in der Kickbase-App
        "competition_id": 2,        # 2 = 2. Bundesliga
        "odds_div": "D2",
        "odds_api_sport": "soccer_germany_bundesliga2",
        "fixture_source": "openligadb",
        "openligadb_shortcut": "bl2",
        "season": "2026",
    },
    {
        "name": "WirSchaffenStudium!!!",  # Liga-Name wie in der Kickbase-App
        "competition_id": 3,             # UNVERIFIZIERT: La-Liga-ID ggf. anpassen
        "odds_div": "SP1",
        "odds_api_sport": "soccer_spain_la_liga",
        "fixture_source": "football-data",
        "football_data_competition": "PD",  # PD = Primera División
        "season": "2026",
    },
]

FOOTBALL_DATA_API_KEY = _env("FOOTBALL_DATA_API_KEY")  # optional, La-Liga-Fallback

# the-odds-api.com Free Tier (500 Requests/Monat). Nur Fallback-Nutzung (s.o.),
# max. 1 Request pro Liga pro Lauf. Outright-/Meisterquoten NICHT verfügbar
# für D2/La Liga auf diesem Tier (geprüft: INVALID_MARKET_COMBO) - daher kein
# zusätzliches Team-Power-Signal, nur Ersatz für die regulären Spielquoten.
ODDS_API_KEY = _env("ODDS_API_KEY")

# Scoring-Gewichte (Summe = 1.0). Ausgewogen: Trading & Punkte Hand in Hand.
WEIGHTS = {
    "value_efficiency": 0.22,
    "momentum": 0.26,
    "availability": 0.20,
    "fixtures": 0.14,
    "form": 0.18,
}

# Passwort-Sperre für die HTML-Briefing-Seite (GitHub Pages ist öffentlich
# erreichbar!). Kein echter Schutz (Client-seitiges JS, SHA-256-Hash liegt im
# Quelltext) - reicht nur gegen zufälliges Finden der URL. Leer lassen = keine
# Sperre (z.B. für lokale Vorschau).
PAGE_PASSWORD = _env("PAGE_PASSWORD")
