"""Abruf der Agents: parallel per HTTP, Lückenfüllung über /history, ein DB-Schreibzyklus pro Takt."""
import functools
import http.client
import json
import logging
import sqlite3
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone

from .. import pki
from ..auth import audit
from ..auth.sessions import SessionStore
from ..collector import Collector
from ..config import Settings
from ..secretbox import SecretBox, SecretError
from ..timeutil import iso, parse_iso
from . import alerting, db, tsdb
from .selfmetrics import PollStats
from .servers import ServerStore

log = logging.getLogger("moni.poller")

_MAX_BYTES = 20 * 1024 * 1024
_MAINTENANCE_EVERY = 300
_INFO_EVERY = 300
_MAX_FUTURE = 300
_MAX_PAST = 3 * 3600
_MAX_WORKERS = 32  # je Server ein Worker, damit hängende Agents keine anderen verdrängen
_GRACE = 5  # Sekunden über 3 Anfragen x Timeout hinaus, bevor ein Abruf als hängend gilt
_RENEW_BEFORE = 30 * 86400  # Agent-Zertifikat ab 30 Tagen Restlaufzeit erneuern (D1)
_RENEW_RETRY = 3600  # nach einem Fehlschlag frühestens nach einer Stunde erneut, ebenso pki.ensure
_HTTPS_EVERY = 86400  # HTTPS-Zertifikat des Managers einmal täglich prüfen (D7)


