"""Kopplung, Zertifikatserneuerung und Token-Verwaltung am Agent (D2, D3, D9); nur mit MONI_TLS=on.

Ändert nur den eigenen TLS-Zustand und die eigenen Tokens, nie den Host. Nie Token, Schlüssel oder PEM-Inhalte im Log.
"""
import logging
import time
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict, StrictStr, StringConstraints

from moni.auth.deps import manager_cert, require_manager
from moni.auth.throttle import throttle_key
from moni.auth.tokens import NewToken  # ohne scope (§6.3: am Agent immer read); manager/api.py nutzt es mit
from moni.timeutil import utcnow

log = logging.getLogger("moni.agent")
router = APIRouter(prefix="/api/v1")
manager = Depends(require_manager())
_HDR = "x-moni-pairing-token"
_MISMATCH = "Zertifikat passt nicht"
Pem = Annotated[StrictStr, StringConstraints(max_length=16384)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CertBody(_Body):
    certificate: Pem
    ca: Pem


# async: alles am TlsState läuft im Event-Loop-Thread, der laufende SSLContext ist nicht threadsicher (P4-R2)
async def _pairing(request: Request) -> bool:
    """True = Kopplung per Kopplungs-Token (ungekoppelt), False = Erneuerung durch den Manager (gekoppelt)."""
    tls = request.app.state.tls
    if tls.paired:
        if _HDR in request.headers and not manager_cert(request):
            raise HTTPException(409, "Agent ist bereits gekoppelt")
        require_manager()(request)
        return False
    now, key = int(time.time()), throttle_key(request)
    throttle = request.app.state.pairing_throttle
    wait = throttle.retry_after(key, now)
    if wait is not None:
        raise HTTPException(429, "Zu viele Fehlversuche, bitte später erneut versuchen", headers={"Retry-After": str(wait)})
    if not tls.check_token(request.headers.get(_HDR)):
        throttle.fail(key, now)
        raise HTTPException(401, "Kopplungs-Token ungültig")
    return True


def _norm(pem: str) -> str:
    return "".join(pem.split())


@router.post("/tls/csr")
async def tls_csr(request: Request, _: bool = Depends(_pairing)):
    return {"csr": request.app.state.tls.new_csr().decode()}


@router.post("/tls/cert", status_code=204)
async def tls_cert(request: Request, body: CertBody, pairing: bool = Depends(_pairing)):
    tls = request.app.state.tls
    if not pairing and _norm(body.ca) != _norm((tls.dir / "ca.pem").read_text()):  # keine CA-Rotation (D1)
        raise HTTPException(422, _MISMATCH)
    try:
        tls.install(body.certificate.encode(), body.ca.encode(), utcnow())  # UnicodeEncodeError ist ein ValueError
    except ValueError:
        raise HTTPException(422, _MISMATCH) from None
    log.info("Agent gekoppelt" if pairing else "Agent-Zertifikat erneuert")
    return Response(status_code=204)


def _token_out(t) -> dict:  # bewusst ohne hash
    return {"id": t.id, "name": t.name, "scope": t.scope, "created_at": t.created_at, "expires_at": t.expires_at}


@router.get("/tokens", dependencies=[manager])
def list_tokens(request: Request):
    return [_token_out(t) for t in request.app.state.tokens.list()]


@router.post("/tokens", status_code=201, dependencies=[manager])
def create_token(request: Request, body: NewToken):
    exp = utcnow() + timedelta(days=body.expires_days) if body.expires_days else None
    tok, plain = request.app.state.tokens.create(body.name, "read", expires_at=exp)
    return {**_token_out(tok), "token": plain}


@router.delete("/tokens/{token_id}", status_code=204, dependencies=[manager])
def revoke_token(request: Request, token_id: Annotated[str, Path(max_length=64)]):
    if not request.app.state.tokens.revoke(token_id):
        raise HTTPException(404, "Token nicht gefunden")
    return Response(status_code=204)
