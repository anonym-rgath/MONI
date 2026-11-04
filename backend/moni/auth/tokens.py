import hashlib
import hmac
import json
import logging
import os
import re
import secrets
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
            raise RuntimeError(f"Token-Datei nicht lesbar: {e.strerror}") from None
        try:
            data = json.loads(raw)
            if data["version"] != 1:
                raise ValueError
            return [Token(**t) for t in data["tokens"]]
        except (ValueError, KeyError, TypeError):
            log.error("Token-Datei %s ist beschädigt", self.path)
            raise RuntimeError(f"Token-Datei {self.path} ist beschädigt") from None

    def _save(self, tokens: list[Token]) -> None:
        tmp = self.path.with_name(f"{self.path.name}.{secrets.token_hex(4)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"version": 1, "tokens": [asdict(t) for t in tokens]}, f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def create(self, name: str, scope: str = "read", expires_at: datetime | None = None) -> tuple[Token, str]:
        if scope not in SCOPES:
            raise ValueError(f"Unbekannter Scope: {scope!r}")
        tokens = self._load()
        tid = secrets.token_hex(4)
        plain = f"moni_{tid}_{secrets.token_urlsafe(32)}"
        tok = Token(tid, name, scope, iso(utcnow()), iso(expires_at) if expires_at else None, _hash(plain))
        self._save([*tokens, tok])
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
        tokens = self._load()
        rest = [t for t in tokens if t.id != token_id]
        if len(rest) == len(tokens):
            return False
        self._save(rest)
        return True
