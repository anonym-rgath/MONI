import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, StringConstraints, field_validator

from ..fsutil import write_private
from ..timeutil import iso, parse_iso, utcnow

log = logging.getLogger(__name__)

SCOPES = frozenset({"read", "admin"})
_ID_RE = re.compile(r"[0-9a-f]{8}")
_TOUCH = timedelta(hours=1)  # „zuletzt benutzt“ stundengenau: sonst schriebe jede Anfrage (Prometheus alle 15 s) die Datei


class NewToken(BaseModel):
    """Eingabe für neue Tokens; Manager-API erweitert um scope, der Agent nimmt sie unverändert (immer read)."""
    model_config = ConfigDict(extra="forbid")
    name: Annotated[StrictStr, StringConstraints(min_length=1, max_length=64)]
    expires_days: StrictInt | None = Field(None, ge=1, le=3650)

    @field_validator("name")
    @classmethod
    def _visible(cls, v: str) -> str:
        if not v.isprintable() or not v.strip():
            raise ValueError("Name muss sichtbare Zeichen enthalten")
        return v


@dataclass(frozen=True)
class Token:
    id: str
    name: str
    scope: str
    created_at: str
    expires_at: str | None
    hash: str
    created_by: int | None = None  # N7: Benutzer-ID; None = CLI oder vor 2.1 angelegt
    last_used_at: str | None = None


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
                if t.created_by is not None and type(t.created_by) is not int:
                    raise TypeError
                for ts in (t.expires_at, t.last_used_at):
                    if ts is not None:
                        parse_iso(ts)  # ValueError/TypeError bei Unsinn
            return tokens
        except (ValueError, KeyError, TypeError):
            log.error("Token-Datei %s ist beschädigt", self.path)
            raise RuntimeError(f"Token-Datei {self.path} ist beschädigt") from None

    def _save(self, tokens: list[Token]) -> None:
        data = json.dumps({"version": 1, "tokens": [asdict(t) for t in tokens]}, indent=2)
        write_private(self.path, data.encode())

    @contextmanager
    def _lock(self):
        # Sidecar-Lock: schützt Lesen-Ändern-Schreiben über Prozesse hinweg
        fd = os.open(self.path.with_name(self.path.name + ".lock"), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def create(self, name: str, scope: str = "read", expires_at: datetime | None = None,
               created_by: int | None = None) -> tuple[Token, str]:
        if scope not in SCOPES:
            raise ValueError(f"Unbekannter Scope: {scope!r}")
        tid = secrets.token_hex(4)
        plain = f"moni_{tid}_{secrets.token_urlsafe(32)}"
        tok = Token(tid, name, scope, iso(utcnow()), iso(expires_at) if expires_at else None, _hash(plain), created_by)
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
        now = now or utcnow()
        for t in tokens:
            if t.id == parts[1] and hmac.compare_digest(t.hash, h):
                if t.expires_at and parse_iso(t.expires_at) <= now:
                    return None
                if t.last_used_at is None or parse_iso(t.last_used_at) <= now - _TOUCH:
                    self._touch(t.id, now)
                return t
        return None

    def _touch(self, token_id: str, now: datetime) -> None:
        try:
            with self._lock():
                self._save([replace(t, last_used_at=iso(now)) if t.id == token_id else t for t in self._load()])
        except (OSError, RuntimeError) as e:  # Anmeldung geht vor: ohne Schreibrecht bleibt nur der Zeitstempel alt
            log.warning("Token-Nutzung nicht gespeichert (%s)", type(e).__name__)

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
