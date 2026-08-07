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

Neuausrichtung (2026-07-31, SPEC_forecast_coach_scoring.md Punkt 2): der
vorherige Report referierte nur Zahlen, die im Dashboard ohnehin stehen -
Folge der vorigen Kontext-Anreicherung (zu viel Zahlenkontext -> das Modell
fasst zusammen statt beizutragen). Jetzt ausdrücklich umgekehrt: das Modell
bekommt die Zahlen nur als Hintergrund, darf sie aber nicht referieren,
sondern soll NUR externe Information beitragen (Verletzungen, Aufstellungen,
Spielverlaufs-Einordnung).
**Wichtige Einschränkung, live geprüft:** Gemini unterstützt Google-Search-
Grounding (`tools: [{"google_search": {}}]`) für echte Web-Recherche, aber
ein Testcall mit dem aktuellen (Free-Tier-)API-Key lieferte sofort
`429 RESOURCE_EXHAUSTED`, während derselbe Call ohne Grounding-Tool normal
funktioniert - Grounding hat offenbar ein eigenes, auf diesem Key nicht
freigeschaltetes Kontingent (vermutlich Billing-pflichtig). Deshalb OHNE
Grounding gebaut: das Modell arbeitet nur mit seinem Trainingsstand, nicht
mit Live-Daten. Für echte Aktualität (Verletzungsnews von heute) bräuchte es
entweder Billing auf dem Key (dann `tools` ergänzen) oder eigenes Abrufen
bekannter Quellen (Aufstellungsportale etc., größerer Umbau, nicht in dieser
Änderung enthalten).
"""

import json
import os
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
kickbase_color: die OFFIZIELLE Kickbase-Einsatzampel (blau=gesetzt, grün=wahrscheinlich Startelf, gelb=fraglich, rot=eher nicht, grau=keine Einschätzung/fällt aus). Beantwortet NUR "spielt er?", nicht "was tue ich mit ihm?" - das ist weiterhin das Verdikt. kickbase_color_conflict ist gesetzt, wenn beide auseinanderlaufen (z.B. grün, aber Verdikt VERKAUFEN) - das IMMER kommentieren, wenn vorhanden.
self_play_conflicts: bereits ALGORITHMISCH erkannte Fälle, in denen zwei eigene Spieler direkt gegeneinander antreten (Team A vs. Team B, beide im eigenen Kader) - nicht selbst suchen, nur einordnen/kommentieren, wenn das Array nicht leer ist.
lineup_gaps: unbesetzte Aufstellungsslots vor der 20:29-Deadline - das Dashboard zeigt das bereits als Warnung, hier nur relevant wenn du einen externen Grund/Vorschlag beisteuern kannst.
league_comparison: eigener Rang NACH PROGNOSE (my_rank_prognose) + Prognose-Abstand zum Spitzenreiter der Liga fürs kommende Wochenende (Modul 3, bereits berechnet) - kommentiere diese Zahlen, erfinde keine eigenen. Das ist eine Vorhersage, KEIN echter Tabellenstand - nie als bereits erspieltes Ergebnis formulieren.

VERHALTENSREGELN:
- Verwende die gelieferten Verdikte und Begründungen als gesetzt. Deine Aufgabe ist NICHT, sie neu zu berechnen, sondern zu prüfen, ob externe Informationen (Verletzung, Trainerwechsel, Bericht) etwas ändern.
- Ein einzelner niedriger Tageswert bei einem sonst stark steigenden Spieler ist ein Beobachtungs-, kein Alarmsignal - relativiere das im Report.
- Erwähne Marktziele, die als "nicht finanzierbar" markiert sind, NUR wenn explizit danach gefragt wird. Sonst weglassen (sie wurden hier bereits herausgefiltert).
- Bewerte die Budgetlage IMMER im Kontext des täglichen Kader-Ertrags und der Tage bis zum Saisonstart, nicht als isolierten Kontostand.
- Wiederhole KEINE Zahlen, Verdikte oder Kennzahlen aus dem Input - der Nutzer sieht sie bereits im Dashboard. Erwähne eine Zahl nur, wenn deine externe Erkenntnis ihr widerspricht.
- Wenn du zu einem Spieler nichts Externes/Neues zu sagen hast, lass ihn komplett weg. Ein kurzer Report mit drei belegten Erkenntnissen ist besser als zehn Sätze allgemeiner Einordnung."""

