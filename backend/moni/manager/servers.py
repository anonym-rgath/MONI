"""Server-Verwaltung des Managers. Token und Header-Werte liegen nur verschlüsselt in der DB."""
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit

from .. import pki
from ..secretbox import SecretBox

log = logging.getLogger("moni.manager.servers")

_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]+$")
# Steuern Verbindung/Rahmung der Anfrage; als eigener Header würden sie Request Smuggling oder Proxy-Missbrauch erlauben
_FORBIDDEN_HEADERS = {"content-length", "transfer-encoding", "connection", "upgrade", "proxy-authorization", "te",
                      "trailer", "keep-alive"}
_UNREACHABLE_AFTER = 3


@dataclass(frozen=True)
class Server:
    """Bewusst ohne Token und Header-Werte, damit repr() und API-Ausgaben sicher sind."""
    id: int
    name: str
    url: str | None
    is_local: bool
    header_names: list[str]
    enabled: bool
    status: str
    failures: int
    last_error: str | None
    last_seen: int | None
    last_ts: int | None
    created_at: int
    security: str  # "token" oder "mtls" (D5)
    cert_not_after: int | None  # nur mtls: Ablauf des Agent-Zertifikats (Unix-Sekunden)


def _server(r: sqlite3.Row) -> Server:
    return Server(r["id"], r["name"], r["url"], bool(r["is_local"]), json.loads(r["header_names"]), bool(r["enabled"]),
                  r["status"], r["failures"], r["last_error"], r["last_seen"], r["last_ts"], r["created_at"],
                  r["security"], r["cert_not_after"])


def _visible_ascii(value: str, allow_space: bool = False) -> bool:
    return all((0x21 if not allow_space else 0x20) <= ord(c) <= 0x7E for c in value)


