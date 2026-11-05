"""Serverseitige Sessions (D4). In der DB liegt nur der SHA-256-Hash der Session-ID."""
import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field

from ..config import Settings

COOKIE = "moni_session"
PRE_AUTH_SECONDS = 600
TOUCH_EVERY = 60


@dataclass(frozen=True)
class Session:
    id_hash: str = field(repr=False)
    user_id: int
    stage: str
    csrf: str = field(repr=False)
    created_at: int
    last_seen: int


def _hash(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


class SessionStore:
    def __init__(self, conn: sqlite3.Connection, settings: Settings):
        self._conn = conn
        self._idle = settings.session_idle_minutes * 60
        self._max = settings.session_max_hours * 3600

    @contextmanager
    def _tx(self):
        """Committet nur, wenn die Transaktion hier begonnen wurde (P2-R1)."""
        if self._conn.in_transaction:
            yield
            return
        with self._conn:
            yield

    def _expired(self, stage: str, created_at: int, last_seen: int, now: int) -> bool:
        if stage != "full":
            return now - created_at >= PRE_AUTH_SECONDS
        return now - last_seen >= self._idle or now - created_at >= self._max

    def _insert(self, user_id: int, stage: str, now: int, ip: str | None) -> tuple[Session, str]:
        plain, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        s = Session(_hash(plain), user_id, stage, csrf, now, now)
        self._conn.execute("INSERT INTO sessions (id_hash, user_id, stage, csrf, created_at, last_seen, ip) VALUES (?,?,?,?,?,?,?)",
                           (s.id_hash, user_id, stage, csrf, now, now, ip))
        return s, plain

    def create(self, user_id: int, stage: str, now: int, ip: str | None) -> tuple[Session, str]:
        with self._tx():
            return self._insert(user_id, stage, now, ip)

    def get(self, plain: str, now: int) -> Session | None:
        if not isinstance(plain, str):
            return None
        try:
            h = _hash(plain)
        except UnicodeEncodeError:
            return None
        r = self._conn.execute("SELECT * FROM sessions WHERE id_hash=?", (h,)).fetchone()
        if r is None:
            return None
        if self._expired(r["stage"], r["created_at"], r["last_seen"], now):
            self.delete(h)
            return None
        last = r["last_seen"]
        if now - last >= TOUCH_EVERY:
            with self._tx():
                self._conn.execute("UPDATE sessions SET last_seen=? WHERE id_hash=?", (now, h))
            last = now
        return Session(h, r["user_id"], r["stage"], r["csrf"], r["created_at"], last)

    def rotate(self, session: Session, stage: str, now: int, ip: str | None) -> tuple[Session, str]:
        with self._tx():
            self._conn.execute("DELETE FROM sessions WHERE id_hash=?", (session.id_hash,))
            return self._insert(session.user_id, stage, now, ip)

    def delete(self, id_hash: str) -> None:
        with self._tx():
            self._conn.execute("DELETE FROM sessions WHERE id_hash=?", (id_hash,))

    def delete_for_user(self, user_id: int, keep: str | None = None) -> int:
        with self._tx():
            return self._conn.execute("DELETE FROM sessions WHERE user_id=? AND id_hash IS NOT ?", (user_id, keep)).rowcount

    def purge_expired(self, now: int) -> int:
        with self._tx():
            return self._conn.execute(
                "DELETE FROM sessions WHERE (stage != 'full' AND ? - created_at >= ?) OR (stage = 'full' AND (? - last_seen >= ? OR ? - created_at >= ?))",
                (now, PRE_AUTH_SECONDS, now, self._idle, now, self._max)).rowcount
