import base64
import hashlib
import io
import json
import time
import urllib.error
from urllib.parse import parse_qs, quote, urlparse

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

GOOGLE = "https://accounts.google.com"
TENANT = "11111111-2222-3333-4444-555555555555"
MS = f"https://login.microsoftonline.com/{TENANT}/v2.0"
CONSUMER = "https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0"
PUBLIC = "https://moni.example.org"
SECRET = "geheimes-client-secret-42"
MS_SECRET = "ms-client-secret-77"
FAIL = "/login?fehler=oidc"
TENANT_HINT = ("Für Microsoft die mandantenspezifische Issuer-URL verwenden: "
               "https://login.microsoftonline.com/<Mandanten-ID>/v2.0")
CONSUMER_HINT = ("Für private Microsoft-Konten als Issuer "
                 "https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0 eintragen")
REAL_OPENER = oidc._OPENER


def _jwk(key, kid):
    d = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**d, "kid": kid, "use": "sig", "alg": "RS256"}


class Idp:
    """Test-Anbieter mit eigenem Schlüssel und eigenen Endpunkten; zählt Abrufe."""

    def __init__(self, pid, issuer, auth, token, jwks, client_id, secret, label, disc_issuer=None):
        self.pid, self.issuer, self.client_id, self.secret, self.label = pid, issuer, client_id, secret, label
        self.authorization_endpoint, self.token_endpoint, self.jwks_uri = auth, token, jwks
        self.discovery_url = issuer + "/.well-known/openid-configuration"
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.discovery = {"issuer": disc_issuer or issuer, "authorization_endpoint": auth, "token_endpoint": token,
                          "jwks_uri": jwks, "id_token_signing_alg_values_supported": ["RS256"]}
        self.jwks = {"keys": [_jwk(self.key, "k1")]}
        self.id_token = None
        self.token_fails = False
        self.calls = []

    def handles(self, url):
        return url in (self.discovery_url, self.jwks_uri, self.token_endpoint)

    def sign(self, claims, key=None, kid="k1", alg="RS256"):
        return jwt.encode(claims, key or self.key, algorithm=alg, headers={"kid": kid})

    def http(self, method, url, data=None, headers=None):
        self.calls.append((method, url, data, headers))
        if url == self.discovery_url and method == "GET":
            return json.loads(json.dumps(self.discovery))
        if url == self.jwks_uri and method == "GET":
            return json.loads(json.dumps(self.jwks))
        if url == self.token_endpoint and method == "POST":
            if self.token_fails:
                raise oidc.OidcError("Token-Endpunkt antwortet mit HTTP 500")
            return {"access_token": "at-geheim", "token_type": "Bearer", "id_token": self.id_token}
        raise oidc.OidcError("unerwartete Anfrage")


def _google():
    return Idp("google", GOOGLE, "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token",
               "https://www.googleapis.com/oauth2/v3/certs", "moni", SECRET, "Google")


def _microsoft(issuer=MS, pid="microsoft", disc_issuer=None):
    base = issuer.removesuffix("/v2.0")
    return Idp(pid, issuer, base + "/oauth2/v2.0/authorize", base + "/oauth2/v2.0/token", base + "/discovery/v2.0/keys",
               "moni-ms", MS_SECRET, "Microsoft", disc_issuer)


class Net:
    """Ersatz für oidc._http: verteilt Anfragen nach URL auf die Test-Anbieter."""

    def __init__(self):
        self.idps, self.calls = [], []

    def add(self, idp):
        self.idps.append(idp)
        return idp

    def http(self, method, url, data=None, headers=None):
        self.calls.append((method, url))
        for i in self.idps:
            if i.handles(url):
                return i.http(method, url, data, headers)
        raise oidc.OidcError("unerwartete Anfrage")


@pytest.fixture
def net(monkeypatch):
    n = Net()
    oidc.reset_caches()
    monkeypatch.setattr(oidc, "_http", n.http)
    yield n
    oidc.reset_caches()


@pytest.fixture
def idp_google(net):
    return net.add(_google())


@pytest.fixture
def idp_microsoft(net):
    return net.add(_microsoft())


@pytest.fixture
def idp(idp_google):
    return idp_google


def _conn(app):
    return db.connect(app.state.settings.db_path)


def _configure(app, *idps, public_url=PUBLIC):
    conn = _conn(app)
    try:
        oidc.set_public_url(conn, public_url)
        for i in idps:
            oidc.save_provider(conn, app.state.box, i.pid, issuer=i.issuer, client_id=i.client_id,
                               client_secret=i.secret, label=i.label)
    finally:
        conn.close()


def _provider(app, pid):
    conn = _conn(app)
    try:
        return oidc.load_provider(conn, app.state.box, pid)
    finally:
        conn.close()


@pytest.fixture
def oidc_client(mgr_app, mgr_client, idp_google, idp_microsoft):
    _configure(mgr_app, idp_google, idp_microsoft)
    return mgr_client


def last_audit(client):
    conn = _conn(client.app)
    try:
        return audit.entries(conn, limit=1)[0]
    finally:
        conn.close()


