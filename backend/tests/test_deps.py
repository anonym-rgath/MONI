import pytest
from fastapi import Depends, Request

from moni.auth.users import UserStore
from moni.manager import db

NEW_SERVER = {"name": "a", "url": "https://a", "token": "t"}


def test_viewer_session_reads_but_cannot_write(mgr_client, make_user, login_cookie):
    cookie, csrf = login_cookie(make_user("viewer"))
    mgr_client.cookies.set("moni_session", cookie)
    assert mgr_client.get("/api/v1/servers").status_code == 200
    r = mgr_client.post("/api/v1/servers", json=NEW_SERVER, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403 and r.json()["detail"] == "Keine Berechtigung"


def test_admin_session_needs_csrf(mgr_client, make_user, login_cookie):
    cookie, csrf = login_cookie(make_user("admin"))
    mgr_client.cookies.set("moni_session", cookie)
    for h in ({}, {"X-CSRF-Token": ""}, {"X-CSRF-Token": "falsch"}, {"X-CSRF-Token": "ä".encode("latin-1")}):
        r = mgr_client.post("/api/v1/servers", json=NEW_SERVER, headers=h)
        assert r.status_code == 403 and r.json()["detail"] == "CSRF-Token fehlt oder ist ungültig" and csrf not in r.text
    assert mgr_client.post("/api/v1/servers", json=NEW_SERVER, headers={"X-CSRF-Token": csrf}).status_code == 201


def test_bearer_needs_no_csrf(mgr_client, mgr_app):
    h = {"Authorization": f"Bearer {mgr_app.state.tokens.create('a', 'admin')[1]}"}
    assert mgr_client.post("/api/v1/servers", json=NEW_SERVER, headers=h).status_code == 201


def test_read_token_on_manager_keeps_token_text(mgr_client, mgr_app):
    h = {"Authorization": f"Bearer {mgr_app.state.tokens.create('r', 'read')[1]}"}
    r = mgr_client.post("/api/v1/servers", json=NEW_SERVER, headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "Token hat nicht den nötigen Scope"


@pytest.mark.parametrize("stage", ["mfa", "enroll"])
def test_pre_auth_session_has_no_access(mgr_client, make_user, login_cookie, stage):
    mgr_client.cookies.set("moni_session", login_cookie(make_user("admin"), stage)[0])
    r = mgr_client.get("/api/v1/servers")
    assert r.status_code == 401 and r.json()["detail"] == "Nicht angemeldet"


@pytest.mark.parametrize("cookie", [None, "unbekannt"])
def test_no_or_unknown_cookie_401(mgr_client, cookie):
    if cookie:
        mgr_client.cookies.set("moni_session", cookie)
    r = mgr_client.get("/api/v1/servers")
    assert r.status_code == 401 and r.json()["detail"] == "Nicht angemeldet"


def test_disabled_or_demoted_user_effective_immediately(mgr_client, mgr_app, make_user, login_cookie):
    make_user("admin")  # bleibt aktiver Admin, damit Herabstufen erlaubt ist
    demoted, disabled = make_user("admin"), make_user("viewer")
    c_dem, csrf_dem = login_cookie(demoted)
    c_dis, _ = login_cookie(disabled)
    conn = db.connect(mgr_app.state.settings.db_path)
    try:
        UserStore(conn, mgr_app.state.box).update(demoted.id, role="viewer")
        UserStore(conn, mgr_app.state.box).update(disabled.id, disabled=True)
    finally:
        conn.close()
    mgr_client.cookies.set("moni_session", c_dis)
    assert mgr_client.get("/api/v1/servers").status_code == 401
    mgr_client.cookies.set("moni_session", c_dem)
    assert mgr_client.get("/api/v1/servers").status_code == 200
    r = mgr_client.post("/api/v1/servers", json=NEW_SERVER, headers={"X-CSRF-Token": csrf_dem})
    assert r.status_code == 403 and r.json()["detail"] == "Keine Berechtigung"


@pytest.mark.parametrize("header", ["Bearer", "Bearer ", "", "Basic abc"])
def test_bad_authorization_header_does_not_fall_back_to_cookie(mgr_client, make_user, login_cookie, header):
    mgr_client.cookies.set("moni_session", login_cookie(make_user("admin"))[0])
    r = mgr_client.get("/api/v1/servers", headers={"Authorization": header})
    assert r.status_code == 401 and r.json()["detail"] == "Token fehlt oder ist ungültig"


def test_agent_ignores_session_cookie(client):
    client.cookies.set("moni_session", "irgendwas")
    r = client.get("/api/v1/info")
    assert r.status_code == 401 and r.json()["detail"] == "Token fehlt oder ist ungültig"


def test_principal_from_session_and_token(mgr_client, mgr_app, make_user, login_cookie):
    from moni.auth.deps import Principal, require_scope

    @mgr_app.get("/_who")
    def who(p: Principal = Depends(require_scope("read"))):
        assert "csrf" not in repr(p) and "id_hash" not in repr(p)
        return {"scope": p.scope, "actor": p.actor, "user_id": p.user_id, "session": p.session is not None}

    u = make_user("admin", email="chef@example.org")
    mgr_client.cookies.set("moni_session", login_cookie(u)[0])
    assert mgr_client.get("/_who").json() == {"scope": "admin", "actor": "user:chef@example.org", "user_id": u.id, "session": True}
    mgr_client.cookies.clear()
    tok, plain = mgr_app.state.tokens.create("r", "read")
    assert mgr_client.get("/_who", headers={"Authorization": f"Bearer {plain}"}).json() == {
        "scope": "read", "actor": f"token:{tok.id}", "user_id": None, "session": False}


def test_require_stage(mgr_client, mgr_app, make_user, login_cookie):
    from moni.auth.deps import require_stage

    @mgr_app.post("/_mfa")
    def mfa(st=Depends(require_stage("mfa", "enroll"))):
        return {"stage": st[0].stage, "user": st[1].id}

    u = make_user("viewer")
    cookie, csrf = login_cookie(u, "mfa")
    mgr_client.cookies.set("moni_session", cookie)
    assert mgr_client.post("/_mfa").status_code == 403
    assert mgr_client.post("/_mfa", headers={"X-CSRF-Token": csrf}).json() == {"stage": "mfa", "user": u.id}
    mgr_client.cookies.set("moni_session", login_cookie(u, "full")[0])
    assert mgr_client.post("/_mfa", headers={"X-CSRF-Token": csrf}).status_code == 401
    mgr_client.cookies.clear()
    h = {"Authorization": f"Bearer {mgr_app.state.tokens.create('a', 'admin')[1]}"}
    r = mgr_client.post("/_mfa", headers=h)
    assert r.status_code == 401 and r.json()["detail"] == "Nicht angemeldet"


def test_locked_db_during_session_lookup_is_503(mgr_client, make_user, login_cookie, monkeypatch):
    import sqlite3

    from moni.auth.sessions import SessionStore
    mgr_client.cookies.set("moni_session", login_cookie(make_user("admin"))[0])
    monkeypatch.setattr(SessionStore, "get", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    r = mgr_client.get("/api/v1/servers")
    assert r.status_code == 503 and r.headers["Retry-After"] == "5"


def test_client_ip(mgr_client, mgr_app):
    from moni.auth.deps import client_ip

    @mgr_app.get("/_ip")
    def ip(request: Request):
        return {"ip": client_ip(request)}

    assert mgr_client.get("/_ip").json() == {"ip": "testclient"}


def test_manager_cert_only_on_paired_agent(mgr_app, client):
    from moni import pki
    from moni.auth.deps import manager_cert
    from moni.timeutil import utcnow
    ca = pki.create_ca(utcnow())
    key = pki.new_key()
    cert = pki.issue(*ca, pki.make_csr(key, "moni-manager"), cn="moni-manager", sans=["moni-manager"], usage="client",
                     now=utcnow(), days=365)
    scope = {"type": "http", "headers": [], "extensions": {"tls": {"client_cert_chain": [pki.cert_pem(cert).decode()]}}}
    for app in (mgr_app, client.app):  # Manager-Rolle und Agent mit MONI_TLS=off
        assert manager_cert(Request({**scope, "app": app})) is False
