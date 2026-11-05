"""OIDC-Anmeldung des Managers (D3, D7): Authorization-Code-Flow mit PKCE, state und nonce.

Konten werden nie angelegt, nur vorher angelegte Konten mit kind="oidc" verknüpft.
Nie Client-Secret, code, id_token oder access_token in Logs, Antworten, Audit oder Fehlermeldungen.
"""
import base64
import hashlib
import hmac
import http.client
import json
import logging
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, StrictStr, StringConstraints, field_validator

from ..manager import db
from ..manager.poller import _OPENER  # kein Proxy aus der Umgebung, keine Weiterleitungen (wie P2-R6)
from ..secretbox import SecretBox, SecretError
from . import audit
from .api import _set_cookie, throttle_key
from .deps import Principal, client_ip, require_scope
from .sessions import SessionStore
from .users import Conflict, User, UserStore

log = logging.getLogger("moni.oidc")
router = APIRouter(prefix="/api/v1/auth/oidc")
admin = Depends(require_scope("admin"))

FLOW_COOKIE = "moni_oidc"
FLOW_SECONDS = 600
_FLOW_ATTRS = {"path": "/api/v1/auth/oidc", "secure": True, "httponly": True, "samesite": "lax"}
_FAIL_URL = "/login?fehler=oidc"
_KEY = "oidc"
_MAX_BYTES = 1024 * 1024
_TIMEOUT = 10
_CACHE_SECONDS = 3600
_LEEWAY = 60
_ASYMMETRIC = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "EdDSA"}
_ENDPOINTS = ("authorization_endpoint", "token_endpoint", "jwks_uri")
_UNREACHABLE = "OIDC-Anbieter nicht erreichbar oder Konfiguration passt nicht"


class OidcError(Exception):
    """Fester deutscher Text ohne Token, Secret oder Antwortinhalt."""


class _UnknownKid(OidcError):
    pass


# --- HTTP (einzige Netzwerkstelle) -------------------------------------------

def _https(url) -> bool:
    if not isinstance(url, str):
        return False
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return p.scheme == "https" and bool(p.hostname)


def _http(method: str, url: str, data: dict | None = None, headers: dict | None = None) -> dict:
    if not _https(url):
        raise OidcError("OIDC-Adressen müssen mit https:// beginnen")
    hdrs = {"Accept": "application/json", **(headers or {})}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
    raw = None
    deadline = time.monotonic() + _TIMEOUT  # Gesamtfrist: der urllib-Timeout gilt nur je Socket-Operation
    try:
        with _OPENER.open(req, timeout=_TIMEOUT) as resp:
            buf = bytearray()
            while len(buf) <= _MAX_BYTES and (chunk := resp.read1(65536)):
                buf += chunk
                if time.monotonic() > deadline:
                    raise TimeoutError
            raw = buf
    except urllib.error.HTTPError as e:  # Nicht-2xx und jede Weiterleitung (_NoRedirect folgt nie)
        e.close()
    except (OSError, http.client.HTTPException, ValueError):  # URLError, Timeout, Abbruch
        pass
    data_out = None
    if raw is not None and len(raw) <= _MAX_BYTES:
        try:
            data_out = json.loads(raw)
        except (ValueError, RecursionError):
            pass
    if raw is None:  # außerhalb von except: kein __context__ mit Anfrage- oder Antwortdetails
        raise OidcError("OIDC-Anbieter nicht erreichbar")
    if not isinstance(data_out, dict):
        raise OidcError("Ungültige Antwort vom OIDC-Anbieter")
    return data_out


def _basic(client_id: str, client_secret: str) -> str:
    """client_secret_basic nach RFC 6749 §2.3.1: beide Teile form-kodiert vor Base64."""
    q = urllib.parse.quote_plus
    return "Basic " + base64.b64encode(f"{q(client_id, safe='')}:{q(client_secret, safe='')}".encode()).decode()


_cache: dict[str, tuple[float, dict]] = {}  # URL -> (abgerufen, Antwort); Discovery und JWKS
_cache_lock = threading.Lock()


def reset_caches() -> None:
    with _cache_lock:
        _cache.clear()


def _fetch(url: str, now: float, fresh: bool = False) -> dict:
    with _cache_lock:
        hit = _cache.get(url)
    if hit and not fresh and 0 <= now - hit[0] < _CACHE_SECONDS:
        return hit[1]
    data = _http("GET", url)
    with _cache_lock:
        _cache[url] = (now, data)
    return data


