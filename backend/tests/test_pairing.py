"""Kopplung Manager → Agent per Code, Fingerabdruck-Pinning und CSR (D2), gegen einen echten TLS-Agent."""
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from moni import errors, pki
from moni.auth.tokens import TokenStore
from moni.config import Settings
from moni.manager import db, pairing
from moni.manager.api import router
from moni.manager.pairing import PairingError
from moni.manager.poller import AgentClient, AgentError
from moni.manager.servers import ServerStore
from moni.secretbox import SecretBox, generate_key
from moni.timeutil import utcnow

NOW = utcnow()
NOW_TS = int(NOW.timestamp())
FP = "A" * 43
TOK = "B" * 43


@pytest.fixture
def spy(agent_tls_server, monkeypatch):
    """Zählt jede Kopplungsanfrage, die die App des Agents erreicht (erste Prüfung in _pairing) und jede CSR."""
    hits = []
    throttle, state = agent_tls_server.app.state.pairing_throttle, agent_tls_server.state
    real_retry, real_csr = throttle.retry_after, state.new_csr
    monkeypatch.setattr(throttle, "retry_after", lambda *a: hits.append("request") or real_retry(*a))
    monkeypatch.setattr(state, "new_csr", lambda: hits.append("csr") or real_csr())
    return hits


def _code_with(server, fp=None, token=None, url=None):
    _, f, t = server.code.split(":")
    return f"moni-pair-v1:{fp or f}:{token or t}" + (f":{url}" if url else "")


# --- parse_code ---------------------------------------------------------------

def test_parse_code_valid():
    assert pairing.parse_code(f"moni-pair-v1:{FP}:{TOK}") == (FP, TOK, None)
    assert pairing.parse_code(f"moni-pair-v1:{FP}:{TOK}:https://a.example:8443") == (FP, TOK, "https://a.example:8443")


@pytest.mark.parametrize("code", ["", "moni-pair-v2:x:y", "moni-pair-v1:kurz:t", "moni-pair-v1:" + "A" * 43 + ":",
                                  f"moni-pair-v1:{FP}", f"moni-pair-v1:{FP}:{TOK}:", f"moni-pair-v1:{FP}+:{TOK}",
                                  f"moni-pair-v1:{FP}:{TOK[:-1]}ä", f" moni-pair-v1:{FP}:{TOK}", None, 5])
def test_parse_code_invalid(code):
    with pytest.raises(ValueError) as e:
        pairing.parse_code(code)
    assert str(e.value) == "Kopplungscode ist ungültig" and e.value.__context__ is None


# --- pair gegen echten Agent --------------------------------------------------

def test_pair_end_to_end_against_real_tls_agent(agent_tls_server, manager_pki):
    exp = pairing.pair(agent_tls_server.url, agent_tls_server.code, manager_pki, NOW, 5)
    client = AgentClient(agent_tls_server.url, "", {}, 5, ssl_context=manager_pki.client_context())
    assert client.get("/api/v1/info")["paired"] is True and exp > NOW_TS
    cert = pki.load_cert((agent_tls_server.state.dir / "cert.pem").read_bytes())
    assert exp == int(cert.not_valid_after_utc.timestamp()) and 89 < pki.days_left(cert, NOW) <= 90
    assert (agent_tls_server.state.dir / "ca.pem").read_bytes() == manager_pki.ca_pem


def test_pair_aborts_on_wrong_fingerprint_before_sending_token(agent_tls_server, manager_pki, spy):
    code = agent_tls_server.code.replace(agent_tls_server.fp, "A" * 43)
    with pytest.raises(PairingError, match="Fingerabdruck") as e:
        pairing.pair(agent_tls_server.url, code, manager_pki, NOW, 5)
    assert e.value.message == "Fingerabdruck des Agents passt nicht zum Kopplungscode" and e.value.status == 502
    assert e.value.__context__ is None
    assert spy == []  # Agent hat keine Anfrage gesehen
    assert not agent_tls_server.state.paired
    pairing.pair(agent_tls_server.url, agent_tls_server.code, manager_pki, NOW, 5)  # Token ist noch gültig
    assert spy == ["request", "csr", "request"]


