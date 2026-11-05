"""Anmelde-API des Managers: Ersteinrichtung, Login mit Pflicht-2FA, TOTP, Logout, eigenes Konto.

Nie Passwörter, Codes, Secrets, Cookies oder CSRF-Werte in Logs, Audit-Details oder Fehlermeldungen.
"""
import hashlib
import hmac
import ipaddress
import re
import sqlite3
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, StrictStr, StringConstraints

from ..manager import db
from . import audit, passwords
from .deps import Principal, client_ip, require_scope, require_stage
from .sessions import COOKIE, Session, SessionStore
from .users import Conflict, User, UserStore, normalize_email

router = APIRouter(prefix="/api/v1")
Str = Annotated[StrictStr, StringConstraints(max_length=1024)]  # Grenze schützt Argon2 vor Riesen-Bodies
_FAILED = "Anmeldung fehlgeschlagen"
_TOTP_CODE = re.compile(r"[0-9]{6}")
_COOKIE_ATTRS = {"path": "/", "secure": True, "httponly": True, "samesite": "strict"}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetupBody(_Body):
    setup_token: Str
    email: Str
    name: Str
    password: Str


class LoginBody(_Body):
    email: Str
    password: Str


class CodeBody(_Body):
    code: Str


class TotpSetupBody(_Body):
    current_code: Str | None = None


class PasswordBody(_Body):
    current_password: Str
    new_password: Str


def throttle_key(request: Request) -> str:
    """IP für die Drosselung; IPv6 auf das /64-Präfix gekürzt (P3-R2), sonst wechselt ein Angreifer einfach die Adresse."""
    ip = client_ip(request) or ""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if a.version == 4:
        return ip
    if a.ipv4_mapped:
        return str(a.ipv4_mapped)
    return f"{ipaddress.IPv6Address(int(a) >> 64 << 64)}/64"


def _throttle(request: Request, now: int) -> None:
    wait = request.app.state.throttle.retry_after(throttle_key(request), now)
    if wait is not None:
        raise HTTPException(429, "Zu viele Fehlversuche, bitte später erneut versuchen", headers={"Retry-After": str(wait)})


def _set_cookie(response: Response, plain: str) -> None:
    response.set_cookie(COOKIE, plain, **_COOKIE_ATTRS)


def _actor(user: User) -> str:
    return f"user:{user.email}"


def _is_totp(code: str | None) -> bool:
    """Nur ein TOTP-Code, damit ein Wiederherstellungscode hier nicht verbraucht wird."""
    return code is not None and _TOTP_CODE.fullmatch("".join(code.split())) is not None


def _factor_failed(request: Request, conn: sqlite3.Connection, user: User, session: Session, now: int, action: str,
                   already_locked: bool = False):
    """Fehlversuch mit zweitem Faktor oder Passwort bei bestehender Session: zählt zur Sperre (D8), bei Sperre Session weg."""
    ip = client_ip(request)
    request.app.state.throttle.fail(throttle_key(request), now)
    locked = not already_locked and UserStore(conn, request.app.state.box).register_failure(user.id, now)
    audit.record(conn, _actor(user), action, target=user.email, ip=ip, now=now)
    if locked:
        audit.record(conn, _actor(user), "login.locked", target=user.email, ip=ip, now=now)
    if locked or already_locked:
        SessionStore(conn, request.app.state.settings).delete(session.id_hash)
    raise HTTPException(401, _FAILED)


def _check_factor(request, conn, user, session, now, action, ok) -> None:
    """Gesperrte Konten prüfen keinen Faktor mehr; sonst ginge das Raten während der Sperre weiter."""
    if UserStore.is_locked(user, now):
        _factor_failed(request, conn, user, session, now, action, already_locked=True)
    if not ok():
        _factor_failed(request, conn, user, session, now, action)


def _email_or_none(raw: str) -> str | None:
    """Nur eine gültige E-Mail ins Audit; sonst könnte dort ein ins falsche Feld getipptes Passwort landen."""
    try:
        return normalize_email(raw)
    except ValueError:
        return None


def _no_reenrol(session: Session, user: User) -> None:
    """P3-R3: eine alte Enroll-Session darf ein aktives TOTP nicht ohne zweiten Faktor ersetzen."""
    if session.stage == "enroll" and user.totp_enabled:
        raise HTTPException(401, _FAILED)


def _browser(principal: Principal) -> None:
    if principal.session is None:
        raise HTTPException(400, "Nur mit Anmeldung im Browser möglich")


