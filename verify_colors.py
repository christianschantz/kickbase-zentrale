"""
Klaert die Farbcodierung: druckt fuer beide Ligen alle Kaderspieler plus die
aktuellen Marktspieler mit ihren prob- und st-Werten.

DANACH: in der App nachsehen, welche Farbe die genannten Spieler haben, und
die Zuordnung hier eintragen. Dann ist die Mapping-Tabelle belegt statt geraten.

Bekannt (User, 05.08.2026, Liga 1899):
    Berkan Taz          blau
    Deniz Emre Ofli     gruen
    Ibrahim El Kadiri   gelb
    Vaeaenaenen         rot
    Leon Schneider      grau   (steht auf dem Transfermarkt)

AUSFUEHREN im Projektordner:
    python verify_colors.py
"""

import time
import requests

try:
    from config import EMAIL, PASSWORD
except ImportError:
    EMAIL = "DEINE-MAIL"
    PASSWORD = "DEIN-PASSWORT"

BASE = "https://api.kickbase.com"
HEADERS = {
    "User-Agent": "Kickster/4.8.0/8776 (iPhone; iOS 26.5.2; Scale/3.00)",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Accept-Language": "de-DE;q=1, en-DE;q=0.9",
}

# Beide Ligen - IDs aus den bisherigen Aufnahmen
LEAGUES = [("1899 (2. BL)", "5348134"), ("wirschaffenstudium (La Liga)", "11280421")]

# Spieler, deren Farbe der User genannt hat
KNOWN = {
    "Taz": "blau",
    "Ofli": "gruen",
    "El Kadiri": "gelb",
    "Väänänen": "rot",
    "Vaeaenaenen": "rot",
    "Schneider": "grau (Markt)",
}


def login():
    payload = {"rep": {}, "pass": PASSWORD, "ext": True, "em": EMAIL, "loy": False}
    r = requests.post(f"{BASE}/v4/user/login", json=payload, headers=HEADERS, timeout=20)
    tkn = r.json().get("tkn")
    if not tkn:
        print("Login fehlgeschlagen:", r.text[:300])
        return False
    HEADERS["Authorization"] = f"Bearer {tkn}"
    return True


def get(path, params=None):
    try:
        r = requests.get(BASE + path, headers=HEADERS, params=params, timeout=20)
        if r.status_code != 200:
            return None
        d = r.json()
        return None if isinstance(d, dict) and "err" in d else d
    except (requests.RequestException, ValueError):
        return None


def show(title, players):
    print(f"\n  {title}")
    print(f"    {'Name':<24} {'pos':>3} {'prob':>4} {'st':>4} {'lo':>4}  Farbe laut App")
    print("    " + "-" * 68)
    for p in players:
        name = (p.get("n") or p.get("ln") or "?")
        hit = next((v for k, v in KNOWN.items() if k.lower() in name.lower()), "")
        marker = f"  <== {hit}" if hit else ""
        print(f"    {name[:23]:<24} {str(p.get('pos')):>3} {str(p.get('prob')):>4} "
              f"{str(p.get('st')):>4} {str(p.get('lo', '-')):>4}{marker}")


def main():
    if not login():
        return
    print("Login ok")

    for label, lid in LEAGUES:
        print("\n" + "=" * 72)
        print(f"LIGA: {label}  (id {lid})")
        print("=" * 72)

        lineup = get(f"/v4/leagues/{lid}/lineup")
        if lineup:
            xi = [p for p in lineup.get("it", []) if p.get("lo") is not None]
            bench = [p for p in lineup.get("it", []) if p.get("lo") is None]
            xi.sort(key=lambda p: p["lo"])
            show(f"STARTELF ({len(xi)}/11 belegt)", xi)
            show("BANK", bench)
        else:
            squad = get(f"/v4/leagues/{lid}/squad") or {}
            show("KADER", squad.get("it", []))

        market = get(f"/v4/leagues/{lid}/market") or {}
        free = [p for p in market.get("it", []) if "u" not in p]
        show("TRANSFERMARKT (frei)", free[:15])
        time.sleep(0.3)

    print("\n" + "=" * 72)
    print("NAECHSTER SCHRITT")
    print("=" * 72)
    print("""
Die mit <== markierten Spieler in der App aufrufen und pruefen, ob die dort
angezeigte Farbe zum prob-Wert oben passt. Vermutete Zuordnung:

    prob 1 -> blau     (gesetzt)
    prob 2 -> gruen    (wahrscheinlich Startelf)
    prob 3 -> gelb     (fraglich)
    prob 4 -> rot      (eher nicht)
    prob 5 -> grau     (keine Einschaetzung / faellt aus)

Stimmt das NICHT, koennte stattdessen die st-Bitmaske (0/1/2/4/16/256)
die Farbe bestimmen - dann bitte die st-Werte der markierten Spieler melden.
Wichtig: Farben aendern sich taeglich, deshalb App und Skript zeitnah
nacheinander pruefen.
""")


if __name__ == "__main__":
    main()