def test_pair_wrong_token_401_and_second_pair_409(agent_tls_server, manager_pki, spy):
    with pytest.raises(PairingError) as e:
        pairing.pair(agent_tls_server.url, _code_with(agent_tls_server, token=TOK), manager_pki, NOW, 5)
    assert (e.value.status, e.value.message) == (401, "Kopplungs-Token ungültig") and e.value.__context__ is None
    assert TOK not in repr(e.value) and spy == ["request"]
    pairing.pair(agent_tls_server.url, agent_tls_server.code, manager_pki, NOW, 5)
    # gekoppelt: neues Zertifikat, also passenden Fingerabdruck einsetzen, damit die Anfrage den Agent erreicht
    new_fp = pki.fingerprint(pki.load_cert((agent_tls_server.state.dir / "cert.pem").read_bytes()))
    with pytest.raises(PairingError) as e:
        pairing.pair(agent_tls_server.url, _code_with(agent_tls_server, fp=new_fp), manager_pki, NOW, 5)
    assert (e.value.status, e.value.message) == (409, "Agent ist bereits gekoppelt")


def test_pair_no_reconnect_after_connection_close(agent_tls_server, manager_pki, spy, monkeypatch):
    """Schließt der Agent nach /tls/csr die Verbindung, gibt es keine neue, ungeprüfte Verbindung für /tls/cert."""
    import http.client
    real_begin, real_connect, connects = http.client.HTTPResponse.begin, http.client.HTTPSConnection.connect, []

    def begin(self):
        real_begin(self)
        self.will_close = True  # wie "Connection: close" vom Agent

    monkeypatch.setattr(http.client.HTTPResponse, "begin", begin)
    monkeypatch.setattr(http.client.HTTPSConnection, "connect", lambda self: connects.append(1) or real_connect(self))
    with pytest.raises(PairingError) as e:
        pairing.pair(agent_tls_server.url, agent_tls_server.code, manager_pki, NOW, 5)
    assert (e.value.status, e.value.message) == (502, "Agent nicht erreichbar") and e.value.__context__ is None
    assert spy == ["request", "csr"] and connects == [1]  # /tls/cert hat keinen Peer erreicht, kein zweiter Handshake
    assert not agent_tls_server.state.paired


def test_pair_unreachable_agent(manager_pki):
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))  # gebunden, aber kein listen
        with pytest.raises(PairingError) as e:
            pairing.pair(f"https://127.0.0.1:{s.getsockname()[1]}", f"moni-pair-v1:{FP}:{TOK}", manager_pki, NOW, 0.5)
    assert (e.value.status, e.value.message) == (502, "Agent nicht erreichbar") and e.value.__context__ is None


def test_mtls_server_uses_no_bearer_and_checks_hostname(agent_tls_server, manager_pki):
    pairing.pair(agent_tls_server.url, agent_tls_server.code, manager_pki, NOW, 5)

    @agent_tls_server.app.get("/_test/auth")
    def auth_header(request: Request):
        return {"authorization": request.headers.get("authorization")}

    ctx = manager_pki.client_context()
    assert AgentClient(agent_tls_server.url, "", {}, 5, ssl_context=ctx).get("/_test/auth") == {"authorization": None}
    port = urlsplit(agent_tls_server.url).port
    with pytest.raises(AgentError) as e:  # SAN ist 127.0.0.1, nicht localhost
        AgentClient(f"https://localhost:{port}", "", {}, 5, ssl_context=ctx).get("/api/v1/info")
    assert (e.value.message, e.value.fatal) == ("TLS-Prüfung des Agents fehlgeschlagen", True)
    assert e.value.__context__ is None
    with pytest.raises(AgentError) as e:  # Token-Modus mit https: System-Vertrauensspeicher kennt die Moni-CA nicht
        AgentClient(agent_tls_server.url, "t", {}, 5).get("/api/v1/info")
    assert (e.value.message, e.value.fatal) == ("TLS-Prüfung des Agents fehlgeschlagen", True)