def _local(user: User) -> None:
    if user.kind != "local":
        raise HTTPException(400, "Nur für lokale Konten möglich")


@router.get("/setup")
def setup_status(request: Request):
    if request.app.state.setup_hash is None:
        return {"required": False}
    with db.request_conn(request) as conn:
        return {"required": UserStore(conn, request.app.state.box).count() == 0}


@router.post("/setup", status_code=201)
def setup(request: Request, response: Response, body: SetupBody):
    now = int(time.time())
    expected = request.app.state.setup_hash
    if expected is None:
        raise HTTPException(409, "Ersteinrichtung ist bereits abgeschlossen")
    _throttle(request, now)
    if not hmac.compare_digest(hashlib.sha256(body.setup_token.encode("utf-8", "surrogatepass")).digest(), expected):
        request.app.state.throttle.fail(throttle_key(request), now)
        raise HTTPException(403, "Setup-Token ist ungültig")
    ip = client_ip(request)
    with db.request_conn(request) as conn:
        users = UserStore(conn, request.app.state.box)
        try:
            with users._tx(immediate=True):  # Zählen und Anlegen atomar: gleichzeitige Setups ergeben genau einen Admin
                if users.count():
                    raise HTTPException(409, "Ersteinrichtung ist bereits abgeschlossen")
                user = users.create(body.email, body.name, "admin", "local", body.password, now)
                _, plain = SessionStore(conn, request.app.state.settings).create(user.id, "enroll", now, ip)
                audit.record(conn, "setup", "setup.admin_created", target=user.email, ip=ip, now=now)
        except (ValueError, Conflict) as e:  # Texte nennen nie das Passwort
            raise HTTPException(422, str(e)) from None
    request.app.state.setup_hash = None
    _set_cookie(response, plain)
    return {"stage": "enroll"}


@router.post("/auth/login")
def login(request: Request, response: Response, body: LoginBody):
    now = int(time.time())
    _throttle(request, now)
    ip = client_ip(request)
    with db.request_conn(request) as conn:
        users = UserStore(conn, request.app.state.box)
        user = users.by_email(body.email)
        if user is None or user.kind != "local" or user.disabled or users.is_locked(user, now):
            passwords.dummy_verify(body.password)  # gleiche Antwortzeit wie eine echte Prüfung (D8)
            ok = locked = False
        else:
            ok = users.check_password(user.id, body.password)
            locked = not ok and users.register_failure(user.id, now)
        if not ok:
            request.app.state.throttle.fail(throttle_key(request), now)
            actor = _actor(user) if user else "anonym"
            target = user.email if user else _email_or_none(body.email)
            audit.record(conn, actor, "login.failure", target=target, ip=ip, now=now)
            if locked:
                audit.record(conn, actor, "login.locked", target=target, ip=ip, now=now)
            raise HTTPException(401, _FAILED)
        stage = "mfa" if user.totp_enabled else "enroll"  # Zähler erst nach dem zweiten Faktor zurücksetzen
        _, plain = SessionStore(conn, request.app.state.settings).create(user.id, stage, now, ip)
    _set_cookie(response, plain)
    return {"stage": stage}


@router.post("/auth/login/mfa")
def login_mfa(request: Request, response: Response, body: CodeBody, auth=Depends(require_stage("mfa"))):
    session, user = auth
    now = int(time.time())
    _throttle(request, now)
    ip = client_ip(request)
    with db.request_conn(request) as conn:
        users = UserStore(conn, request.app.state.box)
        method = None

        def ok():
            nonlocal method
            method = users.verify_second_factor(user.id, body.code, now)
            return method is not None
        _check_factor(request, conn, user, session, now, "mfa.failure", ok)
        _, plain = SessionStore(conn, request.app.state.settings).rotate(session, "full", now, ip)
        users.register_success(user.id, now)
        audit.record(conn, _actor(user), "login.success", target=user.email, ip=ip, details={"method": method}, now=now)
        if method == "recovery":
            audit.record(conn, _actor(user), "recovery_code.used", target=user.email, ip=ip, now=now)
    _set_cookie(response, plain)
    return {"stage": "full"}


