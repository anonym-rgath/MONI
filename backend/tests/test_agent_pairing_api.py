"""Agent: Manager-Anmeldung per Client-Zertifikat (D3), Kopplung, Erneuerung und Token-Verwaltung (D9)."""
import logging
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from moni import pki
from moni.agent.tls import TlsState
from moni.app import create_app
from moni.config import Settings
from moni.timeutil import utcnow

AGENT_SAN = ["127.0.0.1"]


def pem(cert) -> str:
    return pki.cert_pem(cert).decode()


def _with_cert(app, cert_pem: str | None):
    """Test-Middleware: setzt die ASGI-TLS-Erweiterung wie MoniH11Protocol nach erfolgreicher OpenSSL-Prüfung."""
    async def wrapped(scope, receive, send):
        if cert_pem is not None and scope["type"] == "http":
            scope = {**scope, "extensions": {**scope.get("extensions", {}), "tls": {"client_cert_chain": [cert_pem]}}}
        await app(scope, receive, send)
    return wrapped


def _client_cert(ca, cn="moni-manager", usage="client", now=None, days=365) -> str:
    key = pki.new_key()
    return pem(pki.issue(*ca, pki.make_csr(key, cn), cn=cn, sans=[cn], usage=usage, now=now or utcnow(), days=days))


@pytest.fixture
def state(tmp_path):
    st = TlsState(tmp_path)
    st.ensure(utcnow())
    return st


@pytest.fixture
def agent_app(tmp_path, state, measured):
    s = Settings.from_env({"MONI_DATA_DIR": str(tmp_path), "MONI_TLS": "on", "MONI_RATE_LIMIT_PER_MINUTE": "10000"})
    return create_app(s, measured, tls_state=state)


@pytest.fixture
def ca():
    return pki.create_ca(utcnow())


@pytest.fixture
def pairing_token(state):
    return state.pairing_code("").split(":")[2]


@pytest.fixture
def agent_client(agent_app):
    return TestClient(agent_app)


@pytest.fixture
def connect(agent_app):
    def make(cert_pem=None):
        return TestClient(_with_cert(agent_app, cert_pem))
    return make


@pytest.fixture
def paired(state, ca):
    cert = pki.issue(*ca, state.new_csr(), cn="moni-agent", sans=AGENT_SAN, usage="server", now=utcnow(), days=90)
    state.install(pki.cert_pem(cert), pki.cert_pem(ca[1]), utcnow())
    return state


@pytest.fixture
def manager(paired, ca, connect):
    return connect(_client_cert(ca))


def _detail(r):
    return r.status_code, r.json()["detail"]


def test_pairing_flow_via_token(agent_client, state, ca, pairing_token, caplog):
    hdr = {"X-Moni-Pairing-Token": pairing_token}
    assert _detail(agent_client.post("/api/v1/tls/csr")) == (401, "Kopplungs-Token ungültig")
    assert _detail(agent_client.post("/api/v1/tls/csr", headers={"X-Moni-Pairing-Token": "falsch"})) == (
        401, "Kopplungs-Token ungültig")
    agent_client.post("/api/v1/tls/csr", headers=hdr)  # mehrfach erlaubt, der letzte Schlüssel gilt
    csr = agent_client.post("/api/v1/tls/csr", headers=hdr).json()["csr"]
    cert = pki.issue(*ca, csr.encode(), cn="moni-agent", sans=AGENT_SAN, usage="server", now=utcnow(), days=90)
    wrong = pki.issue(*ca, pki.make_csr(pki.new_key(), "x"), cn="moni-agent", sans=AGENT_SAN, usage="server",
                      now=utcnow(), days=90)
    r = agent_client.post("/api/v1/tls/cert", headers=hdr, json={"certificate": pem(wrong), "ca": pem(ca[1])})
    assert _detail(r) == (422, "Zertifikat passt nicht") and not state.paired  # Token noch nicht verbraucht
    with caplog.at_level(logging.INFO):
        r = agent_client.post("/api/v1/tls/cert", headers=hdr, json={"certificate": pem(cert), "ca": pem(ca[1])})
    assert r.status_code == 204 and state.paired
    assert "Agent gekoppelt" in caplog.text and pairing_token not in caplog.text and "BEGIN" not in caplog.text
    for path, body in (("/api/v1/tls/csr", None), ("/api/v1/tls/cert", {"certificate": pem(cert), "ca": pem(ca[1])})):
        assert _detail(agent_client.post(path, headers=hdr, json=body)) == (409, "Agent ist bereits gekoppelt")


