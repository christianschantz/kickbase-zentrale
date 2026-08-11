# Kickbase-Zentrale — Projekt-Briefing für Claude Code

## Wer / Wofür

Christian spielt Kickbase (Fantasy-Fußball-Manager-App) in zwei Ligen:
- **2. Bundesliga**, Liga-Name `"1899"`, 11 Mitspieler — **die wichtige Liga**
- **La Liga**, Liga-Name `"WirSchaffenStudium!!!"`, mittlerweile 11 Mitspieler (Liga-Name in config eingetragen und live bestätigt)

Ziel: Eine "Kickbase-Zentrale" — ein Python-Tool, das täglich ein fundiertes Briefing liefert und ihn zum bestinformierten Manager seiner Ligen macht. Alle Empfehlungen müssen **faktenbasiert und transparent begründet** sein (keine Blackbox-Scores). Endausbau: täglich automatisch (GitHub Actions) + Zustellung per E-Mail aufs Handy. Aktuell läuft alles lokal via `python main.py` (Windows, PowerShell, Ordner ehemals `F:\Downloads\kickbase-zentrale` bzw. `C:\Users\chris\Desktop\Kickbase App`).

## Kickbase-Spielmechanik (Basis aller Logik — recherchiert & vom User bestätigt)

- **Marktwert (MW)** wird täglich um **22:00 Uhr** aktualisiert. Treiber ist Community-Verhalten (Käufe/Verkäufe), Form, erwartete Einsatzzeit — nicht direkt die Punkte.
- **Transfers werden beim 22-Uhr-Update abgewickelt.** Liegt ein Gebot dann unter dem neuen MW, ist es ungültig → deshalb **Overpay-Puffer** über dem erwarteten 22-Uhr-MW bieten. Gebote >10% unter MW sind nicht abgebbar.
- **Verkauf:** bringt exakt den aktuellen Marktwert. Kein Abschlag, keine Angebotsspanne (Korrektur 2026-07-31, User-bestätigt). Folge: Ein Verkauf lässt den Netto-Teamwert unverändert (Kaderwert −m, Budget +m) und erhöht die Kaufkraft um genau den vollen MW (`capacity_nachher = capacity_vorher + m`, siehe `bid_advisor`/`squad_analysis`).
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
- `GET /v4/leagues/{id}/me` → `b` Budget (kann negativ sein), `mppu` max. Kadergröße (20), `mgc` Mitgliederzahl, `cpi` Competition-ID ("2" = 2. Bundesliga) — **wird jetzt für `cid` genutzt statt dem hartkodierten `config.competition_id`** (löst die alte "La-Liga-ID unverifiziert"-Unsicherheit). `tpc` ist **nicht** das Vereinslimit (frühere Fehlannahme, korrigiert 2026-07-31) - live geprüft: Liste `[{"tid", "npt", "tim"}]`, `npt` = AKTUELLE Spieleranzahl je Verein im eigenen Kader. Das echte Vereinslimit ist User-bestätigt **3 Spieler pro Verein** (`main.CLUB_LIMIT`), steht aber nirgends in der API selbst.
- `GET /v4/leagues/{id}/market` → `it`-Array. Felder pro Spieler: `i` ID, `fn`/`n` Name, `tid` Team-ID, `pos` (1 TW, 2 ABW, 3 MF, 4 ANG), `mv`, `ap` Punkteschnitt, `p` Gesamtpunkte, `prc` Preis, `exs` Restlaufzeit Sekunden, `prob`, `dt`. **`u`-Key nur vorhanden, wenn ein Mitspieler den Spieler gelistet hat** (mit dessen Name/ID) → Filter `'u' not in p` = echte Kickbase-Auktionen.
- `GET /v4/leagues/{id}/players/{pid}` → Goldgrube: `tfhmvt` = **echte 24h-MW-Änderung als Feld** (Chart-Abfrage unnötig), `st` Fitness-Status - **live als Bitmaske beobachtet** (0/1/2/4/16/256 in der 2. Liga gesehen), nur 0/1/2 geklärt (0 fit, 2 angeschlagen/verletzt), 4/16/256 noch offen (User prüft in der App) - unbekannte Werte werden als `status_known=False` in `scoring.score_player`'s meta ausgewiesen statt still neutral bewertet. `stxt` Klartext ("Rückenprobleme – trainiert individuell"), `prob` Einsatz-Indikator (beobachtet 1–5, **niedriger = besser**), `mdsum` letzte/kommende Spiele mit Team-IDs und `mdst` (2=beendet), `tn` Teamname, `tid`, `cv`, `mv`, `sdmvt` (7-Tage-MW-Differenz, s.u.).
- `GET /v4/leagues/{id}/squad` → `it`-Array der Kaderspieler enthält `sdmvt` **direkt mit** (kein Zusatz-Call nötig) - identisch zu Teamprofile/marketValue, s. A1-Fix unten.
- `GET /v4/competitions/{cid}/players/{pid}/marketValue/92?leagueId={lid}` → MW-Historie: `{"it": [{"dt": <Tagesnummer>, "mv": <float>}], "hmv": <Allzeithoch>}`. Führende `mv: 0.0`-Einträge sind Padding (vor Tracking-Beginn) und müssen gefiltert werden. Seit dem `sdmvt`-Fund (s.u.) nur noch für Detailansichten gedacht (`scoring.analyze_mv_history`), nicht mehr für die tägliche Massen-Trendberechnung.
- `GET /v4/leagues/{id}/ranking?dayNumber=N` → **verifiziert (2026-07-31)**: `us`-Array, ein Eintrag je Manager - `i` User-ID, `n` Name, `sp`/`spl` Saisonpunkte + Ligaplatz (aktuelle Saison - beide 0 in der Saisonvorbereitung, da noch kein Spieltag lief!), `mdp`/`mdpl` Spieltagspunkte + -platz, `tv` Teamwert (ebenfalls 0.0 in der Vorbereitung, wird erst mit Saisonstart befüllt), `psp`/`pspts`/`pswc` = Vorsaison-Platz/-Punkte/-Siege (historischer Kontext). `report_builder.compute_kpis` erkennt Saisonstart über `any(sp > 0)` und zeigt bis dahin "Ligaplatz: Saison startet bald" statt einer bedeutungslosen 0-Punkte-Rangliste.
- `GET /v4/leagues/{id}/lineup` → **verifiziert (2026-08-05, HAR-Mitschnitt der Aufstellungsansicht)**: liefert den KOMPLETTEN Kader mit Aufstellungsinfo unter `it`. Feld `lo` = Slot in der Startelf (0-10, Slot 0 immer TW) - fehlt `lo`, sitzt der Spieler auf der Bank. Zusätzlich je Spieler `os` (Kürzel des nächsten Gegners, z.B. "FCM" - macht Team-Fuzzy-Matching für die EIGENE Kader-Gegneranzeige überflüssig) und `ht` (`true` = Heimspiel). `mdst` (0 = Spieltag noch nicht begonnen) ebenfalls enthalten. `kickbase_api.get_lineup()`. **`POST /v4/leagues/{id}/lineup` (setzt die echte Aufstellung, Body `{"type": "4-4-2", "players": [...]}` mit 11 Slots, `null` für leer) ist bewusst NICHT implementiert** - würde den echten Kickbase-Kader verändern, gehört nicht in den automatischen Lauf.
- **Kickbase-Statusfarbe verifiziert (2026-08-05)**: `prob` (1-5) bestimmt die Farbe - 1=blau (gesetzt), 2=grün (wahrscheinlich Startelf), 3=gelb (fraglich), 4=rot (eher nicht), 5=grau (keine Einschätzung/fällt aus). Kreuzprobe gegen 4 vom User genannte Spieler mit bekannter App-Farbe: 4/4 Treffer (Taz blau/prob1, Ofli grün/prob2, El Kadiri gelb/prob3, Väänänen rot/prob4). `scoring.kickbase_color()`. Ergänzt das eigene Verdikt, ersetzt es nicht.
- **Eigene User-ID/Name**: steht NICHT direkt in `/me`, aber im JWT-Payload (`kb.uid`, `kb.name` - Base64-dekodierbar ohne Signaturprüfung, da selbst gerade erhalten). `kickbase_api.KickbaseAPI._decode_user_from_token()` befüllt `kb.user_id`/`kb.user_name` beim Login. `kb.name` kommt wie `onm` mit angehängtem Leerzeichen zurück (`.strip()`).
- `GET /v4/competitions/{cid}/table` → Liga-Tabelle, `it` mit allen 18 Teams (`tid`, `tn`, `cp`, `cpl`, `pcpl`, `gd`, `tim`) - Einstiegspunkt für alle Team-IDs (B5).
- `GET /v4/competitions/{cid}/teams/{tid}/teamprofile?leagueId={lid}` → kompletter Vereinskader (21-30 Spieler) unter `it`. Je Spieler u.a. `mv`, `ap`, `st`, `prob`, `pos`, und **`sdmvt`** = echte 7-Tage-MW-Differenz als Feld (verifiziert exakt gegen `marketValue/92`, 1:1-Übereinstimmung). Die mitgelieferten `onm`/`iotm`/`oui`/`uim` sind hier nur lückenhaft befüllt (~35/447) - NICHT als Besitzquelle nutzen, dafür `players/search` (s.u.).
- `GET /v4/competitions/{cid}/players/search?query=&leagueId={lid}&start=N` → durchblätterbare Gesamtliste aller Spieler der Competition (448-449 in der 2. Liga). `query=""` funktioniert. Paginierung **ausschließlich** über `start` in 25er-Schritten (offset/page/limit werden ignoriert), Ende = leere Liste. Felder: `pi`, `n`, `mv`, `pos`, `st`, `onm` (zu 100% befüllt - **Besitzquelle**, kommt aber mit angehängtem Leerzeichen zurück, `.strip()` nötig!), `iotm`, `tid`, `pim`. Response ist `{"it": [...]}`, kein rohes Array.
- `GET /v4/competitions/{cid}/players` → feste Top-25-Bestenliste (`p`, `g`, `a`, `cs`, `mt`), **nicht paginierbar**, alle Parameter werden ignoriert. Für B5 unbrauchbar, evtl. später als "Top-Scorer der Liga"-Zusatzsektion.
- `GET /v4/leagues/{id}/players/{pid}` liefert zusätzlich `ph` (Punkte je Spieltag, chronologisch bis zum aktuellen `day`, `{"hp": bool, "p": int}`) und `mdsum`-Einträge mit `day`-Feld → über den gemeinsamen `day`-Index lässt sich pro Spieltag Punkte + Sieg/Niederlage/Unentschieden des Spielerteams rekonstruieren (Basis für `scoring.player_reliability_profile`). Funktioniert auch in der Sommerpause, da beide Felder die letzten Spiele der Vorsaison zeigen.
- `GET /v4/leagues/{id}/managers/{uid}/squad` → **verifiziert (2026-08-05, SPEC_gebote_ki_team_KOMPLETT.md Abschnitt 6, "das laut Nutzer wichtigste Modul")**: kompletter Kader EINES Mitspielers inkl. seiner echten gesetzten Aufstellung. Top-Level `u, unm, uim, st, nps, it`. Je Spieler (17 Felder) u.a. `pi`, `pn` (Name - **nicht** `n`!), `tid`, `pos`, `lo` (Startelf-Slot 0-10, fehlt=Bank), `tfhmvt`/`sdmvt` liegen direkt bei - kein Einzel-Spielerabruf für fremde Kader nötig. **WICHTIG (live über einen Kalibrierungsbug entdeckt, 2026-08-05): enthält KEIN `prob`-Feld** (den Einsatz-Indikator 1-5, den `get_player_details`/der eigene Kader hat) - `league_teams.py` muss `prob` deshalb aus `lo`+`st` NÄHERN (`_estimate_prob()`), nicht direkt lesen; ein blindes `.get("prob", 3)` defaultet sonst für jeden Mitspieler auf gelb. `kickbase_api.get_manager_squad()`. Basis von `league_teams.py` (Modul 3).
- `GET /v4/leagues/{id}/managers/{uid}/dashboard` → `u, unm, st, ap, mdw, pl, tv, prft, not, ph, mds, fp, nd` - `ph`/`mds`/`fp` in der Saisonvorbereitung noch nicht abschließend geklärt (evtl. anders befüllt ab Spieltag 1). `kickbase_api.get_manager_dashboard()`, aktuell ungenutzt (kein konkreter Bedarf identifiziert).
- `GET /v4/leagues/{id}/managers/{uid}/teamvaluehistory?timeFrame=92` → `{"it": [{"ts": Tagesnummer, "tv": Teamwert}]}`, Teamwert-Verlauf eines Mitspielers analog zur eigenen MW-Historie. `kickbase_api.get_manager_team_value_history()`, aktuell ungenutzt (kein konkreter Bedarf identifiziert, evtl. für künftige Teamwert-Trend-Vergleiche in Modul 3).

**Unverifizierte Endpoints (Kandidaten im Code, scheitern defensiv):**
- Activity-Feed (für empirisches Overpay-Lernen aus echten Liga-Transfers): probiert `/activitiesFeed`, `/feed`. Unverifiziert.
- Antworten sind teils **Brotli-komprimiert** (`Content-Encoding: br`) — requests handhabt das normal automatisch; bei HAR-Auswertung manuell dekomprimieren.
- Rate-Limiting: zwischen Spieler-Detail-Calls `time.sleep(0.25)`, zwischen Teamprofile-/Search-Calls (B5) `time.sleep(0.15)`.
- `Accept-Language: de-DE;q=1, en-DE;q=0.9`-Header ergänzt (2026-07-31) - fehlte, echte App sendet ihn; Kandidat für plötzlich englischsprachige Responses.

## Architektur (Module in VS Code sichtbar)

- `config.py` (lokal, mit echten Credentials) / `config_template.py`: `LEAGUES`-Liste (je Liga: name, competition_id, odds_div, fixture_source + Quell-Parameter, seit 2026-08-05 zusätzlich `min_price` [2. BL 250k, La Liga 500k, SPEC_kalibrierung_fairvalue.md 3.1] und `matchdays` [34/38, für den pspts-Kalibrierungsanker 1.1]), `WEIGHTS` (Scoring-Gewichte), `FOOTBALL_DATA_API_KEY` (optional, ungenutzt solange Quoten laufen).
- `kickbase_api.py`: API-Client (Login, Ligen, /me, Markt, Spieler-Details, MW-Historie, Kader- und Feed-Kandidaten). Seit 2026-07-31 zusätzlich `get_competition_table`, `get_team_profile`, `search_players`/`search_all_players` (paginiert) für B5 (s. `league_board.py`).
- `odds.py`: **primäre Datenquelle für Spielplan & Teamstärke.** Lädt football-data.co.uk/fixtures.csv (kostenlos, keylos; anstehende Spiele mit Bet365-Quoten; Div `D2` = 2. BL, `SP1` = La Liga). Rechnet Quoten in implizite Sieg-WKs um (Marge normiert), baut daraus (a) pro Team die nächsten Gegner mit Sieg-WK und (b) Team-"Power" = min-max-normierte Ø-Sieg-WK. **Buchmacher definieren die Topteams, nicht die Vorsaison-Tabelle** (User-Feedback: Hertha war fälschlich Topteam).
- `fixtures.py`: Fallback-Quellen, wenn fixtures.csv leer (tiefe Sommerpause): OpenLigaDB (2. BL: Tabelle mit Vorsaison-Fallback + kompletter Saison-Spielplan) und TheSportsDB (La Liga, keylos, Liga-ID 4335 — **noch nie live getestet**). Fuzzy-Teamnamen-Matching (`_best_match`, SequenceMatcher + Substring), da Kickbase "Kiel" schreibt und Quellen "Holstein Kiel". `get_season_start_date()` (neu 2026-08-05): frühestes Datum aus den noch nicht gespielten Partien des OpenLigaDB-Spielplans = Spieltag 1 in der Saisonvorbereitung - live verifiziert, 2. Bundesliga Spieltag 1 = 07.08.2026 18:30 UTC. Nur für openligadb-Quellen (2. BL), nicht für football-data.org (La Liga).
  **`league_avg_win_prob()` (neu 2026-08-05, SPEC_kalibrierung_fairvalue.md Abschnitt 0)**: Fußball hat 3 Ausgänge - die reale Ø-Siegwahrscheinlichkeit pro Team liegt bei ~35-40%, nicht bei 50%. `coach.opponent_factor()` zentriert sich jetzt auf diesen echten Wert statt auf 0,5 (einer der beiden Hauptgründe für die zu niedrige Punkteprognose, s. `coach.py`). odds-Modus: Mittel der echten Sieg-WK (nächste 3 Spiele) über alle Teams aus `upcoming`. table/openligadb-Modus: `build_strength_map()` verteilt uniform 1..0 nach Tabellenrang - das Mittel ist rechnerisch immer exakt 0,5 (hier trotzdem generisch berechnet statt hartkodiert).
- `scoring.py`: 5-Komponenten-Score 0–100, Komponenten einzeln ausgewiesen: Preis-Leistung (Punkte/Mio MW), MW-Momentum (`tfhmvt`/mv, ±1,5%/Tag = Extreme), Einsatz-WK (Status × prob), Spielplan (Ø Sieg-WK), Form. Dazu `sporting_core` = Score ohne Momentum (Basis der Kader-Verdikte) und **Datenlage-Erkennung**: Spieler ohne `ap` UND `p` (Ligawechsler ohne Kickbase-Historie) werden nicht bestraft — Form wird aus MW geschätzt (`mv_implied_form`: 1M→0, 10M+→1), Preis-Leistung neutral 0.5, Kennzeichnung "[⚠️ keine Punktehistorie – Form aus MW geschätzt]". **Diese Skala bleibt bewusst unverändert** (s. `league_board.py`-Eintrag, "Bewusst NICHT angefasst").
  **Mindestpreis-Zensierung + Peer-Vergleichswert (neu 2026-08-05, SPEC_kalibrierung_fairvalue.md 3.1/3.2)**: `is_min_price_player(mv, min_price, margin=0.10)` erkennt Spieler am/nahe dem Liga-Mindestmarktwert (`config.LEAGUES[].min_price`, 2. BL 250k, La Liga 500k) - ihr Preis ist zensiert (kann nicht billiger sein), nicht marktbestimmt. `scoring.fit_price_curve(players, min_price=...)` schließt sie jetzt vom Kurven-Fit aus (verzerrten sonst das untere Kurvenende), Aufrufer (main.py/league_board.py/html_report.py) zeigen für sie "neutral - Mindestpreis" statt "über-/unterbewertet". `estimate_ap_from_peers()`/`build_peer_lookup()`: Median-`ap` je (Position, Teamstärke-Terzil, Kickbase-Farbe) über alle Spieler MIT echter Historie - Vergleichswert für Ligawechsler OHNE jede Historie (statt der reinen, bei 0,7 gedeckelten MW-Schätzung `mv_implied_form`) in `coach._punktebasis()`, progressiver Fallback bis zum reinen Positions-Median.