@router.post("/auth/totp/setup")
def totp_setup(request: Request, body: TotpSetupBody, auth=Depends(require_stage("enroll", "full"))):
    session, user = auth
    _local(user)
    _no_reenrol(session, user)
    with db.request_conn(request) as conn:
        users = UserStore(conn, request.app.state.box)
        if session.stage == "full" and user.totp_enabled:
            now = int(time.time())
            _throttle(request, now)
            _check_factor(request, conn, user, session, now, "mfa.failure",
                          lambda: _is_totp(body.current_code)
                          and users.verify_second_factor(user.id, body.current_code, now) == "totp")
        secret, uri = users.begin_totp(user.id)
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/auth/totp/confirm")
def totp_confirm(request: Request, response: Response, body: CodeBody, auth=Depends(require_stage("enroll", "full"))):
    session, user = auth
    _no_reenrol(session, user)  # user kommt frisch aus der DB (deps), gilt also auch nach Aktivierung durch eine andere Session
    now = int(time.time())
    _throttle(request, now)
    ip = client_ip(request)
    with db.request_conn(request) as conn:
        users = UserStore(conn, request.app.state.box)
        codes = users.confirm_totp(user.id, body.code, now)
        if codes is None:  # Tippfehler beim Einrichten zählt nicht zur Kontosperre, nur zur IP-Drosselung
            request.app.state.throttle.fail(throttle_key(request), now)
            raise HTTPException(401, _FAILED)
        audit.record(conn, _actor(user), "totp.enabled", target=user.email, ip=ip, now=now)
        if session.stage == "enroll":
            sessions = SessionStore(conn, request.app.state.settings)
            new, plain = sessions.rotate(session, "full", now, ip)
            sessions.delete_for_user(user.id, keep=new.id_hash, pre_auth_only=True)  # andere mfa/enroll-Sessions (P3-R3)
            users.register_success(user.id, now)
            audit.record(conn, _actor(user), "login.success", target=user.email, ip=ip, details={"method": "enroll"}, now=now)
            _set_cookie(response, plain)
    return {"recovery_codes": codes}


@router.get("/auth/session")
def session_info(auth=Depends(require_stage("mfa", "enroll", "full"))):
    session, user = auth
    return {"stage": session.stage, "csrf_token": session.csrf,
            "user": {"id": user.id, "email": user.email, "name": user.name, "role": user.role, "kind": user.kind,
                     "totp_enabled": user.totp_enabled}}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, auth=Depends(require_stage("mfa", "enroll", "full"))):
    session, user = auth
    with db.request_conn(request) as conn:
        SessionStore(conn, request.app.state.settings).delete(session.id_hash)
        audit.record(conn, _actor(user), "logout", target=user.email, ip=client_ip(request))
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, **_COOKIE_ATTRS)
    return response


def _account(request: Request, principal: Principal, conn: sqlite3.Connection) -> User:
    user = UserStore(conn, request.app.state.box).get(principal.user_id)
    if user is None:
        raise HTTPException(401, "Nicht angemeldet")
    _local(user)
    return user


@router.post("/account/password", status_code=204)
def change_password(request: Request, body: PasswordBody, principal: Principal = Depends(require_scope("read"))):
    _browser(principal)
    now = int(time.time())
    _throttle(request, now)
    with db.request_conn(request) as conn:
        user = _account(request, principal, conn)
        users = UserStore(conn, request.app.state.box)
        _check_factor(request, conn, user, principal.session, now, "password.failure",
                      lambda: users.check_password(user.id, body.current_password))
        with users._tx(immediate=True):  # Passwort, Sessions und Audit gemeinsam oder gar nicht
            try:
                users.set_password(user.id, body.new_password)
            except ValueError as e:  # Regeltexte nennen nie das Passwort
                raise HTTPException(422, str(e)) from None
            SessionStore(conn, request.app.state.settings).delete_for_user(user.id, keep=principal.session.id_hash)
            audit.record(conn, _actor(user), "password.changed", target=user.email, ip=client_ip(request), now=now)
    return Response(status_code=204)


@router.post("/account/recovery-codes")
def regenerate_recovery_codes(request: Request, body: CodeBody, principal: Principal = Depends(require_scope("read"))):
    _browser(principal)
    now = int(time.time())
    _throttle(request, now)
    with db.request_conn(request) as conn:
        user = _account(request, principal, conn)
        users = UserStore(conn, request.app.state.box)
        _check_factor(request, conn, user, principal.session, now, "mfa.failure",
                      lambda: _is_totp(body.code) and users.verify_second_factor(user.id, body.code, now) == "totp")
        codes = users.regenerate_recovery_codes(user.id)
        audit.record(conn, _actor(user), "recovery_codes.regenerated", target=user.email, ip=client_ip(request), now=now)
    return {"recovery_codes": codes}
