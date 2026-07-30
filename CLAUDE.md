# Kickbase-Zentrale — Projekt-Briefing für Claude Code

## Wer / Wofür

Christian spielt Kickbase (Fantasy-Fußball-Manager-App) in zwei Ligen:
- **2. Bundesliga**, Liga-Name `"1899"`, 11 Mitspieler — **die wichtige Liga**
- **La Liga**, 5 Mitspieler (Liga-Name muss noch in config eingetragen sein)

Ziel: Eine "Kickbase-Zentrale" — ein Python-Tool, das täglich ein fundiertes Briefing liefert und ihn zum bestinformierten Manager seiner Ligen macht. Alle Empfehlungen müssen **faktenbasiert und transparent begründet** sein (keine Blackbox-Scores). Endausbau: täglich automatisch (GitHub Actions) + Zustellung per E-Mail aufs Handy. Aktuell läuft alles lokal via `python main.py` (Windows, PowerShell, Ordner ehemals `F:\Downloads\kickbase-zentrale` bzw. `C:\Users\chris\Desktop\Kickbase App`).

## Kickbase-Spielmechanik (Basis aller Logik — recherchiert & vom User bestätigt)

- **Marktwert (MW)** wird täglich um **22:00 Uhr** aktualisiert. Treiber ist Community-Verhalten (Käufe/Verkäufe), Form, erwartete Einsatzzeit — nicht direkt die Punkte.
- **Transfers werden beim 22-Uhr-Update abgewickelt.** Liegt ein Gebot dann unter dem neuen MW, ist es ungültig → deshalb **Overpay-Puffer** über dem erwarteten 22-Uhr-MW bieten. Gebote >10% unter MW sind nicht abgebbar.
- **Verkauf:** Angebote bringen ~90–100% des MW; Sofortverkauf zum aktuellen MW.
- **Kreditregel (vom User exakt spezifiziert, ist so implementiert):** Man darf bis zu **33% des NETTO-Teamwerts** ins Minus. Basis = Kaderwert + Budget (negatives Budget senkt die Basis!). Beispiel: Kader 136M, Budget −20M → Basis 116M → max. Schulden 38,28M → 20M schon verbraucht → **echte Kaufkraft 18,28M**. Formel: `capacity = 0.33 * (squad_value + budget) + budget`.
- **Punktesystem:** positionsabhängige Scorer-Punkte (Tor ABW 100 > MF 90 > ANG 80) + Rohpunkte (Pässe, Zweikämpfe) → "Punktetyp" (stabiler Rohpunkte-Floor vs. volatiler Scorer) ist perspektivisch relevant, noch nicht implementiert.
- Aufstellungs-Deadline 20:29 Uhr; Punkteprämien ab 1000/1500/2000 Punkten pro Spieltag.
- Saisonkontext: aktuell **Saisonvorbereitung 2026/27** (Ende Juli 2026). Das hat Folgen: Kickbase' `mdsum` enthält nur beendete Vorsaison-Spiele, Tabellen der neuen Saison sind leer, viele MW-Historien beginnen erst ~Mitte Juli.

## Kickbase-API v4 (inoffiziell, per HAR-Mitschnitt der iOS-App verifiziert)

Base: `https://api.kickbase.com`. Referenz-Doku: github.com/kevinskyba/kickbase-api-doc (unvollständig).

**Login (verifiziert, funktioniert):**
- `POST /v4/user/login` mit Body `{"rep": {}, "pass": PW, "ext": true, "em": EMAIL, "loy": false}`
- Token im Response-Feld `"tkn"`, danach `Authorization: Bearer <tkn>`
- **User-Agent ist Pflicht:** `Kickster/4.8.0/8776 (iPhone; iOS 26.5.2; Scale/3.00)` — die App heißt intern "Kickster". Ohne app-ähnlichen UA kommt `err:5 ClientTooOld`, mit falschem Payload `err:1 AccessDenied`. Fehler kommen mit **HTTP 200** und `{"err": N, "errMsg": "..."}` im Body!
- Token-Lebensdauer: **1 Stunde** (JWT iat/exp).
- Login-Account: Apple-PrivateRelay-Mail (war Sign-in-with-Apple, Passwort nachträglich gesetzt). Credentials in `config.py` (nicht committen!).

