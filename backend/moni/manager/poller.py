"""Abruf der Agents: parallel per HTTP, Lückenfüllung über /history, ein DB-Schreibzyklus pro Takt."""
import http.client
import json
import logging
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..collector import Collector
from ..config import Settings
from ..secretbox import SecretBox, SecretError
from ..timeutil import iso, parse_iso
from . import db, tsdb
from .servers import ServerStore

log = logging.getLogger("moni.poller")

_MAX_BYTES = 20 * 1024 * 1024
_MAINTENANCE_EVERY = 300
_INFO_EVERY = 300
_MAX_FUTURE = 300
_MAX_PAST = 3 * 3600
_MAX_WORKERS = 8


class AgentError(Exception):
    """Fehler beim Abruf. `message` ist ein fester deutscher Text ohne Geheimnisse, URLs oder Antwortinhalte."""

    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.message = message
        self.fatal = fatal


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # nie folgen: 3xx wird zum HTTPError
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class AgentClient:
    def __init__(self, url: str, token: str, headers: dict[str, str], timeout: float):
        self._base = url.rstrip("/")
        self._headers = {**headers, "Authorization": f"Bearer {token}", "Accept": "application/json"}
        self._timeout = timeout

    def __repr__(self) -> str:
        return "AgentClient(…)"

    def get(self, path: str, params: dict | None = None) -> dict:
        url = self._base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            raise AgentError("Agent-URL muss mit http:// oder https:// beginnen", fatal=True)
        err = body = None
        try:
            with _OPENER.open(urllib.request.Request(url, headers=self._headers), timeout=self._timeout) as resp:
                body = resp.read(_MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            e.close()
            err = AgentError("Agent lehnt Token ab", fatal=True) if e.code in (401, 403) else \
                AgentError(f"Agent antwortet mit HTTP {e.code}")
        except (OSError, http.client.HTTPException, ValueError):  # URLError, Timeout, Abbruch, kaputte Antwort
            err = AgentError("Agent nicht erreichbar")
        if err is None:
            try:
                data = json.loads(body) if len(body) <= _MAX_BYTES else None
            except ValueError:
                data = None
            if not isinstance(data, dict):
                err = AgentError("Ungültige Antwort vom Agent")
        if err is not None:  # außerhalb von except: kein __context__ mit Anfrage- oder Antwortdetails
            raise err
        return data


def _ts(snap) -> int | None:
    try:
        return int(parse_iso(snap["timestamp"]).timestamp())
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return None


def fetch_new(client: AgentClient, last_ts: int | None, now: int, interval: int) -> list[dict]:
    snaps = []
    if last_ts is not None and now - last_ts > 2 * interval:
        points = client.get("/api/v1/history", {"since": iso(datetime.fromtimestamp(last_ts, timezone.utc))}).get("points")
        if not isinstance(points, list):
            raise AgentError("Ungültige Antwort vom Agent")
        snaps = list(points)
    snaps.append(client.get("/api/v1/snapshot"))
    out, seen = [], set()
    for s in snaps:
        if not isinstance(s, dict):
            raise AgentError("Ungültige Antwort vom Agent")
        v = s.get("schema_version")
        if not (type(v) is int and v == 1):
            raise AgentError(f"Snapshot-Schema {v if type(v) is int else '?'} wird nicht unterstützt", fatal=True)
        ts = _ts(s)
        if ts is None or (last_ts is not None and ts <= last_ts) or ts in seen:
            continue
        seen.add(ts)
        out.append(s)
    return out


class Poller:
    def __init__(self, settings: Settings, box: SecretBox, collector: Collector, client_factory=AgentClient):
        self._settings = settings
        self._box = box
        self._collector = collector
        self._client_factory = client_factory
        self._conn: sqlite3.Connection | None = None
        self._last_maintenance: int | None = None
        self._info_at: dict[int, int] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _fetch(self, server, client, now: int):
        """Läuft im Worker-Thread: nur HTTP, keine DB."""
        try:
            snaps = fetch_new(client, server.last_ts, now, self._settings.collect_interval_seconds)
            info = None
            if server.id not in self._info_at or now - self._info_at[server.id] >= _INFO_EVERY:
                try:
                    info = client.get("/api/v1/info")
                except AgentError:
                    pass  # Info ist Beiwerk; nächster Zyklus versucht es erneut
            return snaps, info
        except AgentError as e:
            return e
        except Exception as e:  # unerwartete Antwortform darf andere Server nicht mitreißen
            log.error("Abruf von %s fehlgeschlagen (%s)", server.name, type(e).__name__)
            return AgentError("Ungültige Antwort vom Agent")

    def _write(self, store: ServerStore, server, result, now: int) -> bool:
        """Ergebnis eines Servers speichern. True, wenn Agent-Info mitgespeichert wurde."""
        if isinstance(result, AgentError):
            store.record_failure(server.id, now, result.message, fatal=result.fatal)
            log.warning("Abruf von %s fehlgeschlagen: %s", server.name, result.message)
            return False
        snaps, info = result
        plausible = [s for s in snaps if now - _MAX_PAST <= _ts(s) <= now + _MAX_FUTURE]
        if not plausible:
            if snaps:
                store.record_failure(server.id, now, "Zeitstempel des Agents unplausibel")
                log.warning("Abruf von %s: Zeitstempel des Agents unplausibel", server.name)
            return False  # leer = nichts Neues seit last_ts: weder Erfolg noch Fehler
        newest = tsdb.write_snapshots(self._conn, server.id, plausible)
        latest = max(plausible, key=_ts)
        store.record_success(server.id, now, newest, json.dumps(latest), json.dumps(info) if info else None)
        return info is not None

    def _store(self, store: ServerStore, server, result, now: int) -> bool:
        """Eigener Savepoint je Server: kaputte Daten eines Agents rollen nur dessen Schreibvorgänge zurück."""
        conn = self._conn
        conn.execute("SAVEPOINT server")
        try:
            saved_info = self._write(store, server, result, now)
        except sqlite3.OperationalError:  # DB-Problem (z. B. gesperrt), nicht Schuld des Agents: ganzer Zyklus
            raise
        except Exception as e:
            conn.execute("ROLLBACK TO server")
            saved_info = False
            if store.get(server.id) is not None:  # während des Abrufs gelöscht: nichts mehr zu vermerken
                log.error("Antwort von %s nicht speicherbar (%s)", server.name, type(e).__name__)
                store.record_failure(server.id, now, "Ungültige Antwort vom Agent", fatal=True)
        conn.execute("RELEASE server")
        return saved_info

    def poll_once(self, now: int) -> None:
        if self._conn is None:
            self._conn = db.connect(self._settings.db_path)
        conn = self._conn
        store = ServerStore(conn, self._box)
        enabled = [s for s in store.list() if s.enabled]
        results = {}
        for s in enabled:
            if s.is_local:
                snap = self._collector.latest()
                ts = _ts(snap) if snap is not None else None
                if ts is not None and (s.last_ts is None or ts > s.last_ts):
                    results[s.id] = ([snap], None)
        jobs = []
        for s in enabled:
            if s.is_local:
                continue
            try:
                token, headers = store.credentials(s.id)
            except SecretError:
                results[s.id] = AgentError("Zugangsdaten nicht entschlüsselbar", fatal=True)
                continue
            jobs.append((s, self._client_factory(s.url, token, headers, self._settings.poll_timeout_seconds)))
        if jobs:  # parallel und außerhalb jeder DB-Transaktion
            with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(jobs))) as ex:
                for (s, _), r in zip(jobs, ex.map(lambda j: self._fetch(j[0], j[1], now), jobs)):
                    results[s.id] = r
        maintenance = self._last_maintenance is None or now - self._last_maintenance >= _MAINTENANCE_EVERY
        got_info = []
        conn.execute("BEGIN")
        try:
            for s in enabled:
                if s.id in results and self._store(store, s, results[s.id], now):
                    got_info.append(s.id)
            if maintenance:
                tsdb.rollup(conn, now)
                tsdb.prune(conn, now, self._settings)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        if maintenance:
            self._last_maintenance = now
        self._info_at.update(dict.fromkeys(got_info, now))

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _run_one_cycle(self) -> None:
        try:
            self.poll_once(int(time.time()))
        except Exception:  # Sicherheitsnetz: der Abruf-Thread darf nie sterben
            log.exception("Abrufzyklus fehlgeschlagen")

    def _loop(self) -> None:
        try:
            while True:
                self._run_one_cycle()
                if self._stop.wait(self._settings.collect_interval_seconds):
                    break
        finally:
            self.close()  # Verbindung gehört diesem Thread

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="moni-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self._settings.poll_timeout_seconds * 3 + 5)