def _discover(issuer: str, now: float, fresh: bool = False) -> dict:
    d = _fetch(issuer.rstrip("/") + "/.well-known/openid-configuration", now, fresh)
    if (d.get("issuer") != issuer or not all(_https(d.get(k)) for k in _ENDPOINTS)
            or not isinstance(d.get("id_token_signing_alg_values_supported", []), list)):
        raise OidcError(_UNREACHABLE)
    return d


# --- Konfiguration in meta (D7) -----------------------------------------------

def _check_base(url: str) -> str:
    if not _https(url) or "?" in url or "#" in url or "@" in url or not url.isprintable() or " " in url:
        raise ValueError("Adresse muss mit https:// beginnen und darf weder Query noch Fragment enthalten")
    return url


def _stored(conn: sqlite3.Connection) -> dict | None:
    r = conn.execute("SELECT value FROM meta WHERE key=?", (_KEY,)).fetchone()
    return None if r is None else json.loads(r[0])


def _put(conn: sqlite3.Connection, cfg: dict) -> None:
    own = not conn.in_transaction  # in der CLI-Transaktion von `secret reencrypt` nicht selbst committen
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (_KEY, json.dumps(cfg)))
    if own:
        conn.commit()


def load_config(conn: sqlite3.Connection, box: SecretBox) -> dict | None:
    s = _stored(conn)
    if s is None:
        return None
    enc = s.pop("client_secret_enc")
    return {**s, "client_secret": box.decrypt(enc)}


def save_config(conn: sqlite3.Connection, box: SecretBox, *, issuer: str, client_id: str, client_secret: str | None,
                public_url: str, label: str | None) -> None:
    """client_secret=None behält das gespeicherte Secret, nur bei gleichem Issuer und gleicher Client-ID."""
    _check_base(issuer)
    _check_base(public_url)
    old = _stored(conn)
    # Secret nur behalten, wenn es an denselben Anbieter geht; sonst könnte ein neuer Issuer es abgreifen
    if client_secret is None and (old is None or (old["issuer"], old["client_id"]) != (issuer, client_id)):
        raise ValueError("Client-Secret fehlt")
    enc = old["client_secret_enc"] if client_secret is None else box.encrypt(client_secret)
    _put(conn, {"issuer": issuer, "client_id": client_id, "public_url": public_url.rstrip("/"), "label": label,
                "client_secret_enc": enc})


def delete_config(conn: sqlite3.Connection) -> None:
    own = not conn.in_transaction
    conn.execute("DELETE FROM meta WHERE key=?", (_KEY,))
    if own:
        conn.commit()


def reencrypt(conn: sqlite3.Connection, box: SecretBox) -> int:
    s = _stored(conn)
    if s is None:
        return 0
    _put(conn, {**s, "client_secret_enc": box.rotate(s["client_secret_enc"])})
    return 1


# --- laufende Anmeldungen -----------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Flows:
    """state -> (nonce, PKCE-Verifier, Beginn); nur im Speicher, jeder state ist einmal verwendbar."""
    MAX = 1000

    def __init__(self):
        self._d: dict[str, tuple[str, str, float]] = {}
        self._lock = threading.Lock()

    def add(self, now: float) -> tuple[str, str, str]:
        state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self._lock:
            for s in [s for s, e in self._d.items() if now - e[2] >= FLOW_SECONDS]:
                del self._d[s]
            while len(self._d) >= self.MAX:  # älteste zuerst (Einfügereihenfolge)
                del self._d[next(iter(self._d))]
            self._d[state] = (nonce, verifier, now)
        return state, nonce, _b64(hashlib.sha256(verifier.encode()).digest())

    def pop(self, state: str, now: float) -> tuple[str, str] | None:
        with self._lock:
            e = self._d.pop(state, None)
        if e is None or now - e[2] >= FLOW_SECONDS:
            return None
        return e[0], e[1]


# --- ID-Token -----------------------------------------------------------------

def validate_id_token(id_token: str, *, issuer: str, client_id: str, nonce: str, jwks: dict, algs: list[str],
                      now: float) -> dict:
    allowed = [a for a in algs if isinstance(a, str) and a in _ASYMMETRIC]
    claims = None
    unknown_kid = False
    try:
        header = jwt.get_unverified_header(id_token)
        alg, kid = header.get("alg"), header.get("kid")
        if alg in allowed:  # vor der Schlüsselsuche: "none" und HS* kommen nie bis zum Schlüssel
            keys = [k for k in jwt.PyJWKSet.from_dict(jwks).keys if k.public_key_use != "enc"]
            match = [k for k in keys if k.key_id == kid] if kid is not None else keys if len(keys) == 1 else []
            if match:
                claims = jwt.decode(id_token, match[0], algorithms=allowed, audience=client_id, issuer=issuer,
                                    leeway=_LEEWAY, options={"require": ["exp", "iat", "iss", "aud", "sub"]})
            else:
                unknown_kid = True
    except (jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError):
        claims = None
    if unknown_kid:  # außerhalb von except: kein __context__ mit Token-Inhalt
        raise _UnknownKid("ID-Token ungültig")
    if claims is None or not _claims_ok(claims, client_id, nonce, now):
        raise OidcError("ID-Token ungültig")
    return claims