**Verifizierte Endpoints (aus HAR):**
- `GET /v4/leagues` → Ligen-Liste unter `lins` (Feld `n`=Name, `i`=ID). Seine 2.-Liga-Liga-ID: 5348134.
- `GET /v4/leagues/{id}/me` → `b` Budget (kann negativ sein), `mppu` max. Kadergröße (20), `tpc` Spieler pro Verein (Vereinslimit), `mgc` Mitgliederzahl, `cpi` Competition-ID ("2" = 2. Bundesliga).
- `GET /v4/leagues/{id}/market` → `it`-Array. Felder pro Spieler: `i` ID, `fn`/`n` Name, `tid` Team-ID, `pos` (1 TW, 2 ABW, 3 MF, 4 ANG), `mv`, `ap` Punkteschnitt, `p` Gesamtpunkte, `prc` Preis, `exs` Restlaufzeit Sekunden, `prob`, `dt`. **`u`-Key nur vorhanden, wenn ein Mitspieler den Spieler gelistet hat** (mit dessen Name/ID) → Filter `'u' not in p` = echte Kickbase-Auktionen.
- `GET /v4/leagues/{id}/players/{pid}` → Goldgrube: `tfhmvt` = **echte 24h-MW-Änderung als Feld** (Chart-Abfrage unnötig), `st` Fitness-Status (0 fit, 2 angeschlagen/verletzt) + `stxt` Klartext ("Rückenprobleme – trainiert individuell"), `prob` Einsatz-Indikator (beobachtet 1–5, **niedriger = besser**), `mdsum` letzte/kommende Spiele mit Team-IDs und `mdst` (2=beendet), `tn` Teamname, `tid`, `cv`, `mv`.
- `GET /v4/competitions/{cid}/players/{pid}/marketValue/92?leagueId={lid}` → MW-Historie: `{"it": [{"dt": <Tagesnummer>, "mv": <float>}], "hmv": <Allzeithoch>}`. Führende `mv: 0.0`-Einträge sind Padding (vor Tracking-Beginn) und müssen gefiltert werden.
- `GET /v4/leagues/{id}/ranking?dayNumber=N` existiert (Response nicht näher analysiert).
- `GET /v4/leagues/{id}/squad` → **verifiziert (2026-07-30)**, live geprüft: liefert `{"it": [...], "mppu": ...}`, `it` = Kaderspieler-Array (gleiche Felder wie Markt-Items). Die beiden anderen Kandidaten (`/lineupex` leer, `/teamcenter/myeleven` liefert andere Struktur `lp/nlp/p/pa/lpc/clpc/cpte` — ungenutzt) bleiben als Fallback im Code, greifen aber nicht mehr.
- `GET /v4/leagues/{id}/players/{pid}` liefert zusätzlich `ph` (Punkte je Spieltag, chronologisch bis zum aktuellen `day`, `{"hp": bool, "p": int}`) und `mdsum`-Einträge mit `day`-Feld → über den gemeinsamen `day`-Index lässt sich pro Spieltag Punkte + Sieg/Niederlage/Unentschieden des Spielerteams rekonstruieren (Basis für `scoring.player_reliability_profile`). Funktioniert auch in der Sommerpause, da beide Felder die letzten Spiele der Vorsaison zeigen.

**Unverifizierte Endpoints (Kandidaten im Code, scheitern defensiv):**
- Activity-Feed (für empirisches Overpay-Lernen aus echten Liga-Transfers): probiert `/activitiesFeed`, `/feed`. Unverifiziert.
- Antworten sind teils **Brotli-komprimiert** (`Content-Encoding: br`) — requests handhabt das normal automatisch; bei HAR-Auswertung manuell dekomprimieren.
- Rate-Limiting: zwischen Spieler-Detail-Calls `time.sleep(0.25)`.

## Architektur (Module in VS Code sichtbar)

