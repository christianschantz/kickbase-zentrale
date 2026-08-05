import time
import requests
from config import EMAIL, PASSWORD

BASE = "https://api.kickbase.com"


class KickbaseAPI:
    def __init__(self):
        self.token = None
        self.user_id = None
        self.user_name = None
        self.headers = {
            "User-Agent": "Kickster/4.8.0/8776 (iPhone; iOS 26.5.2; Scale/3.00)",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Language": "de-DE;q=1, en-DE;q=0.9",
        }

    # ---------- Auth ----------
    def login(self):
        if not EMAIL or not PASSWORD:
            print("❌ Login: EMAIL/PASSWORD sind leer (Secret nicht gesetzt oder falsch benannt?)")
            return False

        payload = {"rep": {}, "pass": PASSWORD, "ext": True, "em": EMAIL, "loy": False}
        try:
            r = requests.post(f"{BASE}/v4/user/login", json=payload, headers=self.headers, timeout=20)
        except requests.RequestException as e:
            print(f"❌ Login-Request fehlgeschlagen: {e}")
            return False

        if r.status_code != 200:
            print(f"❌ Login HTTP {r.status_code}: {r.text[:300]}")
            return False

        # Kickbase liefert Fehler mit HTTP 200 und {"err": N, "errMsg": "..."} im Body!
        data = r.json()
        token = data.get("tkn")
        if not token:
            print(f"❌ Login-Fehler: err={data.get('err')} msg={data.get('errMsg')}")
            return False

        self.token = token
        self.headers["Authorization"] = f"Bearer {self.token}"
        self._decode_user_from_token()
        return True

    def _decode_user_from_token(self):
        """
        Eigene User-ID/Name aus dem JWT-Payload (kb.uid/kb.name) - steht
        nirgends direkt in /me. kb.name kommt wie onm mit angehängtem
        Leerzeichen zurück (.strip() nötig). Rein informativ, keine
        Signaturprüfung nötig (wir haben den Token gerade selbst erhalten).
        """
        import base64
        import json
        try:
            payload_b64 = self.token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload_b64))
            self.user_id = claims.get("kb.uid")
            self.user_name = (claims.get("kb.name") or "").strip()
        except (IndexError, ValueError, UnicodeDecodeError):
            self.user_id = None
            self.user_name = None

    def _get(self, path, params=None):
        r = requests.get(f"{BASE}{path}", headers=self.headers, params=params, timeout=20)
        if r.status_code == 200:
            return r.json()
        return None

    # ---------- Liga ----------
    def get_league_id(self, league_name):
        data = self._get("/v4/leagues") or {}
        for l in data.get("lins", []):
            if l.get("n") == league_name:
                return l.get("i")
        return None

    def get_me(self, league_id):
        """
        Budget (b), max. Kadergröße (mppu), Competition-ID (cpi), etc.
        `tpc` ist NICHT das Vereinslimit (frühere Fehlannahme) - es ist eine
        Liste {tid, npt} = AKTUELLE Spieleranzahl je Verein im Kader.
        """
        return self._get(f"/v4/leagues/{league_id}/me") or {}

    def get_ranking(self, league_id, day_number=None):
        params = {"dayNumber": day_number} if day_number else None
        return self._get(f"/v4/leagues/{league_id}/ranking", params) or {}

    # ---------- Liga-weite Spielerdaten (B5) ----------
    def get_competition_table(self, competition_id):
        """Tabelle der Competition -> alle Team-IDs (tid) unter 'it'."""
        return self._get(f"/v4/competitions/{competition_id}/table") or {}

    def get_team_profile(self, competition_id, team_id, league_id):
        """
        Kompletter Vereinskader (~21-30 Spieler) unter 'it': mv, sdmvt
        (verifiziert: exakte 7-Tage-MW-Differenz), mvgl, ap, st, prob, pos,
        mvt, iotm. Die mitgelieferten onm/iotm/oui/uim sind hier nur
        lückenhaft befüllt - NICHT als Besitzquelle nutzen, dafür
        search_players() (onm dort zu 100% befüllt).
        """
        return self._get(f"/v4/competitions/{competition_id}/teams/{team_id}/teamprofile",
                         params={"leagueId": league_id}) or {}

    def search_players(self, competition_id, league_id, query="", start=0):
        """
        Eine Seite der durchblätterbaren Gesamtliste aller Spieler der
        Competition. Paginierung AUSSCHLIESSLICH über 'start' in 25er-
        Schritten (offset/page/limit werden ignoriert). Ende = leere Liste.
        Felder: pi, n, mv, pos, st, onm (Besitzquelle, zu 100% befüllt -
        ACHTUNG: kommt mit angehängtem Leerzeichen zurück, .strip() nötig!),
        iotm, tid, pim.
        """
        return self._get(f"/v4/competitions/{competition_id}/players/search",
                         params={"query": query, "leagueId": league_id, "start": start}) or {}

    def search_all_players(self, competition_id, league_id, page_size=25, sleep=0.15):
        """Blättert search_players() komplett durch bis 0 Treffer."""
        out = []
        start = 0
        while True:
            data = self.search_players(competition_id, league_id, start=start)
            items = data.get("it", []) if isinstance(data, dict) else (data or [])
            if not items:
                break
            out.extend(items)
            start += page_size
            time.sleep(sleep)
        return out

    # ---------- Markt ----------
    def get_transfer_market(self, league_id, only_free=True):
        data = self._get(f"/v4/leagues/{league_id}/market") or {}
        players = data.get("it", [])
        if only_free:
            players = [p for p in players if "u" not in p]
        return players

    def get_market_all(self, league_id):
        """Alle Markt-Einträge inkl. von Mitspielern gelistete Spieler."""
        data = self._get(f"/v4/leagues/{league_id}/market") or {}
        return data.get("it", [])

    # ---------- Spieler ----------
    def get_player_details(self, league_id, player_id):
        """
        Verifizierter Endpoint. Liefert u.a.:
        mv, tfhmvt (24h-MW-Änderung!), st/stxt (Fitness), prob
        (Einsatz-Indikator, niedriger = besser), mdsum (letzte + kommende
        Spiele mit Gegner-Team-IDs), tn (Teamname), pos.
        """
        return self._get(f"/v4/leagues/{league_id}/players/{player_id}") or {}

    def get_mv_history(self, competition_id, player_id, league_id, days=92):
        return self._get(
            f"/v4/competitions/{competition_id}/players/{player_id}/marketValue/{days}",
            params={"leagueId": league_id},
        ) or {}

    # ---------- Eigenes Team ----------
    def get_squad(self, league_id):
        """Verifiziert (2026-07-30): liefert {"it": [...], "mppu": ...}."""
        for path in (f"/v4/leagues/{league_id}/squad",
                     f"/v4/leagues/{league_id}/lineupex",
                     f"/v4/leagues/{league_id}/teamcenter/myeleven"):
            data = self._get(path)
            if data:
                return data
        return {}

    def get_lineup(self, league_id):
        """
        Verifiziert (2026-08-05, HAR-Mitschnitt der Aufstellungsansicht):
        liefert den KOMPLETTEN Kader mit Aufstellungsinfo unter "it". Feld
        `lo` = Slot in der Startelf (0-10, Slot 0 immer TW) - fehlt `lo`,
        sitzt der Spieler auf der Bank. Zusätzlich je Spieler `os` (Kürzel
        des nächsten Gegners, z.B. "FCM") und `ht` (True=Heimspiel) - macht
        Team-Fuzzy-Matching für die eigene Kader-Gegnerbestimmung unnötig.
        `mdst` (0 = Spieltag noch nicht begonnen) ebenfalls enthalten.
        POST /v4/leagues/{id}/lineup setzt die echte Aufstellung - bewusst
        NICHT implementiert (würde den echten Kickbase-Kader verändern,
        gehört nicht in den automatischen Lauf).
        """
        return self._get(f"/v4/leagues/{league_id}/lineup") or {}

    # ---------- Aktivitäten (für Overpay-Statistik) ----------
    def get_activities(self, league_id, max_items=200):
        """UNVERIFIZIERT: Activity-Feed mit abgeschlossenen Transfers.
        Wird fürs empirische Overpay-Lernen genutzt; scheitert leise."""
        for path in (f"/v4/leagues/{league_id}/activitiesFeed",
                     f"/v4/leagues/{league_id}/feed"):
            data = self._get(path, params={"max": max_items})
            if data:
                return data
        return {}