- `mv_forecast.py` (neu 2026-08-04, SPEC_forecast_coach_scoring.md Punkt 1 - Bug: die alte lineare Fortschreibung `mv + tfhmvt×4,5` explodierte bei Neueinsteigern, ein 3,7-Mio-Debütant mit riesigem Einstiegssprung bekam eine ~14-Mio-Gebotsempfehlung): **Regime-Erkennung zuerst** - `detect_regime()` klassifiziert jeden Spieler anhand der vollen `marketValue/92`-Historie in `INITIALISIERUNG` (<5 Datenpunkte ODER letzter Tagessprung >25% - der Neueinsteiger-Detektor, unabhängig von sonstiger Historielänge), `INSTABIL` (5-13 Punkte oder Streuung der Tagesraten >8%) oder `STABIL` (≥14 Punkte, ruhig). Bei INITIALISIERUNG **keine Trendprojektion** - Gebot bleibt nah am aktuellen MW. Sonst **gedämpftes Wachstumsmodell**: `mv_{t+k} = mv_t × Π(1 + g0×d^i)`, `g0` = Median der letzten 3 Tagesraten, `d` = Verhältnis Ø letzte 3 Tage zu Ø 4 Tage davor (auf [0.55, 1.0] begrenzt - dieselbe Größe wie die schon eingeführte Momentum-Ratio, hier als Fortschreibungsfaktor statt Signal). Drei Szenarien (pessimistisch/Basis/optimistisch) ergeben einen Korridor statt eines Punktwerts. **Wichtiger Bugfix beim Bauen**: bei fallendem Trend (negatives g0) kehrt sich "pessimistisch"/"optimistisch" rechnerisch um (dieselbe Dämpfung wirkt auf negative Werte umgekehrt) - `forecast()` sortiert die Grenzen deshalb explizit, sonst schlägt der Korridor-Trefferquote-Check bei fallenden Spielern systematisch fehl.
  **Konstanten empirisch kalibriert, nicht geraten** (`backtest_mv_forecast.py`, Punkt 1.6): testet für jeden Spieler jeden Zeitpunkt t rückwirkend gegen die vorhandene bis zu 92-Tage-Historie (Prognose für t+1/t+4 NUR aus Daten bis t). Die Spec-Vorschläge (pess=d×0,7, opt=min(1,0, d×1,15)) trafen den echten Wert nur in ~40-44% der Fälle (74 Spielerhistorien / 10.624 Vergleiche, beide Ligen) - deutlich zu eng. Kalibriert auf `PESS_MULT=0.0` (Stillstand als Pessimismus-Grenze, keine Umkehr ins Negative), `OPT_MULT=2.5` gedeckelt bei `OPT_CAP=1.5`.
  **Bugfix (2026-08-05, SPEC_gebote_ki_team_KOMPLETT.md Punkt 1.1 - vom User mit konkretem Beispiel gemeldet)**: `OPT_CAP=1.5` erlaubte dem Dämpfungsfaktor `d` (Wachstumsformel `mv_{t+k} = mv_t × Π(1 + g0×d^i)`) über 1.0 zu steigen - dadurch WÄCHST `d^i` exponentiell mit dem Horizont statt zu zerfallen, die Obergrenze explodierte (Allgeier-Beispiel: 8,77 Mio statt ~6,9 Mio, implizierte 8,1%/Tag statt der echten ~3%/Tag). Die vorher gemeldeten 78-81% Trefferquote waren dadurch künstlich (mathematisch unsauber) aufgebläht. Fix: `OPT_CAP=1.0` (Dämpfung darf eine Prognose nie verstärken, nur abschwächen). Ehrlicher Backtest nach dem Fix: **57-64% Korridor-Trefferquote, MAPE 1,4-10,0%**. Zusätzlich als Verteidigungslinie ein expliziter Plausibilitätstest (`_plausibility_clamp`): die implizite Tagesrate von MW-heute zu MW-Projektion wird gegen das 1,5-fache von `|g0|` gedeckelt (`PLAUSIBILITY_FACTOR`), Ergebnis-Flag `plausibility_clamped`. Kein Teil der täglichen Pipeline, gelegentlich von Hand laufen lassen zur Nachkalibrierung.
- `bid_advisor.py`: Gebotsempfehlung. **Seit 2026-08-04 zwei Pfade**: mit `mv_history` (mv_forecast.clean_mv_series()) läuft die neue regime-basierte Logik.
  **Neu redesignt (2026-08-05, SPEC_gebote_ki_team_KOMPLETT.md Punkt 1.2, "Fair Value")**: bisher leitete sich die Gebots-Obergrenze NUR aus der MW-Prognose ab (optimistisches Szenario) - die sportliche Bewertungstiefe floss nur indirekt über den Score in die Aggressivität ein, nie als harte Grenze. Jetzt: Mindestgebot = Basis-Prognose morgen 22 Uhr (`untergrenze`), Trading-Obergrenze = **Basis**-Prognose (nicht mehr optimistisch, s. Bugfix oben) am Ende des Halte-Horizonts, **Gebots-Obergrenze `max_gebot = max(trading_ceiling, fair_value_mv)`** - ein Spieler wird geboten, wenn ENTWEDER die MW-Prognose ODER der sportliche Fair Value ihn rechtfertigt. Übersteigt das Wunschgebot (Untergrenze + dynamischer Puffer) beide Grenzen, liefert `recommend_bid()` `verdict="nicht_bieten"` mit Klartext-Begründung (`verdict_reason`) statt eines beschönigten Gebots. `fair_value_mv` ist optional (Parameter `recommend_bid(..., fair_value_mv=None)`) - ohne Kurve (z.B. `league_board.py`, dort wird trotzdem best-effort berechnet, s.u.) fällt es auf die reine Trading-Obergrenze zurück.
  Ohne `mv_history` (aktuell nur `league_board.py`/B5, wo eine 92-Tage-Historie pro Spieler bei 449 Spielern/Liga zu teuer wäre - das Gebot dort ist ohnehin nur Zusatzinfo) fällt es auf die **alte lineare Fortschreibung** zurück (`_recommend_bid_legacy`): erwarteter 22-Uhr-MW = mv + max(tfhmvt, 0); Puffer = 3% × Aggressivität; **Aggressivität dynamisch nach Transfer-Stärke** (`0.55 + score/100 × 1.1` → Score 30 ≈ 2,6% Puffer, Score 90 ≈ 4,6%) — explizite User-Anforderung. `main.py` lädt `mv_history` deshalb nur für den Tagesmarkt (bounded ~20-40 Spieler/Liga, ein Zusatz-Call pro Spieler). Zuschlags-WK bleibt eine **grobe Heuristik**, keine Statistik. `learn_league_overpay()` würde Median-Overpay aus dem Activity-Feed lernen (blockiert durch unverifizierten Endpoint).
  **Projektions-Floor** (2026-07-30, User-Feedback "bei stark steigenden Spielern bieten alle auf die 4-5-Tage-Entwicklung, nicht nur auf morgen"): ab `STRONG_RISE_PCT` (0,8%/Tag, deckt sich mit `squad_analysis.STRONG_RISE`) wird die Gebotsbasis von `mv + tfhmvt` auf `mv + tfhmvt × PROJECTION_DAYS` (4,5 Tage) angehoben - AUSSER der Spieler ist sportlich schwach (`sporting_core < WEAK_SPORTING_CORE` 0.4), dann bewusst NICHT (User: "wenn der Spieler schlecht punktet natürlich weniger"). Zusätzlich **Star-Ausnahme-Info** (`star >= STAR_THRESHOLD` 0.72): informativer `star_ceiling` bis +3 Mio über MW ("in Einzelfällen belegt, nicht die Regel" - User-Erfahrungswert, NICHT aus dem Activity-Feed gelernt, wird nicht automatisch ins Gebot übernommen, nur ausgewiesen).
  `squad_analysis.finalize_headline_recommendations()` (User-Feedback "du wägst gut ab, aber keine finale Handlungsempfehlung"): kürt aus den Marktkandidaten pro Liga bis zu 2 mit klarem Verdikt statt nur Abwägung - **eine Kaufempfehlung** (höchster Score, finanzierbar, echter Ansatz UPGRADE/KAUFEN) und optional **eine Nicht-Kaufen-Empfehlung** (auffälligster Star-Kandidat, der gerade NICHT finanzierbar ist). Beide optional (0-2 Treffer je nach Marktlage), alle anderen Kandidaten bleiben unverändert Pro/Contra + Gebot.
  **SPEC_spieltagsmodell_v2.md 3.5/3.6 (2026-08-05)**: Fair-Value-Aufschlag - bei Unterbewertung (`fair_value > mv`) wird der Puffer mit `min(1.5, 1 + 0.5×relative_Unterbewertung)` multipliziert (`FAIR_VALUE_BOOST_FACTOR`/`_CAP`), wirkt NUR innerhalb der bestehenden Wertobergrenze (`max(trading_ceiling, fair_value)`), kann sie nicht überschreiben - live verifiziert (Allgeier +34% unterbewertet: Puffer 4,3%→5,0%). Neues Feld `projection_short`: bei Regime INITIALISIERUNG/INSTABIL "zu wenig Historie für eine Prognose" statt der vollen Regime-Methodik in der Hauptansicht (die volle `projection_note` bleibt im `<details>`-Bereich, s. html_report.py).
- `squad_analysis.py`: Kader-Verdikte mit **fester Hierarchie** (mehrfach nach User-Feedback korrigiert): 1. **STAMM** hat Vorrang — sporting_core ≥ 0.55 (0.60 ohne Punktehistorie) + fit + prob ≤ 2 → Stamm, egal was der MW macht (fallender MW wird nur als Info genannt: "bei Stammspielern kein Verkaufsgrund"). 2. **HALTEN (Trading)** nur wenn kein Stamm-Fall, aber MW ≥ +0,8%/Tag ("reines Wert-Investment" — z.B. Spieler der 200k/Tag steigt aber nicht spielt: HALTEN, niemals Verkauf wegen "Einsatz fraglich"!). 3. **BEOBACHTEN** (Anstieg flacht laut `scoring.momentum_ratio` NACHWEISBAR ab → Verkaufsfenster naht). 4. **VERKAUFEN** nur wenn MW fällt/stagniert UND sportlich schwach.
  **A1-Fix, Backlog-Punkt "Logikfehler BEOBACHTEN"** (2026-07-31, zweimal überarbeitet): "flacht ab" war ursprünglich aus einem einzelnen 24h-Wert (`tfhmvt`) geraten - mathematisch keine Abflachung (zweite Ableitung). Erste Lösung zog die volle `marketValue/92`-Historie pro Kaderspieler (`scoring.analyze_mv_history`, echter 3-Tage-vs-3-Tage-Vergleich, `accelerating`-Flag, `dist_to_hmv` als Überhitzungs-Indikator - bleibt im Code für Detailansichten). Dann per Live-Verifizierung stark vereinfacht: `sdmvt` (7-Tage-MW-Differenz) kommt bereits im Kader-/Teamprofil-Response mit, exakt identisch zur selbst berechneten 7-Tage-Summe. `scoring.momentum_ratio(tfhmvt, sdmvt)` = (tfhmvt×7)/sdmvt beantwortet "flacht der Anstieg ab?" **ohne jeden Zusatz-API-Call** - Ratio <0,6 (24h-Wert unter 60% des 7-Tage-Schnitts) löst BEOBACHTEN aus. Einschränkung bewusst im Reasons-Text: Ratio ist eine Momentaufnahme, kein über mehrere Tage bestätigter Trend (bräuchte Tagessnapshots, s. Baustelle B4/History).
  `market_vs_squad` (2026-07-30 grundlegend überarbeitet nach User-Feedback: "Trading-Eignung und sportliche Eignung sind zwei getrennte Fragen, nicht eine Score-Zahl"): jeder Marktspieler bekommt **zwei unabhängige Spuren**, beide im `team_verdict`-Text ausgewiesen: (a) **Trading-Spur** (`_trading_assessment`) — Vergleich der MW-Momentum (%/Tag) NUR gegen die eigenen `HALTEN (Trading)`-Spieler, konkret gegen den am schwächsten steigenden; Stämme zählen hier nicht mit. Kein Trading-Hold im Kader → Standardschwelle (`STRONG_RISE`) als Referenz. (b) **Sportliche Spur** (`_sporting_assessment`) — Vergleich über `sporting_core` (momentum-frei!) + Punkteschnitt (`ap`), primär gegen Kaderspieler auf derselben Position, positionsübergreifend nur bei großem Mehrwert (`BIG_UPSIDE_CORE`/`BIG_UPSIDE_AP_GAP`, da Kickbase-Formationen Positionen fest vorschreiben). Dazu `scoring.player_reliability_profile`/`punktetyp_label` ("Punktetyp"-Signal aus `ph`+`mdsum`, s.o.): Rohpunkte-Typ (punktet auch bei Niederlagen ordentlich) vs. Scorer-Typ (Punkte hängen am Sieg) — beantwortet explizit "lohnt sich der Kauf trotz schwachem Spielplan/niedriger Sieg-WK am Wochenende". Headline-Priorität bei fehlendem freiem Kaderplatz: sportliches UPGRADE > TRADING-UPGRADE > informativ "kein Zwang" > KEIN BEDARF — beide Spuren werden aber immer im Text gezeigt, auch wenn nur eine die Headline stellt (Trading und Punkte sind laut User gleichwertig, s.u.). Trading-Holds und Stämme sind als Verkaufsziel NICHT ersetzbar (tauchen nur als "kein Zwang" auf). Kaufkraft nach 33%-Regel (s.o.), bei Upgrades nach Verkauf neu gerechnet. Schwellen (`MIN_CORE_GAP` 0.08, `MIN_AP_GAP` 8, `BIG_UPSIDE_CORE` 0.65, `BIG_UPSIDE_AP_GAP` 15, `RELIABLE_RATIO` 0.6 in scoring.py) sind Erstkalibrierung, noch nicht am echten Output gegengeprüft.
  **Bugfix sportliche Spur (2026-08-05, SPEC_gebote_ki_team_KOMPLETT.md Punkt 1.3)**: die sportliche Spur verglich bisher NUR gegen `sporting_core`/`ap` des Referenzspielers, ignorierte aber, ob dieser laut `scoring.kickbase_color()` überhaupt gesetzt ist - ein Marktspieler konnte "stärker, aber kein sportlicher Zwang" ausgewiesen bekommen, obwohl der eigene Referenzspieler blau/grün (voraussichtlich Startelf) war, was den Text unglaubwürdig machte. Fix: `COLOR_RANK` (blau 4 > grün 3 > gelb 2 > rot 1 > grau 0) fließt in `_sportlich_vergleich()` mit ein - "kein sportlicher Zwang" erscheint jetzt NIE mehr für einen blau/grün eingestuften Referenzspieler, stattdessen "stärker als X (Farbe, selbst sportlich relevant) - kein Ersatzbedarf, X ist gesetzt". `recommend_bid()`-Aufruf übergibt jetzt `fair_value_mv=m.get("fair_value")` (s. bid_advisor.py).
  **`apply_fair_value_note()` (neu 2026-08-05, SPEC_kalibrierung_fairvalue.md 4.1)**: Fair Value ERGÄNZT die bestehenden Kader-Verdikte, ersetzt sie nicht - die Farbregel (STAMM bei blau/grün) bleibt vorrangig, ein Verkauf wird NIE automatisch ausgelöst, nur eine zusätzliche `reasons`-Zeile angehängt (`FAIR_VALUE_SELL_THRESHOLD`/`FAIR_VALUE_HOLD_THRESHOLD` = ±25%, Erstkalibrierung). Mindestpreis-Spieler (`scoring.is_min_price_player()`) werden nie als über-/unterbewertet ausgewiesen. Bei STAMM-Spielern ausdrücklich "kein automatischer Verkaufsgrund, nur Beobachtungshinweis" statt einer Verkaufsempfehlung. `main.py` ruft das NACH dem Aufbau der Liga-Preiskurve für jeden Kaderspieler auf (`c["fair_value"]`/`c["fair_value_sell_flag"]`).
  **`bridge_to_ideal_elf()` + `recommendation_tier()` (neu 2026-08-05, SPEC_spieltagsmodell_v2.md 3.2/3.3)**: die "eigentliche Brücke zwischen Transfermarkt und Trainer-Modul" - beantwortet direkt "bringt mir der Spieler Punkte?" statt den Spieler nur abstrakt zu bewerten. Bei freiem Kaderplatz: reine erwartete Punkte. Sonst: Vergleich gegen den schwächsten Spieler der IDEAL-ELF (`coach.optimize_lineup()`) auf derselben Position - "verdrängt Ofli (78 P) aus Slot 2" oder "käme nicht in deine Elf (Slot besetzt)". `recommendation_tier()` leitet eine vierstufige Einordnung (KLARE KAUFEMPFEHLUNG · INTERESSANT · NUR TRADING · KEIN BEDARF) aus dem bereits vorhandenen `team_verdict`/Ideal-Elf-Beitrag ab - **kein Ersatz** für die bestehende, über viele Feedback-Runden kalibrierte `market_vs_squad`-Logik, nur eine gröbere Einordnung obendrauf. Ausschluss "KEIN BEDARF" bei Farbe rot/grau ohne Startelf-Aussicht ODER fehlendem Ideal-Elf-Beitrag bei nicht-steigendem MW.