def _claims_ok(c: dict, client_id: str, nonce: str, now: float) -> bool:
    sub, got, aud = c.get("sub"), c.get("nonce"), c.get("aud")
    if not isinstance(sub, str) or not sub or len(sub) > 255 or not isinstance(got, str):
        return False
    if not hmac.compare_digest(got.encode("utf-8", "surrogatepass"), nonce.encode()):
        return False
    if (isinstance(aud, list) and len(aud) > 1 or "azp" in c) and c.get("azp") != client_id:
        return False
    # PyJWT prüft gegen die Uhr; hier zusätzlich gegen `now` des Aufrufers
    return int(c["exp"]) > now - _LEEWAY and int(c["iat"]) <= now + _LEEWAY


# --- Routen -------------------------------------------------------------------

Url = Annotated[StrictStr, StringConstraints(max_length=1024)]


class ConfigBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issuer: Url
    client_id: Annotated[StrictStr, StringConstraints(min_length=1, max_length=255)]
    client_secret: Annotated[StrictStr, StringConstraints(min_length=1, max_length=1024)] | None = None
    public_url: Url
    label: Annotated[StrictStr, StringConstraints(min_length=1, max_length=100)] | None = None

    @field_validator("issuer", "public_url")
    @classmethod
    def _base(cls, v: str) -> str:
        return _check_base(v)

    @field_validator("client_id", "label")
    @classmethod
    def _printable(cls, v: str | None) -> str | None:
        if v is not None and not v.isprintable():
            raise ValueError("Steuerzeichen sind nicht erlaubt")
        return v


def _config_out(s: dict) -> dict:
    return {"issuer": s["issuer"], "client_id": s["client_id"], "public_url": s["public_url"], "label": s["label"],
            "client_secret_set": bool(s.get("client_secret_enc"))}


def _redirect_uri(cfg: dict) -> str:
    return cfg["public_url"] + "/api/v1/auth/oidc/callback"


@router.get("")
def status(request: Request):
    with db.request_conn(request) as conn:
        s = _stored(conn)
    return {"enabled": s is not None, "label": s["label"] if s else None}


@router.get("/config")
def get_config(request: Request, principal: Principal = admin):
    with db.request_conn(request) as conn:
        s = _stored(conn)
    if s is None:
        raise HTTPException(404, "OIDC ist nicht eingerichtet")
    return _config_out(s)


@router.put("/config")
def put_config(request: Request, body: ConfigBody, principal: Principal = admin):
    try:
        _discover(body.issuer, time.time(), fresh=True)
    except OidcError:
        raise HTTPException(422, _UNREACHABLE) from None
    with db.request_conn(request) as conn:
        try:
            save_config(conn, request.app.state.box, **body.model_dump())
        except ValueError as e:  # Texte nennen nie das Secret
            raise HTTPException(422, str(e)) from None
        audit.record(conn, principal.actor, "oidc.config_changed", target=body.issuer, ip=client_ip(request),
                     details={"client_id": body.client_id, "public_url": body.public_url, "label": body.label,
                              "client_secret_changed": body.client_secret is not None})
        return _config_out(_stored(conn))


@router.delete("/config", status_code=204)
def remove_config(request: Request, principal: Principal = admin):
    with db.request_conn(request) as conn:
        delete_config(conn)
        audit.record(conn, principal.actor, "oidc.config_changed", ip=client_ip(request), details={"deleted": True})
    return Response(status_code=204)


@router.get("/start")
def start(request: Request):
    now = int(time.time())
    with db.request_conn(request) as conn:
        cfg = _stored(conn)
    if cfg is None:
        raise HTTPException(404, "OIDC ist nicht eingerichtet")
    try:
        ep = _discover(cfg["issuer"], now)["authorization_endpoint"]
    except OidcError:
        log.warning("OIDC-Anbieter nicht erreichbar")
        return RedirectResponse(_FAIL_URL, 303)
    state, nonce, challenge = request.app.state.oidc_flows.add(now)
    query = urllib.parse.urlencode({"response_type": "code", "client_id": cfg["client_id"], "redirect_uri": _redirect_uri(cfg),
                                    "scope": "openid email profile", "state": state, "nonce": nonce,
                                    "code_challenge": challenge, "code_challenge_method": "S256"})
    response = RedirectResponse(ep + ("&" if "?" in ep else "?") + query, 302)
    response.set_cookie(FLOW_COOKIE, state, max_age=FLOW_SECONDS, **_FLOW_ATTRS)  # Lax: kommt beim Rücksprung mit
    return response


