import base64
import hashlib
import io
import json
import time
import urllib.error
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from moni import cli
from moni.auth import audit, oidc
from moni.auth.sessions import COOKIE
from moni.auth.users import UserStore
from moni.manager import db
from moni.manager.poller import _NoRedirect
from moni.secretbox import SecretBox, generate_key

ISSUER = "https://idp.example.org/realms/moni"
DISCOVERY = ISSUER + "/.well-known/openid-configuration"
AUTH_EP = "https://idp.example.org/auth"
TOKEN_EP = "https://idp.example.org/token"
JWKS_URI = "https://idp.example.org/jwks"
PUBLIC = "https://moni.example.org"
SECRET = "geheimes-client-secret-42"
REAL_OPENER = oidc._OPENER


def _jwk(key, kid):
    d = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**d, "kid": kid, "use": "sig", "alg": "RS256"}


class Idp:
    """Test-Anbieter: liefert Discovery, JWKS und Token-Antwort aus Dicts, zählt Abrufe."""

    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.discovery = {"issuer": ISSUER, "authorization_endpoint": AUTH_EP, "token_endpoint": TOKEN_EP,
                          "jwks_uri": JWKS_URI, "id_token_signing_alg_values_supported": ["RS256"]}
        self.jwks = {"keys": [_jwk(self.key, "k1")]}
        self.id_token = None
        self.token_fails = False
        self.calls = []

    def sign(self, claims, key=None, kid="k1", alg="RS256"):
        return jwt.encode(claims, key or self.key, algorithm=alg, headers={"kid": kid})

    def http(self, method, url, data=None, headers=None):
        self.calls.append((method, url, data, headers))
        if url == DISCOVERY and method == "GET":
            return json.loads(json.dumps(self.discovery))
        if url == JWKS_URI and method == "GET":
            return json.loads(json.dumps(self.jwks))
        if url == TOKEN_EP and method == "POST":
            if self.token_fails:
                raise oidc.OidcError("Token-Endpunkt antwortet mit HTTP 500")
            return {"access_token": "at-geheim", "token_type": "Bearer", "id_token": self.id_token}
        raise oidc.OidcError("unerwartete Anfrage")


@pytest.fixture
def idp(monkeypatch):
    i = Idp()
    oidc.reset_caches()
    monkeypatch.setattr(oidc, "_http", i.http)
    yield i
    oidc.reset_caches()


def _conn(app):
    return db.connect(app.state.settings.db_path)


@pytest.fixture
def oidc_client(mgr_app, mgr_client, idp):
    conn = _conn(mgr_app)
    try:
        oidc.save_config(conn, mgr_app.state.box, issuer=ISSUER, client_id="moni", client_secret=SECRET,
                         public_url=PUBLIC, label="Firmen-Login")
    finally:
        conn.close()
    return mgr_client


def last_audit(client):
    conn = _conn(client.app)
    try:
        return audit.entries(conn, limit=1)[0]
    finally:
        conn.close()