# --- API ----------------------------------------------------------------------

@pytest.fixture
def api(tmp_path, manager_pki):
    d = tmp_path / "mgr"
    s = Settings.from_env({"MONI_DATA_DIR": str(d), "MONI_ROLE": "manager", "MONI_TLS": "off", "MONI_POLL_TIMEOUT": "5",
                           "MONI_SECRET_KEY": generate_key()})
    db.migrate(s.db_path)
    a = FastAPI()
    a.state.settings, a.state.tokens = s, TokenStore(d / "tokens.json")
    a.state.box, a.state.pki = SecretBox(generate_key()), manager_pki
    errors.install(a)
    a.include_router(router)
    c = TestClient(a)
    c.headers["Authorization"] = f"Bearer {a.state.tokens.create('a', 'admin')[1]}"
    return c


def _audit(api):
    conn = db.connect(api.app.state.settings.db_path)
    try:
        return [json.loads(r[0]) for r in conn.execute("SELECT details FROM audit_log WHERE action='server.added'")]
    finally:
        conn.close()


def test_api_add_by_pairing_code_and_by_token(api, agent_tls_server, caplog):
    caplog.set_level(logging.DEBUG)
    code = _code_with(agent_tls_server, url=agent_tls_server.url)  # URL steckt im Code
    r = api.post("/api/v1/servers", json={"name": "pi", "pairing_code": code})
    assert r.status_code == 201, r.text
    d = r.json()
    assert (d["security"], d["url"]) == ("mtls", agent_tls_server.url)
    exp = datetime.fromisoformat(d["cert_not_after"])
    assert exp.tzinfo is not None and exp > datetime.now(timezone.utc)
    assert agent_tls_server.state.paired
    r = api.post("/api/v1/servers", json={"name": "sau", "url": "https://sau.example", "token": "geheim-t"})
    assert r.status_code == 201 and r.json()["security"] == "token" and r.json()["cert_not_after"] is None
    assert "Server sau ohne mTLS angelegt (schwächere Variante, nur hinter vertrauenswürdigem Proxy)" in [
        rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING]
    assert "Server pi ohne mTLS" not in caplog.text
    assert _audit(api) == [{"url": agent_tls_server.url, "security": "mtls", "header_names": []},
                           {"url": "https://sau.example", "security": "token", "header_names": []}]
    listed = api.get("/api/v1/servers").text
    secret = code.split(":")[2]
    for text in (listed, caplog.text, json.dumps(_audit(api))):
        assert secret not in text and "geheim-t" not in text


def test_api_pairing_with_url_in_body(api, agent_tls_server):
    r = api.post("/api/v1/servers", json={"name": "pi", "pairing_code": agent_tls_server.code, "url": agent_tls_server.url})
    assert r.status_code == 201 and r.json()["security"] == "mtls"


def test_api_pairing_url_compare_ignores_trailing_slash(api, agent_tls_server):  # P4-R4b
    code = _code_with(agent_tls_server, url=agent_tls_server.url)
    r = api.post("/api/v1/servers", json={"name": "pi", "pairing_code": code, "url": agent_tls_server.url + "/"})
    assert r.status_code == 201, r.text


_EITHER = "Entweder token oder pairing_code angeben"