- `config.py` (lokal, mit echten Credentials) / `config_template.py`: `LEAGUES`-Liste (je Liga: name, competition_id, odds_div, fixture_source + Quell-Parameter), `WEIGHTS` (Scoring-Gewichte), `FOOTBALL_DATA_API_KEY` (optional, ungenutzt solange Quoten laufen).
- `kickbase_api.py`: API-Client (Login, Ligen, /me, Markt, Spieler-Details, MW-Historie, Kader- und Feed-Kandidaten).
- `odds.py`: **primäre Datenquelle für Spielplan & Teamstärke.** Lädt football-data.co.uk/fixtures.csv (kostenlos, keylos; anstehende Spiele mit Bet365-Quoten; Div `D2` = 2. BL, `SP1` = La Liga). Rechnet Quoten in implizite Sieg-WKs um (Marge normiert), baut daraus (a) pro Team die nächsten Gegner mit Sieg-WK und (b) Team-"Power" = min-max-normierte Ø-Sieg-WK. **Buchmacher definieren die Topteams, nicht die Vorsaison-Tabelle** (User-Feedback: Hertha war fälschlich Topteam).
- `fixtures.py`: Fallback-Quellen, wenn fixtures.csv leer (tiefe Sommerpause): OpenLigaDB (2. BL: Tabelle mit Vorsaison-Fallback + kompletter Saison-Spielplan) und TheSportsDB (La Liga, keylos, Liga-ID 4335 — **noch nie live getestet**). Fuzzy-Teamnamen-Matching (`_best_match`, SequenceMatcher + Substring), da Kickbase "Kiel" schreibt und Quellen "Holstein Kiel".
- `scoring.py`: 5-Komponenten-Score 0–100, Komponenten einzeln ausgewiesen: Preis-Leistung (Punkte/Mio MW), MW-Momentum (`tfhmvt`/mv, ±1,5%/Tag = Extreme), Einsatz-WK (Status × prob), Spielplan (Ø Sieg-WK), Form. Dazu `sporting_core` = Score ohne Momentum (Basis der Kader-Verdikte) und **Datenlage-Erkennung**: Spieler ohne `ap` UND `p` (Ligawechsler ohne Kickbase-Historie) werden nicht bestraft — Form wird aus MW geschätzt (`mv_implied_form`: 1M→0, 10M+→1), Preis-Leistung neutral 0.5, Kennzeichnung "[⚠️ keine Punktehistorie – Form aus MW geschätzt]".
- `bid_advisor.py`: Gebotsempfehlung. Erwarteter 22-Uhr-MW = mv + max(tfhmvt, 0); Puffer = 3% × Aggressivität; **Aggressivität dynamisch nach Transfer-Stärke** (`0.55 + score/100 × 1.1` → Score 30 ≈ 2,6% Puffer, Score 90 ≈ 4,6%) — explizite User-Anforderung. Zuschlags-WK ist eine **grobe Heuristik**, keine Statistik. `learn_league_overpay()` würde Median-Overpay aus dem Activity-Feed lernen (blockiert durch unverifizierten Endpoint).
- `squad_analysis.py`: Kader-Verdikte mit **fester Hierarchie** (mehrfach nach User-Feedback korrigiert): 1. **STAMM** hat Vorrang — sporting_core ≥ 0.55 (0.60 ohne Punktehistorie) + fit + prob ≤ 2 → Stamm, egal was der MW macht (fallender MW wird nur als Info genannt: "bei Stammspielern kein Verkaufsgrund"). 2. **HALTEN (Trading)** nur wenn kein Stamm-Fall, aber MW ≥ +0,8%/Tag ("reines Wert-Investment" — z.B. Spieler der 200k/Tag steigt aber nicht spielt: HALTEN, niemals Verkauf wegen "Einsatz fraglich"!). 3. **BEOBACHTEN** (Anstieg flacht ab → Verkaufsfenster naht). 4. **VERKAUFEN** nur wenn MW fällt/stagniert UND sportlich schwach.
  `market_vs_squad` (2026-07-30 grundlegend überarbeitet nach User-Feedback: "Trading-Eignung und sportliche Eignung sind zwei getrennte Fragen, nicht eine Score-Zahl"): jeder Marktspieler bekommt **zwei unabhängige Spuren**, beide im `team_verdict`-Text ausgewiesen: (a) **Trading-Spur** (`_trading_assessment`) — Vergleich der MW-Momentum (%/Tag) NUR gegen die eigenen `HALTEN (Trading)`-Spieler, konkret gegen den am schwächsten steigenden; Stämme zählen hier nicht mit. Kein Trading-Hold im Kader → Standardschwelle (`STRONG_RISE`) als Referenz. (b) **Sportliche Spur** (`_sporting_assessment`) — Vergleich über `sporting_core` (momentum-frei!) + Punkteschnitt (`ap`), primär gegen Kaderspieler auf derselben Position, positionsübergreifend nur bei großem Mehrwert (`BIG_UPSIDE_CORE`/`BIG_UPSIDE_AP_GAP`, da Kickbase-Formationen Positionen fest vorschreiben). Dazu `scoring.player_reliability_profile`/`punktetyp_label` ("Punktetyp"-Signal aus `ph`+`mdsum`, s.o.): Rohpunkte-Typ (punktet auch bei Niederlagen ordentlich) vs. Scorer-Typ (Punkte hängen am Sieg) — beantwortet explizit "lohnt sich der Kauf trotz schwachem Spielplan/niedriger Sieg-WK am Wochenende". Headline-Priorität bei fehlendem freiem Kaderplatz: sportliches UPGRADE > TRADING-UPGRADE > informativ "kein Zwang" > KEIN BEDARF — beide Spuren werden aber immer im Text gezeigt, auch wenn nur eine die Headline stellt (Trading und Punkte sind laut User gleichwertig, s.u.). Trading-Holds und Stämme sind als Verkaufsziel NICHT ersetzbar (tauchen nur als "kein Zwang" auf). Kaufkraft nach 33%-Regel (s.o.), bei Upgrades nach Verkauf neu gerechnet. Schwellen (`MIN_CORE_GAP` 0.08, `MIN_AP_GAP` 8, `BIG_UPSIDE_CORE` 0.65, `BIG_UPSIDE_AP_GAP` 15, `RELIABLE_RATIO` 0.6 in scoring.py) sind Erstkalibrierung, noch nicht am echten Output gegengeprüft.