def _start(client):
    r = client.get("/api/v1/auth/oidc/start", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    return r, q


def _claims(nonce, **over):
    now = int(time.time())
    return {"iss": ISSUER, "aud": "moni", "sub": "s1", "iat": now, "exp": now + 300, "nonce": nonce, **over}


def complete_flow(client, idp, claims, sign=None):
    _, q = _start(client)
    c = _claims(q["nonce"][0], **claims)
    idp.id_token = sign(c) if sign else idp.sign(c)
    return client.get(f"/api/v1/auth/oidc/callback?code=c-geheim&state={q['state'][0]}", follow_redirects=False)


def _session(client):
    return client.get("/api/v1/auth/session")


# --- Start und erfolgreiche Anmeldung ---------------------------------------

def test_start_redirect_has_pkce_state_nonce(oidc_client):
    r, q = _start(oidc_client)
    assert r.status_code == 302 and q["code_challenge_method"] == ["S256"] and q["state"] and q["nonce"]
    assert r.headers["location"].startswith(AUTH_EP + "?")
    assert q["redirect_uri"] == ["https://moni.example.org/api/v1/auth/oidc/callback"]
    assert q["response_type"] == ["code"] and q["client_id"] == ["moni"] and q["scope"] == ["openid email profile"]
    assert len(q["code_challenge"][0]) == 43
    sc = r.headers["set-cookie"].lower()
    assert "samesite=lax" in sc and "httponly" in sc and "secure" in sc and "path=/api/v1/auth/oidc" in sc
    assert "max-age=600" in sc and f"moni_oidc={q['state'][0]}".lower() in sc


def test_start_and_status_without_config(mgr_client, idp):
    assert mgr_client.get("/api/v1/auth/oidc").json() == {"enabled": False, "label": None}
    assert mgr_client.get("/api/v1/auth/oidc/start", follow_redirects=False).status_code == 404


def test_status_is_public(oidc_client):
    assert oidc_client.get("/api/v1/auth/oidc").json() == {"enabled": True, "label": "Firmen-Login"}


def test_happy_path_binds_and_logs_in(oidc_client, idp, make_user, caplog):
    u = make_user("viewer", kind="oidc", email="o@example.org")
    r = complete_flow(oidc_client, idp, claims={"email": "o@example.org", "email_verified": True, "sub": "s1"})
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "moni_oidc=" in r.headers["set-cookie"] and COOKIE in r.headers["set-cookie"]
    s = _session(oidc_client).json()
    assert s["stage"] == "full" and s["user"]["id"] == u.id
    e = last_audit(oidc_client)
    assert e["action"] == "oidc.login" and e["actor"] == "user:o@example.org"
    # Token-Austausch: client_secret_basic, PKCE-Verifier passt zur Challenge, redirect_uri wie beim Start
    method, url, data, headers = [c for c in idp.calls if c[1] == TOKEN_EP][0]
    assert data["grant_type"] == "authorization_code" and data["code"] == "c-geheim"
    assert data["redirect_uri"] == "https://moni.example.org/api/v1/auth/oidc/callback"
    assert headers["Authorization"] == "Basic " + base64.b64encode(f"moni:{SECRET}".encode()).decode()
    assert "client_secret" not in data
    conn = _conn(oidc_client.app)
    try:
        assert UserStore(conn, oidc_client.app.state.box).by_oidc(ISSUER, "s1").id == u.id
        assert "c-geheim" not in str(audit.entries(conn)) and SECRET not in str(audit.entries(conn))
    finally:
        conn.close()
    own = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("moni"))  # httpx loggt die Test-URL selbst
    assert idp.id_token not in own and "c-geheim" not in own and "at-geheim" not in own and SECRET not in own
    # zweiter Login mit geänderter E-Mail im Token, gleiche sub → weiterhin derselbe Benutzer
    oidc_client.cookies.clear()
    r = complete_flow(oidc_client, idp, claims={"email": "neu@example.org", "email_verified": False, "sub": "s1"})
    assert r.headers["location"] == "/" and _session(oidc_client).json()["user"]["id"] == u.id
    # andere sub mit derselben E-Mail: Konto ist schon verknüpft → keine Anmeldung
    oidc_client.cookies.clear()
    r = complete_flow(oidc_client, idp, claims={"email": "o@example.org", "email_verified": True, "sub": "s2"})
    assert r.headers["location"] == "/login?fehler=oidc" and last_audit(oidc_client)["details"] == {"grund": "unknown_user"}


def test_pkce_verifier_matches_challenge(oidc_client, idp, make_user):
    make_user("viewer", kind="oidc", email="o@example.org")
    _, q = _start(oidc_client)
    idp.id_token = idp.sign(_claims(q["nonce"][0], email="o@example.org", email_verified=True))
    oidc_client.get(f"/api/v1/auth/oidc/callback?code=c&state={q['state'][0]}", follow_redirects=False)
    verifier = [c for c in idp.calls if c[1] == TOKEN_EP][0][2]["code_verifier"]
    assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() == q["code_challenge"][0]