@pytest.mark.parametrize("make,detail", [
    (lambda s: {"name": "x", "pairing_code": s.code, "token": "t", "url": s.url}, _EITHER),
    (lambda s: {"name": "x", "url": s.url}, _EITHER),
    (lambda s: {"name": "x", "pairing_code": s.code, "url": s.url, "headers": {"A": "b"}}, _EITHER),
    (lambda s: {"name": "x", "pairing_code": s.code + ":https://other.example:1", "url": s.url},
     "URL im Kopplungscode passt nicht"),
    (lambda s: {"name": "x", "pairing_code": s.code}, "URL fehlt"),
    (lambda s: {"name": "x", "pairing_code": s.code, "url": s.url + "/moni"},
     "URL darf für gekoppelte Server keinen Pfad haben"),
    (lambda s: {"name": "x", "pairing_code": s.code + ":" + s.url + "/a/"}, "URL darf für gekoppelte Server keinen Pfad haben"),
    (lambda s: {"name": "x", "pairing_code": s.code, "url": "http://127.0.0.1:1"}, "mTLS braucht eine https://-URL"),
    (lambda s: {"name": "x", "pairing_code": s.code + ":http://127.0.0.1:1"}, "mTLS braucht eine https://-URL"),
    (lambda s: {"name": "x", "pairing_code": "kaputt", "url": s.url}, "Kopplungscode ist ungültig"),
    (lambda s: {"name": "", "pairing_code": s.code, "url": s.url}, "Name muss 1 bis 64 Zeichen lang sein"),
    (lambda s: {"name": "x", "token": "t"}, "URL fehlt"),
])
def test_api_body_shapes(api, agent_tls_server, spy, make, detail):
    r = api.post("/api/v1/servers", json=make(agent_tls_server))
    assert (r.status_code, r.json()["detail"]) == (422, detail)
    assert spy == [] and not agent_tls_server.state.paired
    assert agent_tls_server.code.split(":")[2] not in r.text


@pytest.mark.parametrize("body", [
    {"name": "x", "pairing_code": "x" * 513, "url": "https://a"},
    {"name": "x", "pairing_code": 5, "url": "https://a"},
    {"name": "x", "pairing_code": f"moni-pair-v1:{FP}:{TOK}", "url": "https://a", "extra": 1},
])
def test_api_rejects_bad_pairing_fields(api, body):
    r = api.post("/api/v1/servers", json=body)
    assert r.status_code == 422 and TOK not in r.text


def test_api_pairing_errors_map_to_status(api, agent_tls_server, spy):
    bad_fp = agent_tls_server.code.replace(agent_tls_server.fp, FP)
    r = api.post("/api/v1/servers", json={"name": "pi", "pairing_code": bad_fp, "url": agent_tls_server.url})
    assert (r.status_code, r.json()["detail"]) == (502, "Fingerabdruck des Agents passt nicht zum Kopplungscode")
    assert spy == []
    r = api.post("/api/v1/servers", json={"name": "pi", "pairing_code": _code_with(agent_tls_server, token=TOK),
                                          "url": agent_tls_server.url})
    assert (r.status_code, r.json()["detail"]) == (401, "Kopplungs-Token ungültig") and TOK not in r.text
    assert api.get("/api/v1/servers").json() == [] and _audit(api) == []


def test_api_pairing_refuses_taken_name_before_contacting_agent(api, agent_tls_server, spy):
    assert api.post("/api/v1/servers", json={"name": "pi", "url": "https://a", "token": "t"}).status_code == 201
    r = api.post("/api/v1/servers", json={"name": "pi", "pairing_code": agent_tls_server.code, "url": agent_tls_server.url})
    assert (r.status_code, r.json()["detail"]) == (422, "Name ist bereits vergeben") and spy == []


# --- lokale Fehler und Kontrollabruf (P4-R4c/d) --------------------------------