def test_pairing_without_token_never_reaches_body_validation(agent_client):
    r = agent_client.post("/api/v1/tls/cert", json={"certificate": 1})
    assert _detail(r) == (401, "Kopplungs-Token ungültig")


@pytest.mark.parametrize("body", [
    {"certificate": "x"},
    {"certificate": "x", "ca": "y", "extra": 1},
    {"certificate": 1, "ca": "y"},
    {"certificate": "x" * 16385, "ca": "y"},
])
def test_cert_body_is_strict(agent_client, pairing_token, body):
    r = agent_client.post("/api/v1/tls/cert", headers={"X-Moni-Pairing-Token": pairing_token}, json=body)
    assert r.status_code == 422


def test_non_ascii_pairing_token_is_401(agent_client, state):
    r = agent_client.post("/api/v1/tls/csr", headers={"X-Moni-Pairing-Token": "ä".encode("latin-1") * 43})
    assert _detail(r) == (401, "Kopplungs-Token ungültig")
    assert not state.check_token("\ud800" * 43) and not state.check_token(None)  # einsame Surrogate werfen nicht


@pytest.mark.parametrize("cert_kind,expected", [("manager", 200), ("agent_server_cert", 401), ("other_cn", 401),
                                                ("foreign_ca", 401), ("expired", 401), ("none", 401), ("garbage", 401)])
def test_snapshot_with_client_cert(paired, ca, connect, cert_kind, expected):
    cert = {
        "manager": lambda: _client_cert(ca),
        "agent_server_cert": lambda: _client_cert(ca, usage="server"),
        "other_cn": lambda: _client_cert(ca, cn="moni-andere"),
        "foreign_ca": lambda: _client_cert(pki.create_ca(utcnow())),
        "expired": lambda: _client_cert(ca, now=utcnow() - timedelta(days=400)),
        "none": lambda: None,
        "garbage": lambda: "-----BEGIN CERTIFICATE-----\nkaputt\n-----END CERTIFICATE-----\n",
    }[cert_kind]()
    assert connect(cert).get("/api/v1/snapshot").status_code == expected


def test_manager_cert_ignored_while_unpaired(ca, connect):
    r = connect(_client_cert(ca)).get("/api/v1/snapshot")
    assert r.status_code == 401
    assert connect(_client_cert(ca)).get("/api/v1/tokens").status_code == 401


def test_bearer_still_works_when_paired(paired, ca, connect, agent_app):
    _, plain = agent_app.state.tokens.create("r", "read")
    bearer = {"Authorization": f"Bearer {plain}"}
    assert connect().get("/api/v1/snapshot", headers=bearer).status_code == 200
    assert connect().get("/api/v1/snapshot", headers={"Authorization": "Bearer falsch"}).status_code == 401
    # Zertifikat gewinnt, auch neben einem kaputten Token
    mgr = connect(_client_cert(ca))
    assert mgr.get("/api/v1/tokens", headers={"Authorization": "Bearer falsch"}).status_code == 200
    assert mgr.get("/api/v1/info", headers=bearer).json()["paired"] is True