TASK_INSTRUCTIONS = """Du bekommst den fertigen Kickbase-Report meines Analyse-Skripts als JSON - NUR
als Hintergrund, nicht zum Referieren. Die Zahlen, Verdikte und die
Finanzlage zeigt das Dashboard dem Nutzer bereits deterministisch an. Dein
Beitrag ist das GEGENTEIL davon: alles, was NICHT in den Zahlen steht.

Feste Gliederung (SPEC_gebote_ki_team_KOMPLETT.md Punkt 2.2 - damit nichts
wegfällt). Bearbeite jeden Punkt, für den du mit deinem Wissensstand etwas
beitragen kannst - KEINE Live-Web-Recherche verfügbar, verlasse dich auf
deinen Trainingsstand und kennzeichne Unsicherheit statt zu erfinden. Ein
Punkt ohne Substanz entfällt im Report ersatzlos, statt ihn mit Zahlen aus
dem Input zu füllen:

1. SPIELTAGSBILD: Welche Partien mit eigenen Spielern stehen an, welche
   Begegnungen sind für den eigenen Kader entscheidend? Nicht "wer gewinnt"
   (das zeigen die Quoten bereits), sondern WIE: dominante Ballbesitz-
   mannschaft, tiefstehender Konter unter Druck, offener Schlagabtausch -
   das entscheidet, welche Positionen eher punkten (-> matchday_outlook).
2. BAUSTELLEN IM EIGENEN KADER:
   - Spieler mit wenig erwarteter Spielzeit (kickbase_color gelb/rot/grau)
   - self_play_conflicts IMMER kommentieren, wenn das Array nicht leer ist:
     zwei eigene Spieler spielen direkt gegeneinander, das ist bereits
     algorithmisch erkannt (nicht selbst suchen) - ordne ein, wem der
     Vorzug gebührt, falls dir dazu etwas Externes bekannt ist.
   - lineup_gaps (unbesetzte Aufstellungsslots) nur erwähnen, wenn du dazu
     einen konkreten externen Grund/Vorschlag hast (die Lücke selbst zeigt
     das Dashboard bereits als Warnung).
3. VERLETZUNGEN UND SPERREN: mit Rückkehr-Perspektive, wenn bekannt (wann
   voraussichtlich wieder einsatzbereit) - sonst weglassen statt zu raten.
4. TRANSFERLAGE: was an den market_targets heute wirklich relevant ist
   (externe Gründe FÜR/GEGEN einen Kauf), was nur Rauschen wäre.
5. MARKTDYNAMIK INSGESAMT: steigt oder fällt der Markt/die Liga insgesamt
   gerade breit, bilden sich irgendwo Blasen (auffällig viele stark
   steigende Spieler eines Teams/einer Position im Input)? Nur wenn aus dem
   JSON-Kontext ein Muster erkennbar ist oder dir dazu externes Wissen
   vorliegt - nicht spekulieren.
6. KONKURRENZVERGLEICH: league_comparison zeigt den Rang NACH PROGNOSE
   (my_rank_prognose) und den Prognose-Abstand zum Spitzenreiter für den
   KOMMENDEN Spieltag (Modul 3, bereits berechnet) - kommentiere DIESE
   Zahlen (z.B. woran der Rückstand liegen könnte, falls aus Kader/Flags
   ableitbar), erfinde keine eigenen. **Wichtig**: das ist eine PROGNOSE,
   KEIN echter Tabellenstand - schreibe niemals "du liegst auf Platz X"
   oder "X Punkte Rückstand" als wäre das ein bereits gespieltes Ergebnis
   (live gefunden: das Modell verwechselte die Prognosetabelle mit einer
   echten Ligatabelle vor dem allerersten Spieltag der Saison, wo es noch
   gar keinen echten Tabellenstand geben kann). Formuliere es explizit als
   Erwartung/Prognose ("nach Prognose liegst du vor dem Spieltag auf
   Rang X").

Deine Aufgabe:
1. Trage NUR bei, was du zusätzlich zum JSON-Kontext weißt oder ableiten
   kannst - keine Zusammenfassung des Inputs.
2. Schreibe einen Kurzreport (max. 150 Wörter, gerne kürzer wenn wenig
   Substanz vorliegt) - grob entlang der sechs Punkte, aber ohne
   Pflicht-Überschriften und ohne Punkte ohne Substanz aufzufüllen.
3. Wenn eine Einschätzung aus deiner Sicht falsch ist, sag es klar und mit
   Begründung/Quelle - aber widersprich nicht ohne externen Anlass.
4. Fülle player_flags NUR mit Spielern, zu denen du etwas Externes beiträgst.
5. Fülle matchday_outlook NUR, wenn du zum erwarteten Spielverlauf einer
   Partie mit eigenen Spielern etwas Konkretes beitragen kannst - sonst leer
   lassen, nicht raten.
6. Prüfe explizit jeden Spieler mit gesetztem kickbase_color_conflict -
   ein bereits erkannter Widerspruch zwischen offizieller Kickbase-
   Einsatzampel und unserem Verdikt, den IMMER kurz einordnen (welche
   Seite ist wahrscheinlicher richtig, falls einschätzbar).

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
        "matchday_outlook": {
            "type": "array",
            "description": ("NUR befüllen, wenn zum erwarteten Spielverlauf (nicht zum "
                            "Sieger - das zeigen die Quoten bereits) etwas Konkretes "
                            "bekannt ist. Sonst leeres Array, nicht raten."),
            "items": {
                "type": "object",
                "properties": {
                    "match": {"type": "string"},
                    "expected_script": {"type": "string"},
                    "beneficiary_positions": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["TW", "ABW", "MF", "ANG"]},
                    },
                    "reason": {"type": "string"},
                },
                "required": ["match", "expected_script", "beneficiary_positions", "reason"],
            },
        },
    },
    "required": ["report", "player_flags", "matchday_outlook"],
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


def build_context(report, strength_map, fixture_mode, matcher, generated_at, season_start_date=None):
    """
    Baut die zusätzlichen Eingangsdaten für den Prompt (SPEC v2 Abschnitt 4) -
    verdichtete Finanzkennzahlen im Zeitverlauf statt nur dem nackten Budget,
    verdict_scope je Kaderspieler (was das Verdikt NICHT aussagt), gefilterte
    (nur finanzierbare) Marktziele.

    season_start_date: echtes, live aus OpenLigaDB abgeleitetes Datum (2026-
    08-05 verifiziert, main.py/fixtures.get_season_start_date) - nur für die
    2. Bundesliga verfügbar. Ohne das (z.B. La Liga, football-data.org ohne
    Datumsfeld) fällt es auf die grobe SEASON_START_ESTIMATE-Schätzung zurück.
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

    if season_start_date is not None:
        days_until_season = (season_start_date.date() - generated_at.date()).days
    else:
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
        color = s.get("kickbase_color")
        # Punkt 4.3 (SPEC_lineup_verified.md): Widerspruch Kickbase-Farbe vs.
        # eigenes Verdikt geht explizit an die KI - die Farbe sagt "spielt
        # er?" (offizielle Kickbase-Einschätzung), das Verdikt "was tue ich
        # mit ihm?" (unsere Trading-/Sport-Logik). Beides kann auseinander-
        # laufen (z.B. MW fällt trotz gesetztem Startplatz).
        conflict = None
        if color in ("blau", "grün") and s["verdict"] == "VERKAUFEN":
            conflict = f"Kickbase stuft ihn als {color} ein (voraussichtlich im Kader), Skript empfiehlt aber VERKAUFEN"
        elif color in ("rot", "grau") and s["verdict"] == "STAMM":
            conflict = f"Kickbase stuft ihn als {color} ein (Einsatz fraglich/unwahrscheinlich), Skript sieht ihn aber als STAMM"
        squad_ctx.append({
            "name": s["name"], "team": s.get("team", ""), "pos": s["pos"],
            "verdict": s["verdict"],
            "verdict_reason": s["reasons"][0] if s["reasons"] else "",
            "verdict_scope": VERDICT_SCOPE.get(s["verdict"], ""),
            "sporting_core": s.get("sporting_core"),
            "team_context": _team_context(s, strength_map, matcher, fixture_mode),
            "tfhmvt": s["tfhmvt"],
            "momentum_ratio": s.get("momentum_ratio"),
            "kickbase_color": color,
            "kickbase_color_conflict": conflict,
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

    # Punkt 2.2 Thema 2: bereits algorithmisch erkannt (coach.
    # duel_hints_for_xi() in main.py, NUR innerhalb der echten Startelf,
    # SPEC_spieltagsmodell_v2.md 2.3), hier nur durchgereicht - die KI soll
    # es einordnen, nicht selbst suchen.
    self_play_conflicts = report.get("self_play_conflicts") or []

    lineup_status = report.get("lineup_status") or {}
    lineup_gaps = {
        "empty_slots": lineup_status.get("empty_slots", 0),
        "missing_positions": report.get("lineup_missing") or {},
    } if lineup_status.get("empty_slots") else None

    # Punkt 2.2 Thema 6: Brücke zu Modul 3 (league_teams, bereits sortiert
    # nach Prognose) - eigener Rang + Abstand zum Spitzenreiter, damit die KI
    # etwas Konkretes zum Kommentieren hat statt selbst zu rechnen.
    league_comparison = None
    league_teams = report.get("league_teams") or []
    my_uid = report.get("my_uid")
    if league_teams and my_uid is not None:
        my_entry = next((t for t in league_teams if str(t.get("uid")) == str(my_uid)), None)
        if my_entry:
            my_rank = league_teams.index(my_entry) + 1
            top_entry = league_teams[0]
            league_comparison = {
                # "_prognose"-Suffix bewusst statt "my_rank" (REVIEW_
                # architektur_KOMPLETT.md 2.9) - macht schon am Feldnamen
                # klar, dass das eine Vorhersage für den kommenden
                # Spieltag ist, kein echter, bereits erspielter Tabellenrang.
                "my_rank_prognose": my_rank,
                "of_managers": len(league_teams),
                "my_prognose": my_entry["prognose"],
                "top_manager": top_entry["name"],
                "top_prognose": top_entry["prognose"],
                "gap_to_top": round(top_entry["prognose"] - my_entry["prognose"], 1),
            }

    return {
        "team_financials": team_financials,
        "squad": squad_ctx,
        "market_targets": market_targets[:10],
        "self_play_conflicts": self_play_conflicts,
        "lineup_gaps": lineup_gaps,
        "league_comparison": league_comparison,
    }


def build_prompt(context):
    context_json = json.dumps(context, ensure_ascii=False, indent=2)
    return f"{DOMAIN_BLOCK}\n\nAKTUELLER REPORT (JSON):\n{context_json}\n\n{TASK_INSTRUCTIONS}"


# ---------- Gemini REST API (Muster aus test_gemini.py) ----------

# SPEC_ranking_faktoren_llm.md 6.1: Aufrufzähler - der gemeldete 429 RPM
# widersprach der Annahme "ein gebündelter Call je Liga" ("erste Maßnahme,
# vor jeder Anbieterdiskussion: Aufrufe je Lauf zählen und protokollieren").
# Jeder tatsächliche HTTP-Request (inkl. Retries) zählt einzeln, getrennt
# nach Modell-Liste/generateContent. `call_summary()` wird am Laufende in
# main.py ausgegeben.
_call_log = []


def _log_call(kind, status_code):
    _call_log.append({"kind": kind, "status_code": status_code})


def call_summary():
    """Zusammenfassung aller in diesem Prozess bisher getätigten Gemini-
    HTTP-Requests (SPEC_ranking_faktoren_llm.md 6.1)."""
    by_kind = {}
    for c in _call_log:
        by_kind[c["kind"]] = by_kind.get(c["kind"], 0) + 1
    return {"total": len(_call_log), "by_kind": by_kind, "calls": list(_call_log)}


# Modul-globaler Cache (SPEC 6.1): `_list_models()` lieferte bisher pro Liga
# einen eigenen Request, obwohl sich die verfügbaren Modelle innerhalb
# desselben Prozesslaufs nicht ändern - bei 2 Ligen unnötig verdoppelt.
_models_cache = None


def _list_models(api_key):
    global _models_cache
    if _models_cache is not None:
        return _models_cache
    r = requests.get(f"{BASE}/models", headers={"X-goog-api-key": api_key}, timeout=30)
    _log_call("list_models", r.status_code)
    if r.status_code != 200:
        return []
    out = []
    for m in r.json().get("models", []):
        name = m.get("name", "").replace("models/", "")
        if "generateContent" in m.get("supportedGenerationMethods", []):
            out.append(name)
    _models_cache = out
    return out


# Nicht-Chat-Modelle, die "flash" im Namen tragen können und die generische
# Fallback-Suche unten sonst fälschlich träfen (SPEC_lernzyklus.md 6.2 -
# live beobachtet: das Modellangebot enthält inzwischen TTS-, Bild-, Robotik-
# und Recherche-Varianten, die alle "-preview" tragen und schon über den
# EXCLUDE_MARKERS-Check rausfallen, hier zusätzlich als Verteidigungslinie).
EXCLUDE_MARKERS = ("preview", "-tts", "-image", "computer-use", "robotics",
                  "lyria", "nano-banana", "deep-research", "antigravity")


def _pick_model(available):
    """
    SPEC_lernzyklus.md 6.2: der Verdacht war ein automatisch gewähltes
    Vorschaumodell (`gemini-3-flash-preview`) mit engerem Kontingent als die
    stabilen Produktivmodelle. Live gegengeprüft (2026-08-05, echte
    Modell-Liste mit 42 Einträgen inkl. gemini-3.x/3.5/3.6-Generationen):
    `_pick_model()` wählt korrekt `gemini-flash-latest` (kein Preview-Name),
    der Bug reproduzierte sich mit dem aktuellen Code NICHT - trotzdem
    zusätzliche Härtung, weil das Modellangebot sich sichtbar schnell
    weiterentwickelt (neue Generationen, neue Nicht-Chat-Varianten).
    """
    stable = [n for n in available if not any(x in n.lower() for x in EXCLUDE_MARKERS)]
    for want in PREFERRED:
        for name in stable:
            if name.startswith(want):
                return name
    for name in stable:
        if "flash" in name:
            return name
    return stable[0] if stable else None


QUOTA_STATE_PATH = os.path.join("data", "llm_factors", "quota_state.json")

# Modul-globaler Merker (SPEC_lernzyklus.md 6.3 "Tageslimit merken") - beide
# Ligen laufen im selben main.py-Prozess nacheinander; wird RPD bei Liga 1
# erkannt, braucht Liga 2 es gar nicht erst zu versuchen (spart Kontingent,
# das laut Spec beim Reset ohnehin erst um ~09:00 MESZ wieder verfügbar ist).
_rpd_exhausted_today = False


def _classify_429(error_text):
    """
    SPEC 6.2: 429 hat drei grundverschiedene Auslöser mit grundverschiedener
    Behandlung - der Fehlertext aus dem Antwortkörper muss ausgewertet
    werden, der Statuscode allein reicht nicht.
    """
    t = (error_text or "").lower()
    if "quota" in t and ("day" in t or "daily" in t or "perday" in t):
        return "rpd"
    if "token" in t:
        return "tpm"
    if "rate limit" in t or "per minute" in t or "requests" in t:
        return "rpm"
    return "unknown"


def _check_daily_quota_marker():
    """Persistierter Tagesmerker (SPEC 6.3) - überlebt auch einen neuen
    main.py-Prozess am selben Tag (z.B. ein manueller Re-Run)."""
    global _rpd_exhausted_today
    if _rpd_exhausted_today:
        return True
    if not os.path.exists(QUOTA_STATE_PATH):
        return False
    try:
        with open(QUOTA_STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
        if state.get("rpd_exhausted_date") == date.today().isoformat():
            _rpd_exhausted_today = True
            return True
    except (json.JSONDecodeError, OSError):
        pass
    return False


def _set_daily_quota_marker():
    global _rpd_exhausted_today
    _rpd_exhausted_today = True
    os.makedirs(os.path.dirname(QUOTA_STATE_PATH), exist_ok=True)
    with open(QUOTA_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"rpd_exhausted_date": date.today().isoformat()}, f)


def _call_gemini(prompt, api_key, model, retries=3):
    """
    SPEC_lernzyklus.md 6.1/6.3: liefert IMMER ein Diagnose-Dict statt zu
    werfen oder die Fehlerantwort zu verwerfen - "Kontingent oder API-Fehler"
    fasste bisher völlig verschiedene Fälle zusammen, jede Ursachenanalyse
    war Raten. Jetzt: Statuscode, Fehlertext, finishReason, Token-Zahlen,
    Modell werden immer mitgeliefert.

    Retry mit wachsender Wartezeit + Zufallsstreuung (5s/15s/45s ± Jitter)
    bei 503 und bei 429 vom Typ RPM/TPM. **Kein Retry bei RPD** (Tages-
    kontingent) - das verbraucht nur weiteres Kontingent, hilft aber nicht;
    stattdessen wird der Tagesmerker gesetzt. 400 wird ebenfalls wiederholt
    (im Testlauf mehrfach beobachtet: derselbe Payload scheitert mal
    transient Google-seitig, ohne erkennbare Payload-Ursache). Andere 4xx
    (401/403/404) werden NICHT wiederholt - echte Konfigurationsfehler.
    """
    import random
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
    backoffs = [5, 15, 45]
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, headers={"X-goog-api-key": api_key}, json=payload, timeout=60)
        except requests.RequestException as e:
            _log_call("generate_content", None)
            last = {"ok": False, "status_code": None, "error_text": str(e),
                    "finish_reason": None, "tokens_in": None, "tokens_out": None,
                    "model": model, "quota_kind": None}
            if attempt < retries:
                _time.sleep(backoffs[min(attempt, len(backoffs) - 1)] + random.uniform(0, 2))
                continue
            return last
        _log_call("generate_content", r.status_code)

        if r.status_code == 200:
            body = r.json()
            usage = body.get("usageMetadata", {})
            candidates = body.get("candidates") or [{}]
            finish_reason = candidates[0].get("finishReason")
            diag = {"ok": True, "status_code": 200, "model": model,
                   "finish_reason": finish_reason,
                   "tokens_in": usage.get("promptTokenCount"),
                   "tokens_out": usage.get("candidatesTokenCount")}
            try:
                text = candidates[0]["content"]["parts"][0]["text"]
                diag["data"] = json.loads(text)
            except (KeyError, IndexError, ValueError, json.JSONDecodeError):
                # Teilausgabe verwerten (SPEC 6.3): manchmal ist der Text da,
                # aber nicht valides JSON (z.B. bei finishReason=MAX_TOKENS
                # mitten im Satz abgeschnitten) - dann lieber nichts
                # erzwingen als raten, aber die Diagnose bleibt vollständig.
                diag["data"] = None
                diag["error_text"] = f"Antwort nicht als JSON parsebar (finishReason={finish_reason})"
            return diag

        error_text = r.text[:800]
        quota_kind = _classify_429(error_text) if r.status_code == 429 else None
        last = {"ok": False, "status_code": r.status_code, "error_text": error_text,
               "finish_reason": None, "tokens_in": None, "tokens_out": None,
               "model": model, "quota_kind": quota_kind}

        if r.status_code == 429 and quota_kind == "rpd":
            _set_daily_quota_marker()
            return last  # kein Retry - würde nur weiteres Kontingent verbrauchen

        retryable = r.status_code in (400, 500, 502, 503, 504) or (r.status_code == 429 and quota_kind != "rpd")
        if retryable and attempt < retries:
            _time.sleep(backoffs[min(attempt, len(backoffs) - 1)] + random.uniform(0, 2))
            continue
        return last
    return last


def generate_insights(report, strength_map, fixture_mode, matcher, generated_at, api_key,
                      season_start_date=None):
    """
    Liefert (insights, diagnostics). `insights` ist {"report": str,
    "player_flags": [...]} oder None (kein Key, kein Modell, Fehler nach
    allen Retries) - schlägt NIE hart fehl, die Pipeline in main.py läuft
    ohne KI-Schicht unverändert weiter. `diagnostics` (SPEC 6.1/6.4) trägt
    IMMER die Ursache, auch bei Erfolg (Modell/Token für die
    Verbrauchsprotokollierung, SPEC 6.3).
    """
    if not api_key:
        return None, {"status": "no_key"}
    if _check_daily_quota_marker():
        return None, {"status": "rpd_exhausted",
                      "message": "Tageskontingent laut vorherigem Aufruf heute bereits erschöpft - "
                                "kein neuer Versuch (Reset ca. 09:00 MESZ)"}
    try:
        models = _list_models(api_key)
        model = _pick_model(models)
        if not model:
            return None, {"status": "no_model", "message": "kein passendes Gemini-Modell gefunden"}
        context = build_context(report, strength_map, fixture_mode, matcher, generated_at,
                                season_start_date=season_start_date)
        prompt = build_prompt(context)
        print(f"   (Prompt: {len(prompt):,} Zeichen, Modell {model})")
        diag = _call_gemini(prompt, api_key, model)
        print(f"   (Modell {diag.get('model')} · Tokens {diag.get('tokens_in')}→"
              f"{diag.get('tokens_out')} · finishReason {diag.get('finish_reason')})")
        if not diag.get("ok"):
            print(f"   ⚠️ Gemini {diag.get('status_code')}: {(diag.get('error_text') or '')[:300]}")
            return None, {"status": "error", **diag}
        result = diag.get("data")
        if not result or "report" not in result or "player_flags" not in result:
            return None, {"status": "bad_response", **diag}
        return result, {"status": "ok", **diag}
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        print(f"   ⚠️ KI-Schicht übersprungen ({type(e).__name__}: {e})")
        return None, {"status": "exception", "message": f"{type(e).__name__}: {e}"}
