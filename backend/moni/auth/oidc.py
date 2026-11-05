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
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
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
_ID = re.compile(r"[a-z0-9-]{1,32}")
_MAX_PROVIDERS = 10
# Microsofts eigener Mandant für private Konten: dort kann kein fremder Mandant Adressen setzen (P3-R9)
MS_CONSUMER_ISSUER = "https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0"
_MS_CONSUMERS_ALIAS = "https://login.microsoftonline.com/consumers/v2.0"
_TENANT_HINT = ("Für Microsoft die mandantenspezifische Issuer-URL verwenden: "
                "https://login.microsoftonline.com/<Mandanten-ID>/v2.0")
_CONSUMER_HINT = f"Für private Microsoft-Konten als Issuer {MS_CONSUMER_ISSUER} eintragen"


class OidcError(Exception):
    """Fester deutscher Text ohne Token, Secret oder Antwortinhalt."""


class _UnknownKid(OidcError):
    pass


class _Hint(OidcError):
    """Konfigurationshinweis für den Admin."""


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
    # /common und /organizations: mehrmandantenfähig, Ursache der „nOAuth“-Kontoübernahmen
    if isinstance(d.get("issuer"), str) and "{tenantid}" in d["issuer"].lower():
        raise _Hint(_TENANT_HINT)
    if (d.get("issuer") != issuer or not all(_https(d.get(k)) for k in _ENDPOINTS)
            or not isinstance(d.get("id_token_signing_alg_values_supported", []), list)):
        raise OidcError(_UNREACHABLE)
    return d


# --- Konfiguration in meta (D7) -----------------------------------------------
# {"public_url": str | None, "providers": [{"id", "issuer", "client_id", "client_secret_enc", "label"}]}

def _check_base(url: str) -> str:
    if not _https(url) or "?" in url or "#" in url or "@" in url or not url.isprintable() or " " in url:
        raise ValueError("Adresse muss mit https:// beginnen und darf weder Query noch Fragment enthalten")
    return url


def _check_public_url(url: str) -> str:
    """Nur https://host[:port]: ein Pfad würde den Cookie-Pfad von moni_oidc und die festen Rücksprungziele brechen."""
    if urllib.parse.urlsplit(_check_base(url)).path not in ("", "/"):
        raise ValueError("public_url darf keinen Pfad enthalten")
    return url.rstrip("/")


def _stored(conn: sqlite3.Connection) -> dict:
    r = conn.execute("SELECT value FROM meta WHERE key=?", (_KEY,)).fetchone()
    return {"public_url": None, "providers": []} if r is None else json.loads(r[0])