def test_key_rotation_refetches_jwks_once(oidc_client, idp, make_user):
    make_user("viewer", kind="oidc", email="o@example.org")
    assert complete_flow(oidc_client, idp, {"email": "o@example.org", "email_verified": True}).headers["location"] == "/"
    new = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    idp.jwks = {"keys": [_jwk(new, "k2")]}  # Anbieter rotiert, JWKS steht noch im Cache
    oidc_client.cookies.clear()
    r = complete_flow(oidc_client, idp, {"email": "o@example.org", "email_verified": True},
                      sign=lambda c: idp.sign(c, key=new, kid="k2"))
    assert r.headers["location"] == "/"
    assert sum(1 for c in idp.calls if c[1] == JWKS_URI) == 2


# --- manipulierte Rücksprünge -----------------------------------------------

def run_tampered(client, idp, tamper):
    app = client.app
    conn = _conn(app)
    try:
        users = UserStore(conn, app.state.box)
        if tamper == "local_account":
            users.create("o@example.org", "", "viewer", "local", "Test-Passwort-123")
        elif tamper != "unknown_email":
            u = users.create("o@example.org", "", "viewer", "oidc")
            if tamper == "disabled":
                users.update(u.id, disabled=True)
    finally:
        conn.close()
    _, q = _start(client)
    state, nonce = q["state"][0], q["nonce"][0]
    now = int(time.time())
    claims = _claims(nonce, email="o@example.org", email_verified=True)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    n_secret = idp.jwks["keys"][0]["n"].encode()
    tokens = {
        "wrong_nonce": lambda: idp.sign({**claims, "nonce": "falsch"}),
        "wrong_aud": lambda: idp.sign({**claims, "aud": "fremd"}),
        "wrong_iss": lambda: idp.sign({**claims, "iss": "https://evil.example.org"}),
        "expired": lambda: idp.sign({**claims, "iat": now - 7200, "exp": now - 3600}),
        "alg_none": lambda: jwt.encode(claims, None, algorithm="none", headers={"kid": "k1"}),
        "alg_hs256_with_jwks_n": lambda: jwt.encode(claims, n_secret, algorithm="HS256", headers={"kid": "k1"}),
        "unknown_kid": lambda: idp.sign(claims, key=other, kid="k9"),
        "email_unverified": lambda: idp.sign({**claims, "email_verified": "true"}),
    }
    idp.id_token = tokens.get(tamper, lambda: idp.sign(claims))()
    idp.token_fails = tamper == "token_endpoint_500"
    params = f"code=c&state={state}"
    if tamper == "wrong_state":
        params = "code=c&state=falsch"
    elif tamper == "no_cookie":
        client.cookies.delete("moni_oidc")
    elif tamper == "idp_error_param":
        params = f"error=access_denied&state={state}"
    elif tamper == "replayed_state":
        conn = _conn(app)  # erster Rücksprung scheitert an der Zuordnung und verbraucht den state
        try:
            UserStore(conn, app.state.box).delete(UserStore(conn, app.state.box).by_email("o@example.org").id)
        finally:
            conn.close()
        assert client.get(f"/api/v1/auth/oidc/callback?{params}", follow_redirects=False).status_code == 303
        assert last_audit(client)["details"] == {"grund": "unknown_user"}
        client.cookies.set("moni_oidc", state)
    return client.get(f"/api/v1/auth/oidc/callback?{params}", follow_redirects=False)


@pytest.mark.parametrize("tamper,grund", [
    ("wrong_state", "state"), ("no_cookie", "state"), ("replayed_state", "state"),
    ("wrong_nonce", "id_token"), ("wrong_aud", "id_token"), ("wrong_iss", "id_token"), ("expired", "id_token"),
    ("alg_none", "id_token"), ("alg_hs256_with_jwks_n", "id_token"), ("unknown_kid", "id_token"),
    ("email_unverified", "email_unverified"), ("unknown_email", "unknown_user"), ("local_account", "unknown_user"),
    ("disabled", "disabled"), ("token_endpoint_500", "token"), ("idp_error_param", "anbieter"),
])
def test_tampered_callbacks_never_login(oidc_client, idp, tamper, grund):
    r = run_tampered(oidc_client, idp, tamper)
    assert r.status_code == 303 and r.headers["location"] == "/login?fehler=oidc"
    assert "moni_oidc=" in r.headers["set-cookie"] and COOKIE not in r.headers["set-cookie"]
    assert _session(oidc_client).status_code == 401
    e = last_audit(oidc_client)
    assert e["action"] == "oidc.failure" and e["actor"] == "anonym" and e["details"] == {"grund": grund}