- `html_report.py` (neu, 2026-07-30): rendert die main.py-Ergebnisse als eine selbstständige `site/index.html` (kein Server, keine externen Assets) - Kader-/Markt-Karten, Dark/Light via `prefers-color-scheme`, "Zum Home-Bildschirm hinzufügen"-taugliche Meta-Tags. Optionale Client-seitige Passwort-Sperre (`config.PAGE_PASSWORD`, SHA-256-Hash im Quelltext via `crypto.subtle`) - **kein echter Schutz**, nur gegen zufälliges Finden der öffentlichen GitHub-Pages-URL. Mit Playwright (System-Edge, `channel="msedge"`) getestet: Gate, Falsch-Passwort, Entsperren - funktioniert.
- `config_template.py` (neu): secret-freie Kopie von `config.py` für CI - Credentials/Keys kommen über `os.environ.get(...)`, befüllt aus GitHub Secrets (siehe `.github/workflows/briefing.yml`). `config.py` selbst bleibt lokal, wird nie committet (`.gitignore`).
- `.github/workflows/briefing.yml` (neu): täglicher Cron (16:00 UTC = 18:00 CEST, vor dem 22-Uhr-Update; driftet 1h bei Sommer-/Winterzeit-Wechsel) + `workflow_dispatch`. Baut `config.py` aus dem Template, führt `main.py` aus, published `site/` via `peaceiris/actions-gh-pages@v4` auf den `gh-pages`-Branch → GitHub Pages hostet automatisch. **Setup noch offen** (User hat weder git noch gh CLI lokal installiert): Repo anlegen, Secrets eintragen (`KICKBASE_EMAIL`, `KICKBASE_PASSWORD`, `ODDS_API_KEY`, `PAGE_PASSWORD`, optional `FOOTBALL_DATA_API_KEY`), Pages-Quelle auf `gh-pages` stellen.
- `main.py`: Orchestrierung pro Liga: /me → Quoten/Fallback laden → Kader klassifizieren (sortiert VERKAUFEN→HALTEN) → Markt analysieren → **Star-Power**: `star = 0.55 × MW-Perzentil im Markt-Set + 0.45 × Team-Power`; star > 0.5 gibt bis +10 Score-Bonus; star ≥ 0.72 + fit + prob ≤ 2 = 💎 **BANGER** mit eigener Sektion ganz oben (User-Anforderung: Bellingham auf dem Markt MUSS als Top-Ziel erscheinen; Superstars haben systematisch schlechte Preis-Leistung und oft keine Punktehistorie — Star-Mechanismus gleicht das aus).
- `analytics.py`: nur Kompat-Wrapper, kann weg wenn nichts mehr importiert.

## User-Präferenzen (aus mehreren Feedback-Runden — unbedingt beibehalten)

- Trading und Punkte sind gleichwertig ("geht Hand in Hand"): Teamwert maximieren → beste Spieler → Spieltage gewinnen.
- Transparenz ist Pflicht: jede Empfehlung mit Komponenten-Aufschlüsselung und Klartext-Begründung.
- Keine pauschalen Fehlschlüsse: fehlende Daten ≠ schlechter Spieler; steigender MW ≠ Verkaufskandidat; Stammspieler (Beispiel des Users: Hauke Wahl, Kapitän/Abwehrchef VfL Wolfsburg, Aufstiegsfavorit) ≠ "Trading-Hold" oder "Beobachten".
- Kommunikation: informell deutsch, direkt, er testet selbst und gibt präzises Feedback; er arbeitet teils parallel mit Gemini am selben Code.

