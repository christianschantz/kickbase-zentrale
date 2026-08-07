"""
Retrospektive: Wasserfall-Zerlegung der Prognoseabweichung je Manager
(SPEC_punkteformel_final.md Abschnitt 3). Reihenfolge fest (von außen nach
innen - zuerst das, was der Spieler nicht beeinflusst):

E0 = B x M_prog x G_prog x F x V + Z_prog      Ausgangsprognose
E1 = B x M_ist  x G_prog x F x V + Z_prog      -> Delta Einsatzzeit
E2 = B x M_ist  x G_ist  x F x V + Z_prog      -> Delta Spielausgang
E3 = B x M_ist  x G_ist  x F x V + Z_ist       -> Delta Zu-Null
Rest = Ist - E3                                 -> Delta Leistung (unerklärt)

B/F/V bleiben über die ganze Kaskade konstant (nicht Teil der Zerlegung -
"Basis"/"Form"/"Spielverlauf" sind keine Ist/Prognose-Gegensätze in diesem
Modell, nur Einsatz/Gegner/Zu-Null haben einen beobachtbaren Ist-Wert).

**Datenlage (wichtig)**: Player-Level-Ist-Werte (tatsächliche Einsatzminuten,
tatsächliches Zu-Null, tatsächliches Spielergebnis je Partie) werden aktuell
NICHT erfasst - `prediction_log.save_matchday_actuals()` ist bewusst auf
Manager-Ebene begrenzt (`ranking.us[].mdp`, s. CLAUDE.md). Diese Datei liefert
den REINEN Rechenmechanismus der Zerlegung, geprüft per Trockenlauf
(SPEC Abschnitt 4.1, s. `test_retrospective_dryrun.py`) gegen erfundene
Ist-Werte - noch NICHT an main.py angebunden, weil es aktuell nichts Echtes
zum Zerlegen gibt. Die Kopplung an echte Player-Level-Ist-Werte ist ein
separater, noch unverifizierter Datenbeschaffungsschritt (analog anderer
unverifizierter Endpoints im Projekt - defensiv scheitern statt raten,
sobald eine Quelle dafür identifiziert und gegen echte Spieltage geprüft ist).
"""


def waterfall_player(factors, ist):
    """
    factors: die gespeicherten `ep_factors` eines Spielers aus der Prognose
             (basis, einsatzfaktor, gegnerfaktor, formfaktor,
             spielverlaufsfaktor, zu_null_bonus - exakt das Schema aus
             coach.expected_points()).
    ist: {"einsatzfaktor": M_ist, "gegnerfaktor": G_ist,
          "zu_null_bonus": Z_ist, "punkte": tatsächliche Punkte} - fehlende
         Schlüssel fallen auf den PROGNOSE-Wert zurück (kein Beitrag an
         dieser Stelle der Kaskade, entspricht "kein Ist-Wert bekannt").

    Liefert {"e0","e1","e2","e3","delta_einsatz","delta_ausgang",
    "delta_zunull"} immer, zusätzlich "ist"/"delta_leistung"/"checksum_ok"
    wenn `ist["punkte"]` vorliegt.
    """
    b = factors.get("basis", 0)
    m_prog = factors.get("einsatzfaktor", 1.0)
    g_prog = factors.get("gegnerfaktor", 1.0)
    f = factors.get("formfaktor", 1.0)
    v = factors.get("spielverlaufsfaktor", 1.0)
    z_prog = factors.get("zu_null_bonus", 0.0)

    m_ist = ist.get("einsatzfaktor", m_prog)
    g_ist = ist.get("gegnerfaktor", g_prog)
    z_ist = ist.get("zu_null_bonus", z_prog)
    punkte_ist = ist.get("punkte")

    e0 = b * m_prog * g_prog * f * v + z_prog
    e1 = b * m_ist * g_prog * f * v + z_prog
    e2 = b * m_ist * g_ist * f * v + z_prog
    e3 = b * m_ist * g_ist * f * v + z_ist

    result = {
        "e0": round(e0, 2), "e1": round(e1, 2), "e2": round(e2, 2), "e3": round(e3, 2),
        "delta_einsatz": round(e1 - e0, 2),
        "delta_ausgang": round(e2 - e1, 2),
        "delta_zunull": round(e3 - e2, 2),
    }
    if punkte_ist is not None:
        rest = punkte_ist - e3
        result["ist"] = round(punkte_ist, 2)
        result["delta_leistung"] = round(rest, 2)
        # Prüfsumme (SPEC 4.1): die vier Deltas müssen exakt Ist - Prognose
        # (bezogen auf E0) ergeben - eine Teleskopsumme, die bei korrekter
        # Kaskade immer exakt aufgeht. Weicht sie ab, ist die Zerlegung
        # fehlerhaft (z.B. ein falsch wiederverwendeter Zwischenwert).
        checksum = (result["delta_einsatz"] + result["delta_ausgang"]
                   + result["delta_zunull"] + result["delta_leistung"])
        result["checksum_ok"] = abs(checksum - (punkte_ist - e0)) < 0.01
    return result