def test_unreadable_manager_keys_fail_before_contacting_agent(api, agent_tls_server, manager_pki, spy):
    (manager_pki.dir / "ca.key.enc").write_text("kaputt")
    with pytest.raises(PairingError) as e:
        pairing.pair(agent_tls_server.url, agent_tls_server.code, manager_pki, NOW, 5)
    assert (e.value.status, e.value.message) == (500, "Manager-Zertifikate nicht lesbar") and e.value.__context__ is None
    r = api.post("/api/v1/servers", json={"name": "pi", "pairing_code": agent_tls_server.code, "url": agent_tls_server.url})
    assert (r.status_code, r.json()["detail"]) == (500, "Manager-Zertifikate nicht lesbar")
    assert spy == [] and not agent_tls_server.state.paired


_PAIRED_BUT = ("Agent wurde gekoppelt, antwortet aber nicht wie erwartet – am Agent 'python -m moni.cli pair reset' "
               "ausführen und neu koppeln")


@pytest.mark.parametrize("answer,reason", [
    (AgentError("TLS-Prüfung des Agents fehlgeschlagen", fatal=True), "TLS-Prüfung des Agents fehlgeschlagen"),
    (AgentError("Agent lehnt Token ab", fatal=True), "TLS-Prüfung des Agents fehlgeschlagen"),
    (AgentError("Agent antwortet mit HTTP 500"), "Agent nicht erreichbar"),
    ({"paired": False}, "Agent bestätigt die Kopplung nicht"),
])
def test_failed_control_call_after_pairing_hints_reset(api, agent_tls_server, monkeypatch, caplog, answer, reason):
    class Control:
        def __init__(self, *a, **kw):
            pass

        def get(self, path):
            if isinstance(answer, Exception):
                raise answer
            return answer
    monkeypatch.setattr(pairing, "AgentClient", Control)
    r = api.post("/api/v1/servers", json={"name": "pi", "pairing_code": agent_tls_server.code, "url": agent_tls_server.url})
    assert (r.status_code, r.json()["detail"]) == (502, _PAIRED_BUT)
    assert agent_tls_server.state.paired and api.get("/api/v1/servers").json() == []
    assert f"Kontrollabruf nach der Kopplung fehlgeschlagen: {reason}" in [
        rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING"]


# --- Agent-Tokens über den Manager (D9) ---------------------------------------

def _pair_via_api(api, agent):
    r = api.post("/api/v1/servers", json={"name": "pi", "pairing_code": agent.code, "url": agent.url})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _token_audit(api):
    conn = db.connect(api.app.state.settings.db_path)
    try:
        return [(r[0], r[1], json.loads(r[2])) for r in
                conn.execute("SELECT action, target, details FROM audit_log WHERE action LIKE 'agent_token.%' ORDER BY id")]
    finally:
        conn.close()


def test_agent_tokens_via_manager(api, agent_tls_server, caplog):
    caplog.set_level(logging.DEBUG)
    sid = _pair_via_api(api, agent_tls_server)
    before = {t.id for t in agent_tls_server.app.state.tokens.list()}
    r = api.post(f"/api/v1/servers/{sid}/agent-tokens", json={"name": "grafana", "expires_days": 30})
    assert r.status_code == 201, r.text
    d = r.json()
    plain = d["token"]
    assert set(d) == {"id", "name", "scope", "created_at", "expires_at", "token"}
    assert (d["name"], d["scope"]) == ("grafana", "read") and d["expires_at"] and plain.startswith(f"moni_{d['id']}_")
    assert agent_tls_server.app.state.tokens.verify(plain).id == d["id"]  # gilt am Agent
    listed = api.get(f"/api/v1/servers/{sid}/agent-tokens")
    assert listed.status_code == 200
    mine = [t for t in listed.json() if t["id"] not in before]
    assert mine == [{k: d[k] for k in ("id", "name", "scope", "created_at", "expires_at")}]
    assert api.delete(f"/api/v1/servers/{sid}/agent-tokens/{d['id']}").status_code == 204
    assert d["id"] not in {t["id"] for t in api.get(f"/api/v1/servers/{sid}/agent-tokens").json()}
    assert agent_tls_server.app.state.tokens.verify(plain) is None
    assert _token_audit(api) == [("agent_token.created", "pi", {"token_id": d["id"]}),
                                 ("agent_token.revoked", "pi", {"token_id": d["id"]})]
    for text in (listed.text, caplog.text, json.dumps(_token_audit(api))):
        assert plain not in text and plain.split("_", 2)[2] not in text


def test_agent_token_routes_validate_and_map_agent_errors(api, agent_tls_server, manager_pki):
    import socket
    sid = _pair_via_api(api, agent_tls_server)
    base = f"/api/v1/servers/{sid}/agent-tokens"
    for bad in ({"name": "x", "scope": "admin"}, {"name": ""}, {"name": "x", "expires_days": 0}, {}):
        assert api.post(base, json=bad).status_code == 422
    for bad_id in ("ABCDEF12", "abc", "abcdef123", "abcdefg1"):
        assert api.delete(f"{base}/{bad_id}").status_code == 422, bad_id
    assert api.get("/api/v1/servers/999/agent-tokens").status_code == 404
    r = api.delete(f"{base}/0000abcd")  # am Agent unbekannt
    assert (r.status_code, r.json()["detail"]) == (404, "Agent-Token nicht gefunden")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]  # nach dem Schließen: Verbindung wird sofort abgelehnt
    conn = db.connect(api.app.state.settings.db_path)
    try:
        gone = ServerStore(conn, api.app.state.box).add_mtls("weg", f"https://127.0.0.1:{closed_port}", NOW_TS).id
    finally:
        conn.close()
    for method, path, body in (("GET", "", None), ("POST", "", {"name": "x"}), ("DELETE", "/0000abcd", None)):
        r = api.request(method, f"/api/v1/servers/{gone}/agent-tokens{path}", json=body)
        assert (r.status_code, r.json()["detail"]) == (502, "Agent nicht erreichbar"), method
    assert _token_audit(api) == []
    read_tok = api.app.state.tokens.create("r", "read")[1]
    r = api.get(base, headers={"Authorization": f"Bearer {read_tok}"})
    assert r.status_code == 403