- `html_report.py` (neu, 2026-07-30): rendert die main.py-Ergebnisse als eine selbstständige `site/index.html` (kein Server, keine externen Assets) - Kader-/Markt-Karten, Dark/Light via `prefers-color-scheme`, "Zum Home-Bildschirm hinzufügen"-taugliche Meta-Tags. Optionale Client-seitige Passwort-Sperre (`config.PAGE_PASSWORD`, SHA-256-Hash im Quelltext via `crypto.subtle`) - **kein echter Schutz**, nur gegen zufälliges Finden der öffentlichen GitHub-Pages-URL. Mit Playwright (System-Edge, `channel="msedge"`) getestet: Gate, Falsch-Passwort, Entsperren - funktioniert.
- `config_template.py` (neu): secret-freie Kopie von `config.py` für CI - Credentials/Keys kommen über `os.environ.get(...)`, befüllt aus GitHub Secrets (siehe `.github/workflows/briefing.yml`). `config.py` selbst bleibt lokal, wird nie committet (`.gitignore`).
- `.github/workflows/briefing.yml`: täglicher Cron (16:00 UTC = 18:00 CEST, vor dem 22-Uhr-Update; driftet 1h bei Sommer-/Winterzeit-Wechsel) + `workflow_dispatch`. Baut `config.py` aus dem Template, führt `main.py` aus, committet `data/` zurück auf `main` (Tagesvergleich, s.o.), published `site/` via `peaceiris/actions-gh-pages@v4` auf den `gh-pages`-Branch → GitHub Pages hostet automatisch. **Setup erledigt (2026-07-31)**: Repo `christianschantz/kickbase-zentrale`, Secrets `KICKBASE_EMAIL`/`KICKBASE_PASSWORD`/`ODDS_API_KEY`/`PAGE_PASSWORD`/`GEMINI_API_KEY` gesetzt, Pages auf `gh-pages` aktiv, läuft automatisch inkl. KI-Einordnung. **Neuer Schritt (2026-08-05, SPEC_lernzyklus.md 5.4)**: `python test_determinism.py` direkt nach "Briefing erzeugen", vor dem Daten-Commit - lässt den Workflow fehlschlagen, wenn die reine Berechnungsschicht nicht deterministisch reproduziert.
- `league_board.py` (neu 2026-07-31, B5; **grundlegend überarbeitet 2026-07-31** wegen Skalenfehlern, s.u.): Liga-weite Bestenliste über die GESAMTE Competition (~449 Spieler), nicht nur Kader+Tagesmarkt. `fetch_league_universe()` holt Tabelle (1 Call) + alle 18 Teamprofile + volle `players/search`-Pagination (~38 Requests/Liga/Lauf). `resolve_ownership()` liefert EIGEN/MITSPIELER/MARKT/FREI - **EIGEN wird bewusst NICHT über Namensvergleich erkannt** (der eigene Anzeigename steht nirgends direkt in `/me`, und `onm` hat die Whitespace-Tücke), sondern robust über die ID-Menge des eigenen Kaders (`kb.get_squad()`).
  **Scoring-Rework** (Anlass: Pedri Ø 158 P/50 Mio fehlte in den La-Liga-Top-10, Campos Ø 100 P/1 Mio stand auf Platz 1 - die alten absoluten Skalen aus `scoring.score_player()` waren auf die 2. Bundesliga kalibriert und für teure Spieler strukturell unerreichbar): `build_league_lists()` scort jetzt komplett **ligarelativ über Perzentile** der kompletten Competition-Population (`scoring.percentile_rank`) statt fixer Konstanten. **Zwei komplett getrennte Ranglisten** je Position lösen den Zielkonflikt "objektiv bester Spieler" vs. "bester Deal" statt ihn in einer Zahl zu verwischen: Liste A "Qualität" (`config.WEIGHTS_QUALITY`: Form 0.40, Verfügbarkeit 0.25, Teamstärke 0.20, Spielplan 0.15 - kein Preis, kein Momentum) und Liste B "Deals" (`config.WEIGHTS_VALUE`: Preis-Residuum 0.45, Momentum 0.30, Verfügbarkeit 0.15, Spielplan 0.10 - kein Formanteil). Doppelnennungen markiert (`in_both`); "Liga-Banger" = Top 5 in BEIDEN Listen derselben Position. `scoring.mv_implied_form` zusätzlich bei 0.70 gedeckelt (ein Spieler ohne Punktehistorie darf nie wie ein belegter Topscorer bewertet werden). Gegenprobe live bestanden: Mbappé/Lamine Yamal/Pedri/Valverde oben in Liste A, Campos/Boyé oben in Liste B, Reese/Wanitzek oben in der 2.-Liga-Liste A.
  **Preiskurve zweimal überarbeitet** (2026-08-04, SPEC_forecast_coach_scoring.md Punkt 3 - Bug: fast jeder teure Spieler stand "über Preiserwartung", statistisch unmöglich bei korrektem Fit; Ursache: der Ausschluss von `ap=0`-Spielern verengte die Stichprobe im teuren Segment auf reine Leistungsträger, da dort viele Neuzugänge ohne Kickbase-Historie liegen - die log-lineare Regression lernte von einer nach oben verzerrten Stichprobe): `scoring.fit_price_curve()` ist jetzt eine **Dezil-Median-Kurve** (10 gleich große Preis-Gruppen, Median-MW/Median-Punkte je Dezil, linear interpoliert, an den Rändern abgeflacht statt extrapoliert) statt einer globalen Regression - robust, direkt als Tabelle darstellbar. **`ap=0` wird jetzt differenziert**: `league_board._resolve_zero_ap_history()` prüft für `ap=0`-Spieler im teuren Preissegment (oberes Drittel, live gemessen 30-55 Spieler/Liga - für ALLE ~450 wäre es zu teuer) per `get_player_details`, ob `ph` echte Einsätze zeigt (`hp=true`) → dann **echte Nullleistung, gehört in den Fit**; ohne jeden Eintrag → Ligawechsler ohne Historie, bleibt **unbewertet** (Residuum None, nicht "positiv" - das war der Kern des Bugs). `scoring.value_residual()` liefert jetzt ein **relatives** Residuum (`(ap-erwartung)/erwartung` statt absolut - +10 P sind bei einem 2-Mio-Spieler stark, bei 30 Mio marginal), Report zeigt beides ("+44 P (+39%) ggü. Preiserwartung"). **Pflicht-Selbstprüfung** `scoring.price_curve_diagnostics()`: Anteil "über Erwartung" muss 40-60% sein, sonst Warnung - live geprüft nach dem Fix: 51%/52% in beiden Ligen (vorher wäre es weit über 60% gewesen).
  **Bewusst NICHT angefasst**: `scoring.score_player()` (Kader-Klassifizierung + Tagesmarkt) hat dieselben Skalenfehler (z.B. Bellingham bekam dort "Preis-Leistung: 3%"), wurde aber NICHT umgestellt - die STAMM/HALTEN/BEOBACHTEN-Schwellen (`squad_analysis.STAMM_CORE` etc.) sind über viele User-Feedback-Runden auf diese Skala kalibriert, ein Rescale würde sie ungefragt invalidieren. Der Star-Power-Patch in `main.py` (Kompensation für dieselben Skalenfehler im Tagesmarkt) bleibt deshalb ebenfalls bestehen. Eine Übertragung des Perzentil-Ansatzes auf Kader/Tagesmarkt wäre ein sinnvoller Folgeschritt, braucht aber die Liga-Population VOR der Kader-Klassifizierung (Reihenfolge in `main.run_league` müsste sich ändern) und eine Neukalibrierung der Schwellen - noch nicht gemacht, User-Rückfrage nötig.
  Punktetyp/Fitness-Text (bräuchten `get_player_details` je Spieler) werden für die volle Liga-Population bewusst NICHT gezogen - Kostengrund, bleibt Kader/Tagesmarkt vorbehalten. `form_raw()` mischt aktuelle Form (`ph`, letzte 5 Spieltage) mit Saisonschnitt (`ap`), Gewicht wächst mit der Spieltagszahl - aktuell `CURRENT_SEASON_DAYS=0` hartkodiert (Saisonvorbereitung, Gewicht ist 0, `ph` wird nicht gebraucht/gezogen) - **TODO vor Saisonstart (~7./15.8.)**: echte Spieltagszahl ableiten (Kandidat lt. Spec: `/v4/competitions/{cid}/players` Felder `day`/`sn`/`mdsn`/`nsn`, noch nicht verifiziert).
  **Fair-Value-Rework (2026-08-05, SPEC_kalibrierung_fairvalue.md)**: `build_league_lists()` läuft jetzt zweiphasig - Pass 1 berechnet für jeden Spieler einmal Team-Stärke/Ease/Farbe (`base`-Dict), Pass 2 nutzt das für Perzentile UND für den neuen Peer-Lookup (`scoring.build_peer_lookup()`, aus allen Spielern MIT echter Historie), der Ligawechslern ohne jede Historie einen Vergleichswert für `coach.fair_value()`s `peer_estimate` liefert statt der reinen MW-Schätzung. `min_price` (aus `config.LEAGUES[].min_price`) zensiert Mindestpreis-Spieler aus dem Kurven-Fit UND aus dem Residuum (`value_residual` liefert für sie explizit None, nicht "positiv"). `liga_avg_win_prob` zentriert `coach.opponent_factor()` in `fair_value()` korrekt (s. `coach.py`/`fixtures.py`). **Selbstprüfungs-Unterdrückung (Spec 3.3, jetzt tatsächlich verdrahtet)**: `price_curve_diagnostics()` existierte schon, wurde aber nur angezeigt, nie zur Unterdrückung genutzt - `fair_value_ok = price_diag["plausible"] is not False` wird jetzt zurückgegeben und von allen Aufrufern (`main.py`, `html_report.py`) geprüft, bevor Fair Value überhaupt berechnet/angezeigt wird. Rückgabedict um `"peer_lookup"` und `"fair_value_ok"` erweitert.
- `main.py`: Orchestrierung pro Liga: /me (→ `cid` aus `cpi`, `club_limit=3`) → Quoten/Fallback laden → Kader klassifizieren (sortiert VERKAUFEN→HALTEN) → Markt analysieren → **Star-Power**: `star = 0.55 × MW-Perzentil in Kader+Markt zusammen + 0.45 × Team-Power` (A2-Fix 2026-07-31: vorher nur gegen das ~16 Spieler kleine Tages-Markt-Set normiert, dadurch volatil - ein zufällig teurer Markttag machte jeden zum "Star"); star > 0.5 gibt bis +10 Score-Bonus; star ≥ 0.72 + fit + prob ≤ 2 = 💎 **BANGER** mit eigener Sektion ganz oben (User-Anforderung: Bellingham auf dem Markt MUSS als Top-Ziel erscheinen; Superstars haben systematisch schlechte Preis-Leistung und oft keine Punktehistorie — Star-Mechanismus gleicht das aus). Danach `league_board.build_league_lists()` (B5) für die ligaweite Bestenliste, dann `report_builder` (s.u.) für die Dashboard-Bausteine.
  **SPEC_kalibrierung_fairvalue.md-Verdrahtung (2026-08-05)**: `liga_avg_win_prob` (`fixtures.league_avg_win_prob()`) und `min_price` (`cfg.get("min_price")`) werden einmal pro Liga berechnet und an ALLE `coach.expected_points()`/`fair_value()`-Aufrufe (Kader-Loop, Tagesmarkt-Loop, `build_league_lists()`, `build_league_teams()`) durchgereicht. Fair Value für Kaderspieler wird NACH dem Aufbau der Liga-Preiskurve (`board`) in einem zweiten Durchlauf über `squad_classified` nachgereicht (`squad_analysis.apply_fair_value_note()`, s.u.) - die Kurve selbst braucht `own_ids` aus dem fertigen Kader, kann also nicht schon im Kader-Loop vorliegen.
  **`_check_pspts_anchor()` (SPEC 1.1, Pflicht-Selbstprüfung)**: Vorsaison-Gesamtpunkte je Manager (`ranking.us[].pspts`) geteilt durch `cfg["matchdays"]` (34/38) ergibt den real erreichten Spieltagsschnitt - Median der `league_teams`-Prognosen wird dagegen geprüft, Warnung bei >25% Abweichung. **Nach dem Rekalibrierungsfix + dem `managers/squad`-prob-Bugfix (s. `league_teams.py`) live verifiziert: -4% Abweichung, PLAUSIBEL** (La Liga hat keine `pspts`-Vorsaisondaten - `_check_pspts_anchor()` liefert dort `None`, kein Crash, keine Anzeige). **Plausibilitätslog je Spieler** (SPEC 1.2): `E[Punkte]` weicht >Faktor 2 von `ap` ab → Konsolen-Warnung statt stillem Weiterrechnen.
  **SPEC_spieltagsmodell_v2.md-Verdrahtung (2026-08-05)**: `coach.diagnose_prognose()` wird je Manager mit >25% Eigenanker-Abweichung als kompakte Kaskaden-Zeile gedruckt (aktuell 0 Treffer nach dem Prob-Fix - alle Manager innerhalb der Toleranz). Aufstellungsblock nutzt jetzt `coach.xi_prognose()` für Ideal- UND Ist-Elf (`ideal_prognose`/`ist_prognose` im Report-Dict) statt des ungedämpften `lineup_opt['best_total']`, `swaps_from_ideal()` statt `suggest_swaps()`, `kaderstaerke_reason` bei nicht berechenbarer Formation. `prediction_log`-Aufrufe (Datei A/B/C, s.u.) laufen direkt nach der Kalibrierungsprüfung. Tagesmarkt-Loop berechnet zusätzlich `expected_points_mine` (coach.expected_points im EIGENEN Kontext) je Marktspieler - Grundlage für `squad_analysis.bridge_to_ideal_elf()`/`recommendation_tier()` (s.u.), die NACH `market_vs_squad()`/`finalize_headline_recommendations()` als zweiter Durchlauf über `compared` laufen (brauchen `free_slots`/`team_verdict`).
- `report_builder.py` (neu 2026-07-31, Dashboard-Redesign): trennt Analyse von Darstellung - verdichtet squad_classified/compared/league_board zu entscheidungsorientierten Bausteinen, die `html_report.py` nur noch rendert. `compute_kpis()` (Teamwert+Δ24h, Kaufkraft, Budget, Ligaplatz via `/ranking`, Kaderplätze). `build_actions()` - **max. 5 Einträge**: klare Kauf-/Nicht-Kauf-Empfehlung > die 2 bestbewerteten ablaufenden Marktspieler mit Score ≥60 (Deckelung nötig: bei freien Kaderplätzen bekommt sonst praktisch jeder Marktspieler die generische Headline "KAUFEN (freier Kaderplatz)" und flutet die Liste mit Fast-Duplikaten) > eigene VERKAUFEN-Kandidaten. `build_squad_action_items()` filtert auf VERKAUFEN/BEOBACHTEN (STAMM/HALTEN sind Bestätigungen ohne Handlungsbedarf). `build_targets()` - Watchlist aus `league_board`: `in_both` (Top 5 Qualität UND Deals) plus Top 3 aus Liste A je Position, ohne EIGEN, gruppiert nach Erreichbarkeit (MARKT/MITSPIELER/FREI). `build_risks()` - Kaufkraft-Puffer <5% des Kaderwerts, erreichtes Vereinslimit, fehlender Spielplan; leer wenn kein Anlass.
  **JSON-Persistenz + Tagesvergleich** (löst B4 nebenbei): `save_report()` schreibt einen SCHLANKEN Ausschnitt (KPIs + reduzierter Kader/Markt, nicht der volle ~80-Einträge-`league_board`, sonst wächst das Repo täglich unnötig) nach `data/report_<liga-slug>_<datum>.json`. `.github/workflows/briefing.yml` committet `data/` nach jedem Lauf zurück auf `main` (eigener Schritt, git-Bot-Identität, nur wenn sich was geändert hat) - **ohne diesen Commit gäbe es in der nächsten Actions-Ausführung (frischer Checkout!) keinen Vortag zum Vergleichen**. `load_previous_snapshot()`/`diff_reports()` finden die jüngste Datei vor dem aktuellen Datum und vergleichen Verdikt-Wechsel, Statuswechsel (fit↔verletzt, Basis für zukünftige Rückkehrer-Erkennung C2), neue Marktspieler, Teamwert-Änderung. Beim allerersten Lauf (kein Vortag) zeigt der Report das transparent an, statt zu raten.
- `html_report.py` (Dashboard-Redesign 2026-07-31, **Modul-Umbau 2026-08-04**): zeigt **Entscheidungen, nicht Daten** - Startseite pro Liga in Prioritätsreihenfolge: Handlungsleiste (`_action_list`, max. 5) → KI-Kurzkommentar (`_llm_block`, nur mit `GEMINI_API_KEY`) → KPI-Grid (`_kpi_grid`, 2-3-spaltig) → Risiko-Banner (nur bei Anlass) → **Aufstellungsempfehlung** (`_lineup_block`, neu - beste Elf aus `coach.optimize_lineup()`, Faktoren je Spieler transparent, Alternativ-Formationen mit Punktedifferenz) → **ein Transfermarkt-Modul** (`_transfermarkt_section`, SPEC_forecast_coach_scoring.md Punkt 5.1 - Tagesmarkt jetzt der sichtbare Hauptteil statt in `<details>` versteckt, plus kompakter "Bei Mitspielern"-Anhang mit 4 besten erreichbaren Fremdkader-Spielern; die große positionsweise Transferziel-Liste (`_watchlist`) ist in die Vertiefung gewandert) → Kader-Handlungsbedarf (nur VERKAUFEN/BEOBACHTEN) → **Stärkste MW-Steiger der Liga** (Punkt 5.2 verkleinert: nur noch Top 5 GESAMT einzeilig statt 4×10 großer Karten je Position - `league_board.climbers` liefert jetzt eine flache Liste statt nach Position gruppiert) → alles andere (kompletter Kader/Markt/Liga-Bestenlisten, weitere Transferziele, Tagesvergleich) in aufklappbaren nativen `<details>`-Blöcken (kein JS nötig, behält Zustand beim Scrollen). **Beide Ligen in einer Datei** als Tabs (`TABS_SCRIPT`, vanilla JS - Wechsel ohne Reload, Deep-Link über den URL-Hash `#league-0`/`#league-1`, funktioniert auch mit aktiver Passwort-Sperre da die Tab-Logik unabhängig vom Gate-Zustand läuft). Zahlen konsequent als `_mio()`-Kurzform ("12,48 Mio €" statt "12.480.000 €"). Sticky Header mit Stand + grober MW-Update-Countdown (`_next_22_countdown`, gleiche DST-Näherung wie der Workflow-Cron).
  **Ergänzungen 2026-08-05 (SPEC_gebote_ki_team_KOMPLETT.md)**: Marktkarten zeigen jetzt `fair-value` (fett, grün/rot bei >5% Abweichung von MV - "unterbewertet"/"überbewertet"/"fair bewertet") direkt unter der Stats-Zeile, prominent platziert; Gebots-Internals (`projection_note`, `star_ceiling`) bleiben in der bestehenden gedämpften `.meta`-Optik, Regime/Dämpfung wurden nie in die Karte übernommen (nur Konsole) - Punkt 1.4 ("Internals in Detailbereich") war dadurch bereits erfüllt, keine weitere Umstrukturierung nötig. `verdict="nicht_bieten"` bekommt eine eigene rote `.bid.nicht-bieten`-Zeile statt eines beschönigten Gebotsbetrags. Neue Sektion `_league_teams_table()` (Modul 3, s. `league_teams.py`) direkt nach der Aufstellungsempfehlung, vor dem Transfermarkt-Modul - eigene Zeile optisch hervorgehoben (`.team-row.me`), Warnhinweise bei unbesetzten Slots/Klumpenrisiko ≥30%, jetzt zusätzlich `formation_hint`-Zeile.
  **Ergänzungen 2026-08-05 (SPEC_kalibrierung_fairvalue.md)**: Mindestpreis-Spieler zeigen explizit "Fair Value: neutral - Mindestpreis" statt eines Urteils. `_squad_card()` zeigt Fair Value dezent (kleine `.meta`-Zeile, nicht die fette Markt-Optik - Spec 4.1 "nicht dominant") mit ⚠️-Tag bei `fair_value_sell_flag`. Neuer `_model_health_banner()` im Dashboard (nach dem Risiko-Banner): Kalibrierungs-Warnung bei >25% Abweichung vom pspts-Anker, Hinweis wenn Fair Value wegen verzerrter Preiskurve unterdrückt ist, `self_play_conflicts` als Warnzeilen, `deviation_report` (Modellgüte nach Spieltagsende, s. prediction_log.py) sobald verfügbar.
  **`_market_card()` grundlegend überarbeitet (2026-08-05, SPEC_spieltagsmodell_v2.md 3.7 "Karte verschlanken")**: die Kaufkraft-Zeile (global, stand bisher auf JEDER Karte) ist raus (steht bereits einmal im KPI-Grid), Komponenten-Prozente + volle Regime-Methodik (`projection_note`, Star-Ausnahme, Finanzierungs-Satz) wandern in einen `<details>`-Block ("Details"). Fair Value ist jetzt EINE Aussage statt zwei nebeneinander leicht widersprüchlich wirkender Zeilen (die frühere "+34% unterbewertet" NEBEN "liefert 67 P, üblich wären 60 P (+12%)" - beides korrekt, wirkte aber widersprüchlich, da die Kurve am oberen Preisende steiler ist; die zweite Zeile steckt jetzt im Detailbereich). Neu in der Hauptansicht: die Ideal-Elf-Brücke (`ideal_elf_bridge`, kombiniert mit dem nächsten Gegner in einer Zeile) und die vierstufige Tier-Badge (`.tier-*`-CSS-Klassen) direkt vor der Verdikt-Headline. Bid-Zeile kompakt ("💶 Gebot X · Zuschlags-WK ~Y% ✅" statt der alten langen Zeile mit 22h-MW/Puffer/Finanzierung), `projection_short` statt der vollen Regime-Zeile in der Hauptansicht.
  `_lineup_row()` zeigt Form-/Zu-Null-Faktor und die (jetzt Sigma-basierte) Bandbreite je Spieler. `_lineup_block()` nutzt `coach.xi_prognose()`-Ergebnisse (`ideal_prognose`/`ist_prognose`, direktduell-gedämpft) statt des ungedämpften `lineup['best_total']` - EINE Zahl in Konsole und HTML statt zweier potenziell widersprüchlicher. `_league_teams_table()` zeigt `kaderstaerke_reason`/`effizienz_text`/`duel_hints`, max. 2 Kontext-Hinweise je Manager nach Wirkung sortiert (SPEC 1.4).
