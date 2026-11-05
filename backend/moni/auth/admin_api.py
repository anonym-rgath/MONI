"""Verwaltungs-API des Managers (nur Admin): Benutzer, API-Tokens, Audit-Log.

Nie Passwörter, Hashes, Token-Klartext (außer einmalig bei der Erzeugung) in Antworten oder Audit-Details.
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, StringConstraints, field_validator

from ..manager import db
from ..timeutil import iso, utcnow
from . import audit
from .deps import Principal, client_ip, require_scope
from .sessions import SessionStore
from .users import Conflict, User, UserStore

router = APIRouter(prefix="/api/v1")
admin = Depends(require_scope("admin"))
Str = Annotated[StrictStr, StringConstraints(max_length=1024)]
UserId = Annotated[int, Path(ge=1, le=2**63 - 1)]
Role = Literal["admin", "viewer"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewUser(_Body):
    email: Str
    name: Str = ""
    role: Role
    kind: Literal["local", "oidc"] = "local"
    password: Str | None = None


class UserPatch(_Body):
    name: Str = None  # type: ignore[assignment]  # fehlt = unverändert; explizites null ergibt 422
    role: Role = None  # type: ignore[assignment]
    disabled: StrictBool = None  # type: ignore[assignment]


class PasswordBody(_Body):
    password: Str


class NewToken(_Body):
    name: Annotated[StrictStr, StringConstraints(min_length=1, max_length=64)]
    scope: Literal["read", "admin"] = "read"
    expires_days: StrictInt | None = Field(None, ge=1, le=3650)

    @field_validator("name")
    @classmethod
    def _visible(cls, v: str) -> str:
        if not v.isprintable() or not v.strip():
            raise ValueError("Name muss sichtbare Zeichen enthalten")
        return v


def _user_out(u: User, now: int) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "kind": u.kind, "totp_enabled": u.totp_enabled,
            "disabled": u.disabled, "locked": UserStore.is_locked(u, now), "oidc_bound": u.oidc_bound,
            "created_at": _ts(u.created_at), "last_login": None if u.last_login is None else _ts(u.last_login)}


def _ts(t: int) -> str:
    return iso(datetime.fromtimestamp(t, timezone.utc))


def _need(users: UserStore, user_id: int) -> User:
    u = users.get(user_id)
    if u is None:
        raise HTTPException(404, "Benutzer nicht gefunden")
    return u


def _local(u: User) -> None:
    if u.kind != "local":
        raise HTTPException(422, "Nur für lokale Konten möglich")


@router.get("/users", dependencies=[admin])
def list_users(request: Request):
    now = int(time.time())
    with db.request_conn(request) as conn:
        return [_user_out(u, now) for u in UserStore(conn, request.app.state.box).list()]


@router.post("/users", status_code=201)
def create_user(request: Request, body: NewUser, p: Principal = Depends(require_scope("admin"))):
    now = int(time.time())
    with db.request_conn(request) as conn:
        try:
            u = UserStore(conn, request.app.state.box).create(body.email, body.name, body.role, body.kind, body.password, now)
        except Conflict as e:
            raise HTTPException(409, str(e)) from None
        except ValueError as e:  # Regeltexte nennen nie das Passwort
            raise HTTPException(422, str(e)) from None
        audit.record(conn, p.actor, "user.created", target=u.email, ip=client_ip(request),
                     details={"role": u.role, "kind": u.kind}, now=now)
        return _user_out(u, now)


@router.patch("/users/{user_id}")
def update_user(request: Request, user_id: UserId, body: UserPatch, p: Principal = Depends(require_scope("admin"))):
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if not changes:
        raise HTTPException(422, "Mindestens ein Feld angeben")
    now = int(time.time())
    with db.request_conn(request) as conn:
        users = UserStore(conn, request.app.state.box)
        with users._tx(immediate=True):  # Änderung, Sessions und Audit gemeinsam oder gar nicht
            try:
                u = users.update(user_id, **changes)
            except Conflict as e:
                raise HTTPException(409, str(e)) from None
            except ValueError as e:
                raise HTTPException(422, str(e)) from None
            if u is None:
                raise HTTPException(404, "Benutzer nicht gefunden")
            if changes.get("disabled"):
                SessionStore(conn, request.app.state.settings).delete_for_user(user_id)
            audit.record(conn, p.actor, "user.updated", target=u.email, ip=client_ip(request), details=changes, now=now)
        return _user_out(u, now)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(request: Request, user_id: UserId, p: Principal = Depends(require_scope("admin"))):
    with db.request_conn(request) as conn:
        users = UserStore(conn, request.app.state.box)
        u = _need(users, user_id)
        try:
            users.delete(user_id)
        except Conflict as e:
            raise HTTPException(409, str(e)) from None
        audit.record(conn, p.actor, "user.deleted", target=u.email, ip=client_ip(request))
    return Response(status_code=204)


@router.post("/users/{user_id}/password", status_code=204)
def reset_password(request: Request, user_id: UserId, body: PasswordBody, p: Principal = Depends(require_scope("admin"))):
    with db.request_conn(request) as conn:
        users = UserStore(conn, request.app.state.box)
        u = _need(users, user_id)
        _local(u)
        with users._tx(immediate=True):  # Passwort, Sessions und Audit gemeinsam oder gar nicht
            try:
                users.set_password(user_id, body.password)
            except ValueError as e:
                raise HTTPException(422, str(e)) from None
            SessionStore(conn, request.app.state.settings).delete_for_user(user_id)
            audit.record(conn, p.actor, "password.reset", target=u.email, ip=client_ip(request))
    return Response(status_code=204)


@router.post("/users/{user_id}/reset-2fa", status_code=204)
def reset_2fa(request: Request, user_id: UserId, p: Principal = Depends(require_scope("admin"))):
    with db.request_conn(request) as conn:
        users = UserStore(conn, request.app.state.box)
        u = _need(users, user_id)
        _local(u)
        with users._tx(immediate=True):  # sonst könnte eine übrige Session ohne Code einen neuen Faktor einrichten
            users.reset_totp(user_id)
            SessionStore(conn, request.app.state.settings).delete_for_user(user_id)
            audit.record(conn, p.actor, "totp.reset", target=u.email, ip=client_ip(request))
    return Response(status_code=204)


@router.post("/users/{user_id}/unlock", status_code=204)
def unlock_user(request: Request, user_id: UserId, p: Principal = Depends(require_scope("admin"))):
    with db.request_conn(request) as conn:
        users = UserStore(conn, request.app.state.box)
        u = _need(users, user_id)
        users.unlock(user_id)
        audit.record(conn, p.actor, "user.unlocked", target=u.email, ip=client_ip(request))
    return Response(status_code=204)


def _token_out(t) -> dict:  # bewusst ohne hash
    return {"id": t.id, "name": t.name, "scope": t.scope, "created_at": t.created_at, "expires_at": t.expires_at}


@router.get("/tokens", dependencies=[admin])
def list_tokens(request: Request):
    return [_token_out(t) for t in request.app.state.tokens.list()]


@router.post("/tokens", status_code=201)
def create_token(request: Request, body: NewToken, p: Principal = Depends(require_scope("admin"))):
    exp = utcnow() + timedelta(days=body.expires_days) if body.expires_days else None
    tok, plain = request.app.state.tokens.create(body.name, body.scope, expires_at=exp)
    with db.request_conn(request) as conn:
        audit.record(conn, p.actor, "token.created", target=tok.id, ip=client_ip(request), details={"scope": tok.scope})
    return {**_token_out(tok), "token": plain}


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(request: Request, token_id: Annotated[str, Path(max_length=64)], p: Principal = Depends(require_scope("admin"))):
    if not request.app.state.tokens.revoke(token_id):
        raise HTTPException(404, "Token nicht gefunden")
    with db.request_conn(request) as conn:
        audit.record(conn, p.actor, "token.revoked", target=token_id, ip=client_ip(request))
    return Response(status_code=204)


@router.get("/audit", dependencies=[admin])
def get_audit(request: Request, limit: Annotated[int, Query(ge=1, le=1000)] = 100,
              before: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None):
    with db.request_conn(request) as conn:
        rows = audit.entries(conn, limit + 1, before)  # eine Zeile mehr zeigt, ob es weitergeht
    more = len(rows) > limit
    rows = rows[:limit]
    return {"entries": rows, "next_before": rows[-1]["id"] if more else None}
