import logging
from collections.abc import Callable

from fastapi import HTTPException, Request

log = logging.getLogger("moni.auth")
_UNAUTH = {"WWW-Authenticate": "Bearer"}


def require_scope(scope: str) -> Callable:
    def dep(request: Request) -> None:
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
        if tok.scope not in ({"read", "admin"} if scope == "read" else {scope}):  # admin schließt read ein
            raise HTTPException(403, "Token hat nicht den nötigen Scope")
    return dep