def test_callback_failures_count_for_throttle(oidc_client, idp):
    app = oidc_client.app
    for _ in range(20):
        oidc_client.get("/api/v1/auth/oidc/callback?code=c&state=falsch", follow_redirects=False)
    r = complete_flow(oidc_client, idp, {"email": "o@example.org", "email_verified": True})
    assert r.headers["location"] == "/login?fehler=oidc"
    assert last_audit(oidc_client)["details"] == {"grund": "drosselung"}
    assert app.state.throttle.retry_after("testclient", int(time.time())) is not None


def test_callback_without_config_and_garbage(oidc_client, idp):
    _, q = _start(oidc_client)
    conn = _conn(oidc_client.app)
    try:
        oidc.delete_config(conn)
    finally:
        conn.close()
    r = oidc_client.get(f"/api/v1/auth/oidc/callback?code=c&state={q['state'][0]}", follow_redirects=False)
    assert r.headers["location"] == "/login?fehler=oidc" and last_audit(oidc_client)["details"] == {"grund": "config"}
    oidc_client.cookies.set("moni_oidc", "x")
    r = oidc_client.get("/api/v1/auth/oidc/callback?state=%C3%A4&code=%00", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?fehler=oidc"


# --- validate_id_token direkt -------------------------------------------------

def _validate(idp, token, nonce="n1", algs=("RS256",)):
    return oidc.validate_id_token(token, issuer=ISSUER, client_id="moni", nonce=nonce, jwks=idp.jwks, algs=list(algs),
                                  now=time.time())


@pytest.mark.parametrize("over,ok", [
    ({}, True),
    ({"aud": ["moni", "x"], "azp": "moni"}, True),
    ({"aud": ["moni", "x"]}, False),                    # mehrere aud ohne azp
    ({"aud": ["moni", "x"], "azp": "x"}, False),
    ({"aud": ["moni"]}, True),
    ({"iat": int(time.time()) + 3600}, False),          # aus der Zukunft
    ({"sub": None}, False),
    ({"nonce": None}, False),
    ({"exp": int(time.time()) - 30}, True),             # innerhalb der 60 s Toleranz
])
def test_validate_id_token_claims(idp, over, ok):
    c = {k: v for k, v in {**_claims("n1"), **over}.items() if v is not None}
    if ok:
        assert _validate(idp, idp.sign(c))["sub"] == "s1"
    else:
        with pytest.raises(oidc.OidcError):
            _validate(idp, idp.sign(c))


def test_validate_rejects_alg_outside_discovery_and_garbage(idp):
    with pytest.raises(oidc.OidcError):
        _validate(idp, idp.sign(_claims("n1")), algs=["ES256"])
    for bad in ("", "kaputt", "a.b.c"):
        with pytest.raises(oidc.OidcError):
            _validate(idp, bad)


# --- Konfiguration ------------------------------------------------------------

@pytest.fixture
def admin(mgr_app, make_user, login_cookie):
    plain, csrf = login_cookie(make_user("admin"))
    c = TestClient(mgr_app, base_url="https://testserver")
    c.cookies.set(COOKIE, plain)
    c.headers["X-CSRF-Token"] = csrf
    return c


def _body(**over):
    return {"issuer": ISSUER, "client_id": "moni", "client_secret": SECRET, "public_url": PUBLIC, "label": "Firma", **over}


def test_config_never_returns_secret(mgr_app, admin, idp):
    assert admin.get("/api/v1/auth/oidc/config").status_code == 404
    r = admin.put("/api/v1/auth/oidc/config", json=_body(client_secret=None))
    assert r.status_code == 422  # beim ersten Speichern ist das Secret Pflicht
    r = admin.put("/api/v1/auth/oidc/config", json=_body())
    assert r.status_code == 200 and SECRET not in r.text
    g = admin.get("/api/v1/auth/oidc/config")
    assert g.json() == {"issuer": ISSUER, "client_id": "moni", "public_url": PUBLIC, "label": "Firma",
                        "client_secret_set": True}
    assert SECRET not in g.text
    r = admin.put("/api/v1/auth/oidc/config", json=_body(client_secret=None, label=None))  # Secret bleibt
    assert r.status_code == 200 and r.json()["client_secret_set"] is True and r.json()["label"] is None
    conn = _conn(mgr_app)
    try:
        raw = conn.execute("SELECT value FROM meta WHERE key='oidc'").fetchone()[0]
        assert SECRET not in raw
        assert oidc.load_config(conn, mgr_app.state.box)["client_secret"] == SECRET
        es = audit.entries(conn)
        assert es[0]["action"] == "oidc.config_changed" and SECRET not in str(es)
    finally:
        conn.close()
    assert admin.delete("/api/v1/auth/oidc/config").status_code == 204
    assert admin.get("/api/v1/auth/oidc/config").status_code == 404
    assert last_audit(admin)["details"] == {"deleted": True}


@pytest.mark.parametrize("over,status", [
    ({"issuer": "https://evil.example.org/realms/moni"}, 422),  # Secret darf nicht an einen neuen Anbieter gehen
    ({"client_id": "andere"}, 422),
    ({"label": "Neu"}, 200),
    ({"public_url": "https://moni2.example.org"}, 200),
])
def test_config_keeps_secret_only_for_same_issuer_and_client(mgr_app, admin, idp, monkeypatch, over, status):
    assert admin.put("/api/v1/auth/oidc/config", json=_body()).status_code == 200
    if "issuer" in over:  # fremder Anbieter mit gültiger Discovery: nur das fehlende Secret darf ablehnen
        evil = {**idp.discovery, "issuer": over["issuer"], "token_endpoint": "https://evil.example.org/token"}
        monkeypatch.setattr(oidc, "_http", lambda m, u, data=None, headers=None:
                            evil if u == over["issuer"] + "/.well-known/openid-configuration" else idp.http(m, u, data, headers))
    before = admin.get("/api/v1/auth/oidc/config").json()
    r = admin.put("/api/v1/auth/oidc/config", json=_body(client_secret=None, **over))
    assert r.status_code == status
    after = admin.get("/api/v1/auth/oidc/config").json()
    conn = _conn(mgr_app)
    try:
        assert oidc.load_config(conn, mgr_app.state.box)["client_secret"] == SECRET
    finally:
        conn.close()
    if status == 422:
        assert r.json()["detail"] == "Client-Secret fehlt" and after == before
    else:
        assert after == {**before, **over}


@pytest.mark.parametrize("over", [
    {"issuer": "http://idp.example.org/realms/moni"},
    {"issuer": ISSUER + "?x=1"},
    {"issuer": ISSUER + "#f"},
    {"public_url": "http://moni.example.org"},
    {"public_url": PUBLIC + "/?"},
    {"client_id": ""},
    {"extra": 1},
])
def test_config_rejects_bad_values(admin, idp, over):
    assert admin.put("/api/v1/auth/oidc/config", json=_body(**over)).status_code == 422
    assert idp.calls == []  # Prüfung vor jedem Abruf


def test_config_rejects_http_and_issuer_mismatch(admin, idp):
    idp.discovery["issuer"] = ISSUER + "/"
    r = admin.put("/api/v1/auth/oidc/config", json=_body())
    assert r.status_code == 422 and r.json()["detail"] == "OIDC-Anbieter nicht erreichbar oder Konfiguration passt nicht"
    idp.discovery["issuer"] = ISSUER
    idp.discovery["token_endpoint"] = "http://idp.example.org/token"
    assert admin.put("/api/v1/auth/oidc/config", json=_body()).status_code == 422
    assert admin.get("/api/v1/auth/oidc/config").status_code == 404


def test_config_needs_admin(mgr_app, make_user, login_cookie, idp):
    plain, csrf = login_cookie(make_user("viewer"))
    c = TestClient(mgr_app, base_url="https://testserver")
    c.cookies.set(COOKIE, plain)
    assert c.get("/api/v1/auth/oidc/config").status_code == 403
    assert c.put("/api/v1/auth/oidc/config", json=_body(), headers={"X-CSRF-Token": csrf}).status_code == 403
    assert TestClient(mgr_app, base_url="https://testserver").delete("/api/v1/auth/oidc/config").status_code == 401


# --- _http (echt) -------------------------------------------------------------

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class _Opener:
    def __init__(self, result):
        self.result, self.requests = result, []

    def open(self, req, timeout):
        self.requests.append((req, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return _Resp(self.result)


def test_http_helper_rejects_non_https_and_redirects(monkeypatch):
    op = _Opener(b"{}")
    monkeypatch.setattr(oidc, "_OPENER", op)
    for url in ("http://idp.example.org/x", "file:///etc/passwd", "ftp://a/b"):
        with pytest.raises(oidc.OidcError):
            oidc._http("GET", url)
    assert op.requests == []  # kein Netz
    # Weiterleitungen werden nicht verfolgt, Proxys aus der Umgebung nicht genutzt
    assert any(isinstance(h, _NoRedirect) for h in REAL_OPENER.handlers)
    assert all(getattr(h, "proxies", {}) == {} for h in REAL_OPENER.handlers)
    redirect = urllib.error.HTTPError("https://idp.example.org/x", 302, "Found", {"Location": "https://evil"}, None)
    for result in (redirect, urllib.error.HTTPError("https://a", 500, "x", {}, None), TimeoutError(),
                   urllib.error.URLError("x"), b"kein json", b"[1]", b"{" + b" " * (1024 * 1024) + b"}"):
        monkeypatch.setattr(oidc, "_OPENER", _Opener(result))
        with pytest.raises(oidc.OidcError) as ei:
            oidc._http("GET", "https://idp.example.org/x")
        assert ei.value.__context__ is None and ei.value.__cause__ is None


def test_http_helper_post_form_and_timeout(monkeypatch):
    op = _Opener(b'{"ok": true}')
    monkeypatch.setattr(oidc, "_OPENER", op)
    r = oidc._http("POST", "https://idp.example.org/t", data={"a": "b c"}, headers={"Authorization": "Basic x"})
    assert r == {"ok": True}
    req, timeout = op.requests[0]
    assert timeout == 10 and req.get_method() == "POST" and req.data == b"a=b+c"
    assert req.get_header("Authorization") == "Basic x"
    assert req.get_header("Content-type") == "application/x-www-form-urlencoded"


def test_basic_auth_is_form_encoded():
    assert oidc._basic("a b", "x:y") == "Basic " + base64.b64encode(b"a+b:x%3Ay").decode()


# --- Flows --------------------------------------------------------------------

def test_flows_ttl_and_bound():
    f = oidc.Flows()
    state, nonce, challenge = f.add(1000)
    n, verifier = f.pop(state, 1599)
    assert n == nonce and base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() == challenge
    assert f.pop(state, 1599) is None  # nur einmal verwendbar
    s2, _, _ = f.add(1000)
    assert f.pop(s2, 1600) is None  # abgelaufen
    first, _, _ = f.add(2000)
    states = [f.add(2000)[0] for _ in range(1000)]
    assert f.pop(first, 2001) is None and f.pop(states[-1], 2001) is not None
    assert len(f._d) <= 1000
    assert f.pop("unbekannt", 2001) is None


# --- Neu verschlüsseln --------------------------------------------------------

def test_reencrypt_includes_oidc_secret(tmp_path, monkeypatch, capsys):
    old, new = generate_key(), generate_key()
    monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MONI_ROLE", "manager")
    monkeypatch.setenv("MONI_SECRET_KEY", old)
    db.migrate(tmp_path / "moni.db")
    conn = db.connect(tmp_path / "moni.db")
    try:
        assert oidc.reencrypt(conn, SecretBox(old)) == 0
        oidc.save_config(conn, SecretBox(old), issuer=ISSUER, client_id="moni", client_secret=SECRET, public_url=PUBLIC,
                         label=None)
        monkeypatch.setenv("MONI_SECRET_KEY", f"{new},{old}")
        assert cli.main(["secret", "reencrypt"]) == 0
        assert capsys.readouterr().out.strip() == "Neu verschlüsselt: 1"
        assert oidc.load_config(conn, SecretBox(new))["client_secret"] == SECRET
    finally:
        conn.close()