class AgentError(Exception):
    """Fehler beim Abruf. `message` ist ein fester deutscher Text ohne Geheimnisse, URLs oder Antwortinhalte."""

    def __init__(self, message: str, fatal: bool = False, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.fatal = fatal
        self.status = status  # HTTP-Status der Agent-Antwort, sonst None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # nie folgen: 3xx wird zum HTTPError
        return None


# Kein Proxy aus HTTP(S)_PROXY: Token und Header gingen sonst an einen Dritten (Proxy-Option: Paket 7, Doku).
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
_TLS_FAILED = "TLS-Prüfung des Agents fehlgeschlagen"


def _ssl_error(e: ssl.SSLError) -> AgentError:
    """Abgebrochene Verbindung ist kein Prüffehler: nur Prüfung und TLS-Alerts sind fatal (P4-R4)."""
    if isinstance(e, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
        return AgentError("Agent nicht erreichbar")
    return AgentError(_TLS_FAILED, fatal=True)


@functools.cache
def _system_context() -> ssl.SSLContext:
    return pki.client_context(None)  # Token-Server mit https: System-Vertrauensspeicher, nie die Moni-CA (D5)


class AgentClient:
    def __init__(self, url: str, token: str, headers: dict[str, str], timeout: float,
                 ssl_context: ssl.SSLContext | None = None):
        """Mit `ssl_context` (mTLS) meldet das Client-Zertifikat an, ohne Authorization-Header."""
        self._base = url.rstrip("/")
        auth = {} if ssl_context is not None else {"Authorization": f"Bearer {token}"}
        self._headers = {**headers, **auth, "Accept": "application/json"}
        self._timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect,
                                                   urllib.request.HTTPSHandler(context=ssl_context or _system_context()))

    def __repr__(self) -> str:
        return "AgentClient(…)"

    def get(self, path: str, params: dict | None = None, expect: type = dict) -> dict | list:
        """`expect=list` nur für Antworten, die laut Vertrag eine Liste sind (z. B. /tokens)."""
        url = self._base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        return self._request("GET", url, None, expect)

    def post(self, path: str, body: dict) -> dict | None:
        """None bei leerer Antwort (204)."""
        return self._request("POST", self._base + path, json.dumps(body).encode(), dict)

    def delete(self, path: str) -> None:
        self._request("DELETE", self._base + path, None, dict)

    def _request(self, method: str, url: str, data: bytes | None, expect: type):
        if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            raise AgentError("Agent-URL muss mit http:// oder https:// beginnen", fatal=True)
        headers = self._headers if data is None else {**self._headers, "Content-Type": "application/json"}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        err = body = None
        deadline = time.monotonic() + self._timeout  # Gesamtfrist: urllib-Timeout gilt nur je Socket-Operation
        try:
            with self._opener.open(req, timeout=self._timeout) as resp:
                body = bytearray()
                while len(body) <= _MAX_BYTES and (chunk := resp.read1(65536)):
                    body += chunk
                    if time.monotonic() > deadline:  # tröpfelnder Agent
                        raise TimeoutError
        except urllib.error.HTTPError as e:
            e.close()
            err = AgentError("Agent lehnt Token ab", fatal=True, status=e.code) if e.code in (401, 403) else \
                AgentError(f"Agent antwortet mit HTTP {e.code}", status=e.code)
        except ssl.SSLError as e:  # auch beim Lesen, z. B. wenn der Agent das Client-Zertifikat ablehnt (TLS 1.3)
            err = _ssl_error(e)
        except urllib.error.URLError as e:
            err = _ssl_error(e.reason) if isinstance(e.reason, ssl.SSLError) else AgentError("Agent nicht erreichbar")
        except (OSError, http.client.HTTPException, ValueError):  # URLError, Timeout, Abbruch, kaputte Antwort
            err = AgentError("Agent nicht erreichbar")
        if err is None:
            if not body and method != "GET":
                return None
            try:
                data = json.loads(body) if len(body) <= _MAX_BYTES else None
            except ValueError:
                data = None
            if not isinstance(data, expect):
                err = AgentError("Ungültige Antwort vom Agent")
        if err is not None:  # außerhalb von except: kein __context__ mit Anfrage- oder Antwortdetails
            raise err
        return data


def _ts(snap) -> int | None:
    try:
        return int(parse_iso(snap["timestamp"]).timestamp())
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return None


def _dumps(obj) -> str | None:
    """Nur Standard-JSON speichern: die API gibt den Text unverändert aus."""
    try:
        return json.dumps(obj, allow_nan=False)
    except ValueError:
        return None


def fetch_new(client: AgentClient, last_ts: int | None, now: int, interval: int) -> list[dict]:
    snaps = []
    if last_ts is not None and now - last_ts > interval:  # P2-R8: jeder verpasste Agent-Takt wird nachgeholt
        try:
            points = client.get("/api/v1/history", {"since": iso(datetime.fromtimestamp(last_ts, timezone.utc))}).get("points")
            if not isinstance(points, list):
                raise AgentError("Ungültige Antwort vom Agent")
            snaps = list(points)
        except AgentError as e:
            if e.fatal:
                raise
            log.info("Lückenfüllung übersprungen, Lücke bleibt: %s", e.message)  # sonst sperrt eine zu große Historie den Server aus
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
    def __init__(self, settings: Settings, box: SecretBox, collector: Collector, client_factory=AgentClient, pki=None):
        self._settings = settings
        self._pki = pki  # ManagerPki; ohne sie sind mTLS-Server nicht abrufbar
        self._box = box
        self._collector = collector
        self._client_factory = client_factory
        self._conn: sqlite3.Connection | None = None
        self._last_maintenance: int | None = None
        self._ensured_at: int | None = None
        self._https_at: int | None = None
        self._info_at: dict[int, int] = {}  # Zeitpunkt des letzten Info-Versuchs, ob erfolgreich oder nicht
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats = PollStats()

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _fetch(self, server, client, now: int):
        """Läuft im Worker-Thread: nur HTTP, keine DB."""
        try:
            snaps = fetch_new(client, server.last_ts, now, self._settings.collect_interval_seconds)
            info = None
            if server.id not in self._info_at or now - self._info_at[server.id] >= _INFO_EVERY:
                self._info_at[server.id] = now  # je Server ein eigener Schlüssel, nur dieser Worker schreibt ihn
                try:
                    info = client.get("/api/v1/info")
                except AgentError:
                    pass  # Info ist Beiwerk; nächster Versuch nach _INFO_EVERY
            return snaps, info
        except AgentError as e:
            return e
        except Exception as e:  # unerwartete Antwortform darf andere Server nicht mitreißen
            log.error("Abruf von %s fehlgeschlagen (%s)", server.name, type(e).__name__)
            return AgentError("Ungültige Antwort vom Agent")

    def _fail(self, store: ServerStore, server, now: int, message: str, fatal: bool = False) -> None:
        status = store.record_failure(server.id, now, message, fatal=fatal)
        self.stats.fail(server.name)
        # Warnung nur beim Statuswechsel, nicht alle 15 s
        log.log(logging.WARNING if status != server.status else logging.DEBUG,
                "Abruf von %s fehlgeschlagen: %s (Status %s)", server.name, message, status)

    def _write(self, store: ServerStore, server, result, now: int) -> None:
        if isinstance(result, AgentError):
            return self._fail(store, server, now, result.message, result.fatal)
        snaps, info = result
        if not snaps:  # nichts Neues seit last_ts: hängt der Agent (oder der lokale Sammler)?
            if now - (server.last_seen or server.created_at) > 2 * self._settings.collect_interval_seconds:
                self._fail(store, server, now, "Lokale Messung liefert keine neuen Daten" if server.is_local
                           else "Agent liefert keine neuen Daten")
            return
        plausible = [s for s in snaps if now - _MAX_PAST <= _ts(s) <= now + _MAX_FUTURE]
        if not plausible:
            return self._fail(store, server, now, "Zeitstempel des Agents unplausibel")
        newest = tsdb.write_snapshots(self._conn, server.id, plausible)
        snap_json = _dumps(max(plausible, key=_ts))
        if snap_json is None:  # Werte stehen trotzdem in der Zeitreihe; die API liefert den vorigen Snapshot
            log.warning("Snapshot von %s nicht als JSON speicherbar (NaN/inf), vorheriger bleibt", server.name)
        store.record_success(server.id, now, newest, snap_json, _dumps(info) if info else None)

    def _store(self, store: ServerStore, server, result, now: int) -> None:
        """Eigener Savepoint je Server: kaputte Daten eines Agents rollen nur dessen Schreibvorgänge zurück."""
        conn = self._conn
        conn.execute("SAVEPOINT server")
        try:
            self._write(store, server, result, now)
        except sqlite3.OperationalError:  # DB-Problem (z. B. gesperrt), nicht Schuld des Agents: ganzer Zyklus
            raise
        except Exception as e:
            conn.execute("ROLLBACK TO server")
            if store.get(server.id) is not None:  # während des Abrufs gelöscht: nichts mehr zu vermerken
                log.error("Antwort von %s nicht speicherbar (%s)", server.name, type(e).__name__)
                self._fail(store, server, now, "Ungültige Antwort vom Agent", fatal=True)
        conn.execute("RELEASE server")

    def _timed(self, server, client, now: int):
        """Worker schreibt nie Kennzahlen, er liefert nur die Dauer mit zurück."""
        t0 = time.monotonic()
        return self._fetch(server, client, now), time.monotonic() - t0

    def _fetch_all(self, jobs, now: int) -> dict:
        """Parallel, außerhalb jeder DB-Transaktion; Nachzügler blockieren den Zyklus nicht."""
        ex = ThreadPoolExecutor(max_workers=max(1, min(_MAX_WORKERS, len(jobs))))
        start = time.monotonic()
        try:
            futs = {ex.submit(self._timed, s, c, now): s for s, c in jobs}
            pending, end = set(futs), time.monotonic() + self._settings.poll_timeout_seconds * 3 + _GRACE
            while pending and not self._stop.is_set() and (left := end - time.monotonic()) > 0:
                _, pending = wait(pending, timeout=min(left, 0.5))  # in Schritten, damit stop() greift
            out = {}
            for f, s in futs.items():
                if f in pending:
                    out[s.id], seconds = AgentError("Agent nicht erreichbar"), time.monotonic() - start
                else:
                    out[s.id], seconds = f.result()
                self.stats.record(s.name, seconds)
            return out
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    def _maintain(self, now: int) -> bool:
        """Verdichten und Aufräumen im eigenen Savepoint: ein Fehler darf die Serverdaten des Zyklus nicht kosten."""
        conn = self._conn
        conn.execute("SAVEPOINT maintenance")
        try:
            tsdb.rollup(conn, now)
            tsdb.prune(conn, now, self._settings)
            SessionStore(conn, self._settings).purge_expired(now)  # beide joinen die offene Transaktion
            audit.prune(conn, now - audit.RETENTION_DAYS * 86400)
            alerting.prune(conn, now)
        except Exception as e:
            conn.execute("ROLLBACK TO maintenance")
            conn.execute("RELEASE maintenance")
            log.error("Verdichtung fehlgeschlagen (%s), neuer Versuch im nächsten Zyklus", type(e).__name__)
            return False
        conn.execute("RELEASE maintenance")
        return True

    def poll_once(self, now: int) -> None:
        if self._conn is None:
            self._conn = db.connect(self._settings.db_path)
        conn = self._conn
        store = ServerStore(conn, self._box)
        enabled = [s for s in store.list() if s.enabled]
        results, jobs = {}, []
        for s in enabled:
            if s.is_local:  # alle Punkte seit last_ts aus dem Puffer, nicht nur den jüngsten (P2-R8)
                snaps = [self._collector.latest()] if s.last_ts is None else \
                    self._collector.history(datetime.fromtimestamp(s.last_ts, timezone.utc))[0]
                results[s.id] = ([x for x in snaps if x is not None and (ts := _ts(x)) is not None
                                  and (s.last_ts is None or ts > s.last_ts)], None)
                continue
            timeout = self._settings.poll_timeout_seconds
            if s.security == "mtls":
                try:
                    if self._pki is None:
                        raise ValueError
                    ctx = self._pki.client_context()
                except (SecretError, OSError, ValueError):  # ssl.SSLError ist ein OSError; Text ohne Details
                    results[s.id] = AgentError("Client-Zertifikat nicht verfügbar", fatal=True)
                    continue
                jobs.append((s, self._client_factory(s.url, "", {}, timeout, ssl_context=ctx)))
                continue
            try:
                token, headers = store.credentials(s.id)
                if not isinstance(headers, dict):
                    raise ValueError
            except (SecretError, ValueError):  # falscher Schlüssel oder kaputte Zeile; Text ohne Details
                results[s.id] = AgentError("Zugangsdaten nicht entschlüsselbar", fatal=True)
                continue
            jobs.append((s, self._client_factory(s.url, token, headers, timeout)))
        if jobs:
            results.update(self._fetch_all(jobs, now))
        if self._stop.is_set():
            return
        maintenance = self._last_maintenance is None or now - self._last_maintenance >= _MAINTENANCE_EVERY
        conn.execute("BEGIN IMMEDIATE")
        try:
            for s in enabled:
                self._store(store, s, results[s.id], now)
            if maintenance:
                maintenance = self._maintain(now)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        if maintenance:
            self._last_maintenance = now
        if not self._stop.is_set():
            self._alerts(now)
        self._renew_certs(now)

    def _alerts(self, now: int) -> None:
        """Eigene kurze Transaktion nach dem Commit: ein Fehler kostet nur die Auswertung, nicht die Serverdaten."""
        conn = self._conn
        try:
            conn.execute("BEGIN IMMEDIATE")  # im try: eine Sperr-Zeitüberschreitung kostet nur diese Auswertung
            alerting.run(conn, now)
            conn.commit()
        except Exception as e:
            if conn.in_transaction:
                conn.rollback()
            log.error("Alarmauswertung fehlgeschlagen (%s)", type(e).__name__)  # Text könnte Werte enthalten
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise

    def _renew_certs(self, now: int) -> None:
        """Nach dem Commit des Zyklus: das Netzwerk läuft nie, während die DB-Schreibsperre gehalten wird."""
        if self._pki is None or self._stop.is_set():
            return
        from .pairing import PairingError, renew  # pairing importiert dieses Modul
        at = datetime.fromtimestamp(now, timezone.utc)
        if self._ensured_at is None or now - self._ensured_at >= _RENEW_RETRY:
            self._ensured_at = now
            try:  # erneuert das Client-Zertifikat; client_context() baut den Kontext danach selbst neu
                self._pki.ensure(at)
            except Exception as e:
                # RuntimeError trägt feste Texte ohne Geheimnisse (P4-R5), alles andere nur den Typ
                log.error("Client-Zertifikat des Managers nicht erneuerbar (%s)",
                          str(e) if type(e) is RuntimeError else type(e).__name__)
        if self._settings.role == "manager" and self._settings.tls == "on" and (
                self._https_at is None or now - self._https_at >= _HTTPS_EVERY):
            self._https_at = now
            self._renew_https(at)
        due = self._conn.execute(
            "SELECT id, name, url FROM servers WHERE security='mtls' AND cert_not_after < ? "
            "AND (cert_renew_tried IS NULL OR cert_renew_tried <= ?) ORDER BY id",
            (now + _RENEW_BEFORE, now - _RENEW_RETRY)).fetchall()
        done = []
        for sid, name, url in due:  # nacheinander: es sind wenige Server
            if self._stop.is_set():
                break
            try:
                client = self._client_factory(url, "", {}, self._settings.poll_timeout_seconds,
                                              ssl_context=self._pki.client_context())
                done.append((sid, renew(client, url, self._pki, at)))
                log.info("Zertifikat für %s erneuert", name)
                continue
            except (AgentError, PairingError) as e:
                msg = e.message
            except (SecretError, OSError, ValueError):
                msg = "Manager-Zertifikate nicht lesbar"
            except Exception as e:  # unerwartete Antwortform darf andere Server nicht mitreißen
                msg = f"unerwarteter Fehler ({type(e).__name__})"
            log.warning("Zertifikat für %s konnte nicht erneuert werden: %s", name, msg)
            done.append((sid, None))
        if not done:
            return
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            for sid, not_after in done:
                if not_after is None:  # altes Zertifikat bleibt in Kraft
                    conn.execute("UPDATE servers SET cert_renew_tried=? WHERE id=?", (now, sid))
                else:
                    conn.execute("UPDATE servers SET cert_not_after=?, cert_renew_tried=NULL WHERE id=?", (not_after, sid))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def _renew_https(self, at: datetime) -> None:
        try:
            if not self._pki.ensure_https(self._settings.manager_hostnames, at):
                return
        except Exception as e:
            log.error("HTTPS-Zertifikat des Managers nicht erneuerbar (%s)", type(e).__name__)
            return
        try:  # Ausgabe nie ins Log; sie könnte Pfade oder Konfiguration enthalten
            r = subprocess.run(["nginx", "-s", "reload"], timeout=10, check=False, capture_output=True)
            err = f"Exit-Code {r.returncode}" if r.returncode else None
        except (OSError, subprocess.SubprocessError) as e:
            err = type(e).__name__
        if err:
            log.warning("nginx -s reload fehlgeschlagen (%s)", err)
        else:
            log.info("HTTPS-Zertifikat des Managers erneuert, nginx neu geladen")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _run_one_cycle(self) -> None:
        t0 = time.monotonic()
        try:
            self.poll_once(int(time.time()))
        except Exception:  # Sicherheitsnetz: der Abruf-Thread darf nie sterben
            log.exception("Abrufzyklus fehlgeschlagen")
        self.stats.cycle(time.monotonic() - t0)

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
            self._thread.join(timeout=10)
