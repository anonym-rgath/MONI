"""Eine Anmeldung für alle Routen: Bearer-Token (Agent und Manager) oder Session-Cookie (nur Manager)."""
import hmac
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

from ..manager import db
from .sessions import COOKIE, Session, SessionStore
from .tokens import SCOPES
from .users import User, UserStore

log = logging.getLogger("moni.auth")
_UNAUTH = {"WWW-Authenticate": "Bearer"}
_SAFE = ("GET", "HEAD", "OPTIONS")
RANK = {"read": 1, "admin": 2}  # admin schließt read ein
if set(RANK) != SCOPES:
    raise RuntimeError("RANK und tokens.SCOPES passen nicht zusammen")


@dataclass(frozen=True)
class Principal:
    scope: str
    actor: str
    user_id: int | None
    session: Session | None = field(default=None, repr=False)


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _bearer(request: Request):
    parts = request.headers.get("authorization", "").split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise HTTPException(401, "Token fehlt oder ist ungültig", headers=_UNAUTH)
    try:
        tok = request.app.state.tokens.verify(parts[1])
    except Exception as e:  # nie Details oder Token nach aussen
        log.error("Token-Prüfung fehlgeschlagen (%s)", type(e).__name__)
        raise HTTPException(500, "Interner Fehler") from None
    if tok is None:
        raise HTTPException(401, "Token fehlt oder ist ungültig", headers=_UNAUTH)
    return tok


def _session(request: Request, stages: tuple[str, ...]) -> tuple[Session, User]:
    plain = request.cookies.get(COOKIE)
    s = user = None
    if plain and request.app.state.settings.role == "manager":
        # Rolle und Status bei jeder Anfrage frisch aus der DB, damit Herabstufen und Deaktivieren sofort wirken
        with db.request_conn(request) as conn:
            s = SessionStore(conn, request.app.state.settings).get(plain, int(time.time()))
            user = UserStore(conn, request.app.state.box).get(s.user_id) if s else None
    if s is None or s.stage not in stages or user is None or user.disabled:
        raise HTTPException(401, "Nicht angemeldet", headers=_UNAUTH)
    if request.method not in _SAFE and not hmac.compare_digest(
            request.headers.get("x-csrf-token", "").encode(), s.csrf.encode()):
        raise HTTPException(403, "CSRF-Token fehlt oder ist ungültig")
    return s, user


def require_scope(scope: str) -> Callable[[Request], Principal]:
    if scope not in RANK:
        raise ValueError(f"Unbekannter Scope: {scope!r}")

    def dep(request: Request) -> Principal:
        # Ein Authorization-Header schließt das Cookie aus, auch wenn er kaputt ist; der Agent kennt nur Tokens.
        if "authorization" in request.headers or request.app.state.settings.role != "manager":
            tok = _bearer(request)
            if RANK.get(tok.scope, 0) < RANK[scope]:
                raise HTTPException(403, "Token hat nicht den nötigen Scope")
            return Principal(tok.scope, f"token:{tok.id}", None)
        s, user = _session(request, ("full",))
        have = "admin" if user.role == "admin" else "read"
        if RANK[have] < RANK[scope]:
            raise HTTPException(403, "Keine Berechtigung")
        return Principal(have, f"user:{user.email}", user.id, s)
    return dep


def require_stage(*stages: str) -> Callable[[Request], tuple[Session, User]]:
    """Nur Session-Cookie, für die Anmeldeschritte (z. B. "mfa", "enroll")."""
    def dep(request: Request) -> tuple[Session, User]:
        return _session(request, stages)
    return dep