def test_agent_tokens_rejected_for_token_servers(api, spy):
    sid = api.post("/api/v1/servers", json={"name": "sau", "url": "https://sau.example", "token": "t"}).json()["id"]
    base = f"/api/v1/servers/{sid}/agent-tokens"
    for method, path, body in (("GET", "", None), ("POST", "", {"name": "x"}), ("DELETE", "/0000abcd", None)):
        r = api.request(method, base + path, json=body)
        assert (r.status_code, r.json()["detail"]) == (422, "Nur für gekoppelte Server"), method
    assert spy == [] and _token_audit(api) == []


def test_pairing_total_deadline(agent_tls_server, manager_pki, monkeypatch):
    """Jeder Schritt bleibt knapp unter dem Socket-Timeout, zusammen überschreiten sie die Gesamtfrist (3 x Timeout)."""
    t = [0.0]
    monkeypatch.setattr(pairing, "_clock", lambda: t[0])
    real_post = pairing._post

    def slow(*a, **kw):
        t[0] += 1.9
        return real_post(*a, **kw)
    monkeypatch.setattr(pairing, "_post", slow)
    with pytest.raises(PairingError) as e:
        pairing.pair(agent_tls_server.url, agent_tls_server.code, manager_pki, NOW, 1)
    assert e.value.status == 504


def test_agent_token_404_via_status(api, agent_tls_server, monkeypatch):
    from moni.manager.poller import AgentClient, AgentError
    sid = _pair_via_api(api, agent_tls_server)

    def gone(self, path):
        raise AgentError("beliebiger Text", status=404)
    monkeypatch.setattr(AgentClient, "delete", gone)
    r = api.delete(f"/api/v1/servers/{sid}/agent-tokens/0000abcd")
    assert (r.status_code, r.json()["detail"]) == (404, "Agent-Token nicht gefunden")