def _check_secret(value, what: str, allow_space: bool = False) -> None:
    """Token und Header-Werte gehen in HTTP-Header: sichtbares ASCII. Fehlermeldungen nennen nie den Wert."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{what} darf nicht leer sein")
    if not _visible_ascii(value, allow_space):  # deckt auch Surrogate, Steuerzeichen und Nicht-ASCII ab
        raise ValueError(f"{what} enthält ungültige Zeichen")


def _check_url(url) -> None:
    if not isinstance(url, str) or not _visible_ascii(url):  # urlsplit würde Tab/CR/LF stillschweigend entfernen
        raise ValueError("URL ist ungültig (nur sichtbares ASCII ohne Leerzeichen erlaubt)")
    try:
        u = urlsplit(url)
        host, _ = u.hostname, u.port
    except ValueError:
        raise ValueError("URL ist ungültig") from None
    if u.scheme not in ("http", "https") or not host:
        raise ValueError("URL muss mit http:// oder https:// beginnen und einen Host enthalten")
    if "@" in u.netloc:
        raise ValueError("URL darf keine Zugangsdaten enthalten")
    if "?" in url or "#" in url:  # auch leere: der Poller hängt die API-Pfade an
        raise ValueError("URL darf keine Query oder Fragment (? oder #) enthalten")


def _check_name(name) -> None:
    if not isinstance(name, str) or not name.strip() or len(name) > 64:
        raise ValueError("Name muss 1 bis 64 Zeichen lang sein")
    if any(not c.isprintable() for c in name):
        raise ValueError("Name enthält ungültige Zeichen")


class ServerStore:
    def __init__(self, conn: sqlite3.Connection, box: SecretBox):
        self._conn = conn
        self._box = box

    @contextmanager
    def _tx(self, immediate: bool = False):
        """Committet nur, wenn die Transaktion hier begonnen wurde; sonst entscheidet der Aufrufer (Poller-Zyklus)."""
        if self._conn.in_transaction:
            yield
            return
        if immediate:  # Schreibsperre vor dem SELECT, damit Prüfen und Einfügen atomar sind
            self._conn.execute("BEGIN IMMEDIATE")
        with self._conn:
            yield

    def _row(self, server_id: int) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM servers WHERE id=?", (server_id,)).fetchone()

    def ensure_local(self, name: str) -> Server:
        with self._tx(immediate=True):
            r = self._conn.execute("SELECT * FROM servers WHERE is_local=1").fetchone()
            try:
                if r is None:
                    cur = self._conn.execute("INSERT INTO servers (name, is_local, created_at) VALUES (?, 1, strftime('%s','now'))", (name,))
                    return _server(self._row(cur.lastrowid))
                if r["name"] != name:
                    self._conn.execute("UPDATE servers SET name=? WHERE id=?", (name, r["id"]))
                    r = self._row(r["id"])
            except sqlite3.IntegrityError:
                raise ValueError("Name ist bereits vergeben") from None
            return _server(r)

    def add(self, name: str, url: str, token: str, headers: dict[str, str] | None = None) -> Server:
        _check_name(name)
        _check_url(url)
        _check_secret(token, "Token")
        headers = dict(headers or {})
        for k, v in headers.items():
            if not isinstance(k, str) or not _HEADER_NAME.match(k):
                raise ValueError("Header-Name darf nur Buchstaben, Ziffern und Bindestriche enthalten")
            if k.lower() == "authorization":
                raise ValueError("Der Header Authorization ist reserviert (Agent-Token)")
            if k.lower() in _FORBIDDEN_HEADERS:
                raise ValueError(f"Der Header {k} ist nicht erlaubt (steuert die Verbindung)")
            _check_secret(v, f"Wert des Headers {k}", allow_space=True)
        try:
            with self._tx():
                cur = self._conn.execute(
                    "INSERT INTO servers (name, url, token_enc, headers_enc, header_names, created_at) "
                    "VALUES (?, ?, ?, ?, ?, strftime('%s','now'))",
                    (name, url, self._box.encrypt(token), self._box.encrypt(json.dumps(headers)), json.dumps(list(headers))))
        except sqlite3.IntegrityError:
            raise ValueError("Name ist bereits vergeben") from None
        log.warning("Server %s ohne mTLS angelegt (schwächere Variante, nur hinter vertrauenswürdigem Proxy)", name)
        return _server(self._row(cur.lastrowid))

    def check_mtls(self, name: str, url: str) -> None:
        """Alles prüfen, was vor der Kopplung geht: danach ist der Agent gekoppelt und braucht diesen Eintrag."""
        _check_name(name)
        _check_url(url)
        u = urlsplit(url)
        if u.scheme != "https":
            raise ValueError("mTLS braucht eine https://-URL")
        pki.san_for_host(u.hostname)
        if self._conn.execute("SELECT 1 FROM servers WHERE name=?", (name,)).fetchone():
            raise ValueError("Name ist bereits vergeben")

    def add_mtls(self, name: str, url: str, cert_not_after: int) -> Server:
        self.check_mtls(name, url)
        try:
            with self._tx():
                cur = self._conn.execute(
                    "INSERT INTO servers (name, url, security, cert_not_after, created_at) "
                    "VALUES (?, ?, 'mtls', ?, strftime('%s','now'))", (name, url, cert_not_after))
        except sqlite3.IntegrityError:
            raise ValueError("Name ist bereits vergeben") from None
        return _server(self._row(cur.lastrowid))

    def list(self) -> list[Server]:
        return [_server(r) for r in self._conn.execute("SELECT * FROM servers ORDER BY id")]

    def get(self, server_id: int) -> Server | None:
        r = self._row(server_id)
        return _server(r) if r else None

    def delete(self, server_id: int) -> bool:
        with self._tx():
            if self._conn.execute("DELETE FROM servers WHERE id=? AND is_local=0", (server_id,)).rowcount:
                return True
        if self._row(server_id) is not None:
            raise ValueError("Der lokale Server kann nicht gelöscht werden")
        return False

    def set_enabled(self, server_id: int, enabled: bool) -> bool:
        with self._tx():
            return self._conn.execute("UPDATE servers SET enabled=? WHERE id=?", (int(enabled), server_id)).rowcount > 0

    def credentials(self, server_id: int) -> tuple[str, dict[str, str]]:
        r = self._row(server_id)
        if r is None or r["token_enc"] is None:
            raise ValueError("Keine Zugangsdaten für diesen Server")
        return self._box.decrypt(r["token_enc"]), json.loads(self._box.decrypt(r["headers_enc"]))

    def record_success(self, server_id: int, now: int, last_ts: int, snapshot_json: str | None, info_json: str | None) -> None:
        with self._tx():
            self._conn.execute(
                "UPDATE servers SET status='ok', failures=0, last_error=NULL, last_seen=?, last_ts=?, "
            "last_snapshot=COALESCE(?, last_snapshot), "
                "agent_info=COALESCE(?, agent_info) WHERE id=?", (now, last_ts, snapshot_json, info_json, server_id))

    # `now` ist noch ungenutzt (Signatur laut Plan; reserviert für einen Fehlerzeitstempel).
    def record_failure(self, server_id: int, now: int, error: str, fatal: bool = False) -> str:
        with self._tx():
            self._conn.execute(
                "UPDATE servers SET failures=failures+1, last_error=?, status=CASE WHEN ? THEN 'error' "
                "WHEN failures+1>=? THEN 'unreachable' ELSE status END WHERE id=?",
                (error, int(fatal), _UNREACHABLE_AFTER, server_id))
            r = self._row(server_id)
        if r is None:
            raise ValueError("Server nicht gefunden")
        return r["status"]

    def reencrypt_all(self) -> int:
        n = 0
        with self._tx():
            for r in self._conn.execute("SELECT id, token_enc, headers_enc FROM servers WHERE token_enc IS NOT NULL").fetchall():
                self._conn.execute("UPDATE servers SET token_enc=?, headers_enc=? WHERE id=?",
                                   (self._box.rotate(r["token_enc"]), self._box.rotate(r["headers_enc"]), r["id"]))
                n += 1
        return n