def waterfall_manager(prediction_entry, ist_by_player):
    """
    prediction_entry: ein `managers[]`-Eintrag aus einer gespeicherten
    Datei A (data/predictions/<liga>_md<N>.json, s. prediction_log.
    save_matchday_prediction()) - trägt `lineup` mit `factors` je Spieler.
    ist_by_player: {player_id: {"einsatzfaktor":.., "gegnerfaktor":..,
                    "zu_null_bonus":.., "punkte":..}, ...} - Spieler ohne
    Eintrag fallen komplett auf ihre Prognose zurück (kein Beitrag zur
    Differenz, `ist` bleibt unbekannt für diesen Spieler).

    Liefert je Spieler die Zerlegung (`rows`) plus eine Team-Aggregation
    (Summe der drei bekannten Kategorien immer; `delta_leistung`/`differenz`/
    `checksum_ok` nur wenn für mindestens einen Spieler ein Ist-Punktewert
    vorliegt).
    """
    rows = []
    for p in prediction_entry.get("lineup", []):
        ist = ist_by_player.get(p["pid"], {})
        wf = waterfall_player(p.get("factors", {}), ist)
        wf["pid"], wf["name"] = p["pid"], p["name"]
        rows.append(wf)

    have_ist = [r for r in rows if "ist" in r]
    team = {
        "delta_einsatz": round(sum(r["delta_einsatz"] for r in rows), 2),
        "delta_ausgang": round(sum(r["delta_ausgang"] for r in rows), 2),
        "delta_zunull": round(sum(r["delta_zunull"] for r in rows), 2),
        "rows": rows,
    }
    if have_ist:
        prognose = round(sum(r["e0"] for r in rows), 2)
        # Spieler ohne bekannten Ist-Wert gehen mit ihrer Prognose (E0) in
        # die Ist-Summe ein - sie tragen dadurch korrekt NICHTS zur
        # Differenz bei, verzerren die Teamsumme aber nicht künstlich nach
        # unten (wie es ein stilles Weglassen täte).
        ist_total = round(sum(r.get("ist", r["e0"]) for r in rows), 2)
        delta_leistung = round(sum(r.get("delta_leistung", 0.0) for r in have_ist), 2)
        differenz = round(ist_total - prognose, 2)
        checksum = round(team["delta_einsatz"] + team["delta_ausgang"]
                         + team["delta_zunull"] + delta_leistung, 2)
        team.update({
            "prognose": prognose, "ist": ist_total, "differenz": differenz,
            "delta_leistung": delta_leistung,
            # Rundungstoleranz proportional zur Spielerzahl (jede Einzelzeile
            # ist bereits auf 2 Nachkommastellen gerundet).
            "checksum_ok": abs(checksum - differenz) < 0.02 * max(1, len(rows)),
        })
    return team


def format_manager_report(name, team, top_n=4):
    """Klartext-Ausgabe im Format aus SPEC_punkteformel_final.md Abschnitt 3
    - für Konsole/Debug, main.py bindet das erst an, sobald echte
    Player-Level-Ist-Werte vorliegen (s. Modul-Docstring)."""
    if "differenz" not in team:
        return f"{name}: keine Ist-Werte vorhanden, keine Zerlegung möglich."
    lines = [
        f"{name}",
        f"Prognose {team['prognose']:.0f} · tatsächlich {team['ist']:.0f} · "
        f"Differenz {team['differenz']:+.0f}",
        "",
        f"  {team['delta_einsatz']:+.0f}  Einsatzzeit",
        f"  {team['delta_ausgang']:+.0f}  Spielausgang",
        f"  {team['delta_zunull']:+.0f}  Zu-Null",
        f"  {team['delta_leistung']:+.0f}  Leistung (unerklärt)",
    ]
    biggest = sorted(team["rows"], key=lambda r: -abs(r.get("delta_leistung", 0)))[:top_n]
    if biggest:
        lines.append("")
        lines.append("  Größte Einzelabweichungen (Leistung):")
        for r in biggest:
            if r.get("delta_leistung"):
                lines.append(f"    {r['name']}: {r['delta_leistung']:+.1f} P")
    if not team.get("checksum_ok"):
        lines.append("  ⚠️ Prüfsumme verletzt - Zerlegung fehlerhaft, Zahlen nicht verwenden")
    return "\n".join(lines)