## Bekannte Baustellen / Nächste Schritte (Priorität nach User-Signalen)

1. ~~Kader-Endpoint verifizieren~~ **erledigt (2026-07-30)**: `/v4/leagues/{id}/squad` liefert live echte Daten (19/20 Spieler in beiden Ligen bestätigt), Kader-Hälfte des Briefings läuft.
2. **Activity-Feed verifizieren** → echter Liga-Overpay statt Standard-Puffer; perspektivisch auch Mitspieler-Analyse (Kader der anderen Manager, wer besitzt was, wessen Spieler wären interessant — ursprüngliche Anforderung, noch nicht gebaut).
3. **La Liga end-to-end testen**: Liga-Name in config, competition_id verifizieren (Platzhalter 3; korrekt auslesbar aus `cpi` in /me wenn er in der La-Liga-Liga ist), TheSportsDB-Fallback live prüfen, Teamnamen-Matching Kickbase↔football-data.co.uk (die CSV kürzt ab: "Ath Madrid", "Sociedad").
4. ~~Saisonwetten/Outright-Quoten~~ **erledigt/verworfen** (2026-07-30): User hat the-odds-api.com-Key besorgt und in `config.py` (`ODDS_API_KEY`) eingetragen. Live geprüft: Outright-/Meister-Markt ist für `soccer_germany_bundesliga2` und `soccer_spain_la_liga` auf dem Free Tier **nicht verfügbar** (`INVALID_MARKET_COMBO`) — kein zusätzliches Team-Power-Signal möglich. Stattdessen als **Tier-2-Fallback** eingebaut (`odds.load_fixture_odds_api`, `main.load_fixture_data`): liefert reguläre h2h-Spielquoten, wenn football-data.co.uk/fixtures.csv leer ist (aktuell der Fall, tiefe Sommerpause vor Saisonstart ~7./15.8.). Kostet Kontingent (500 req/Monat), daher nur 1x pro Liga pro Lauf, nicht pro Spieler. Nebenbei gefixt: `odds_div` fehlte komplett in `config.py` — die "primäre" Quotenquelle lief nie, App fiel direkt auf den Tabellen-Fallback zurück. Jetzt für beide Ligen gesetzt (`D2`, `SP1`).
5. **Automatisierung**: Code-seitig erledigt (2026-07-30) - HTML-Briefing (`html_report.py`) + GitHub-Actions-Workflow + Pages-Deployment gebaut und lokal via Playwright getestet, s.o. Statt E-Mail: statische Seite auf GitHub Pages, "Zum Home-Bildschirm hinzufügen". Offen: eigentliches GitHub-Setup durch den User (Git installieren, Repo erstellen, pushen, Secrets eintragen, Pages aktivieren - Claude Code hat hier weder git noch gh CLI zur Verfügung). MW-History-Snapshot (data/history.json) für mehrtägige Trends über das 24h-Feld hinaus weiterhin offen/gewünscht.
6. **Feintuning nach echten Läufen**: Schwellen (STAMM_CORE 0.55/0.60, STRONG_RISE 0.8%/Tag, Banger 0.72, min_gap 6) sind Erstkalibrierung; User liefert Output-Feedback. Punktetyp-Analyse (Rohpunkte vs. Scorer-Volatilität) und Aufstellungs-Empfehlung (beste 11 nach Sieg-WKs) sind gewünschte, noch nicht gebaute Features aus der ursprünglichen Vision.
7. Kleinkram: `mvt`-Feld (Werte 1/2 beobachtet) nie sicher dekodiert; `prc` vs `mv` bei Mitspieler-Listungen = geforderter Preis; Vereinslimit (`tpc`) wird geladen aber noch nicht in Kaufempfehlungen geprüft.

## Was NICHT tun

- Login-Payload/User-Agent nicht "aufräumen" — exakt so funktioniert er.
- Keine Endpoints erfinden: unverifizierte Pfade klar markieren und defensiv scheitern lassen (bestehende Konvention).
- `config.py` mit echten Credentials nie in Ausgaben/Commits.
- API nicht fluten (Sleep zwischen Detail-Calls beibehalten), Token nach ~1h erneuern falls Läufe länger werden.