- `prediction_log.py` (neu 2026-08-05, SPEC_spieltagsmodell_v2.md 4.4, **zeitkritisch - "was bis zum ersten Anpfiff nicht geschrieben wird, ist dauerhaft verloren"**, deshalb vor Spieltag 1 scharfgeschaltet statt danach): drei Dateien, alle im Repo gehalten (kein Löschen, `.github/workflows/briefing.yml`s bestehendes `git add data/` erfasst die neuen Unterordner automatisch mit). `save_matchday_prediction()` (Datei A, `data/predictions/<liga>_md<N>.json`) schreibt bei JEDEM Lauf VOR `kickoff_first` (aus `fixtures.get_season_start_date()`, nur für die 2. Bundesliga verfügbar - La Liga bekommt keine Datei, kein Rateversuch) - der letzte Lauf vor Anpfiff gewinnt automatisch, danach eingefroren. Enthält je Manager die komplette Startelf MIT allen `ep_factors` (Basis für die spätere Abweichungszerlegung). `save_daily_bids()` (Datei C, `data/predictions/bids_<datum>.json`) läuft täglich, eine Datei für beide Ligen. `save_matchday_actuals()` (Datei B, `data/actuals/<liga>_md<N>.json`) schreibt nach Spieltagsende. **Scope bewusst auf Manager-Ebene begrenzt** (`ranking.us[].mdp`, bereits verifizierter Endpoint, kein Zusatz-Call) - die im Spec-Beispiel gezeigte Player-Level-Abweichungszerlegung ("-48 Einsatz Guedes 14 Min...") bräuchte je Mitspieler-Kader zusätzliche `ph`-Abrufe, hier NICHT gebaut (vor dem ersten echten Spieltag ohnehin nicht validierbar gewesen). `deviation_report()` vergleicht A gegen B sobald beide vorliegen: mittlerer Fehler + Korridor-Trefferquote, automatisch im Report des Folgetags (`main.py`, `html_report._model_health_banner()`). `matchday` ist für Spieltag 1 hartkodiert (`main.py`, `TODO` für eine echte Ableitung ab Spieltag 2 - noch nicht verifizierbar ohne echten zweiten Spieltag).
  **Ersetzt Abschnitt 4.4, SPEC_lernzyklus.md (2026-08-05) - Erweiterungen:** (a) **Gewichte versioniert** (1.1): `active_weights_version()` liest die höchste `data/weights/v<N>.json` (aktuell `v1.json` - Baseline, dokumentiert 1:1 die in `coach.py` hartkodierten Konstanten). `save_matchday_prediction()` stempelt jede Prognosedatei mit `weights_version`. `coach.py` lädt diese Werte noch NICHT dynamisch aus der Datei - bewusst aufgeschoben, bis Stufe 2 (Regression ab Spieltag 5) eine echte v2 mit abweichenden Koeffizienten liefert. (b) **Änderungsnachweis** (5.3): `diff_predictions()` vergleicht die aktuelle Manager-Prognose gegen die zuletzt gespeicherte (vor dem Überschreiben geladen) und benennt für Änderungen ≥5 Punkte die dominante Ursache (`_biggest_factor_change()`). **Bugfix, live beim ersten Testlauf gefunden**: die erste Fassung suchte die größte FAKTOR-Wert-Änderung unabhängig von ihrem Punkte-Einfluss - ein Team fiel um 40 Punkte, die gemeldete "Ursache" veränderte den betroffenen Spieler nur um ~2 Punkte. Fix: Vergleich jetzt über den echten Punkte-Delta je Spieler; dominiert kein einzelner Spieler mindestens die Hälfte des Team-Deltas, wird ehrlich "verteilt auf N Spieler, keine dominante Einzelursache" gemeldet statt eine irreführend kleine Teilursache als DIE Erklärung auszugeben - live verifiziert (eigenes Team, Statusfarbe eines Spielers wechselte zwischen zwei Läufen grün→grau, korrekt als dominante Ursache erkannt). (c) **Anomalie-Erkennung Stufe A** (Abschnitt 3, rein statistisch): `detect_anomalies()` markiert Beobachtungen mit Fehler > 3× Median-Fehler, löscht nichts, in `deviation_report()`s `rows` integriert. **Stufe B (KI-Klassifikation einmalig/systematisch/unklar) und Stufe 2 (Regression) bewusst NICHT gebaut** - brauchen reale Abweichungsfälle bzw. ≥600 Beobachtungen (Spieltag 5+), die es vor dem ersten Spieltag nicht gibt.
- `test_determinism.py` (neu 2026-08-05, SPEC_lernzyklus.md 5.4, "der Test gehört in den Workflow, nicht in die manuelle Prüfung"): rechnet die zuletzt gespeicherte Prognosedatei (`data/predictions/<liga>_md<N>.json`) zweimal durch dieselbe reine Berechnungsschicht (`coach.xi_prognose()`, inkl. Direktduell-Dämpfung + Sigma-Bandbreite) und vergleicht - jede Differenz ist ein Fehler. Bewusst NICHT die Live-API erneut abgefragt (die ist naturgemäß nicht wiederholbar identisch - erwartete Datenänderung, keine Instabilität im gemeinten Sinn) - der Test prüft NUR, ob unsere eigene Rechenschicht bei identischen Faktoren bitgleich reproduziert. Ohne Netzwerk-/Kickbase-Zugriff lauffähig, als eigener Schritt in `.github/workflows/briefing.yml` NACH "Briefing erzeugen" eingebunden. Live bestanden (11 Manager-Prognosen, identisch).
- `llm_insights.py` (neu 2026-07-31): optionale KI-Einordnungsschicht über Gemini (REST-API, kein SDK - Muster aus `test_gemini.py` übernommen, das den API-Zugang zuerst getestet hat). Läuft nur mit gesetztem `config.GEMINI_API_KEY`, sonst übersprungen (kein Hard-Dependency für main.py). **Grundsatz (nach erstem Live-Test verschärft, `SPEC_llm_prompt_v2.md`): das Modell ordnet ein, es rechnet nicht neu.** Verdikte/Scores kommen fertig aus dem Code; die KI prüft nur auf externe Infos (Verletzung, Trainerwechsel) und schreibt einen Kurzkommentar (max. 150 Wörter) + Spieler-Flags. Der erste Testlauf zeigte konkrete Fehlschlüsse, weil dem Modell Kontext fehlte (z.B. "Pieringer zum Zenit verkaufen", obwohl `BEOBACHTEN` sich NUR auf MW-Momentum bezieht, nicht auf die sportliche Rolle) - behoben durch: (a) festen `DOMAIN_BLOCK`-Text (Spielprinzip, Verdikt-Bedeutungen wörtlich, Kennzahlen-Glossar, Verhaltensregeln, immer mitgeschickt), (b) `verdict_scope` je Kaderspieler im Kontext (was das Verdikt NICHT aussagt), (c) `team_financials` mit Tagesertrag/Prognose statt nacktem Budget-Wert, (d) `market_targets` werden VOR dem Prompt-Bau auf `affordable` gefiltert (nicht finanzierbare Ziele gehen gar nicht erst mit). Live verifiziert: Pieringer wird jetzt korrekt NICHT zum Verkauf empfohlen ("kein Alarmsignal, da sportlich gesetzt"), Momentum- und Sport-Flags sauber getrennt (`momentum_fading`/`momentum_accelerating` vs. `in_form`/`out_of_form`/etc. - die reine Enum-Liste reichte dafür nicht, erst eine `description` direkt im JSON-Schema erzwang die Trennung zuverlässig). Ein Retry bei transienten 5xx-Fehlern ist eingebaut (im Testlauf wiederholt beobachtet, Google-seitig). Modellwahl bewusst OHNE Preview-Modelle (`PREFERRED` bevorzugt `gemini-flash-latest`/`gemini-2.5-flash`, `preview` wird hart ausgeschlossen - der allererste Testlauf wählte automatisch ein Preview-Modell). **Sicherheitsfix nebenbei**: der Gemini-Key lag im Klartext in `test_gemini.py` im Arbeitsverzeichnis (zum Glück nie committet/gepusht) - jetzt in `config.py`/`config_template.py` wie alle anderen Secrets.
  **Prompt-Neuausrichtung** (2026-08-04, SPEC_forecast_coach_scoring.md Punkt 2 - Diagnose: der Report referierte nur Zahlen, die im Dashboard ohnehin stehen, Folge der vorigen Kontext-Anreicherung): jetzt ausdrücklich umgekehrt - die Zahlen sind nur Hintergrund, `TASK_INSTRUCTIONS` verbietet explizit das Wiederholen von Zahlen/Verdikten/Kennzahlen und verlangt: Spieler ohne externe Erkenntnis komplett weglassen, drei Analysestränge (A: pro Spieler - Aufstellung/Verletzung/Trainerwechsel/Gerüchte; B: Spieltagseinordnung - WIE gewinnt/verliert ein Team, nicht WER; C: Kader-/Berichtslage-Abgleich). Neues `matchday_outlook`-Schema-Feld (Partie, erwarteter Spielverlauf, begünstigte Positionen, Grund) - live verifiziert funktionsfähig ("FC Barcelona Partien: dominantes Ballbesitzspiel... begünstigt MF"). **Wichtige Einschränkung, live geprüft**: Gemini unterstützt Google-Search-Grounding (`tools: [{"google_search": {}}]`) für echte Web-Recherche, aber ein Testcall mit dem aktuellen Free-Tier-Key lieferte sofort `429 RESOURCE_EXHAUSTED`, während derselbe Call ohne Grounding normal funktioniert - Grounding hat offenbar ein eigenes, auf diesem Key nicht freigeschaltetes (vermutlich Billing-pflichtiges) Kontingent. Deshalb läuft das Modell weiterhin OHNE Live-Web-Zugriff, nur mit Trainingsstand - für echte Aktualität bräuchte es entweder Billing auf dem Key oder eigenes Abrufen bekannter Quellen (größerer, hier nicht enthaltener Umbau). Nebenbei: Retry jetzt auch bei 400 (im Testlauf mehrfach beobachtet - derselbe Payload scheiterte mal mit "Request contains an invalid argument" und ging beim nächsten Versuch unverändert durch, offenbar ebenfalls transient Google-seitig), Antworttext wird bei 4xx/5xx mitgeloggt (vorher nur nacktes HTTPError ohne Grund).
  **Farb-Widerspruch + echter Saisonstart** (2026-08-05): jeder Kaderspieler im Kontext trägt jetzt `kickbase_color` + `kickbase_color_conflict` (gesetzt bei Farbe blau/grün + Verdikt VERKAUFEN, oder Farbe rot/grau + Verdikt STAMM) - `TASK_INSTRUCTIONS` verlangt, das IMMER zu kommentieren. Live bestätigt: Modell griff das eigenständig auf ("Rote Kickbase-Ampel signalisiert Ausfallgefahr", "trotz des grauen Kickbase-Einsatzstatus"). `days_until_season_start_est` nutzt jetzt `fixtures.get_season_start_date()` (echtes OpenLigaDB-Datum, 2. Bundesliga) statt der groben `SEASON_START_ESTIMATE`-Konstante, wenn verfügbar - für La Liga (football-data.org ohne Datumsfeld bei uns) bleibt der Schätzwert.
  **Sichtbarkeits-Fix (2026-08-05, SPEC_gebote_ki_team_KOMPLETT.md Punkt 2.1)**: `report["llm_status"]` unterscheidet jetzt explizit `"ok"` / `"no_key"` (kein `GEMINI_API_KEY` gesetzt) / `"failed"` (Key vorhanden, aber Call ist heute gescheitert - z.B. transientes 503, live beobachtet) statt beides gleich als "nichts anzeigen" zu behandeln. `html_report._llm_block()` zeigt dauerhaft einen Block, nie mehr in einem `<details>` versteckt - bei `no_key`/`failed` einen kurzen Hinweistext statt stillem Verschwinden.
  **Prompt-Gliederung neu (2026-08-05, SPEC_gebote_ki_team_KOMPLETT.md Punkt 2.2)**: die vorherigen drei freien Analysestränge (A/B/C) sind einer **festen 6-Themen-Gliederung** gewichen, damit nichts systematisch wegfällt: 1. Spieltagsbild, 2. Baustellen im eigenen Kader (Farbe gelb/rot/grau, `self_play_conflicts`, `lineup_gaps`), 3. Verletzungen/Sperren mit Rückkehr-Perspektive, 4. Transferlage, 5. Marktdynamik insgesamt, 6. Konkurrenzvergleich (Brücke zu Modul 3). Neue Kontextfelder in `build_context()`: `self_play_conflicts` (durchgereicht aus `report["self_play_conflicts"]`, s. `coach.duel_hints_for_xi()` - **algorithmisch erkannt, nicht der KI überlassen**, seit SPEC_spieltagsmodell_v2.md nur noch für Duelle innerhalb der echten Startelf), `lineup_gaps` (nur gesetzt bei unbesetzten Slots), `league_comparison` (eigener Rang + Prognose-Abstand zum Spitzenreiter aus `report["league_teams"]`/`report["my_uid"]`, Modul 3 - die KI soll DIESE Zahlen kommentieren, keine eigenen erfinden). Live verifiziert: das Modell griff `self_play_conflicts` und `league_comparison` in derselben Antwort korrekt auf ("Direkte Duelle im Kader dämpfen das Punktepotenzial... Der Rückstand von 24,5 Punkten auf Spitzenreiter SvenNumeroUno...").
  **Robustheit überarbeitet (2026-08-05, SPEC_lernzyklus.md Abschnitt 6, Anlass: "Heute nicht verfügbar (Kontingent oder API-Fehler)" für eine Liga, während die andere lief)**: `_call_gemini()` liefert jetzt IMMER ein Diagnose-Dict (`{"ok", "status_code", "error_text", "finish_reason", "tokens_in", "tokens_out", "model", "quota_kind"}`) statt zu werfen oder die Fehlerantwort zu verwerfen - "jede Ursachenanalyse war sonst Raten". `generate_insights()` gibt jetzt `(insights, diagnostics)` zurück (Tupel, Aufrufer `main.py` angepasst). **429 wird klassifiziert** (`_classify_429()`, aus dem Fehlertext, NICHT nur dem Statuscode - RPM/TPM/RPD sind laut Spec grundverschieden zu behandeln): RPM/TPM/503/400 bekommen Retry mit wachsender Wartezeit + Zufallsstreuung (5s/15s/45s ± Jitter, `retries=3`), **RPD NICHT** (verbraucht nur weiteres Kontingent) - stattdessen `_set_daily_quota_marker()` (persistiert in `data/llm_factors/quota_state.json`), sodass eine zweite Liga im selben Lauf oder ein späterer Lauf am selben Tag gar nicht erst versucht. Teilausgabe wird verwertet, wenn `report`-Text da, aber das volle JSON nicht parsebar ist (z.B. `finishReason=MAX_TOKENS`). `_pick_model()`/`EXCLUDE_MARKERS` gehärtet (zusätzlich `-tts`/`-image`/`computer-use`/`robotics`/`lyria`/`nano-banana`/`deep-research`/`antigravity` neben `preview` ausgeschlossen) - **der vermutete Auslöser (automatische Wahl eines Vorschaumodells `gemini-3-flash-preview`) reproduzierte sich beim Live-Gegencheck NICHT** (echte Modell-Liste mit 42 Einträgen inkl. gemini-3.x/3.5/3.6-Generationen abgefragt, `_pick_model()` wählte korrekt `gemini-flash-latest`) - die Härtung bleibt trotzdem als Verteidigungslinie, weil sich das Modellangebot sichtbar schnell weiterentwickelt. `html_report._llm_block()` zeigt jetzt die konkrete Ursache (Statuscode/Kontingent-Art/Modell) statt der pauschalen alten Meldung. **Nicht gebaut**: das "letzten erfolgreichen Stand mit Zeitstempel zeigen" aus Spec 6.4 (bräuchte eine Erweiterung der schlanken Snapshot-Persistenz um den vollen KI-Text) und das KI-Faktoren-Einfrieren aus 5.2a (aktuell fließen ohnehin KEINE live abgefragten KI-Faktoren in `E[Punkte]` ein - `matchday_outlook` wird nirgends mit echten Daten befüllt an `coach.expected_points()` übergeben - die Instabilitätsursache existiert im Code derzeit nicht, das Einfrieren wurde deshalb zurückgestellt, bis die Kopplung tatsächlich gebaut wird).