@contextmanager
def _tx(conn: sqlite3.Connection):
    """Lesen und Schreiben atomar; in der CLI-Transaktion von `secret reencrypt` nicht selbst committen."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    with conn:
        yield


def _put(conn: sqlite3.Connection, cfg: dict) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (_KEY, json.dumps(cfg)))


def _find(cfg: dict, pid: str | None) -> dict | None:
    return next((p for p in cfg["providers"] if p["id"] == pid), None)


def set_public_url(conn: sqlite3.Connection, url: str) -> str:
    """Ändert nur public_url; Secrets bleiben unberührt."""
    url = _check_public_url(url)
    with _tx(conn):
        _put(conn, {**_stored(conn), "public_url": url})
    return url


def save_provider(conn: sqlite3.Connection, box: SecretBox, pid: str, *, issuer: str, client_id: str,
                  client_secret: str | None, label: str | None) -> None:
    """client_secret=None behält das gespeicherte Secret, nur beim selben Anbieter mit gleichem Issuer und Client-ID."""
    if not _ID.fullmatch(pid):
        raise ValueError("Ungültige Anbieter-ID")
    _check_base(issuer)
    with _tx(conn):
        cfg = _stored(conn)
        if cfg["public_url"] is None:
            raise ValueError("Zuerst public_url setzen")
        old = _find(cfg, pid)
        if old is None and len(cfg["providers"]) >= _MAX_PROVIDERS:
            raise ValueError(f"Höchstens {_MAX_PROVIDERS} Anmeldeanbieter")
        # Secret nur behalten, wenn es an denselben Anbieter geht; sonst könnte ein neuer Issuer es abgreifen
        if client_secret is None and (old is None or (old["issuer"], old["client_id"]) != (issuer, client_id)):
            raise ValueError("Client-Secret fehlt")
        enc = old["client_secret_enc"] if client_secret is None else box.encrypt(client_secret)
        new = {"id": pid, "issuer": issuer, "client_id": client_id, "client_secret_enc": enc, "label": label or pid}
        cfg["providers"] = [new if p is old else p for p in cfg["providers"]] if old else [*cfg["providers"], new]
        _put(conn, cfg)


def delete_provider(conn: sqlite3.Connection, pid: str) -> bool:
    with _tx(conn):
        cfg = _stored(conn)
        rest = [p for p in cfg["providers"] if p["id"] != pid]
        if len(rest) == len(cfg["providers"]):
            return False
        _put(conn, {**cfg, "providers": rest})
    return True


def load_provider(conn: sqlite3.Connection, box: SecretBox, pid: str) -> dict | None:
    """Anbieter mit entschlüsseltem Secret und public_url, oder None."""
    cfg = _stored(conn)
    p = _find(cfg, pid)
    if p is None:
        return None
    out = {k: v for k, v in p.items() if k != "client_secret_enc"}
    return {**out, "client_secret": box.decrypt(p["client_secret_enc"]), "public_url": cfg["public_url"]}


def reencrypt(conn: sqlite3.Connection, box: SecretBox) -> int:
    with _tx(conn):
        cfg = _stored(conn)
        ps = [p for p in cfg["providers"] if p.get("client_secret_enc")]
        for p in ps:
            p["client_secret_enc"] = box.rotate(p["client_secret_enc"])
        if ps:
            _put(conn, cfg)
    return len(ps)


# --- laufende Anmeldungen -----------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Flows:
    """state -> (nonce, PKCE-Verifier, Beginn, Anbieter); nur im Speicher, jeder state ist einmal verwendbar.

    Anbieter = (id, issuer, client_id) zum Zeitpunkt des Starts.
    """
    MAX = 1000

    def __init__(self):
        self._d: dict[str, tuple[str, str, float, tuple]] = {}
        self._lock = threading.Lock()

    def add(self, now: float, provider: tuple) -> tuple[str, str, str]:
        state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self._lock:
            for s in [s for s, e in self._d.items() if now - e[2] >= FLOW_SECONDS]:
                del self._d[s]
            while len(self._d) >= self.MAX:  # älteste zuerst (Einfügereihenfolge)
                del self._d[next(iter(self._d))]
            self._d[state] = (nonce, verifier, now, provider)
        return state, nonce, _b64(hashlib.sha256(verifier.encode()).digest())

    def pop(self, state: str, now: float) -> tuple[str, str, tuple] | None:
        with self._lock:
            e = self._d.pop(state, None)
        if e is None or now - e[2] >= FLOW_SECONDS:
            return None
        return e[0], e[1], e[3]


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
ProviderId = Annotated[str, Path(pattern=r"^[a-z0-9-]{1,32}$")]
_NOT_FOUND = "Anmeldeanbieter nicht gefunden"


class PublicUrlBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    public_url: Url

    @field_validator("public_url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _check_public_url(v)


class ProviderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issuer: Url
    client_id: Annotated[StrictStr, StringConstraints(min_length=1, max_length=255)]
    client_secret: Annotated[StrictStr, StringConstraints(min_length=1, max_length=1024)] | None = None
    label: Annotated[StrictStr, StringConstraints(min_length=1, max_length=64)] | None = None

    @field_validator("issuer")
    @classmethod
    def _base(cls, v: str) -> str:
        return _check_base(v)

    @field_validator("client_id", "label")
    @classmethod
    def _printable(cls, v: str | None) -> str | None:
        if v is not None and (not v.isprintable() or not v.strip()):
            raise ValueError("Steuerzeichen sind nicht erlaubt, Text darf nicht leer sein")
        return v


def _provider_out(p: dict) -> dict:
    return {"id": p["id"], "issuer": p["issuer"], "client_id": p["client_id"], "label": p["label"],
            "client_secret_set": bool(p.get("client_secret_enc"))}


def _redirect_uri(public_url: str, pid: str) -> str:
    """Eigene Redirect-URI je Anbieter: Mix-up-Schutz nach RFC 9700 §4.4 (P3-R10)."""
    return f"{public_url}/api/v1/auth/oidc/callback/{pid}"


@router.get("")
def status(request: Request):
    with db.request_conn(request) as conn:
        cfg = _stored(conn)
    return {"providers": [{"id": p["id"], "label": p["label"]} for p in cfg["providers"]]}


@router.put("/public-url")
def put_public_url(request: Request, body: PublicUrlBody, principal: Principal = admin):
    with db.request_conn(request) as conn:
        url = set_public_url(conn, body.public_url)
        audit.record(conn, principal.actor, "oidc.config_changed", ip=client_ip(request), details={"public_url": True})
    return {"public_url": url}


@router.get("/providers")
def list_providers(request: Request, principal: Principal = admin):
    with db.request_conn(request) as conn:
        cfg = _stored(conn)
    return {"public_url": cfg["public_url"], "providers": [_provider_out(p) for p in cfg["providers"]]}


@router.put("/providers/{pid}")
def put_provider(request: Request, pid: ProviderId, body: ProviderBody, principal: Principal = admin):
    if body.issuer.rstrip("/").lower() == _MS_CONSUMERS_ALIAS:  # Alias: Tokens tragen den GUID-Issuer
        raise HTTPException(422, _CONSUMER_HINT)
    try:
        _discover(body.issuer, time.time(), fresh=True)
    except _Hint as e:
        raise HTTPException(422, str(e)) from None
    except OidcError:
        raise HTTPException(422, _UNREACHABLE) from None
    with db.request_conn(request) as conn:
        try:
            save_provider(conn, request.app.state.box, pid, **body.model_dump())
        except ValueError as e:  # Texte nennen nie das Secret
            raise HTTPException(422, str(e)) from None
        audit.record(conn, principal.actor, "oidc.config_changed", target=pid, ip=client_ip(request),
                     details={"provider": pid})
        return _provider_out(_find(_stored(conn), pid))


@router.delete("/providers/{pid}", status_code=204)
def remove_provider(request: Request, pid: ProviderId, principal: Principal = admin):
    with db.request_conn(request) as conn:
        if not delete_provider(conn, pid):
            raise HTTPException(404, _NOT_FOUND)
        audit.record(conn, principal.actor, "oidc.config_changed", target=pid, ip=client_ip(request),
                     details={"provider": pid, "deleted": True})
    return Response(status_code=204)


@router.get("/start")
def start(request: Request, provider: str | None = None):
    now = int(time.time())
    with db.request_conn(request) as conn:
        cfg = _stored(conn)
    p = _find(cfg, provider)
    if p is None:
        raise HTTPException(404, _NOT_FOUND)
    try:
        ep = _discover(p["issuer"], now)["authorization_endpoint"]
    except OidcError:
        log.warning("OIDC-Anbieter nicht erreichbar")
        return RedirectResponse(_FAIL_URL, 303)
    state, nonce, challenge = request.app.state.oidc_flows.add(now, (p["id"], p["issuer"], p["client_id"]))
    query = urllib.parse.urlencode({"response_type": "code", "client_id": p["client_id"],
                                    "redirect_uri": _redirect_uri(cfg["public_url"], p["id"]),
                                    "scope": "openid email profile", "state": state, "nonce": nonce,
                                    "code_challenge": challenge, "code_challenge_method": "S256"})
    response = RedirectResponse(ep + ("&" if "?" in ep else "?") + query, 302)
    response.set_cookie(FLOW_COOKIE, state, max_age=FLOW_SECONDS, **_FLOW_ATTRS)  # Lax: kommt beim Rücksprung mit
    return response


def _verify(request: Request, pid: str, now: int) -> tuple[dict | None, str | None]:
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
    nonce, verifier, (flow_pid, flow_issuer, flow_client_id) = flow
    if pid != flow_pid:  # Rücksprung an die Redirect-URI eines anderen Anbieters: Mix-up, kein Anbieter wird gefragt
        return None, "state"
    if "error" in q:
        return None, "anbieter"
    with db.request_conn(request) as conn:
        try:
            cfg = load_provider(conn, app.state.box, flow_pid)
        except SecretError:
            cfg = None
    # inzwischen gelöscht, umkonfiguriert oder Secret nicht entschlüsselbar
    if cfg is None or (cfg["issuer"], cfg["client_id"]) != (flow_issuer, flow_client_id):
        return None, "config"
    code = q.get("code")
    try:
        d = _discover(cfg["issuer"], now)
    except OidcError:
        return None, "token"
    if d.get("authorization_response_iss_parameter_supported") is True:  # RFC 9207
        iss = q.get("iss")
        if not iss or not hmac.compare_digest(iss.encode("utf-8", "surrogatepass"), cfg["issuer"].encode()):
            return None, "state"
    try:
        if not code:
            raise OidcError("code fehlt")
        tokens = _http("POST", d["token_endpoint"],
                       data={"grant_type": "authorization_code", "code": code,
                             "redirect_uri": _redirect_uri(cfg["public_url"], flow_pid),
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


def _email_trusted(c: dict) -> bool:
    """D7 mit P3-R8/P3-R9: email_verified, Microsofts xms_edov oder Microsofts Mandant für private Konten."""
    if c.get("email_verified") is True or c.get("xms_edov") is True:
        return True
    return c.get("iss") == MS_CONSUMER_ISSUER and isinstance(c.get("email"), str) and bool(c["email"])


def _account(users: UserStore, claims: dict) -> tuple[User | None, str | None, bool]:
    """(Benutzer, None, neu verknüpft) oder (None, Fehlercode, False). Nie automatische Kontoanlage (D7)."""
    user = users.by_oidc(claims["iss"], claims["sub"])
    bind = user is None
    if bind:
        if not _email_trusted(claims):
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


@router.get("/callback/{pid}")
def callback(request: Request, pid: str):  # ohne Pattern: unpassende pid zählt als Fehlversuch (Drosselung, Audit)
    now = int(time.time())
    ip = client_ip(request)
    claims, grund = _verify(request, pid, now)
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