def _verify(request: Request, now: int) -> tuple[dict | None, str | None]:
    """Prüft den Rücksprung bis zum gültigen ID-Token: (claims, None) oder (None, Fehlercode)."""
    app, q = request.app, request.query_params
    if app.state.throttle.retry_after(throttle_key(request), now) is not None:
        return None, "drosselung"
    state, cookie = q.get("state"), request.cookies.get(FLOW_COOKIE)
    if not state or not cookie or not hmac.compare_digest(state.encode("utf-8", "surrogatepass"),
                                                          cookie.encode("utf-8", "surrogatepass")):
        return None, "state"
    flow = app.state.oidc_flows.pop(state, now)
    if flow is None:
        return None, "state"
    if "error" in q:
        return None, "anbieter"
    nonce, verifier = flow
    with db.request_conn(request) as conn:
        try:
            cfg = load_config(conn, app.state.box)
        except SecretError:
            cfg = None
    if cfg is None:
        return None, "config"
    code = q.get("code")
    try:
        if not code:
            raise OidcError("code fehlt")
        d = _discover(cfg["issuer"], now)
        tokens = _http("POST", d["token_endpoint"],
                       data={"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri(cfg),
                             "code_verifier": verifier},
                       headers={"Authorization": _basic(cfg["client_id"], cfg["client_secret"])})
    except OidcError:
        return None, "token"
    id_token = tokens.get("id_token")
    if not isinstance(id_token, str):
        return None, "token"
    args = {"issuer": cfg["issuer"], "client_id": cfg["client_id"], "nonce": nonce,
            "algs": d.get("id_token_signing_alg_values_supported", ["RS256"]), "now": now}
    try:
        try:
            return validate_id_token(id_token, jwks=_fetch(d["jwks_uri"], now), **args), None
        except _UnknownKid:  # Schlüsselwechsel beim Anbieter: JWKS einmal frisch laden
            return validate_id_token(id_token, jwks=_fetch(d["jwks_uri"], now, fresh=True), **args), None
    except OidcError:
        return None, "id_token"


def _account(users: UserStore, claims: dict) -> tuple[User | None, str | None, bool]:
    """(Benutzer, None, neu verknüpft) oder (None, Fehlercode, False). Nie automatische Kontoanlage (D7)."""
    user = users.by_oidc(claims["iss"], claims["sub"])
    bind = user is None
    if bind:
        if claims.get("email_verified") is not True:
            return None, "email_unverified", False
        user = users.by_email(claims.get("email"))
        if user is None or user.oidc_bound:
            return None, "unknown_user", False
    if user.kind != "oidc":
        return None, "unknown_user", False
    if user.disabled:
        return None, "disabled", False
    if bind:
        users.bind_oidc(user.id, claims["iss"], claims["sub"])
    return user, None, bind


@router.get("/callback")
def callback(request: Request):
    now = int(time.time())
    ip = client_ip(request)
    claims, grund = _verify(request, now)
    plain = None
    with db.request_conn(request) as conn:
        if claims is not None:
            users = UserStore(conn, request.app.state.box)
            try:
                with users._tx(immediate=True):  # Suche und Verknüpfung atomar
                    user, grund, bound = _account(users, claims)
                    if user is not None:
                        _, plain = SessionStore(conn, request.app.state.settings).create(user.id, "full", now, ip)
                        users.register_success(user.id, now)
                        audit.record(conn, f"user:{user.email}", "oidc.login", target=user.email, ip=ip,
                                     details={"bound": True} if bound else None, now=now)
            except Conflict:  # dieselbe Identität gleichzeitig an ein anderes Konto gebunden
                grund = "unknown_user"
        if plain is None:
            if grund != "drosselung":
                request.app.state.throttle.fail(throttle_key(request), now)
            audit.record(conn, "anonym", "oidc.failure", ip=ip, details={"grund": grund}, now=now)
    response = RedirectResponse("/" if plain else _FAIL_URL, 303)
    if plain:
        _set_cookie(response, plain)
    response.delete_cookie(FLOW_COOKIE, **_FLOW_ATTRS)
    return response