- `coach.py` (neu 2026-08-04, SPEC_forecast_coach_scoring.md Punkt 6 - **Grundgerüst**; **grundlegend rekalibriert 2026-08-05, SPEC_kalibrierung_fairvalue.md**, s.u.): `expected_points()` = Basis × Einsatzfaktor × Gegnerfaktor × Formfaktor × Spielverlaufsfaktor + Zu-Null-Bonus (nur TW/ABW). `optimize_lineup()`: 7 Standard-Formationen (TW fix 1, **unverifiziert gegen Kickbase** - allgemeines Fantasy-Football-Wissen, nur "4-4-2" ist über den POST-Body-Mitschnitt bestätigt), pro Formation greedy die besten Spieler je Position (bei fixen Positions-Slots zugleich das globale Optimum).
  **Lücke geschlossen (2026-08-05, SPEC_lineup_verified.md)**: `GET /v4/leagues/{id}/lineup` liefert die ECHTE gesetzte Aufstellung über `lo` - `current_lineup_status()`/`missing_positions()` nutzen das. **Kein POST**: das Setzen der Aufstellung bleibt bewusst Empfehlung.
  **`swaps_from_ideal()` ersetzt `suggest_swaps()` (2026-08-05, SPEC_spieltagsmodell_v2.md 2.1)**: die alte Fassung verglich unabhängig "stärkster Bankspieler je Position" gegen die Ist-Aufstellung, während `optimize_lineup()` separat eine ANDERE Formation/Auswahl als "ideal" auswies - beide konnten sich live widersprechen. Jetzt EINE Quelle: die Ideal-Elf (`optimize_lineup()`) ist die Wahrheit, Wechselvorschläge sind das DELTA dazu (für jeden Ideal-Elf-Spieler, der nicht in der echten Startelf steht, wird der schwächste echte Startelf-Spieler derselben Position vorgeschlagen). `knapp=True`-Flag (Differenz <8% der Erwartung, `SWAP_MARGIN`) markiert Abwägungen statt klarer Empfehlungen (SPEC 2.2).
  **Bug gefunden im ersten echten Testlauf, root-cause per Live-Beispiel gemeldet (2026-08-05, SPEC_kalibrierung_fairvalue.md "gemeinsame Ursache")**: `STATUS_PENALTY × PROB_SCORE` (alter Einsatzfaktor) lag selbst für einen fitten "grün"-Spieler bei 0,75, für "grau" bei exakt 0,0 (`PROB_SCORE[5]=0.0`) - eine Verletzung der **Grundregel**, dass ein durchschnittlicher Spieler in durchschnittlicher Lage Faktor 1,0 bekommen muss (Faktoren verschieben, sie dämpfen nicht grundsätzlich; einzige legitime Ausnahme ist der Einsatzfaktor selbst, aber ein blauer Stammspieler MUSS dort 1,0 bekommen). Folge: Spieltagsprognose lag bei ~250-450 statt der aus `pspts`/Spieltagszahl ableitbaren realen ~900 (Kalibrierungsanker, s.u.), UND Fair Value (dieselbe Punktebasis, invertiert gegen die Preiskurve) zeigte praktisch jeden Spieler als "überbewertet". **Fix, jetzt in `coach.py` statt `scoring.STATUS_PENALTY`/`PROB_SCORE`** (die bleiben für `score_player()`s Kader-/Tagesmarkt-Skala unangetastet, über viele Feedback-Runden kalibriert):
  - `EINSATZ_FACTOR` direkt an der Kickbase-Farbe verankert: blau 1,00 · grün 0,95 · gelb 0,60 · rot 0,25 · grau 0,10 (`einsatzfaktor()`, `st==2`/verletzt deckelt zusätzlich bei 0,10)
  - `opponent_factor(pos, win_prob, liga_avg_win_prob)`: `1 + k·(Sieg-WK − Liga-Ø-Sieg-WK)`, geclippt auf (0.80, 1.25), `k` positionsabhängig (`OPPONENT_K`: TW 0,90 · ABW 1,00 · MF 0,50 · ANG 0,75) - zentriert auf die ECHTE Liga-Ø-Sieg-WK (`fixtures.league_avg_win_prob()`, ~35-40% wegen Unentschieden als drittem Ausgang), nicht mehr auf 0,5
  - `form_factor(ap, ph)` (NEU, eigenständiger Multiplikator statt der alten Blend-Logik in `scoring.form_raw`): aktuelle Form relativ zum eigenen Saisonschnitt, 0,80-1,25, neutral 1,0 ohne genug gespielte Spieltage. `_punktebasis()` ist dadurch jetzt ein reiner Anker (`ap` bzw. MW-/Peer-Schätzung ohne Historie), OHNE Blend - vermeidet Doppelzählung mit `form_factor`, sobald `current_season_days` irgendwann aktiviert wird
  - `zu_null_bonus(pos, win_prob)` (NEU, SPEC 2.3): additiver Punkte-Term für TW/ABW aus einer groben Sieg-WK→P(zu Null)-Stützpunkttabelle (`ZU_NULL_ANCHORS`: 15%→12%, 40%→25%, 65%→40% - Startwerte, noch nicht kalibriert), TW hat einen "Paraden-Sockel" (`ZU_NULL_TW_FLOOR=0.15`, fällt nie unter diesen Boden - punktet auch im Underdog-Spiel über Paraden)
  - `_bandwidth()`: Einzelspieler-Bandbreite E±1,0×σ (**echte Sigma, s.u.**, nicht mehr fest)
  Erster Backtest nach diesem Fix: Median-Prognose stieg von ~350 auf ~549 (Anker ~897 aus `pspts`/34) - Verbesserung, aber die 25%-Toleranz wurde noch NICHT erreicht (Abweichung ~-39%). **Zweiter, entscheidender Bug gefunden über die neue Diagnose-Kaskade (SPEC_spieltagsmodell_v2.md, s.u.)** - nicht die Faktoren waren weiterhin falsch kalibriert, sondern eine fehlende Datenquelle, s. `league_teams.py`-Eintrag unten. Nach BEIDEM Fixes: **Abweichung -4%, PLAUSIBEL**.

  **SPEC_spieltagsmodell_v2.md (2026-08-05, Livelauf-Nachbesserung vor Spieltag 1) - weitere Ergänzungen:**
  - `diagnose_prognose(xi)` (Punkt 1.2, Pflicht-Diagnoseausgabe): kaskadierte Tabelle Σap → ×Einsatz → ×Gegner → ×Form → +Zu-Null = Prognose, mit dem jeweils EFFEKTIVEN Faktor je Schritt - macht sofort sichtbar, welcher Faktor wie viel Niveau kostet. Wird in `main.py` automatisch für jeden Manager gedruckt, der >25% vom EIGENEN `pspts`-Anker abweicht. **Diese Tabelle hat den zweiten Bug (s. `league_teams.py`) live aufgedeckt** - Einsatzfaktor lag bei ×0,6 für praktisch jeden Manager, obwohl der eigene Kader (andere Datenquelle) überwiegend blau/grün zeigte.
  - `fixtures.league_avg_win_prob()` verengt auf `next_n=1` (Punkt 1.2: "Mittelwert der Sieg-WK über alle Partien DES Spieltags", nicht über mehrere künftige Spieltage gemittelt).
  - Echte Sigma-Bandbreite statt fester ±15%/±-Prozent (Punkt 1.1): `player_sigma(ph, pos, basis)` - aus `ph` (≥4 Vorsaison-Spieltage) echte Standardabweichung, sonst Positions-/Preisklassen-Schätzung (`SIGMA_ESTIMATE_FACTOR`: TW 0,25 · ABW 0,35 · MF 0,30 · ANG 0,40, geflaggt `sigma_geschaetzt`). `xi_prognose(xi, matcher)` aggregiert das zur Team-Bandbreite: `Var(Team) = ΣVar(Spieler) + Klumpen-Korrelation` (mehrere Spieler desselben Vereins erhöhen die Varianz zusätzlich) - löst den Kernkritikpunkt, dass ein "drei Stürmer, hohe Streuung"-Team vorher exakt dieselbe relative Spanne hatte wie ein ausgeglichenes.
  - **Direktduelle neu gefasst (Punkt 2.1/2.3, ersetzt das bisherige `adjust_for_self_play_duels()`/`detect_self_play_conflicts()`)**: `find_self_play_pairs(xi, matcher)` erkennt Duelle jetzt NUR noch, wenn BEIDE Spieler in der übergebenen STARTELF stehen (Bankspieler zählten vorher fälschlich mit). `xi_prognose()` dämpft den ergebnisabhängigen Anteil (Gegnerfaktor-Ausschlag + Zu-Null-Bonus) für Duell-Paare halb, OHNE die Spieler-Dicts zu mutieren (`_damped_points()`) - dieselbe Elf kann in mehreren Kontexten (Ideal- vs. Ist-Aufstellung) unterschiedlich zu werten sein, eine Mutation hätte das verunmöglicht. `duel_hints_for_xi()` liefert die Text-Hinweise inkl. Einschätzung, wer die bessere Wahl ist.
  - `formation_gap_reason(players)` (Punkt 1.3, Bugfix): wenn `optimize_lineup()` für KEINE der 7 Formationen genug Spieler einer Position findet (z.B. ein 13-Mann-Kader), gab es bisher ein stummes "?" bei Kaderstärke/Effizienz - jetzt wird die fehlende Position konkret benannt (`squad_analysis.MIN_POS_COUNT`).
  **`fair_value()` (2026-08-05, SPEC_gebote_ki_team_KOMPLETT.md 1.2, Faktoren seit dem Rework oben ebenfalls normalisiert)**: "was ist der Spieler sportlich wert" - Gegenstück zu `expected_points()`, teilt sich `_punktebasis()`, MIT `team_factor()` (`TEAM_FACTOR_RANGE=(0.80,1.20)`, symmetrisch um 1.0), OHNE Spielverlaufsfaktor/Zu-Null-Bonus (Fair Value soll die stabilere, saisonweite Einordnung sein, nicht matchday-volatil). Nutzt `scoring.invert_price_curve()`.
  **Skalen-Bugfix (2026-08-05, beim ersten echten Testlauf gefunden, unabhängig vom Einsatzfaktor-Bug oben)**: die erste Fassung multiplizierte Einsatz×Gegner×Team VOR der Kurven-Inversion auf die Punktebasis - Skalenbruch, weil die Preiskurve auf dem ROHEN Saisonschnitt kalibriert ist. Ein Spieler mit Ø 89 Punkten/100% Preis-Leistung bekam denselben Fair-Value-Bodenwert wie zwei punktehistorielose Ligawechsler. Fix: die Kurve wird auf der ROHEN Punktebasis invertiert, erst DANACH wird der resultierende Marktwert linear mit Einsatz×Gegner×Team skaliert. Live nach BEIDEN Fixes (Skala + Einsatzfaktor) verifiziert: Preiskurven-Selbstprüfung 47%/52% "über Erwartung" in beiden Ligen (PLAUSIBEL), Fair Value zeigt jetzt eine gesunde Mischung aus über-/unterbewertet statt durchgängig "überbewertet".
  Direktduelle live gegengeprüft (2. Bundesliga, echter Spielplan über openligadb): die gemeldeten Paare (z.B. "Manu (Cottbus) trifft auf Thórdarson (Hannover)") stimmen exakt mit den über `os`/`ht` verifizierten echten Kickbase-Gegnerdaten überein - kein Fuzzy-Match-Fehltreffer. **Bewusste Heuristik-Einschränkung bleibt bestehen**: bei der La-Liga-Fallback-Quelle (the-odds-api, Vorsaison-Testspiele statt echtem Spielplan) fielen deutlich mehr Treffer an - plausibel durch unregelmäßige Testspiel-Ansetzungen, nicht abschließend verifiziert.
  **`formation_hint()` (neu, SPEC 2.4)**: Textbaustein zur Formationsdynamik (Fünferkette + hohe Zu-Null-Erwartung verstärkt sich, Fünferkette gegen starke Gegner = Klumpenrisiko, drei Stürmer = hohe Erwartung UND hohe Streuung), rein informativ in `league_teams.py`/`html_report._league_teams_table()` angezeigt.
  **Nicht gebaut** (explizit "Woche 2" laut Spec, mittlerweile TEILWEISE durch Modul 3/`league_teams.py` abgedeckt): 6.2 Punktetyp-Streuung für situationsabhängige Aufstellung, 6.5 Rückkopplungs-/Lern-Protokollierung.
- `league_teams.py` (neu 2026-08-05, SPEC_gebote_ki_team_KOMPLETT.md Abschnitt 3, **"das laut Nutzer wichtigste Modul"** - erst im Vergleich zu den anderen Managern wird der eigentliche Handlungsbedarf sichtbar): Team-Analyse ALLER Liga-Manager, ermöglicht durch die in Abschnitt 6 verifizierten Mitspieler-Endpoints (echte gesetzte Elf über `lo`, keine Bestmöglich-Annahme mehr nötig). `fetch_team_map()` (Competition-Tabelle, 1 Call) + `analyze_manager()` je Mitspieler aus `ranking.us` (12-23 Requests/Liga/Lauf). Je Manager: `expected_points()` je Spieler (Wiederverwendung coach.py, ohne Spielverlaufsfaktor - kein KI-Kontext pro Fremdkader vorgesehen, MIT `liga_avg_win_prob`), echte Startelf/Bank über `lo`, `prognose`/Bandbreite jetzt über `coach.xi_prognose()` (echte Sigma + Direktduell-Dämpfung NUR innerhalb der Startelf, s. `coach.py`), `kaderstaerke` (Optimierer-Ergebnis über den GESAMTEN Kader) inkl. `kaderstaerke_reason` bei nicht berechenbarer Formation, `effizienz` + `effizienz_text` (Klartext statt nackter %, SPEC 1.3: "mit optimaler Aufstellung wären +38 Punkte möglich"), `tiefe`, `klumpenrisiko`, `formation_hint`, `duel_hints`. **Kein Peer-Vergleichswert (3.2, SPEC_kalibrierung_fairvalue.md)** - bewusst ausgeklammert (bräuchte Teamstärke+Farbe je Spieler und den ligaweiten Peer-Lookup aus `league_board.py`, dort erst nach der vollen Populations-Analyse verfügbar).

  **KRITISCHER BUG gefunden über die neue Diagnose-Kaskade (2026-08-05, SPEC_spieltagsmodell_v2.md 1.2)**: Nach dem Einsatzfaktor-Rekalibrierungsfix (s. `coach.py`) blieb die Median-Spieltagsprognose bei -39% Abweichung vom `pspts`-Anker hängen. Die neue `diagnose_prognose()`-Kaskade zeigte den Grund sofort: "Einsatz ×0,6" für PRAKTISCH JEDEN Manager - EINSCHLIESSLICH des eigenen Teams, dessen `squad_classified`-Pfad (über `get_player_details`, andere Datenquelle) korrekt überwiegend blau/grün zeigt. Ursache: `GET managers/{uid}/squad` (verifiziert Abschnitt 6, kickbase_api.py) liefert **KEIN `prob`-Feld** (die 17 dokumentierten Felder enthalten `st`, aber nicht `prob`) - `p.get("prob", 3)` defaultete für JEDEN Mitspieler-Spieler auf gelb (Einsatzfaktor 0,60), unabhängig von seinem echten Status. Fix: `_estimate_prob(p)` nutzt `lo` (der Manager hat den Spieler bereits REAL in die Startelf gestellt - ein stärkeres Signal als Kickbases eigene Prognose) + `st`: fit (st=0) und in der Startelf → prob≈2 (grün); verletzt (st=2) → prob≈4 (rot), unabhängig vom Lineup-Status; sonst neutral prob=3 (gelb). **Ergebnis nach dem Fix: Abweichung -4%, PLAUSIBEL** (vorher -39%) - dieser eine Datenquellen-Bug war der dominante Rest-Fehler, keine weitere Modell-/Faktorkalibrierung nötig gewesen.

  In `main.py` nach der Kader-Klassifizierung aufgerufen, Tabelle in Konsole + `html_report._league_teams_table()` (Sektion "👥 Spieltagsprognose" nach der Aufstellungsempfehlung, vor dem Transfermarkt-Modul, inkl. max. 2 Kontext-Hinweisen je Manager nach Wirkung sortiert - SPEC 1.4, keine wortgleiche Wiederholung mehr für jeden Manager). Ergebnis zusätzlich als `report["league_teams"]`/`report["my_uid"]` - Brücke zu `llm_insights` Thema 6 UND Basis für `main._check_pspts_anchor()`/`diagnose_prognose()`/`prediction_log.py` (s.u.).
  **Determinismus-Tiebreaker (2026-08-05, SPEC_lernzyklus.md 5.2c)**: `managers.sort()` sowie die analogen Sortierungen in `league_board.py` (Qualitäts-/Deal-Listen, Climbers), `squad_analysis.market_vs_squad()` und `coach._best_eleven_for_formation()` bekamen alle einen eindeutigen Zweitschlüssel (Spieler-/Manager-ID) - bei exakter Punktgleichheit war die Reihenfolge vorher unbestimmt (Python-Sort ist zwar stabil, aber die Eingabereihenfolge selbst war nicht garantiert reproduzierbar). Grundvoraussetzung dafür, dass der Änderungsnachweis (s.u.) echte Datenänderungen von Sortier-Artefakten unterscheiden kann.

  **SPEC_ranking_faktoren_llm.md (2026-08-06) - drei Fixes:**
  **(1) Ein Rechenweg statt zwei**: die eigene Detailansicht (`main.py`, aus `get_player_details()` mit echtem `prob`) und `league_teams.py` (`managers/{uid}/squad`-Näherung `_estimate_prob()`) berechneten die eigene Spieltagsprognose bisher unabhängig doppelt - live gefunden **916 vs. 840 P für denselben Kader/Spieltag**. Fix: `build_league_teams(..., own_uid, own_entry)` - `main.py` baut `own_entry` aus den bereits oben berechneten (präziseren) Daten zusammen, für `own_uid` wird `analyze_manager()` gar nicht mehr aufgerufen (spart nebenbei einen API-Call). Live verifiziert nach dem Fix: Detailansicht ("AKTUELL GESETZTE ELF") und Ranking-Tabelle zeigen jetzt identisch 1069,6 P (2. Liga) bzw. 1174,0 P (La Liga) für `christianjens`.
  **(2) Untergrenze + MW-Sockel gegen implausible/negative Erwartung**: `coach._punktebasis()`s `has_data`-Gate behandelte JEDEN von Null verschiedenen `ap`-Wert als voll belastbar, ohne Untergrenze - live gefunden Reese (Wolfsburg, 25-Mio-Neuzugang) mit `ap`≈14 (weit unter jedem Topspieler-Niveau) und Gouram (Hertha) mit **negativem** `ap`, beides ungebremst durch die Faktorenkette bis in die Prognose durchgereicht. Fix: `basis = max(ap, mv_implied_form(mv)×130)` - der Markt (Community-Marktwert) ist ein besserer Bodenwert als ein einzelner, evtl. auf wenigen Spieltagen beruhender Rohwert (deckt sich mit dem CLAUDE.md-Grundsatz "fehlende/dünne Daten ≠ schlechter Spieler"), löst nebenbei "kein negativer Erwartungswert" ohne künstlichen harten Nullpunkt (`mv_implied_form` ist per Definition ≥0). Liefert zusätzlich `quelle` (`"real"`/`"real_mv_floor"`/`"peer"`/`"mv_estimate"`) für die "geschätzt"-Kennzeichnung. Live verifiziert: Reese jetzt Ø 125 P, Gouram 34,8 P (vorher -11,1 P) - beide plausibel im Bereich vergleichbarer Spieler. Zusätzlich eine Plausibilitätsprüfung in `league_teams.analyze_manager()`: `E[Punkte]<25` bei einem blauen/grünen gesetzten Startelf-Spieler wird als Konsolenwarnung geloggt (fast immer ein Datenproblem, kein echtes Signal) - live ausgelöst und beobachtet (La Liga: Bernal, Redondo, Galilea, Villares, García).
  **(3) Duelle partieweise statt paarweise**: `coach.find_self_play_pairs()` gruppierte nach SPIELER-Paar - bei 2+ betroffenen Spielern je Seite erschien dieselbe reale Partie mehrfach als eigene Zeile (live im Test gefunden: PaulBowa zeigte "Otto vs. Keller" UND "Keller vs. Zoma" für ein einziges Nürnberg-Dresden-Spiel, KickbaseUser4660 sogar "García" in DREI separaten Zeilen). Ersetzt durch `coach.find_self_play_matches(xi, matcher)` - gruppiert nach TEAM-Paar, ein Eintrag je realer Partie mit allen beteiligten Spielern beider Seiten. `xi_prognose()`s Dämpfung nutzt das identisch (nur der ID-Set für `damped_ids` wird jetzt aus Matches statt Paaren gebildet, keine Verhaltensänderung). `duel_hints_for_xi(xi, matcher, own=False, max_hints=2)`: max. 2 Einträge je Aufruf, Handlungsempfehlung ("X ist die beste Wahl") NUR für das eigene Team (`own=True`, in `main.py`s Aufruf für `self_play_conflicts` gesetzt) - bei fremden Kadern (`league_teams.py`, weiterhin `own=False`/Default) nur der Hinweis, dass sich der Ergebnisbonus gegenseitig aufhebt, keine Empfehlung für fremde Kaderentscheidungen.
  **(4) KI-Aufrufzähler (Punkt 6.1, "erste Maßnahme vor jeder Anbieterdiskussion")**: `llm_insights._log_call()`/`call_summary()` zählen jeden tatsächlichen Gemini-HTTP-Request (getrennt nach `list_models`/`generate_content`, inkl. Retries) in einem modul-globalen Log; `main.py` druckt die Summe nach beiden Ligen. `_list_models()` zusätzlich modul-global gecacht (`_models_cache`) - lief vorher pro Liga neu, obwohl sich das Modellangebot innerhalb eines Prozesslaufs nicht ändert. Untersuchung bestätigte die Spec-Annahme: die Architektur ruft bereits nur "ein gebündelter Call je Liga" (`generate_insights()` wird genau 1x pro `run_league()` aufgerufen) - der gemeldete 429 RPM lag vermutlich an Retries innerhalb eines transienten Fehlers, nicht an einer versteckten Schleife; die Zählung macht das jetzt sichtbar/belegbar statt geraten.
  **Geprüft, nicht gebaut**: Über/Unter-2,5-Tore-Quoten (Punkt 4.6) - die Spalten (`Avg>2.5`/`Avg<2.5`/`B365>2.5`/`B365<2.5`) existieren in football-data.co.uk/fixtures.csv, aber die Datei enthält aktuell (tiefe Sommerpause) nur schottische Ligen (`SC0-3`), keine `D2`/`SP1`-Zeilen - Füllgrad für unsere Ligen kann erst nach Saisonstart geprüft werden, zurückgestellt.
- `analytics.py`: nur Kompat-Wrapper, kann weg wenn nichts mehr importiert.
- `retrospective.py` (neu 2026-08-07) + `retrospective_data.py` (neu 2026-08-10):
  Wasserfall-Zerlegung (s. SPEC_punkteformel_final.md-Eintrag) plus Player-
  Level-Ist-Werte-Fetcher (`fetch_player_actuals()`, cached in
  `data/actuals/<liga>_md<N>_players.json` - teuer, ~1 Request/Spieler,
  deshalb idempotent). `analyze_matchday1.py` (neu, einmaliger Analyselauf,
  kein Teil der main.py-Pipeline) nutzt beide für die AUSWERTUNG_spieltag1.md-
  Untersuchung, s. eigener Abschnitt unten.

## AUSWERTUNG_spieltag1.md (2026-08-10/11) — der eigentliche Basis-Bug

