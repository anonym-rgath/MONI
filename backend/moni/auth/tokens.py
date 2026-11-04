import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from ..timeutil import iso, parse_iso, utcnow

log = logging.getLogger(__name__)

SCOPES = frozenset({"read"})
_ID_RE = re.compile(r"[0-9a-f]{8}")


@dataclass(frozen=True)
class Token:
    id: str
    name: str
    scope: str
    created_at: str
    expires_at: str | None
    hash: str


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


class TokenStore:
    """Liest tokens.json bei jedem Aufruf neu (kein Cache), damit CLI-Änderungen sofort greifen."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> list[Token]:
        # wirft RuntimeError bei kaputter Datei; fehlende Datei = leer
        try:
            raw = self.path.read_text()
        except FileNotFoundError:
            return []
        except OSError as e:
            log.error("Token-Datei %s nicht lesbar: %s", self.path, e.strerror)
            raise RuntimeError(f"Token-Datei nicht lesbar: {e.strerror}") from None
        except UnicodeDecodeError:
            raw = None
        try:
            if raw is None:
                raise ValueError
            data = json.loads(raw)
            if data["version"] != 1:
                raise ValueError
            tokens = [Token(**t) for t in data["tokens"]]
            for t in tokens:
                if not all(isinstance(v, str) for v in (t.id, t.name, t.scope, t.created_at, t.hash)):
                    raise TypeError
                if t.expires_at is not None:
                    parse_iso(t.expires_at)  # ValueError/TypeError bei Unsinn
            return tokens
        except (ValueError, KeyError, TypeError):
            log.error("Token-Datei %s ist beschädigt", self.path)
            raise RuntimeError(f"Token-Datei {self.path} ist beschädigt") from None

    def _save(self, tokens: list[Token]) -> None:
        tmp = self.path.with_name(f"{self.path.name}.{secrets.token_hex(4)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"version": 1, "tokens": [asdict(t) for t in tokens]}, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    @contextmanager
    def _lock(self):
        # Sidecar-Lock: schützt Lesen-Ändern-Schreiben über Prozesse hinweg
        fd = os.open(self.path.with_name(self.path.name + ".lock"), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def create(self, name: str, scope: str = "read", expires_at: datetime | None = None) -> tuple[Token, str]:
        if scope not in SCOPES:
            raise ValueError(f"Unbekannter Scope: {scope!r}")
        tid = secrets.token_hex(4)
        plain = f"moni_{tid}_{secrets.token_urlsafe(32)}"
        tok = Token(tid, name, scope, iso(utcnow()), iso(expires_at) if expires_at else None, _hash(plain))
        with self._lock():
            self._save([*self._load(), tok])
        return tok, plain

    def verify(self, plaintext: str, now: datetime | None = None) -> Token | None:
        parts = plaintext.split("_", 2)
        if len(parts) != 3 or parts[0] != "moni" or not _ID_RE.fullmatch(parts[1]) or not parts[2]:
            return None
        try:
            tokens = self._load()
        except RuntimeError:
            return None
        h = _hash(plaintext)
        for t in tokens:
            if t.id == parts[1] and hmac.compare_digest(t.hash, h):
                if t.expires_at and parse_iso(t.expires_at) <= (now or utcnow()):
                    return None
                return t
        return None

    def list(self) -> list[Token]:
        try:
            return self._load()
        except RuntimeError:
            return []

    def revoke(self, token_id: str) -> bool:
        with self._lock():
            tokens = self._load()
            rest = [t for t in tokens if t.id != token_id]
            if len(rest) == len(tokens):
                return False
            self._save(rest)
            return True