def _start(client, provider="google"):
    r = client.get(f"/api/v1/auth/oidc/start?provider={provider}", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    return r, q


def _claims(idp, nonce, **over):
    now = int(time.time())
    return {"iss": idp.issuer, "aud": idp.client_id, "sub": "s1", "iat": now, "exp": now + 300, "nonce": nonce, **over}


def _cb(provider, params):
    return f"/api/v1/auth/oidc/callback/{provider}?{params}"


def complete_flow(client, idp, claims, sign=None, provider=None, extra=""):
    provider = provider or idp.pid
    _, q = _start(client, provider)
    c = _claims(idp, q["nonce"][0], **claims)
    idp.id_token = sign(c) if sign else idp.sign(c)
    return client.get(_cb(provider, f"code=c-geheim&state={q['state'][0]}{extra}"), follow_redirects=False)


def _token_calls(*idps):
    return [c for i in idps for c in i.calls if c[1] == i.token_endpoint]


def _session(client):
    return client.get("/api/v1/auth/session")


# --- Start und erfolgreiche Anmeldung ---------------------------------------

def test_start_redirect_has_pkce_state_nonce(oidc_client, idp):
    r, q = _start(oidc_client)
    assert r.status_code == 302 and q["code_challenge_method"] == ["S256"] and q["state"] and q["nonce"]
    assert r.headers["location"].startswith(idp.authorization_endpoint + "?")
    assert q["redirect_uri"] == ["https://moni.example.org/api/v1/auth/oidc/callback/google"]
    assert q["response_type"] == ["code"] and q["client_id"] == ["moni"] and q["scope"] == ["openid email profile"]
    assert len(q["code_challenge"][0]) == 43
    sc = r.headers["set-cookie"].lower()
    assert "samesite=lax" in sc and "httponly" in sc and "secure" in sc and "path=/api/v1/auth/oidc" in sc
    assert "max-age=600" in sc and f"moni_oidc={q['state'][0]}".lower() in sc


def test_two_providers_listed_and_started(oidc_client, idp_google, idp_microsoft):
    assert {p["id"] for p in oidc_client.get("/api/v1/auth/oidc").json()["providers"]} == {"google", "microsoft"}
    loc = oidc_client.get("/api/v1/auth/oidc/start?provider=microsoft", follow_redirects=False).headers["location"]
    assert loc.startswith(idp_microsoft.authorization_endpoint)
    q = parse_qs(urlparse(loc).query)
    assert q["client_id"] == ["moni-ms"]
    assert q["redirect_uri"] == ["https://moni.example.org/api/v1/auth/oidc/callback/microsoft"]


def test_start_and_status_without_config(mgr_client, net):
    assert mgr_client.get("/api/v1/auth/oidc").json() == {"providers": []}
    assert mgr_client.get("/api/v1/auth/oidc/start?provider=google", follow_redirects=False).status_code == 404


def test_status_is_public(oidc_client):
    assert oidc_client.get("/api/v1/auth/oidc").json() == {
        "providers": [{"id": "google", "label": "Google"}, {"id": "microsoft", "label": "Microsoft"}]}


def test_unknown_provider_404(oidc_client):
    for q in ("", "?provider=", "?provider=github", "?provider=Google"):
        assert oidc_client.get(f"/api/v1/auth/oidc/start{q}", follow_redirects=False).status_code == 404


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
    method, url, data, headers = [c for c in idp.calls if c[1] == idp.token_endpoint][0]
    assert data["grant_type"] == "authorization_code" and data["code"] == "c-geheim"
    assert data["redirect_uri"] == "https://moni.example.org/api/v1/auth/oidc/callback/google"
    assert headers["Authorization"] == "Basic " + base64.b64encode(f"moni:{SECRET}".encode()).decode()
    assert "client_secret" not in data
    conn = _conn(oidc_client.app)
    try:
        assert UserStore(conn, oidc_client.app.state.box).by_oidc(GOOGLE, "s1").id == u.id
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
    assert r.headers["location"] == FAIL and last_audit(oidc_client)["details"] == {"grund": "unknown_user"}


def test_microsoft_xms_edov_binds(oidc_client, idp_google, idp_microsoft, make_user):
    u = make_user("viewer", kind="oidc", email="m@firma.de")
    r = complete_flow(oidc_client, idp_microsoft, provider="microsoft",
                      claims={"email": "m@firma.de", "xms_edov": True, "sub": "ms1"})       # kein email_verified
    assert r.headers["location"] == "/"
    # Code ging an den Microsoft-Token-Endpunkt, mit den Microsoft-Zugangsdaten, nie an Google
    headers = [c for c in idp_microsoft.calls if c[1] == idp_microsoft.token_endpoint][0][3]
    assert headers["Authorization"] == "Basic " + base64.b64encode(f"moni-ms:{MS_SECRET}".encode()).decode()
    assert idp_google.calls == []
    conn = _conn(oidc_client.app)
    try:
        assert UserStore(conn, oidc_client.app.state.box).by_oidc(MS, "ms1").id == u.id
    finally:
        conn.close()


@pytest.mark.parametrize("over", [{}, {"xms_edov": False}, {"xms_edov": "true"}, {"xms_edov": 1},
                                  {"email_verified": False}])
def test_microsoft_without_verification_rejected(oidc_client, idp_microsoft, make_user, over):
    make_user("viewer", kind="oidc", email="m@firma.de")
    r = complete_flow(oidc_client, idp_microsoft, claims={"email": "m@firma.de", "sub": "ms1", **over})
    assert r.headers["location"] == FAIL and last_audit(oidc_client)["details"] == {"grund": "email_unverified"}


def test_personal_microsoft_account_binds(oidc_client, net, make_user):
    assert oidc.MS_CONSUMER_ISSUER == CONSUMER
    privat = net.add(_microsoft(CONSUMER, "privat"))
    _configure(oidc_client.app, privat)
    make_user("viewer", kind="oidc", email="p@outlook.de")
    r = complete_flow(oidc_client, privat, claims={"sub": "c0"})  # ohne email: nichts zu bestätigen
    assert r.headers["location"] == FAIL and last_audit(oidc_client)["details"] == {"grund": "email_unverified"}
    r = complete_flow(oidc_client, privat, claims={"email": "p@outlook.de", "sub": "c1"})
    assert r.headers["location"] == "/"


@pytest.mark.parametrize("pid", ["google", "microsoft"])
def test_consumer_rule_not_applied_to_other_issuers(oidc_client, idp_google, idp_microsoft, make_user, pid):
    make_user("viewer", kind="oidc", email="p@outlook.de")
    i = {"google": idp_google, "microsoft": idp_microsoft}[pid]
    r = complete_flow(oidc_client, i, claims={"email": "p@outlook.de", "sub": "c1",
                                              "tid": "9188040d-6c67-4c5b-b112-36a304b66dad"})
    assert r.headers["location"] == FAIL and last_audit(oidc_client)["details"] == {"grund": "email_unverified"}


def test_same_email_two_providers_binds_once(oidc_client, idp_google, idp_microsoft, make_user):
    make_user("viewer", kind="oidc", email="o@example.org")
    assert complete_flow(oidc_client, idp_google, {"email": "o@example.org", "email_verified": True}).headers["location"] == "/"
    oidc_client.cookies.clear()
    r = complete_flow(oidc_client, idp_microsoft, {"email": "o@example.org", "email_verified": True, "xms_edov": True})
    assert r.headers["location"] == FAIL and last_audit(oidc_client)["details"] == {"grund": "unknown_user"}


def test_bound_user_disabled_later(oidc_client, idp, make_user):
    u = make_user("viewer", kind="oidc", email="o@example.org")
    assert complete_flow(oidc_client, idp, {"email": "o@example.org", "email_verified": True}).headers["location"] == "/"
    conn = _conn(oidc_client.app)
    try:
        UserStore(conn, oidc_client.app.state.box).update(u.id, disabled=True)
    finally:
        conn.close()
    oidc_client.cookies.clear()
    r = complete_flow(oidc_client, idp, {"email": "o@example.org", "email_verified": True})  # Fund über (iss, sub)
    assert r.headers["location"] == FAIL and last_audit(oidc_client)["details"] == {"grund": "disabled"}


def test_pkce_verifier_matches_challenge(oidc_client, idp, make_user):
    make_user("viewer", kind="oidc", email="o@example.org")
    _, q = _start(oidc_client)
    idp.id_token = idp.sign(_claims(idp, q["nonce"][0], email="o@example.org", email_verified=True))
    oidc_client.get(_cb("google", f"code=c&state={q['state'][0]}"), follow_redirects=False)
    verifier = [c for c in idp.calls if c[1] == idp.token_endpoint][0][2]["code_verifier"]
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
    assert sum(1 for c in idp.calls if c[1] == idp.jwks_uri) == 2


def test_provider_deleted_between_start_and_callback(oidc_client, idp_microsoft, make_user):
    make_user("viewer", kind="oidc", email="m@firma.de")
    _, q = _start(oidc_client, "microsoft")
    conn = _conn(oidc_client.app)
    try:
        assert oidc.delete_provider(conn, "microsoft") is True
    finally:
        conn.close()
    idp_microsoft.id_token = idp_microsoft.sign(_claims(idp_microsoft, q["nonce"][0], email="m@firma.de", xms_edov=True))
    r = oidc_client.get(_cb("microsoft", f"code=c&state={q['state'][0]}"), follow_redirects=False)
    assert r.headers["location"] == FAIL and last_audit(oidc_client)["details"] == {"grund": "config"}
    assert not [c for c in idp_microsoft.calls if c[1] == idp_microsoft.token_endpoint]


@pytest.mark.parametrize("started,returned", [("microsoft", "google"), ("google", "microsoft")])
def test_mix_up_callback_on_other_provider_path(oidc_client, idp_google, idp_microsoft, make_user, started, returned):
    """RFC 9700 §4.4: Flow für A gestartet, code kommt an der Redirect-URI von H an → kein Token-Aufruf."""
    make_user("viewer", kind="oidc", email="o@example.org")
    idps = {"google": idp_google, "microsoft": idp_microsoft}
    _, q = _start(oidc_client, started)
    for i in idps.values():
        i.id_token = i.sign(_claims(i, q["nonce"][0], email="o@example.org", email_verified=True))
    r = oidc_client.get(_cb(returned, f"code=c-ehrlich&state={q['state'][0]}"), follow_redirects=False)
    assert r.headers["location"] == FAIL and last_audit(oidc_client)["details"] == {"grund": "state"}
    assert _token_calls(idp_google, idp_microsoft) == []
    assert _session(oidc_client).status_code == 401
    # der state ist verbraucht: auch der passende Rücksprung danach scheitert
    oidc_client.cookies.set("moni_oidc", q["state"][0])
    r = oidc_client.get(_cb(started, f"code=c&state={q['state'][0]}"), follow_redirects=False)
    assert r.headers["location"] == FAIL and last_audit(oidc_client)["details"] == {"grund": "state"}
    assert _token_calls(idp_google, idp_microsoft) == []


@pytest.mark.parametrize("over", [{"client_id": "moni-ms-neu"},
                                  {"issuer": "https://login.microsoftonline.com/99999999-0000-0000-0000-000000000000/v2.0"}])
def test_provider_changed_between_start_and_callback(oidc_client, idp_microsoft, make_user, over):
    make_user("viewer", kind="oidc", email="m@firma.de")
    _, q = _start(oidc_client, "microsoft")
    conn = _conn(oidc_client.app)
    try:
        cfg = {"issuer": MS, "client_id": "moni-ms", "client_secret": "neues-secret", "label": None, **over}
        oidc.save_provider(conn, oidc_client.app.state.box, "microsoft", **cfg)
    finally:
        conn.close()
    idp_microsoft.id_token = idp_microsoft.sign(_claims(idp_microsoft, q["nonce"][0], email="m@firma.de", xms_edov=True))
    r = oidc_client.get(_cb("microsoft", f"code=c&state={q['state'][0]}"), follow_redirects=False)
    assert r.headers["location"] == FAIL and last_audit(oidc_client)["details"] == {"grund": "config"}
    assert _token_calls(idp_microsoft) == []


@pytest.mark.parametrize("advertised,iss,ok", [
    (True, MS, True),
    (True, None, False),
    (True, "", False),
    (True, "https://evil.example.org", False),
    (True, GOOGLE, False),               # Issuer eines anderen konfigurierten Anbieters
    (True, MS + "/", False),
    (False, None, True),
])
def test_rfc9207_iss_parameter(oidc_client, idp_microsoft, make_user, advertised, iss, ok):
    make_user("viewer", kind="oidc", email="m@firma.de")
    idp_microsoft.discovery["authorization_response_iss_parameter_supported"] = advertised
    extra = "" if iss is None else "&iss=" + quote(iss, safe="")
    r = complete_flow(oidc_client, idp_microsoft, {"email": "m@firma.de", "xms_edov": True}, extra=extra)
    if ok:
        assert r.headers["location"] == "/"
    else:
        assert r.headers["location"] == FAIL and last_audit(oidc_client)["details"] == {"grund": "state"}
        assert _token_calls(idp_microsoft) == []


# --- manipulierte Rücksprünge -----------------------------------------------

def run_tampered(client, idp, other, tamper):
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
    _, q = _start(client, idp.pid)
    state, nonce = q["state"][0], q["nonce"][0]
    now = int(time.time())
    claims = _claims(idp, nonce, email="o@example.org", email_verified=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    n_secret = idp.jwks["keys"][0]["n"].encode()
    tokens = {
        "wrong_nonce": lambda: idp.sign({**claims, "nonce": "falsch"}),
        "wrong_aud": lambda: idp.sign({**claims, "aud": "fremd"}),
        "wrong_iss": lambda: idp.sign({**claims, "iss": "https://evil.example.org"}),
        "other_provider_iss": lambda: idp.sign({**claims, "iss": other.issuer}),
        "other_provider_token": lambda: other.sign({**claims, "iss": other.issuer, "aud": other.client_id}),
        "expired": lambda: idp.sign({**claims, "iat": now - 7200, "exp": now - 3600}),
        "alg_none": lambda: jwt.encode(claims, None, algorithm="none", headers={"kid": "k1"}),
        "alg_hs256_with_jwks_n": lambda: jwt.encode(claims, n_secret, algorithm="HS256", headers={"kid": "k1"}),
        "unknown_kid": lambda: idp.sign(claims, key=key, kid="k9"),
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
    elif tamper == "provider_in_query":  # Callback nimmt den Anbieter nur aus dem Flow, nie aus der Anfrage
        params += f"&provider={other.pid}"
        idp.token_fails = True
    elif tamper == "replayed_state":
        conn = _conn(app)  # erster Rücksprung scheitert an der Zuordnung und verbraucht den state
        try:
            UserStore(conn, app.state.box).delete(UserStore(conn, app.state.box).by_email("o@example.org").id)
        finally:
            conn.close()
        assert client.get(_cb(idp.pid, params), follow_redirects=False).status_code == 303
        assert last_audit(client)["details"] == {"grund": "unknown_user"}
        client.cookies.set("moni_oidc", state)
    return client.get(_cb(idp.pid, params), follow_redirects=False)


@pytest.mark.parametrize("pid", ["google", "microsoft"])
@pytest.mark.parametrize("tamper,grund", [
    ("wrong_state", "state"), ("no_cookie", "state"), ("replayed_state", "state"),
    ("wrong_nonce", "id_token"), ("wrong_aud", "id_token"), ("wrong_iss", "id_token"), ("expired", "id_token"),
    ("other_provider_iss", "id_token"), ("other_provider_token", "id_token"),
    ("alg_none", "id_token"), ("alg_hs256_with_jwks_n", "id_token"), ("unknown_kid", "id_token"),
    ("email_unverified", "email_unverified"), ("unknown_email", "unknown_user"), ("local_account", "unknown_user"),
    ("disabled", "disabled"), ("token_endpoint_500", "token"), ("idp_error_param", "anbieter"),
    ("provider_in_query", "token"),
])
def test_tampered_callbacks_never_login(oidc_client, idp_google, idp_microsoft, pid, tamper, grund):
    idp, other = (idp_google, idp_microsoft) if pid == "google" else (idp_microsoft, idp_google)
    r = run_tampered(oidc_client, idp, other, tamper)
    assert r.status_code == 303 and r.headers["location"] == FAIL
    assert "moni_oidc=" in r.headers["set-cookie"] and COOKIE not in r.headers["set-cookie"]
    assert _session(oidc_client).status_code == 401
    e = last_audit(oidc_client)
    assert e["action"] == "oidc.failure" and e["actor"] == "anonym" and e["details"] == {"grund": grund}
    assert not [c for c in other.calls if c[1] == other.token_endpoint]  # der code geht nur an den Anbieter des Flows


def test_callback_failures_count_for_throttle(oidc_client, idp):
    app = oidc_client.app
    for _ in range(20):
        oidc_client.get(_cb("google", "code=c&state=falsch"), follow_redirects=False)
    before = last_audit(oidc_client)
    r = complete_flow(oidc_client, idp, {"email": "o@example.org", "email_verified": True})
    assert r.status_code == 303 and r.headers["location"] == FAIL and "moni_oidc=" in r.headers["set-cookie"]
    assert last_audit(oidc_client) == before  # gedrosselt: keine Audit-Zeile, kein DB-Schreibzugriff
    assert app.state.throttle.retry_after("testclient", int(time.time())) is not None


def test_callback_garbage(oidc_client, idp):
    oidc_client.cookies.set("moni_oidc", "x")
    for path in ("google", "%00x", "x" * 300):
        r = oidc_client.get(_cb(path, "state=%C3%A4&code=%00"), follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == FAIL
    # die gemeinsame Redirect-URI ohne Anbieter gibt es nicht mehr
    assert oidc_client.get("/api/v1/auth/oidc/callback?code=c&state=x", follow_redirects=False).status_code == 404


# --- validate_id_token direkt -------------------------------------------------

def _validate(idp, token, nonce="n1", algs=("RS256",), jwks=None):
    return oidc.validate_id_token(token, issuer=idp.issuer, client_id="moni", nonce=nonce, jwks=jwks or idp.jwks,
                                  algs=list(algs), now=time.time())


@pytest.mark.parametrize("over,ok", [
    ({}, True),
    ({"aud": ["moni", "x"], "azp": "moni"}, True),
    ({"aud": ["moni", "x"]}, False),                    # mehrere aud ohne azp
    ({"aud": ["moni", "x"], "azp": "x"}, False),
    ({"aud": ["moni"]}, True),
    ({"azp": "moni"}, True),
    ({"azp": "x"}, False),                              # ein aud, aber azp passt nicht
    ({"iat": 3600}, False),                             # aus der Zukunft (Sekunden ab jetzt)
    ({"sub": None}, False),
    ({"nonce": None}, False),
    ({"exp": -30}, True),                               # innerhalb der 60 s Toleranz (Sekunden ab jetzt)
])
def test_validate_id_token_claims(idp, over, ok):
    over = {k: int(time.time()) + v if k in ("iat", "exp") else v for k, v in over.items()}  # erst zur Laufzeit
    c = {k: v for k, v in {**_claims(idp, "n1"), **over}.items() if v is not None}
    if ok:
        assert _validate(idp, idp.sign(c))["sub"] == "s1"
    else:
        with pytest.raises(oidc.OidcError):
            _validate(idp, idp.sign(c))


def test_validate_token_without_kid(idp):
    token = jwt.encode(_claims(idp, "n1"), idp.key, algorithm="RS256")  # ohne kid im Header
    assert "kid" not in jwt.get_unverified_header(token)
    assert _validate(idp, token)["sub"] == "s1"  # ein einziger Schlüssel: eindeutig
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(oidc.OidcError):  # mehrere Schlüssel: nicht raten
        _validate(idp, token, jwks={"keys": [_jwk(idp.key, "k1"), _jwk(other, "k2")]})


def test_validate_rejects_alg_outside_discovery_and_garbage(idp):
    with pytest.raises(oidc.OidcError):
        _validate(idp, idp.sign(_claims(idp, "n1")), algs=["ES256"])
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


def _body(idp, **over):
    b = {"issuer": idp.issuer, "client_id": idp.client_id, "client_secret": idp.secret, "label": idp.label, **over}
    return {k: v for k, v in b.items() if v is not None}


def _put(admin, idp, pid=None, **over):
    return admin.put(f"/api/v1/auth/oidc/providers/{pid or idp.pid}", json=_body(idp, **over))


def test_providers_never_return_secret(mgr_app, admin, idp_google, idp_microsoft):
    assert admin.get("/api/v1/auth/oidc/providers").json() == {"public_url": None, "providers": []}
    r = _put(admin, idp_google)
    assert r.status_code == 422 and r.json()["detail"] == "Zuerst public_url setzen"
    r = admin.put("/api/v1/auth/oidc/public-url", json={"public_url": PUBLIC + "/"})
    assert r.status_code == 200 and r.json() == {"public_url": PUBLIC}
    assert last_audit(admin)["details"] == {"public_url": True}
    r = _put(admin, idp_google, client_secret=None)
    assert r.status_code == 422 and r.json()["detail"] == "Client-Secret fehlt"  # beim Anlegen ist das Secret Pflicht
    r = _put(admin, idp_google)
    assert r.status_code == 200 and SECRET not in r.text
    e = last_audit(admin)
    assert e["action"] == "oidc.config_changed" and e["details"] == {"provider": "google"}
    assert _put(admin, idp_microsoft, label=None).status_code == 200  # label fehlt → id
    g = admin.get("/api/v1/auth/oidc/providers")
    assert g.json() == {"public_url": PUBLIC, "providers": [
        {"id": "google", "issuer": GOOGLE, "client_id": "moni", "label": "Google", "client_secret_set": True},
        {"id": "microsoft", "issuer": MS, "client_id": "moni-ms", "label": "microsoft", "client_secret_set": True}]}
    assert SECRET not in g.text and MS_SECRET not in g.text
    r = _put(admin, idp_google, client_secret=None, label="Google Workspace")  # Secret bleibt
    assert r.status_code == 200 and r.json()["client_secret_set"] is True and r.json()["label"] == "Google Workspace"
    conn = _conn(mgr_app)
    try:
        raw = conn.execute("SELECT value FROM meta WHERE key='oidc'").fetchone()[0]
        assert SECRET not in raw and MS_SECRET not in raw
        assert oidc.load_provider(conn, mgr_app.state.box, "google")["client_secret"] == SECRET
        assert oidc.load_provider(conn, mgr_app.state.box, "microsoft")["client_secret"] == MS_SECRET
        es = str(audit.entries(conn))
        assert SECRET not in es and MS_SECRET not in es
    finally:
        conn.close()
    assert admin.delete("/api/v1/auth/oidc/providers/microsoft").status_code == 204
    assert last_audit(admin)["details"] == {"provider": "microsoft", "deleted": True}
    assert admin.delete("/api/v1/auth/oidc/providers/microsoft").status_code == 404
    assert [p["id"] for p in admin.get("/api/v1/auth/oidc/providers").json()["providers"]] == ["google"]
    assert _provider(mgr_app, "google")["client_secret"] == SECRET


@pytest.mark.parametrize("over,status", [
    ({"issuer": "https://evil.example.org/realms/moni"}, 422),  # Secret darf nicht an einen neuen Anbieter gehen
    ({"client_id": "andere"}, 422),
    ({"label": "Neu"}, 200),
])
def test_provider_keeps_secret_only_for_same_issuer_and_client(mgr_app, admin, net, idp_google, idp_microsoft, over,
                                                                status):
    assert admin.put("/api/v1/auth/oidc/public-url", json={"public_url": PUBLIC}).status_code == 200
    assert _put(admin, idp_google).status_code == 200 and _put(admin, idp_microsoft).status_code == 200
    if "issuer" in over:  # fremder Anbieter mit gültiger Discovery: nur das fehlende Secret darf ablehnen
        net.add(Idp("evil", over["issuer"], "https://evil.example.org/auth", "https://evil.example.org/token",
                    "https://evil.example.org/jwks", "moni-ms", None, None))
    before = admin.get("/api/v1/auth/oidc/providers").json()
    r = _put(admin, idp_microsoft, client_secret=None, **over)
    assert r.status_code == status
    after = admin.get("/api/v1/auth/oidc/providers").json()
    assert _provider(mgr_app, "microsoft")["client_secret"] == MS_SECRET
    assert _provider(mgr_app, "google")["client_secret"] == SECRET
    if status == 422:
        assert r.json()["detail"] == "Client-Secret fehlt" and after == before
    else:
        assert after["providers"][1] == {**before["providers"][1], **over} and after["providers"][0] == before["providers"][0]


def test_new_provider_never_inherits_other_secret(mgr_app, admin, idp_google):
    _configure(mgr_app, idp_google)
    r = _put(admin, idp_google, pid="google2", client_secret=None)  # gleicher issuer und client_id, andere id
    assert r.status_code == 422 and r.json()["detail"] == "Client-Secret fehlt"
    assert _provider(mgr_app, "google2") is None


@pytest.mark.parametrize("url", [
    "http://moni.example.org", PUBLIC + "/moni", PUBLIC + "/moni/", PUBLIC + "//", PUBLIC + "?x=1", PUBLIC + "#f",
    PUBLIC + "/?", "https://user@moni.example.org", "https://", "", "https://moni.example.org\n", 5,
])
def test_public_url_rejects_paths_and_non_https(admin, net, url):
    r = admin.put("/api/v1/auth/oidc/public-url", json={"public_url": url})
    assert r.status_code == 422
    assert admin.get("/api/v1/auth/oidc/providers").json()["public_url"] is None


def test_public_url_change_keeps_secrets(mgr_app, admin, idp_google, idp_microsoft):
    _configure(mgr_app, idp_google, idp_microsoft)
    r = admin.put("/api/v1/auth/oidc/public-url", json={"public_url": "https://moni2.example.org:8443/"})
    assert r.status_code == 200 and r.json() == {"public_url": "https://moni2.example.org:8443"}
    assert _provider(mgr_app, "google")["client_secret"] == SECRET
    assert _provider(mgr_app, "microsoft")["client_secret"] == MS_SECRET
    assert all(p["client_secret_set"] for p in admin.get("/api/v1/auth/oidc/providers").json()["providers"])
    _, q = _start(admin, "microsoft")
    assert q["redirect_uri"] == ["https://moni2.example.org:8443/api/v1/auth/oidc/callback/microsoft"]


@pytest.mark.parametrize("pid,over", [
    ("google", {"issuer": "http://accounts.google.com"}),
    ("google", {"issuer": GOOGLE + "?x=1"}),
    ("google", {"issuer": GOOGLE + "#f"}),
    ("google", {"client_id": ""}),
    ("google", {"label": ""}),
    ("google", {"label": "   "}),
    ("google", {"label": "a\nb"}),
    ("google", {"label": "x" * 65}),
    ("google", {"public_url": PUBLIC}),
    ("google", {"extra": 1}),
    ("Google", {}),
    ("goo_gle", {}),
    ("g" * 33, {}),
    ("google%0A", {}),
])
def test_provider_rejects_bad_values(mgr_app, admin, net, idp_google, pid, over):
    _configure(mgr_app)
    assert admin.put(f"/api/v1/auth/oidc/providers/{pid}", json=_body(idp_google, **over)).status_code == 422
    assert net.calls == []  # Prüfung vor jedem Abruf
    assert admin.get("/api/v1/auth/oidc/providers").json()["providers"] == []


def test_provider_label_limits(mgr_app, admin, idp_google):
    _configure(mgr_app)
    assert _put(admin, idp_google, label="Ä" * 64).status_code == 200


def test_provider_rejects_http_and_issuer_mismatch(mgr_app, admin, idp):
    _configure(mgr_app)
    idp.discovery["issuer"] = GOOGLE + "/"
    r = _put(admin, idp)
    assert r.status_code == 422 and r.json()["detail"] == "OIDC-Anbieter nicht erreichbar oder Konfiguration passt nicht"
    idp.discovery["issuer"] = GOOGLE
    idp.discovery["token_endpoint"] = "http://oauth2.googleapis.com/token"
    assert _put(admin, idp).status_code == 422
    assert admin.get("/api/v1/auth/oidc/providers").json()["providers"] == []


@pytest.mark.parametrize("tenant", ["common", "organizations"])
def test_common_issuer_rejected_with_hint(mgr_app, admin, net, tenant):
    _configure(mgr_app)
    multi = net.add(_microsoft(f"https://login.microsoftonline.com/{tenant}/v2.0",
                               disc_issuer="https://login.microsoftonline.com/{tenantid}/v2.0"))
    r = _put(admin, multi)
    assert r.status_code == 422 and r.json()["detail"] == TENANT_HINT
    assert admin.get("/api/v1/auth/oidc/providers").json()["providers"] == []


@pytest.mark.parametrize("issuer", ["https://login.microsoftonline.com/consumers/v2.0",
                                    "https://login.microsoftonline.com/consumers/v2.0/"])
def test_consumers_alias_issuer_rejected_with_hint(mgr_app, admin, net, idp_microsoft, issuer):
    _configure(mgr_app)
    r = _put(admin, idp_microsoft, issuer=issuer)
    assert r.status_code == 422 and r.json()["detail"] == CONSUMER_HINT
    assert net.calls == []  # vor dem Abruf der Discovery


def test_max_ten_providers(mgr_app, admin, idp_google):
    _configure(mgr_app)
    conn = _conn(mgr_app)
    try:
        for n in range(10):
            oidc.save_provider(conn, mgr_app.state.box, f"p{n}", issuer=GOOGLE, client_id="moni", client_secret=SECRET,
                               label=None)
    finally:
        conn.close()
    r = _put(admin, idp_google, pid="p10")
    assert r.status_code == 422 and r.json()["detail"] == "Höchstens 10 Anmeldeanbieter"
    assert _put(admin, idp_google, pid="p3", label="Drei").status_code == 200  # Ändern bleibt möglich
    assert len(admin.get("/api/v1/auth/oidc/providers").json()["providers"]) == 10


def test_config_needs_admin(mgr_app, make_user, login_cookie, idp):
    plain, csrf = login_cookie(make_user("viewer"))
    c = TestClient(mgr_app, base_url="https://testserver")
    c.cookies.set(COOKIE, plain)
    h = {"X-CSRF-Token": csrf}
    assert c.get("/api/v1/auth/oidc/providers").status_code == 403
    assert c.put("/api/v1/auth/oidc/providers/google", json=_body(idp), headers=h).status_code == 403
    assert c.put("/api/v1/auth/oidc/public-url", json={"public_url": PUBLIC}, headers=h).status_code == 403
    assert c.delete("/api/v1/auth/oidc/providers/google", headers=h).status_code == 403
    anon = TestClient(mgr_app, base_url="https://testserver")
    assert anon.delete("/api/v1/auth/oidc/providers/google").status_code == 401
    assert anon.get("/api/v1/auth/oidc/providers").status_code == 401


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
    state, nonce, challenge = f.add(1000, ("microsoft", MS, "moni-ms"))
    n, verifier, provider = f.pop(state, 1599)
    assert n == nonce and base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() == challenge
    assert provider == ("microsoft", MS, "moni-ms")
    assert f.pop(state, 1599) is None  # nur einmal verwendbar
    s2, _, _ = f.add(1000, ("google", GOOGLE, "moni"))
    assert f.pop(s2, 1600) is None  # abgelaufen
    first, _, _ = f.add(2000, ("google", GOOGLE, "moni"))
    states = [f.add(2000, ("google", GOOGLE, "moni"))[0] for _ in range(1000)]
    assert f.pop(first, 2001) is None and f.pop(states[-1], 2001) is not None
    assert len(f._d) <= 1000
    assert f.pop("unbekannt", 2001) is None


# --- Neu verschlüsseln --------------------------------------------------------

def test_reencrypt_covers_all_providers(tmp_path, monkeypatch, capsys):
    old, new = generate_key(), generate_key()
    monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MONI_ROLE", "manager")
    monkeypatch.setenv("MONI_SECRET_KEY", old)
    db.migrate(tmp_path / "moni.db")
    conn = db.connect(tmp_path / "moni.db")
    try:
        assert oidc.reencrypt(conn, SecretBox(old)) == 0
        oidc.set_public_url(conn, PUBLIC)
        assert oidc.reencrypt(conn, SecretBox(old)) == 0
        for pid, secret in (("google", SECRET), ("microsoft", MS_SECRET)):
            oidc.save_provider(conn, SecretBox(old), pid, issuer=GOOGLE if pid == "google" else MS, client_id="moni",
                               client_secret=secret, label=None)
        monkeypatch.setenv("MONI_SECRET_KEY", f"{new},{old}")
        assert cli.main(["secret", "reencrypt"]) == 0
        assert capsys.readouterr().out.strip() == "Neu verschlüsselt: 2"
        assert oidc.load_provider(conn, SecretBox(new), "google")["client_secret"] == SECRET
        assert oidc.load_provider(conn, SecretBox(new), "microsoft")["client_secret"] == MS_SECRET
        assert oidc.load_provider(conn, SecretBox(new), "google")["public_url"] == PUBLIC
    finally:
        conn.close()


def test_oidc_discovery_failure_cached(monkeypatch):
    oidc.reset_caches()
    calls = []

    def down(method, url, *a, **k):
        calls.append(url)
        raise oidc.OidcError("OIDC-Anbieter nicht erreichbar")
    monkeypatch.setattr(oidc, "_http", down)
    for t in (1000, 1005, 1029):
        with pytest.raises(oidc.OidcError):
            oidc._discover(GOOGLE, t)
    assert len(calls) == 1  # innerhalb von 30 s nur ein Netzaufruf
    with pytest.raises(oidc.OidcError):
        oidc._discover(GOOGLE, 1031)
    assert len(calls) == 2
    with pytest.raises(oidc.OidcError):
        oidc._discover(GOOGLE, 1032, fresh=True)  # PUT des Anbieters prüft immer frisch
    assert len(calls) == 3
    oidc.reset_caches()