**Auftrag**: User meldete "Prognosen waren systematisch zu optimistisch"
nach Spieltag 1. Die Retrospektive-Untersuchung ergab etwas ANDERES als
erwartet - kein zu hoher, sondern (an mehreren unabhängigen Messpunkten)
ein zu NIEDRIGER Ausgangswert, UND einen echten, live gefundenen Bug, der
Tage nach Spieltag 1 sichtbar wurde und den User erst dazu brachte,
nachzufragen ("auf der Seite standen wahnsinnig hohe Predictions, ~1500 P
bei manchen Spielern").

**Schritt 1 - Ursprüngliche Prämisse widerlegt**: Drei unabhängige Prüfungen
zeigten das GEGENTEIL von "zu optimistisch": (a) `ranking.us[].mdp`
(offiziell) Ø 1054,5 P Spieltag 1 vs. `pspts/34`-Anker 897,2 P (+17,5%),
(b) Team-Totals: 9/11 Manager schnitten BESSER ab als prognostiziert, (c)
eigener Kader spielerweise verglichen: Summe der 9 vorhersagbaren Spieler
1061 real vs. 867 prognostiziert (+22%). Ursache für den Gesamteindruck laut
User (im Nachhinein identifiziert, s.u.) war NICHT diese Abweichung, sondern
ein separates, tatsächlich vorhandenes Symptom mit Verzögerung.

**Schritt 2 - Datenlücke gefunden, macht Spieltag-1-Retrospektive
unbrauchbar**: der letzte `main.py`-Lauf vor Anpfiff (13:11 UTC) war NICHT
der letzte vor der echten Aufstellungs-Deadline (~18:29-18:30 UTC) - ALLE
11 Manager (inkl. des eigenen Teams, 2 Swaps) änderten ihre Startelf danach
noch. `data/predictions/1899_md1.json` enthält dadurch veraltete Elf-
Zusammensetzungen; ein spielerweiser Soll-Ist-Vergleich für Spieltag 1 ist
im Nachhinein nicht mehr sauber möglich (Lehre für künftige Spieltage:
main.py mehrfach bis kurz vor die reale Deadline laufen lassen, nicht nur
einmal Stunden vorher).

**Schritt 3 - der eigentliche, noch AKTIVE Bug gefunden**: der User konnte
den Ursprung seines Eindrucks konkret benennen (Report-Auszug mit
Teamprognosen von 1544/1402/1277 P und Einzelspieler-Projektionen wie
"Wanitzek 278,9 P" für EIN Spiel). Ursache: `coach._punktebasis()` vertraute
`ap` (Kickbases echtem Punkteschnitt) ab dem ERSTEN echten Spieltag (n=1)
zu 100% - live belegt: Wanitzek erzielte an Spieltag 1 320 Punkte in einem
Einzelspiel (Ausreißer nach oben, `ph=[{"hp":true,"p":320}]`), `ap` sprang
dadurch sofort auf 320 und wurde für Spieltag 2 UNGEBREMST als "typischer"
Wert übernommen - genau der vom User vermutete Mechanismus ("wurden Punkte
nochmal draufgerechnet"), nur ohne echte Dopplung im Code: ein einzelner
Ausreißer-Spitzenwert wurde zur neuen Normalität für die nächste Prognose.

**Fix (`coach.py`, `PUNKTEBASIS_N0=8`)**: `_punktebasis()` blendet `ap` jetzt
mit `n/(n+n₀)` (SPEC_punkteformel_final.md 8.3-Prinzip, hier mit kleinerem
`n₀` für die EINZELSPIELER-Ebene statt der globalen Kalibrierung) gegen
einen stabileren Referenzwert (`peer_estimate`, sonst MW-Sockel) - bei n=1
zählt der reale Wert nur ~11%, bei n=8 zur Hälfte, danach überwiegt der
eigene Schnitt zunehmend automatisch, keine Wartefrist. Live verifiziert:
alle Team-Totals wieder im plausiblen Bereich (600-1100 P statt 600-1544 P).

**Begleitfund 1 - `matchday`-Hartkodierung war kein Kosmetikproblem**: das
alte TODO ("matchday für Spieltag 1 hartkodiert, Ableitung erst ab
Spieltag 2 möglich") wurde live zum echten Problem: `kickoff_first`
(`fixtures.get_season_start_date()`) rückte nach Spieltag 1 automatisch auf
Spieltag 2 vor, der Freeze in `save_matchday_prediction()` öffnete sich
dadurch erneut - `1899_md1.json` wurde durch tägliche Läufe (8./9./10.8.)
STILL mit Spieltag-2-Prognosen überschrieben, aber weiter "matchday: 1"
genannt. Fix: `fixtures.get_current_matchday()` (neu, dieselbe OpenLigaDB-
Quelle wie `get_season_start_date()`, liefert die `groupOrderID` der
frühesten unbeendeten Partie) ersetzt die Konstante. Ab jetzt schreibt jeder
Lauf in die korrekt benannte Datei (`1899_md2.json` bestätigt live).

**Begleitfund 2 - Datei B/Abweichungszerlegung verglich falschen Spieltag**:
direkte Folge von Begleitfund 1s Fix - `save_matchday_actuals()`/
`deviation_report()` liefen bisher unter demselben `matchday`-Wert wie die
Prognose (dem KOMMENDEN Spieltag), prüften aber nur "hat irgendein Spieltag
begonnen" (`mdp>0`), nicht "ist GENAU DIESER Spieltag beendet". Sobald
`matchday` korrekt auf 2 vorrückte, verglich der Report die neue Spieltag-2-
Prognose gegen `ranking.us[].mdp`, das noch Spieltag 1s echtes Ergebnis
zeigte (Spieltag 2 war ja noch nicht gespielt) - unauffällig falsch. Fix:
separater `last_completed_matchday = current_matchday - 1`-Zähler für Datei
B/Abweichungszerlegung, `matchday` bleibt für Datei A (Prognose) zuständig.
Live verifiziert: "ABWEICHUNGSZERLEGUNG · Spieltag 1" vergleicht jetzt
wieder korrekt gegen Spieltag 1s echte Werte.

**Nicht angefasst**: die alte `1899_md1.json`-Vorkickoff-Version ist durch
die tägliche Überschreibung (Begleitfund 1) nicht mehr rekonstruierbar - der
echte, unverfälschte Spieltag-1-Snapshot ist dauerhaft verloren (deckt sich
mit der eigenen Warnung in SPEC_punkteformel_final.md: "ein einziger
fehlender Snapshot ist nicht nachholbar" - hier war es kein fehlender, aber
ein nachträglich überschriebener). Keine Rekonstruktion versucht.

## Nachbesserung nach AUSWERTUNG_spieltag1.md (2026-08-11): Effizienz-Bug + echte Retrospektive-Transparenz

Zwei User-Feedbacks nach dem ersten Live-Blick auf den Report mit den
AUSWERTUNG-Fixes: (a) "mir fehlt komplett die Transparenz über die
Abweichungen" (die neue `ABWEICHUNGSZERLEGUNG`-Zeile zeigte nur eine nackte
Ø-Fehler-% ohne jede Erklärung), (b) Verwirrung über einen Manager mit 106%
"Effizienz" (Prognose/Kaderstärke) - logisch unmöglich, wenn Kaderstärke das
theoretische Maximum sein soll.

**(b) Effizienz-Bug (`coach.py`)**: `optimize_lineup()` durchsuchte NUR die 7
hartkodierten `FORMATIONS` (3-4-3 bis 5-4-1). PaulBowas echte Formation war
4-2-4 (vier Sturm-Slots) - keine der 7 Standardformationen erlaubt mehr als
drei ANG-Slots, der Suchraum war für seinen Kader strukturell unvollständig,
seine reale (nicht optimierte) Elf schlug das rechnerische "Optimum" locker
(961,6 P Ist vs. 904 P "Kaderstärke" = 106%). Fix: `coach.derive_formation(xi)`
(neu) zählt die Positionen einer konkreten Elf zu einem Formations-Dict;
`optimize_lineup(squad, also_try=formation)` (neuer Parameter) nimmt dieses
Dict zusätzlich zu den 7 Standardformationen in den Suchraum auf - die
Spielerauswahl bleibt weiterhin die BESTMÖGLICHE für diese Slotverteilung
(nicht die reale Auswahl selbst), aber die reale Formation ist jetzt
GARANTIERT Teil des Vergleichs, wodurch Kaderstärke ≥ Prognose mathematisch
sichergestellt ist (Effizienz kann nicht mehr >100% werden). Beide Aufrufer
angepasst: `main.py` (eigener Kader, `real_formation` aus
`lineup_status["xi"]`) und `league_teams.py` (Mitspieler, aus `xi`). Live
verifiziert: PaulBowa zeigt jetzt 99% statt 106%, alle anderen 15 Manager
(beide Ligen) liegen konsistent bei 91-100%.

**(a) Echte Wasserfall-Zerlegung im Report verankert**: `retrospective.py`
(Zerlegungsmechanik) und `retrospective_data.py`s `fetch_player_actuals()`
(Player-Level-Ist-Werte) lagen seit AUSWERTUNG_spieltag1.md fertig und per
Trockenlauf verifiziert bereit, waren aber nie an die tägliche Pipeline
angebunden - nur `analyze_matchday1.py` (Einmal-Analyseskript) nutzte sie.
Neu in `retrospective_data.py`: `build_ist()`/`_win_prob_ist()` (Übersetzung
eines Ist-Datensatzes in M_ist/G_ist/Z_ist, identische Herleitung wie im
ursprünglichen Analyseskript - dort unverändert belassen, keine Duplikation
der Aufrufstelle) und `build_waterfall_report(kb, league_id, league_name,
matchday, liga_avg_win_prob_now, prediction=None)` (neu, die main.py-taugliche
Zusammenfassung: lädt Datei A, holt/cached Player-Actuals, baut je Manager
die volle `retrospective.waterfall_manager()`-Zerlegung). `main.py` ruft das
jetzt direkt nach `deviation_report()` für `last_completed_matchday` auf
(Konsole: Top 5 nach Abweichung, mit Einsatz/Ausgang/Zu-Null/Leistung-
Aufschlüsselung + Prüfsummen-Warnung falls verletzt). Report-Dict um
`"retrospective"` (die Team-Liste) und `"last_completed_matchday"` erweitert.
`html_report.py`: neue `_retrospective_section()`, bewusst GANZ OBEN platziert
(gleich nach der Handlungsleiste, vor KPI-Grid/KI-Kommentar - deckt sich mit
AUSWERTUNG_spieltag1.md Abschnitt 5: "Retrospektive gehört ganz oben, vor
Markt und Kader"). Die alte nackte Ø-Fehler-%-Zeile in
`_model_health_banner()` wurde entfernt (redundant zur neuen Sektion, keine
doppelte/widersprüchliche Kurzfassung mehr).

**Kosten/Caching**: `build_waterfall_report()` kostet ~1 API-Call je
einzigartigem Startelf-Spieler über alle Manager (analog `league_board.py`,
typisch ~100-120/Liga) - aber NUR beim ERSTEN Aufruf für einen Spieltag
(`fetch_player_actuals()` cached nach `data/actuals/<liga>_md<N>_players.json`,
jeder weitere Tageslauf liest nur die Datei).

**Live-Ergebnis Spieltag 1 (2. Bundesliga) mit echten Zahlen, wichtige
Einordnung**: die neue Wasserfall-"Ist"-Summe (aus einzeln summierten
Spieler-`ph`-Werten) weicht spürbar von der offiziellen `ranking.mdp`-Summe
ab (z.B. nico: Wasserfall-Ist 1007 vs. offiziell 909; Adrian: 886 vs. 813,
durchweg höher). Das ist NICHT ein neuer Rechenfehler - alle Prüfsummen
gingen exakt auf (`checksum_ok` bei keinem der 11 Manager verletzt), die
Zerlegungsmechanik selbst ist korrekt. Ursache ist die bereits in
AUSWERTUNG_spieltag1.md dokumentierte Datenlücke: `1899_md1.json`s
gespeicherte Startelf ist die VERALTETE 13:11-UTC-Momentaufnahme, nicht die
echte ~18:30-UTC-Deadline-Aufstellung (alle 11 Manager änderten danach noch).
Die Wasserfall-Zerlegung rechnet also mit teils falschen Spielern für
Spieltag 1. **Korrektur (2026-08-11, direkt im Anschluss)**: die Annahme
"sollte sich ab Spieltag 2 von selbst auflösen" war VERFRÜHT - der User
meldete unmittelbar danach konkret falsche Zahlen ("Adrian hat keine 1093
Punkte gemacht, sondern 813"), was zeigte, dass die abweichende
Spieler-Summe nicht nur ein MD1-Sonderfall ist, sondern IMMER auftreten
kann (jede Aufstellungsänderung nach dem letzten Vor-Deadline-Lauf, nicht
nur die MD1-spezifische Datenlücke) und bis dahin unkommentiert als "Ist"
gezeigt wurde. Echter Fix statt Abwarten, s. eigener Abschnitt unten.

## Nachbesserung: Wasserfall-"Ist" stimmte nicht mit dem echten Punktestand überein (2026-08-11)

User-Meldung direkt nach der ersten Live-Ansicht der neuen Wasserfall-
Sektion: "beim rückblick zu spieltag 1 stimmt der reale punktewert nicht.
adrian hat bspw. keine 1093 punkte gemacht sondern 813" - konkret und
verifizierbar falsch, kein Missverständnis.

**Ursache**: die Wasserfall-"Ist"-Zahl war die SUMME der einzeln über
`get_player_details()` abgerufenen Spielerpunkte für die in Datei A
gespeicherte Aufstellung - für Spieltag 1 nachweislich die veraltete
13:11-UTC-Momentaufnahme (s.o.), nicht die echte Deadline-Aufstellung.
Der bereits vorhandene, unabhängig berechnete OFFIZIELLE Wert
(`ranking.us[].mdp`, exakt das, was `deviation_report()`/die
`ABWEICHUNGSZERLEGUNG`-Zeile schon korrekt anzeigte - Adrian dort bereits
813) wurde für die neue Sektion nie herangezogen.

**Fix**: `retrospective.waterfall_manager()` bekommt einen neuen optionalen
Parameter `official_actual` - ist er gesetzt und weicht von der
Spieler-Summe ab, gewinnt IMMER der offizielle Wert für `team["ist"]`/
`team["differenz"]`, die Differenz landet transparent in einer neuen
`delta_datenluecke`-Kategorie (NICHT in `delta_leistung` versteckt - eine
veraltete Aufstellungs-Momentaufnahme ist kein Leistungssignal). Die
Prüfsumme wird um genau diesen Term erweitert, bleibt also weiterhin exakt
verifizierbar. `retrospective_data.build_waterfall_report()` reicht dafür
einen neuen Parameter `official_actuals` (uid→Punkte) durch;
`main.py` baut das Dict aus denselben `deviation["rows"]`, die
`deviation_report()` ohnehin schon lädt (kein Zusatz-Call). Konsole und
`html_report._retrospective_section()` zeigen die Datenlücke jetzt explizit
("🕳️ Datenlücke -73 (Spieler-Summe 886 P statt offizieller 813 P)"), Note-
Text stellt klar: "'Ist' ist der OFFIZIELLE Spieltagspunktestand, kein aus
Einzelspielern zusammengerechneter Wert."

Live verifiziert (2. Bundesliga, Spieltag 1): Adrian zeigt jetzt exakt
813 P (vorher fälschlich 886/1093), alle anderen Manager analog korrigiert,
keine Prüfsummen-Verletzung. Dieselbe Absicherung greift jetzt bei JEDEM
künftigen Spieltag automatisch, nicht nur für den bekannten MD1-Sonderfall -
weicht eine gespeicherte Aufstellung aus irgendeinem Grund von der echten
ab, wird das ab sofort sichtbar statt unkommentiert falsch angezeigt.

## SPEC_spielertyp_matchkontext.md (2026-08-07, Prioritäten 1-3 umgesetzt)

**Punktetyp-Index in k_eff (Priorität 1)**: `scoring.punktetyp_index(profile)`
(neu, nutzt `player_reliability_profile()`s bereits vorhandene `ph`+`mdsum`-
Auswertung - dieselbe Datenbasis, die `punktetyp_label()` schon für den
Transfermarkt-Anzeigetext nutzt, jetzt zusätzlich als Zahl) liefert
`(Ø Punkte bei Sieg − Ø Punkte bei Niederlage) / Ø Punkte gesamt` - nahe 0
Rohpunkte-Typ, deutlich positiv Scorer-Typ. `coach.opponent_factor()` nutzt
das jetzt für `k_eff = k_pos × (1 − 0,5 × (1 − punktetyp_idx))` - ein reiner
Rohpunkte-Spieler bekommt eine halbierte Gegner-Sensitivität, ein reiner
Scorer die volle `k_pos`. `punktetyp_idx=None` (Standardfall ohne Sieg/
Niederlage-Stichprobe, z.B. Mitspieler-Kader über `managers/{uid}/squad`,
das kein `ph`/`mdsum` liefert) lässt `k_eff=k_pos` unverändert. Verdrahtet in
`main.py`s Kader- UND Markt-Loop (dieselbe `player_reliability_profile()`-
Instanz wiederverwendet, kein Zusatz-Call). Live verifiziert: Änderung
zwischen zwei Läufen korrekt auf "Sieg-WK-Faktor" bei einem betroffenen
Spieler zurückgeführt (`prediction_log.diff_predictions()`).

**Matchkontext aus Über/Unter- und Handicap-Quoten (Priorität 2)**: geprüft
und bestätigt gefüllt für D2 (2. Bundesliga) am Spieltag 1 - alle 9
Spieltag-1-Partien haben vollständige `B365>2.5`/`B365<2.5` und `AHh`-Werte.
La Liga (`SP1`) ist noch nicht in der `fixtures.csv` enthalten (läuft weiter
über den the-odds-api-Fallback, der keine Über/Unter-/Handicap-Daten liefert
- gracefully degradiert auf die alte Sieg-WK-Herleitung). Neu in `odds.py`:
`_match_context(row)` leitet aus den Über/Unter-2,5-Quoten die erwarteten
GESAMTTORE ab (lineare Näherung um den 2,5-Anker, ±0,5 Buchmacher-Ausschlag
≈ ±1,5 Tore, geclippt [1,5, 4,0]) und aus der Asian-Handicap-Linie `AHh` die
erwartete TORDIFFERENZ aus Heimsicht (`-AHh`, direkte Markt-Interpretation
der Handicap-Linie). `load_fixture_odds()` liefert jetzt zusätzlich
`match_context: {team: [(gegner, erwartete_tore, erwartete_tordifferenz), ...]}`
(dritter Rückgabewert - **Signatur-Änderung**, der einzige Aufrufer in
`main.py` ist angepasst). `odds.next_match_context()` (neu, analog zu
`fixture_ease_odds()`) liefert Tore/Tordifferenz für die NÄCHSTE Partie
(nicht über mehrere Spiele gemittelt - Zu-Null ist matchday-spezifisch).
`coach.zu_null_probability_from_context(pos, erwartete_tordifferenz,
erwartete_tore)` (neu) ersetzt die reine Sieg-WK-Ankertabelle, WENN
Tordifferenz-Daten vorliegen: Dominanz aus der Tordifferenz (±2 Tore ≈
±0,17 Wahrscheinlichkeitspunkte um den Anker 0,25) plus eine Dämpfung nach
erwarteten Gesamttoren (torarme Partie erhöht P(zu Null) für BEIDE Seiten,
torreiche senkt sie). `zu_null_bonus()` fällt ohne `erwartete_tordifferenz`
(La Liga, Tabellen-/the-odds-api-Fallback) unverändert auf die alte
`zu_null_probability(win_prob, pos)` zurück - keine Verhaltensänderung dort.
Verdrahtet in `main.py`s Kader-Loop UND `league_teams.analyze_manager()`
(kostenlos, da `match_context` ohnehin einmal pro Lauf aufgebaut wird - wie
schon beim `peer_lookup`-Vorbild aus SPEC_punkteformel_final.md). NICHT im
Marktloop für `fair_value()` (der nutzt bewusst keinen Zu-Null-Bonus, s.
bestehende Docstring "matchday-spezifisch/volatil, Fair Value soll
stabiler sein").

**Verlässlichkeits-Kennzahl (Priorität 3)**: `scoring.reliability_score(profile)`
(neu) - Anteil der Spieltage über der halben Ø-Punktzahl, aus derselben
`player_reliability_profile()`-Datenbasis. In `main.py`s Kader-Loop berechnet
und als `c["reliability_score"]` exponiert. **Fließt NOCH NICHT in
`Var_Leistung` ein** - diese Größe ist Teil der in `SPEC_punkteformel_
final.md` Abschnitt 6-9 beschriebenen Unsicherheitsarchitektur, die noch
nicht gebaut ist (s. dortiger CLAUDE.md-Eintrag, inkl. Korrektur: nicht
kalendarisch blockiert, nur noch nicht begonnen). Der Wert liegt bereit,
sobald diese Architektur kommt.

**Akzeptanzkriterium "läuft durch das gemeinsame PlayerEvaluation-Objekt"
NICHT erfüllbar**: dieses Objekt existiert nicht (Teil 1.3 aus
`REVIEW_architektur_KOMPLETT.md`, bewusst als mehrtägiger Umbau
zurückgestellt, s. dortiger Eintrag). Alle neuen Faktoren laufen stattdessen
durch dieselbe `ep_factors`-Dict-Struktur wie alle bisherigen Faktoren
(Basis, Einsatz, Gegner, Form, Verlauf, Zu-Null) - konsistent mit dem
gesamten übrigen Code, aber keine Vereinheitlichung im Sinne der Review.

**Geprüft, nicht verfolgt (Abschnitt 2)**: FootyStats (kostenloser Testkey nur
mit eingefrorener Demoliga, echte Daten ab 30£/Monat), football-data.org
(2. Bundesliga nicht im kostenlosen Rahmen enthalten, Spielerdaten ohnehin
kostenpflichtig), API-Football (nicht weiterverfolgt, kein zusätzlicher
Anbieter gewünscht), Scraping-Quellen wie Understat/FBref (keine stabile
Vertragsgrundlage, zusätzliches Risiko neben der bereits inoffiziellen
Kickbase-Schnittstelle) - keine dieser Quellen wurde eingebunden, wie
gefordert.

## SPEC_punkteformel_final.md (2026-08-07, Spieltag 1 - Kickoff 18:30 UTC)

**Abschnitt 1 (zeitkritisch, vor Anpfiff)**: `prediction_log.save_matchday_prediction()`
lief bereits produktiv mit echtem UTC-Zeitstempel (`datetime.now(timezone.utc)`)
und korrektem Kickoff-Freeze (`generated_at >= kickoff_first` stoppt weitere
Schreibvorgänge, beide tz-aware, verglichen gegen `fixtures.
get_season_start_date()`s live verifiziertes 07.08.2026 18:30 UTC) - verifiziert,
kein Fix nötig. `matchday=1` bleibt bewusst hartkodiert (TODO für Spieltag 2+
unverändert) - für HEUTE ist der Wert per Definition korrekt, eine neue
Erkennungslogik unmittelbar vor dem echten Anpfiff einzuführen und
UNGETESTET zu lassen wäre riskanter gewesen als der Status quo. Ein letzter
`python main.py`-Lauf um 10:04 UTC wurde sofort committet/gepusht (auch
gegen einen zeitlich früheren, bereits vorhandenen Actions-Bot-Snapshot von
08:39 UTC durchgesetzt, da meiner näher am Anpfiff und damit aktueller war -
Ausnahme vom sonst üblichen "Bot-Snapshot gewinnt"-Muster, hier bewusst nach
Zeitstempel statt nach Quelle entschieden).

**Abschnitt 2/5 - "Basis 91,0"-Bug behoben**: `coach._punktebasis()`s
MW-Sockel-Fallback (`mv_implied_form(mv)×130`, gedeckelt bei 0,7×130=91,0)
kollabierte für JEDEN Spieler ohne eigene Punktehistorie mit ausreichend
hohem MW auf denselben Wert, unabhängig von Position/Team/Farbe - live belegt
(Wahl, Pieringer, Taz, Ofli, El Kadiri alle exakt "Basis 91,0" trotz drei
verschiedener Positionen). Der eigentlich vorgesehene, differenziertere
Vergleichsgruppen-Wert (`scoring.estimate_ap_from_peers()`, Median aus
Position×Teamstärke-Drittel×Farbe) existierte zwar bereits und wurde von
`coach.fair_value()` genutzt, aber NICHT von `coach.expected_points()` (die
Zahl, die tatsächlich überall als "E[Punkte]" angezeigt wird) - `peer_lookup`
war zum Zeitpunkt der Kader-/Markt-Klassifizierung schlicht noch nicht
gebaut (die Liga-Bestenliste lief bisher NACH der Kader-Klassifizierung, weil
sie `own_ids` aus dem fertigen Kader brauchte). **Fix**: `own_ids` wird jetzt
aus dem ROHEN Kader (`squad_players`, direkt nach `kb.get_squad()` verfügbar)
statt aus `squad_classified` gebildet - dadurch kann der komplette
Liga-Bestenlisten-/Preiskurven-/Peer-Lookup-Aufbau (`league_board.
build_league_lists()`) VOR die Kader-Klassifizierung vorgezogen werden, ohne
einen einzigen Zusatz-API-Call. `peer_estimate` fließt jetzt in ALLE drei
`coach.expected_points()`-Aufrufstellen ein: eigener Kader (`main.py`,
Kader-Loop), Tagesmarkt (`main.py`, `ep_market`, `peer_est` jetzt UNGATED von
`min_price_now` berechnet statt nur für Fair Value) und Mitspieler-Kader
(`league_teams.analyze_manager()`, neuer Parameter `peer_lookup` - vorher
bewusst ausgeklammert, weil `peer_lookup` zu dem Zeitpunkt noch nicht
vorlag; jetzt kostenlos verfügbar, da derselbe Aufbau bereits für den
eigenen Kader vorgezogen wurde). Live verifiziert: dieselben Spieler zeigen
jetzt differenzierte Werte ("Punktebasis 91.0 → 120.0 bei Pieringer",
"91.0 → 50.0 bei Souza", "32.5 → 96 bei Zielinski") statt eines geteilten
Konstantwerts, Kalibrierungsanker bleibt bei -3% (weiterhin PLAUSIBEL, der
Fix hat die Gesamtkalibrierung nicht verschoben).

**Einsatzfaktor-Tabelle (M) exakt an die Spec angeglichen**: `coach.
EINSATZ_FACTOR` war bereits farbverankert, aber geringfügig anders kalibriert
(0,95/0,60/0,25 statt Spec-Werten 0,92/0,55/0,20 für grün/gelb/rot) - beides
Erstkalibrierung ohne echte Ist-Minuten dahinter, risikoarm angeglichen. **G
(Gegnerfaktor-`k_pos`) und Z (Zu-Null-Wahrscheinlichkeitstabelle) bewusst
NICHT angeglichen** - die Spec schlägt spürbar andere Werte vor (`k_pos`
grob halb so groß wie die aktuell live gegen den `pspts`-Anker verifizierten
Werte), eine blinde Übernahme Stunden vor dem ersten echten Anpfiff hätte die
bereits mehrfach live bestätigte Kalibrierung (-4% bis -3% Abweichung vom
Anker) ohne jede Gegenprobe verändert. Bleibt offen für einen dedizierten
Kalibrierungsdurchlauf, sobald echte Spieltag-1-Ist-Werte vorliegen (deckt
sich mit der Spec-eigenen Aussage "ab Spieltag 5 gegen die tatsächliche
Zu-Null-Quote kalibriert").

**`retrospective.py` (neu) - Wasserfall-Zerlegung (Abschnitt 3)**:
`waterfall_player()`/`waterfall_manager()` implementieren die in der Spec
exakt vorgegebene Kaskade (E0 Ausgangsprognose → E1 nach Einsatzzeit-Ist →
E2 nach Spielausgang-Ist → E3 nach Zu-Null-Ist → Rest = Leistung/unerklärt),
inkl. Prüfsumme (die vier Deltas müssen exakt Ist−Prognose ergeben) und
`format_manager_report()` für die Klartext-Ausgabe im Spec-Format.
**Bewusst NICHT an main.py angebunden**: die Zerlegung braucht
Player-Level-Ist-Werte (tatsächliche Einsatzminuten, tatsächliches Zu-Null,
tatsächliches Ergebnis je Partie), die aktuell nirgends erfasst werden -
`prediction_log.save_matchday_actuals()` ist bewusst auf Manager-Ebene
begrenzt (`ranking.us[].mdp`, s. bestehender CLAUDE.md-Eintrag). Eine
Quelle für Player-Level-Ist-Werte ist noch nicht identifiziert/verifiziert
(vermutlich zusätzliche `ph`-Abrufe je Mitspieler-Kader nach Spieltagsende -
unverifiziert, analog anderer defensiv scheiternder Endpoints im Projekt).

**`test_retrospective_dryrun.py` (neu) - Trockenlauf (Abschnitt 4.1)**:
lädt die ECHTE gespeicherte Prognosedatei (`data/predictions/1899_md1.json`,
reale Basis+Faktoren) und erfindet dazu deterministische Ist-Werte
(`random.Random(42)`, reproduzierbar), um NUR den Rechenmechanismus zu
prüfen, nicht die Datenverfügbarkeit. Kein Netzwerkzugriff, direkt lauffähig
(`python test_retrospective_dryrun.py`). Live bestanden: alle 11 Manager der
2. Bundesliga-Liga, Prüfsumme exakt (keine Rundungsabweichung) - der
Zerlegungsmechanismus ist nachweislich korrekt. **Läuft aktuell NICHT** als
Teil der CI (anders als `test_determinism.py`) - reiner Entwickler-Check für
den Mechanismus, kein Regressionstest für main.py-Verhalten.

**Bewusst zurückgestellt** (Abschnitt 6-9, Varianzzerlegung/Quantile/
Kalibrierung/Team-Kovarianzen): die Spec fordert eine vollständige
Unsicherheits-Architektur (Var_Leistung/Var_Niveau/Var_Spielausgang/
Var_ZuNull/Var_Einsatz je Spieler, rechtsschiefe Quantilverteilung statt
symmetrischem Intervall, Team-Kovarianzen für Vereinsbindung/Direktduell,
Vertrauensgewichtetes Nachziehen `n/(n+n₀)`) - das ist ein eigenständiges,
mehrtägiges statistisches Modellierungsprojekt, keine Bugfix-Serie, deshalb
heute nicht mehr begonnen. **Korrektur (User-Hinweis 2026-08-07, direkt nach
diesem Abschnitt)**: die ursprüngliche Begründung hier ("Spec platziert die
Kalibrierung explizit ab Spieltag 5") war UNGENAU - das "ab Spieltag 5" im
Text bezieht sich nur auf einzelne MESSWERT-ERSETZUNGEN (M-Tabelle durch
echte Ist-Minuten, Zu-Null-Wahrscheinlichkeiten durch echte Quote, jeweils
weil bis dahin schlicht keine Ist-Daten existieren), NICHT auf das
Kalibrierungs-FRAMEWORK selbst. **`SPEC_punkteformel_final.md` Abschnitt 8.3
(Vertrauensgewichtung `n/(n+n₀)`) ist die aktuell gültige Regel und läuft
laut Spec ausdrücklich "ab Spieltag 1, nur zunächst vorsichtig... keine
Wartefrist, keine willkürliche Schwelle"** - die frühere "ab Spieltag 5"-
Schwelle aus `SPEC_lernzyklus.md` betraf etwas ANDERES (Stufe 2: automatisches
Neuschreiben der hartkodierten Gewichte-Konstanten per Regression, braucht
laut jener Spec ≥600 Beobachtungen) und ist für die Intervall-/Unsicherheits-
kalibrierung hier überholt/nicht einschlägig. Der eigentliche Blocker für
Abschnitt 6-9 ist also nicht "zu früh im Kalender", sondern schlicht Umfang -
kann nachgezogen werden, sobald Zeit dafür ist, nicht erst ab einem
bestimmten Spieltag. Aktuelle `player_sigma()`/`xi_prognose()`-Bandbreite
(symmetrisch, SPEC_spieltagsmodell_v2.md) bleibt bis dahin unverändert
bestehen, liefert aber bereits die rohen Ist-Vergleichsdaten (`ep_factors`
in jeder gespeicherten Prognose), auf denen eine künftige Varianzzerlegung
aufbauen könnte, ohne das Speicherformat ändern zu müssen. Abschnitt 10 (Interaktivität: Drill-down,
Aufstellung/Transfer durchspielen) und 11 (Robustheit: Entwicklungsmodus,
Statusleiste) ebenfalls nicht begonnen - explizit UI-/Infrastruktur-Ausbau,
kein Korrektheitsproblem am ersten Spieltag.

## REVIEW_architektur_KOMPLETT.md (2026-08-07, Architektur-Review des Livereports)

**Wurzelbefund (Teil 1)**: "Score"/"Fair Value"/"erwartete Punkte" wurden je
Report-Abschnitt unabhängig neu berechnet statt aus einer kanonischen Quelle
gelesen - live belegt (Reichert 4 verschiedene "Score"-Werte, Zec Fair Value
0,91 Mio vs. faktisch ~11 Mio). Der Review schlägt eine vollständige
`PlayerEvaluation`-Struktur vor (Teil 1.3, "eine Bewertung je Spieler, überall
gelesen"). **Bewusst NICHT als Ganzes umgesetzt** - ein Umbau, der jedes Modul
(coach/scoring/league_board/squad_analysis/main/html_report) auf eine
gemeinsame Datenstruktur umstellt, ist ein mehrtägiges Vorhaben mit echtem
Regressionsrisiko, und zwar **ausgerechnet am ersten echten Spieltag
(07.08.2026)**, an dem das Tool live genutzt wird - der Review selbst reiht
das unter "Diese Woche", nicht "vor dem ersten Anpfiff" ein. Stattdessen:
**gezielte Fixes an jeder konkret belegten Divergenz** (unten), die den
Großteil des praktischen Schadens beheben, ohne die Architektur an einem
Live-Tag komplett umzubauen. Eine echte Vereinheitlichung bleibt ein
sinnvoller Folgeschritt (s. Baustellen-Liste).

**Score-Label-Disambiguierung (Item 1, statt Rescale)**: `scoring.score_player()`
(absolute, auf die 2. Liga kalibrierte Skala, für Kader-/Marktkarten) und
`league_board.py`s ligarelative Perzentil-Scores (`quality_total`/`value_total`,
für die Bestenlisten) sind laut CLAUDE.md **bewusst zwei getrennte Systeme**
(Rescale würde die über viele Runden kalibrierten STAMM/HALTEN-Schwellen
invalidieren, s. `league_board.py`-Eintrag oben "Bewusst NICHT angefasst") -
aber beide hießen bisher identisch "Score", ohne dass ersichtlich war, dass
es zwei verschiedene Zahlen sind. Sofortmaßnahme statt Vereinheitlichung:
Labels disambiguiert - "Kader-Score" (`scoring.score_player()`, Kader-/
Marktkarten, `main.py`+`html_report.py`+`report_builder.py`) vs. "Liga-Score
(Qualität)"/"Liga-Score (Deal)" (`html_report.BOARD_SCORE_LABEL`,
Bestenlisten). Dieselbe Zahl für denselben Spieler kann weiterhin zwischen
beiden Systemen abweichen (das ist beabsichtigt, zwei unterschiedliche
Fragen), aber jetzt immer erkennbar, WELCHES System gemeint ist.

**Marginaler statt absoluter Elf-Beitrag (2.3)**: `squad_analysis.
bridge_to_ideal_elf()` prüfte `free_slots > 0` (freier KADER-Platz, z.B. "3
freie Kaderplätze") VOR dem Ideal-Elf-Vergleich auf derselben Position - ein
Torwart (immer genau 1 Slot je Formation) mit freiem Kaderplatz bekam dadurch
seine ABSOLUTE Erwartung als "+X P" angezeigt, obwohl der gesetzte Torwart
die Elf gar nicht verlässt (live belegt: Reichert "+138 P" trotz gesetztem
Hoffmann mit 101,7 P). Fix: der Ideal-Elf-Vergleich auf derselben Position hat
jetzt IMMER Vorrang und liefert den echten marginalen Zugewinn
(`ep_market - weakest["expected_points"]`); der freie-Kaderplatz-Zweig (keine
Vergleichszahl, absolute Erwartung) greift nur noch, wenn die Position in der
Ideal-Elf wirklich unbesetzt ist. Live verifiziert: Reichert zeigt jetzt
korrekt "+35 P" (verdrängt Hoffmann, 102 P) statt der alten "+138 P".

**Verdikt/Tier/"NICHT BIETEN"-Widerspruch (2.4, Item 7)**: `bid["verdict"]`
(Trading-Obergrenze/Fair-Value-Prüfung in `bid_advisor.py`) und `affordable`
(reine Kaufkraft-Prüfung in `squad_analysis.market_vs_squad()`) liefen bisher
unabhängig - eine Karte konnte "✅ KLARE KAUFEMPFEHLUNG" ZEIGEN und im selben
Atemzug "🚫 NICHT BIETEN" drucken, weil `finalize_headline_recommendations()`/
`recommendation_tier()` beide nur `affordable` lesen, nie `bid["verdict"]`.
Fix: `bid_ok = bid.get("verdict") != "nicht_bieten"` fließt jetzt in JEDE
`affordable`-Zuweisung in `market_vs_squad()` ein - Headline und Tier-Badge
(die beide auf `affordable` aufbauen) können das Bid-Verdikt dadurch nicht
mehr widersprechen.

**Mindestpreis-Sonderregel (2.7, Item 9)**: `bid_advisor.recommend_bid()`
bekam nie `min_price`/`is_min_price` durchgereicht - bei Regime
INITIALISIERUNG (typisch für Mindestpreis-Spieler: kaum Historie) gilt IMMER
`untergrenze == trading_decke == mv`, und der Puffer ist stets > 0, wodurch
`wunsch > max_gebot` MATHEMATISCH GARANTIERT war, unabhängig vom Spieler -
jeder Mindestpreis-Kandidat bekam "NICHT BIETEN", obwohl am Mindestpreis das
Verlustrisiko praktisch null ist (Verkauf bringt immer mindestens denselben
MW zurück). Fix: neuer Parameter `min_price_player` (durchgereicht von
`main.py`s `market_scored[].min_price_player`, `league_board.py`s
`min_price_flag`) schaltet auf `_recommend_bid_min_price()` um - Trading-/
Fair-Value-Bremse greift hier nicht, Verdikt immer `"bieten"`, Bewertung läuft
ausschließlich über den sportlichen Elf-Beitrag (`bridge_to_ideal_elf`/
`recommendation_tier`).

**Fair-Value-Konsistenz + Plausibilitätsgrenze (2.2)**: drei Berechnungsstellen
(`main.py` Kader-Loop, `main.py` Transfermarkt-Loop, `league_board.py`)
gingen bisher unterschiedlich mit Mindestpreis-Spielern um - der
Transfermarkt-Loop klammerte sie schon vorher bewusst aus (`"Fair Value:
neutral - Mindestpreis"`), aber `squad_analysis.apply_fair_value_note()`
setzte `c["fair_value"]` UNABHÄNGIG vom `min_price_player`-Flag (nur der
Reasons-Text/das Sell-Flag wurden unterdrückt, nicht die rohe Zahl), und
`league_board.py` berechnete `min_price_flag`, nutzte ihn aber nie zum Gaten.
Ergebnis: derselbe Mindestpreis-Spieler zeigte "neutral" auf der Marktkarte,
aber eine echte (meist sehr niedrige, kurven-randbedingte) Zahl auf der
Kaderkarte/in der Bestenliste. Fix: alle drei Stellen gaten jetzt identisch
auf `min_price_player`. Zusätzlich neue `scoring.clamp_fair_value(fair_value_mv,
mv, factor=3)` (`FAIR_VALUE_PLAUSIBILITY_FACTOR=3`) - weicht der Fair Value um
mehr als Faktor 3 vom Marktwert ab, ist das fast immer ein Rechenfehler
(Kurven-Randeffekt), nicht ein echtes Signal (live belegt: Zielinski MW 3,04
Mio → Fair Value 10,68 Mio; Zec MW 11,45 Mio → Fair Value 0,91 Mio). Wird an
allen drei Fair-Value-Berechnungsstellen angewendet, klemmt auf `None`
("unterdrückt") statt eine unplausible Zahl zu zeigen, mit Konsolen-Warnung.
Live ausgelöst (La Liga): Johnny Cardoso, Guedes, Uche im Kader, plus mehrere
Transfermarkt-Kandidaten - vorher wären das alles falsche Fair-Value-Zahlen
gewesen.

**Doppelte Banger-Karten (2.5, Item 6)**: `report["bangers"]` ist eine reine
Teilmenge von `report["market"]` (nur nach `banger`-Flag gefiltert, nicht aus
`compared` entfernt) - jeder Banger-Kandidat erschien dadurch zweimal:
hervorgehoben in "💎 Banger-Ziele" UND identisch nochmal im normalen
Transfermarkt-Grid darunter. Fix in `html_report._transfermarkt_section()`
UND im Konsolen-Äquivalent (`main.py`s `compared[:6]`-Loop): Banger-IDs werden
vor dem Rendern des normalen Grids ausgeschlossen. Live verifiziert (Timo
Horn, Maurice Neubauer erscheinen nur noch in "💎 Banger-Ziele", nicht mehr
zusätzlich im Grid darunter).

**Preiskurven-Sättigung oben behoben (2.1, Item 8)**: `scoring.
fit_price_curve()` nutzte feste 10 Dezile - das gesamte teuerste
Preissegment (~14 Mio+) kollabierte dadurch auf EINEN Medianwert (jeder
Spieler darüber bekam exakt dieselbe Erwartung, live belegt: Wanitzek 30,6
Mio und Zoma 17 Mio beide "105 P"). Fix: Bucket-Zahl skaliert jetzt mit der
Populationsgröße (`buckets = max(10, min(30, n // 10))`, bei ~250-270
bewerteten Spielern/Liga ~25-27 statt 10 Buckets) statt fix bei 10 zu
bleiben - weiterhin robuste Mediane je Bucket, weiterhin KEINE Regression/
Extrapolation über die Randpunkte hinaus (das war der explizite Grund für den
früheren Wechsel weg von der Regression, bleibt unangetastet). Live
verifiziert: die Kurve zeigt jetzt 13 statt 10 Stützpunkte mit echter
Differenzierung am teuren Ende (18,6 Mio → 103 P, 28,6 Mio → 121 P - vorher
beide identisch ~105 P).

**Plausibilitätswarnung auch für den eigenen Kader + HTML-Sichtbarkeit (2.6)**:
eine Faktor-2-Plausibilitätsprüfung (SPEC_kalibrierung_fairvalue.md 1.2)
existierte für den eigenen Kader bereits, aber NUR als Konsolen-Print - die
Review basierte auf dem gerenderten HTML-Livereport und sah sie nie, obwohl
der gemeldete Zec-Ausreißer (Ø 85 P, blau, angeblich E[Punkte]≈14) genau der
Fall gewesen wäre, den sie fangen sollte. Zusätzlich fehlte für den EIGENEN
Kader die absolute "<25 P bei blau/grün"-Warnung, die `league_teams.py` für
MITSPIELER-Kader schon hatte (SPEC_ranking_faktoren_llm.md 2.3) - nachgezogen.
Beide Warnungen fließen jetzt in `report["plausibility_warnings"]` und
erscheinen im `_model_health_banner()` des HTML-Reports, nicht mehr nur in
der Konsole. **Live-Ergebnis**: Zec zeigt in der aktuellen Berechnung
plausible 110 P (kein Ausreißer mehr reproduzierbar, wahrscheinlich bereits
durch den MW-Sockel-Fix aus SPEC_ranking_faktoren_llm.md 2.3 miterledigt,
bevor dieser Review geschrieben wurde) - die neue Warnung bleibt trotzdem als
Verteidigungslinie gegen künftige Fälle dieser Art bestehen und feuerte live
für andere Spieler (La Liga: Guedes im eigenen Kader).

**"Dein Kader heute" zeigte scheinbar fremde Manager-Namen (2.8)**: keine
Vertauschung von Kaderdaten (das wurde geprüft und verworfen), sondern ein
Platzierungsproblem: `_model_health_banner()` listet als Modellstabilitäts-
Check (SPEC_lernzyklus.md 5.3, `prediction_log.diff_predictions()`) die
Prognose-Änderungen ALLER Liga-Manager, sitzt aber ohne eigene Überschrift
direkt unter dem KPI-Grid ("Dein Kader heute"-Kachel) - Namen wie
"malte.srn"/"david" (andere Manager) lasen sich dadurch wie eine Aussage über
den eigenen Kader. Fix: diese Zeilen tragen jetzt ein explizites "Liga:"-
Präfix statt reinem Spielernamen.

**KI verwechselte Prognosetabelle mit echtem Tabellenstand (2.9)**:
`llm_insights.py`s `league_comparison`-Kontextfeld hieß `my_rank` (Rang NACH
PROGNOSE für den kommenden Spieltag, aus `league_teams.py`) - das Modell
interpretierte das live als bereits gespielten Tabellenstand ("Rang 8, 131,1
Punkte Rückstand"), obwohl es vor dem ersten Spieltag der Saison noch gar
keinen echten Tabellenstand geben kann. Fix: Feld umbenannt zu
`my_rank_prognose` (macht es schon am Namen klar) + explizite Anweisung in
`TASK_INSTRUCTIONS` Punkt 6, das immer als Vorhersage zu formulieren ("nach
Prognose liegst du..."), nie als bereits erspieltes Ergebnis. Live
verifiziert: beide Ligen formulieren jetzt korrekt "Nach der aktuellen
Spieltagsprognose liegst du... auf Rang X" / "vor dem 1. Spieltag".

**Protokollierung (3.5) - falscher Alarm**: `prediction_log.
save_matchday_prediction()` lief bereits produktiv (`data/predictions/
1899_md1.json` existiert, tägliche `bids_<datum>.json`) - der Review sah nur
keine Erwähnung davon im gerenderten HTML-Report, was auch korrekt ist (die
Protokolldateien sind ein Hintergrund-Log für den späteren Lernzyklus, per
Design nicht Teil der sichtbaren Seite). Kein Fix nötig, nur verifiziert.

**Bewusst zurückgestellt** (Teil 7, "Zeithorizont" - explizit "Danach" laut
Review-eigener Priorisierung, nicht "vor dem ersten Anpfiff"): ein
`restspieltage_gewicht`-Faktor, der die Trading- vs. Sport-Gewichtung über
die Saison verschiebt (früh: Trading wichtiger, spät: nur noch Punkte
zählen), Team-Saisonstärke aus dem Quotenmittel statt nur dem nächsten
Gegner, Spielplanqualität über mehrere Spieltage, Über/Unter-2,5-Tore-Quoten
als Faktor, sowie eine gemeinsame Einheit für Trading- und Sportnutzen
(Abschnitt 7.4). Das ist eine neue, mehrdimensionale Gewichtungs-Erweiterung,
kein Bugfix - verdient einen eigenen dedizierten Durchlauf mit echter
Kalibrierung, nicht einen Anhang an einen bereits sehr breiten Fix-Batch am
ersten Spieltag.

## User-Präferenzen (aus mehreren Feedback-Runden — unbedingt beibehalten)

- Trading und Punkte sind gleichwertig ("geht Hand in Hand"): Teamwert maximieren → beste Spieler → Spieltage gewinnen.
- Transparenz ist Pflicht: jede Empfehlung mit Komponenten-Aufschlüsselung und Klartext-Begründung.
- Keine pauschalen Fehlschlüsse: fehlende Daten ≠ schlechter Spieler; steigender MW ≠ Verkaufskandidat; Stammspieler (Beispiel des Users: Hauke Wahl, Kapitän/Abwehrchef VfL Wolfsburg, Aufstiegsfavorit) ≠ "Trading-Hold" oder "Beobachten".
- Kommunikation: informell deutsch, direkt, er testet selbst und gibt präzises Feedback; er arbeitet teils parallel mit Gemini am selben Code.

## Bekannte Baustellen / Nächste Schritte (Priorität nach User-Signalen)

1. ~~Kader-Endpoint verifizieren~~ **erledigt (2026-07-30)**: `/v4/leagues/{id}/squad` liefert live echte Daten (19/20 Spieler in beiden Ligen bestätigt), Kader-Hälfte des Briefings läuft.
2. **Activity-Feed verifizieren** → echter Liga-Overpay statt Standard-Puffer (weiterhin unverifiziert). Mitspieler-Analyse selbst ist **erledigt (2026-08-05, `league_teams.py`, Modul 3)** - Kader/Aufstellung/Prognose aller Manager, s.o. Offen bleibt nur der Overpay-Lernaspekt aus dem Activity-Feed.
3. **La Liga end-to-end testen**: Liga-Name in config, competition_id verifizieren (Platzhalter 3; korrekt auslesbar aus `cpi` in /me wenn er in der La-Liga-Liga ist), TheSportsDB-Fallback live prüfen, Teamnamen-Matching Kickbase↔football-data.co.uk (die CSV kürzt ab: "Ath Madrid", "Sociedad").
4. ~~Saisonwetten/Outright-Quoten~~ **erledigt/verworfen** (2026-07-30): User hat the-odds-api.com-Key besorgt und in `config.py` (`ODDS_API_KEY`) eingetragen. Live geprüft: Outright-/Meister-Markt ist für `soccer_germany_bundesliga2` und `soccer_spain_la_liga` auf dem Free Tier **nicht verfügbar** (`INVALID_MARKET_COMBO`) — kein zusätzliches Team-Power-Signal möglich. Stattdessen als **Tier-2-Fallback** eingebaut (`odds.load_fixture_odds_api`, `main.load_fixture_data`): liefert reguläre h2h-Spielquoten, wenn football-data.co.uk/fixtures.csv leer ist (aktuell der Fall, tiefe Sommerpause vor Saisonstart ~7./15.8.). Kostet Kontingent (500 req/Monat), daher nur 1x pro Liga pro Lauf, nicht pro Spieler. Nebenbei gefixt: `odds_div` fehlte komplett in `config.py` — die "primäre" Quotenquelle lief nie, App fiel direkt auf den Tabellen-Fallback zurück. Jetzt für beide Ligen gesetzt (`D2`, `SP1`).
5. **Automatisierung**: Code-seitig erledigt (2026-07-30) - HTML-Briefing (`html_report.py`) + GitHub-Actions-Workflow + Pages-Deployment gebaut und lokal via Playwright getestet, s.o. Statt E-Mail: statische Seite auf GitHub Pages, "Zum Home-Bildschirm hinzufügen". Offen: eigentliches GitHub-Setup durch den User (Git installieren, Repo erstellen, pushen, Secrets eintragen, Pages aktivieren - Claude Code hat hier weder git noch gh CLI zur Verfügung). MW-History-Snapshot (data/history.json) für mehrtägige Trends über das 24h-Feld hinaus weiterhin offen/gewünscht.
6. **Feintuning nach echten Läufen**: Schwellen (STAMM_CORE 0.55/0.60, STRONG_RISE 0.8%/Tag, Banger 0.72, min_gap 6) sind Erstkalibrierung; User liefert Output-Feedback. Punktetyp-Analyse (Rohpunkte vs. Scorer-Volatilität) und Aufstellungs-Empfehlung (beste 11 nach Sieg-WKs) sind gewünschte, noch nicht gebaute Features aus der ursprünglichen Vision.
7. Kleinkram: `mvt`-Feld (Werte 1/2 beobachtet) nie sicher dekodiert; `prc` vs `mv` bei Mitspieler-Listungen = geforderter Preis. ~~Vereinslimit unbekannt~~ **erledigt (2026-07-31)**: User-bestätigt max. 3 Spieler pro Verein (`main.CLUB_LIMIT`), `squad_analysis.market_vs_squad(..., club_limit=3)` zählt eigene Spieler je `tid` (verkaufter Spieler bei Upgrades korrekt herausgezählt) und blockt/kennzeichnet Kaufempfehlungen mit "⚠️ VEREINSLIMIT erreicht".
8. ~~Kickbase-Statusfarben offen~~ **erledigt (2026-08-05)**: `prob` 1-5 = blau/grün/gelb/rot/grau, per Kreuzprobe (4/4 Treffer) verifiziert und umgesetzt (`scoring.kickbase_color()`, s.o.). Wird im Kader-Status und Dashboard angezeigt, ergänzt das Verdikt. Widersprüche (z.B. Farbe grün, aber Verdikt VERKAUFEN) gehen als `kickbase_color_conflict` explizit in den `llm_insights`-Kontext (Punkt 4.3 der Spec).
9. **Verbleibend aus SPEC_lineup_verified.md**: nur "4-4-2" ist als gültige Formation aus dem POST-Body-Mitschnitt bestätigt, die anderen 6 in `coach.FORMATIONS` sind allgemeines Fantasy-Football-Wissen (unverifiziert) - bei Zugriff auf die Formations-Auswahl in der App gegenprüfen. ~~Ligaweite Prognosetabelle für Mitspieler (Spec 6.4)~~ **erledigt (2026-08-05)**: `managers/{uid}/squad` liefert die echte Aufstellung jedes Mitspielers direkt über die eigene Session, `league_teams.py` (Modul 3) nutzt das bereits produktiv.
10. **Verbleibend aus SPEC_gebote_ki_team_KOMPLETT.md**: 3.2 (KI-Einsatzminuten-Faktor für Modul 3), 3.4 (Abweichungszerlegung nach dem ersten echten Spieltag, technisch erst dann baubar), 6.2 (Punktetyp-Streuung für situationsabhängige Aufstellung), 6.5 (Rückkopplungs-/Lern-Protokollierung) - alle explizit als "Woche 2"/nach Saisonstart vertagt. Zusätzlich im Auge behalten: `coach.detect_self_play_conflicts()` lieferte in der La-Liga-Vorsaison (the-odds-api-Fallback, Testspiele statt echtem Spielplan) deutlich mehr Treffer als in der 2. Liga (echter Spielplan) - plausibel durch unregelmäßige Testspiel-Ansetzungen erklärbar, für die 2. Liga aber live gegen die echten `os`/`ht`-Gegnerdaten verifiziert (kein Fuzzy-Match-Fehltreffer), für La Liga weiter offen.
11. ~~Kalibrierungsanker-Restabweichung~~ **erledigt (2026-08-05, SPEC_spieltagsmodell_v2.md)**: der verbliebene -39%-Fehler lag NICHT an den Faktoren, sondern an einem fehlenden `prob`-Feld in `managers/{uid}/squad` (s. `league_teams.py`-Eintrag oben) - nach dem Fix -4%, PLAUSIBEL. Lehre für künftige Kalibrierungsarbeit: die `diagnose_prognose()`-Kaskade VOR weiteren Konstanten-Anpassungen befragen, sie zeigt den tatsächlichen Fehlerort statt geraten werden zu müssen.
12. **Verbleibend aus SPEC_spieltagsmodell_v2.md**: 1.4s KI-Teil (**ein gebündelter Gemini-Call für alle 11 Manager**, ein Satz Einordnung je Manager zu Aufstellung/Gegner/Auffälligkeiten - bewusst wegen Zeitbudget in dieser Änderung zurückgestellt, technisch unkompliziert analog zu `llm_insights.py` umsetzbar, sollte als nächstes angegangen werden). `prediction_log.save_matchday_actuals()`/`deviation_report()` sind bewusst auf Manager-Ebene begrenzt (`ranking.us[].mdp`) - die Player-Level-Abweichungszerlegung aus dem Spec-Beispiel ("-48 Einsatz Guedes 14 Min...") bräuchte zusätzliche `ph`-Abrufe je Mitspieler-Kader, noch nicht gebaut. `matchday` ist für Spieltag 1 hartkodiert (`main.py`) - eine echte Spieltagszahl-Ableitung für Spieltag 2+ ist offen (Kandidat: `ranking.us[].sp>0`-Erkennung wie in `report_builder.compute_kpis`, oder `/v4/competitions/{cid}/players` Feld `day`/`sn`, unverifiziert).
13. **Verbleibend aus SPEC_lernzyklus.md**: Stufe 2 (Regression, ab Spieltag 5) und Stufe B (KI-Anomalie-Klassifikation einmalig/systematisch/unklar) - beide brauchen echte Ist-Werte, die es vor dem ersten Spieltag nicht gibt, reine Zukunftsarbeit (Infrastruktur - Gewichtsversionierung, Protokollierung, Stufe-A-Ausreißererkennung - liegt bereit). Player-Level-Ist-Werte (Einsatzminuten, "der wertvollste Ist-Wert überhaupt" laut Spec) sind noch nicht erfasst (s. Punkt 12) - ohne sie bleibt die Statusfarbe-zu-Minuten-Kalibrierung aus Abschnitt 4.4 der Spec unbaubar. KI-Faktoren-Einfrieren (5.2a) zurückgestellt, weil `matchday_outlook` aktuell nirgends mit echten Daten an `coach.expected_points()` übergeben wird - die Instabilitätsursache existiert im Code derzeit nicht. "Letzten erfolgreichen KI-Stand mit Zeitstempel zeigen" (6.4) bräuchte eine Erweiterung der schlanken Snapshot-Persistenz. Abschnitt 4 (fehlende Faktoren: Über-/Unter-2,5-Tore-Quoten, Heimvorteil-Term, Formations-Rolle, Punktetyp-Kopplung) noch nicht geprüft/gebaut - 4.1 (Tore-Quoten) hat laut Spec potenziell den größten Hebel, zuerst prüfen ob die `fixtures.csv`-Spalten für D2/SP1 gefüllt sind.
14. ~~SPEC_ranking_faktoren_llm.md~~ **erledigt (2026-08-06)**: die drei Prioritäten 1-3 (ein Rechenweg für die eigene Prognose, MW-Sockel gegen negative/implausible Erwartung, KI-Aufrufzähler) sowie Priorität 4 (Duelle partieweise gruppiert) sind gebaut und live verifiziert, s. `league_teams.py`-Eintrag oben. Priorität 5 (Über/Unter-2,5-Tore-Quoten) geprüft, aber zurückgestellt - `fixtures.csv` enthält in der aktuellen tiefen Sommerpause keine `D2`/`SP1`-Zeilen (nur schottische Ligen), Füllgrad für unsere Ligen erst nach Saisonstart prüfbar. **Verbleibend aus Abschnitt 4/5**: 4.1-4.5 (Detailanalyse was Spieltagspunkte wirklich treibt: Team-Dominanz ≠ Sieg-WK, Torbeteiligungen gehören in σ nicht in den Erwartungswert, Gegner-Sensitivität `k` sollte an den Punktetyp gekoppelt sein) sind noch nicht umgesetzt - explizit spekulativ/mittelfristig laut Spec. Abschnitt 5 (Bandbreite evtl. zu eng, ±10% beobachtet vs. reale Kickbase-Streuung) explizit erst nach echten Ist-Werten (Spieltag 1, 07.08.2026) validierbar - nicht vorher angehen.
15. ~~REVIEW_architektur_KOMPLETT.md~~ **Sofortfixes erledigt (2026-08-07)**: alle 9 konkret belegten Report-Bugs (Teil 2, Punkte 2.1-2.9: Kurvensättigung, Fair-Value-Konsistenz+Plausibilitätsgrenze, marginaler Elf-Beitrag, Verdikt/Tier/NICHT-BIETEN-Widerspruch, doppelte Karten, Zec-Plausibilität+HTML-Sichtbarkeit, Mindestpreis-Sonderregel, Überschrift-Zuordnung, KI-Tabellenstand-Verwechslung) sowie die Score-Label-Disambiguierung (Item 1) sind gebaut, live gegen beide Ligen verifiziert, s. den eigenen Abschnitt oben. **Bewusst NICHT umgesetzt** (explizit als mehrtägige Folgearbeit eingestuft, nicht am ersten Spieltag): Teil 1.3 (vollständige `PlayerEvaluation`-Struktur - eine echte Vereinheitlichung aller Score-/Fair-Value-/Punkte-Berechnungen statt der jetzt disambiguierten Labels/gezielten Konsistenz-Fixes; bräuchte eine Neukalibrierung der über viele Runden austarierten STAMM/HALTEN-Schwellen, die an `scoring.score_player()`s Skala hängen - **User-Rückfrage nötig**, ob/wann dieser größere Umbau gewünscht ist) und Teil 7 (Zeithorizont/`restspieltage_gewicht`, Team-Saisonstärke aus Quotenmittel, Über/Unter-Tore-Faktor, gemeinsame Trading/Sport-Einheit - neue Gewichtungs-Erweiterung, kein Bugfix, verdient eigenen dedizierten Durchlauf mit echter Kalibrierung).

## Was NICHT tun

- Login-Payload/User-Agent nicht "aufräumen" — exakt so funktioniert er.
- Keine Endpoints erfinden: unverifizierte Pfade klar markieren und defensiv scheitern lassen (bestehende Konvention).
- `config.py` mit echten Credentials nie in Ausgaben/Commits.
- API nicht fluten (Sleep zwischen Detail-Calls beibehalten), Token nach ~1h erneuern falls Läufe länger werden.