@pytest.mark.parametrize("scope", ["read", "admin"])
def test_manager_only_routes_reject_bearer(paired, connect, agent_app, scope):
    bearer = {"Authorization": f"Bearer {agent_app.state.tokens.create('t', scope)[1]}"}
    c = connect()
    calls = [("GET", "/api/v1/tokens", None), ("POST", "/api/v1/tokens", {"name": "x"}),
             ("DELETE", "/api/v1/tokens/abcd1234", None), ("POST", "/api/v1/tls/csr", None),
             ("POST", "/api/v1/tls/cert", {"certificate": "x", "ca": "y"})]
    for method, path, body in calls:
        assert _detail(c.request(method, path, json=body, headers=bearer)) == (403, "Nur für den Manager"), path
        r = c.request(method, path, json=body)
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer", path


def test_renewal_requires_same_ca(manager, paired, ca, caplog):
    before = paired.cert_not_after
    csr = manager.post("/api/v1/tls/csr").json()["csr"].encode()
    cert = pem(pki.issue(*ca, csr, cn="moni-agent", sans=AGENT_SAN, usage="server", now=utcnow(), days=91))
    other = pki.create_ca(utcnow())
    r = manager.post("/api/v1/tls/cert", json={"certificate": cert, "ca": pem(other[1])})
    assert _detail(r) == (422, "Zertifikat passt nicht")
    crlf = pem(ca[1]).replace("\n", "\r\n") + "\n"  # Zeilenenden und Leerraum zählen nicht
    with caplog.at_level(logging.INFO):
        r = manager.post("/api/v1/tls/cert", json={"certificate": cert, "ca": crlf})
    assert r.status_code == 204 and paired.paired and paired.cert_not_after != before
    assert "Agent-Zertifikat erneuert" in caplog.text and "BEGIN" not in caplog.text
    assert manager.get("/api/v1/snapshot").status_code == 200  # gleiche CA: Manager bleibt angemeldet


def test_token_endpoints_create_read_scope_only(manager, connect):
    r = manager.post("/api/v1/tokens", json={"name": "Grafana", "expires_days": 30})
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {"id", "name", "scope", "created_at", "expires_at", "token"} and body["scope"] == "read"
    assert body["expires_at"] is not None and "hash" not in r.text
    listed = manager.get("/api/v1/tokens").json()
    assert listed == [{k: v for k, v in body.items() if k != "token"}] and body["token"] not in str(listed)
    assert connect().get("/api/v1/snapshot", headers={"Authorization": f"Bearer {body['token']}"}).status_code == 200
    assert manager.post("/api/v1/tokens", json={"name": "ohne"}).json()["expires_at"] is None
    for bad in ({"name": "x", "scope": "admin"}, {"name": ""}, {"name": "   "}, {"name": "a" * 65}, {"name": "a\x00"},
                {"name": 5}, {"name": "x", "expires_days": 0}, {"name": "x", "expires_days": 3651},
                {"name": "x", "expires_days": "5"}, {}):
        assert manager.post("/api/v1/tokens", json=bad).status_code == 422, bad
    assert manager.delete(f"/api/v1/tokens/{body['id']}").status_code == 204
    assert _detail(manager.delete(f"/api/v1/tokens/{body['id']}")) == (404, "Token nicht gefunden")
    assert connect().get("/api/v1/snapshot", headers={"Authorization": f"Bearer {body['token']}"}).status_code == 401


def test_pairing_token_bruteforce_429(agent_client, pairing_token, state):
    for i in range(10):
        hdr = {"X-Moni-Pairing-Token": f"falsch{i}"} if i % 2 else {}
        assert agent_client.post("/api/v1/tls/csr", headers=hdr).status_code == 401
    r = agent_client.post("/api/v1/tls/csr", headers={"X-Moni-Pairing-Token": pairing_token})  # auch das richtige
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert state.check_token(pairing_token)  # nicht verbraucht


def test_routes_absent_when_tls_off(client, auth):
    for method, path in (("POST", "/api/v1/tls/csr"), ("POST", "/api/v1/tls/cert"), ("GET", "/api/v1/tokens"),
                         ("POST", "/api/v1/tokens"), ("DELETE", "/api/v1/tokens/abcd1234")):
        assert client.request(method, path, headers=auth).status_code == 404, path
