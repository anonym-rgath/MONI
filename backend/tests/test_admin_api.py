import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_PASSWORD, code_at  # noqa: F401
from moni.auth import audit
from moni.auth.sessions import COOKIE, SessionStore
from moni.auth.users import UserStore
from moni.manager import db

LAST_ADMIN = "Der letzte aktive Admin kann nicht entfernt, herabgestuft oder deaktiviert werden"


def _client(app, user, login_cookie):
    plain, csrf = login_cookie(user)
    c = TestClient(app, base_url="https://testserver")
    c.cookies.set(COOKIE, plain)
    return c, {"X-CSRF-Token": csrf}


@pytest.fixture
def admin_user(make_user):
    return make_user("admin")


@pytest.fixture
def admin_pair(mgr_app, admin_user, login_cookie):
    return _client(mgr_app, admin_user, login_cookie)


@pytest.fixture
def admin_client(admin_pair):
    return admin_pair[0]


@pytest.fixture
def csrf(admin_pair):
    return {"X-CSRF-Token": admin_pair[1]["X-CSRF-Token"]}


@pytest.fixture
def viewer_client(mgr_app, make_user, login_cookie):
    return _client(mgr_app, make_user("viewer"), login_cookie)[0]


def _audit(app, **kw):
    conn = db.connect(app.state.settings.db_path)
    try:
        return audit.entries(conn, **kw)
    finally:
        conn.close()


def _post_user(c, csrf, email="b@b.de", role="viewer", **extra):
    return c.post("/api/v1/users", json={"email": email, "role": role, "password": "x" * 12, **extra}, headers=csrf)


@pytest.mark.parametrize("path", ["/api/v1/users", "/api/v1/tokens", "/api/v1/audit"])
def test_viewer_cannot_see_admin_lists(viewer_client, path):
    assert viewer_client.get(path).status_code == 403


@pytest.mark.parametrize("path", ["/api/v1/users", "/api/v1/tokens", "/api/v1/audit"])
def test_read_token_cannot_see_admin_lists(mgr_app, mgr_client, path):
    _, plain = mgr_app.state.tokens.create("r", "read")
    r = mgr_client.get(path, headers={"Authorization": f"Bearer {plain}"})
    assert r.status_code == 403 and r.json()["detail"] == "Token hat nicht den nötigen Scope"


def test_user_crud_and_audit(admin_client, csrf):
    r = _post_user(admin_client, csrf)
    assert r.status_code == 201 and "password" not in r.text and "argon2" not in r.text
    body = r.json()
    assert set(body) == {"id", "email", "name", "role", "kind", "totp_enabled", "disabled", "locked", "oidc_bound",
                         "created_at", "last_login"}
    assert body["locked"] is False and body["created_at"].endswith("Z") and body["last_login"] is None
    uid = body["id"]
    assert admin_client.patch(f"/api/v1/users/{uid}", json={"role": "admin"}, headers=csrf).json()["role"] == "admin"
    entries = admin_client.get("/api/v1/audit").json()["entries"]
    assert [e["action"] for e in entries[:2]] == ["user.updated", "user.created"]
    assert entries[0]["details"] == {"role": "admin"} and entries[1]["details"] == {"role": "viewer", "kind": "local"}
    assert entries[1]["target"] == "b@b.de" and entries[1]["actor"].startswith("user:")
    assert "x" * 12 not in str(entries)
    assert len(admin_client.get("/api/v1/users").json()) == 2
    assert admin_client.delete(f"/api/v1/users/{uid}", headers=csrf).status_code == 204
    assert admin_client.delete(f"/api/v1/users/{uid}", headers=csrf).status_code == 404
    assert admin_client.get("/api/v1/audit").json()["entries"][0]["action"] == "user.deleted"


def test_create_user_validation(admin_client, csrf):
    assert _post_user(admin_client, csrf).status_code == 201
    assert _post_user(admin_client, csrf).status_code == 409
    assert _post_user(admin_client, csrf, email="c@c.de", role="root").status_code == 422
    assert _post_user(admin_client, csrf, email="c@c.de", password="kurz").status_code == 422
    assert admin_client.post("/api/v1/users", json={"email": "c@c.de", "role": "viewer"}, headers=csrf).status_code == 422
    assert _post_user(admin_client, csrf, email="c@c.de", kind="oidc").status_code == 422
    r = admin_client.post("/api/v1/users", json={"email": "o@c.de", "role": "viewer", "kind": "oidc"}, headers=csrf)
    assert r.status_code == 201 and r.json()["kind"] == "oidc"


def test_last_admin_api_409(admin_client, csrf, admin_user):
    url = f"/api/v1/users/{admin_user.id}"
    for r in (admin_client.delete(url, headers=csrf),
              admin_client.patch(url, json={"role": "viewer"}, headers=csrf),
              admin_client.patch(url, json={"disabled": True}, headers=csrf)):
        assert r.status_code == 409 and r.json()["detail"] == LAST_ADMIN
    assert admin_client.patch(url, json={"name": "Neu"}, headers=csrf).json()["name"] == "Neu"


def test_patch_rejects_unknown_and_empty(admin_client, csrf, admin_user):
    url = f"/api/v1/users/{admin_user.id}"
    assert admin_client.patch(url, json={"email": "x@y.de"}, headers=csrf).status_code == 422
    assert admin_client.patch(url, json={}, headers=csrf).status_code == 422
    assert admin_client.patch(url, json={"role": "root"}, headers=csrf).status_code == 422
    assert admin_client.patch("/api/v1/users/9999", json={"name": "x"}, headers=csrf).status_code == 404


def _sessions(app, user_id):
    conn = db.connect(app.state.settings.db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (user_id,)).fetchone()[0]
    finally:
        conn.close()


def test_disable_ends_sessions(mgr_app, admin_client, csrf, make_user, login_cookie):
    other = make_user("viewer")
    login_cookie(other), login_cookie(other)
    assert _sessions(mgr_app, other.id) == 2
    assert admin_client.patch(f"/api/v1/users/{other.id}", json={"disabled": True}, headers=csrf).json()["disabled"] is True
    assert _sessions(mgr_app, other.id) == 0
    assert _audit(mgr_app)[0]["details"] == {"disabled": True}


def test_password_reset_2fa_unlock(mgr_app, admin_client, csrf, make_user, login_cookie):
    u = make_user("viewer")
    login_cookie(u)
    conn = db.connect(mgr_app.state.settings.db_path)
    store = UserStore(conn, mgr_app.state.box)
    store.begin_totp(u.id)
    conn.execute("UPDATE users SET locked_until=?, failed_logins=5 WHERE id=?", (int(time.time()) + 600, u.id))
    conn.commit()
    assert any(x["locked"] for x in admin_client.get("/api/v1/users").json())
    base = f"/api/v1/users/{u.id}"
    assert admin_client.post(f"{base}/password", json={"password": "kurz"}, headers=csrf).status_code == 422
    assert admin_client.post(f"{base}/password", json={"password": "Ein-ganz-neues-Passwort"}, headers=csrf).status_code == 204
    assert _sessions(mgr_app, u.id) == 0
    assert store.check_password(u.id, "Ein-ganz-neues-Passwort")
    login_cookie(u)
    assert admin_client.post(f"{base}/reset-2fa", headers=csrf).status_code == 204
    assert _sessions(mgr_app, u.id) == 0 and not store.get(u.id).totp_enabled
    assert admin_client.post(f"{base}/unlock", headers=csrf).status_code == 204
    assert store.get(u.id).locked_until is None
    conn.close()
    acts = [(e["action"], e["target"]) for e in _audit(mgr_app)[:3]]
    assert acts == [("user.unlocked", u.email), ("totp.reset", u.email), ("password.reset", u.email)]
    assert "Passwort" not in str(_audit(mgr_app))
    assert admin_client.post("/api/v1/users/9999/unlock", headers=csrf).status_code == 404
    assert admin_client.post("/api/v1/users/9999/reset-2fa", headers=csrf).status_code == 404


def _locked(*a, **kw):
    raise sqlite3.OperationalError("database is locked")


@pytest.mark.parametrize("case", ["password", "reset-2fa", "disable"])
def test_credential_change_and_session_end_are_atomic(mgr_app, admin_client, csrf, make_user, login_cookie,
                                                      monkeypatch, case):
    u = make_user("viewer")
    conn = db.connect(mgr_app.state.settings.db_path)
    store = UserStore(conn, mgr_app.state.box)
    conn.execute("UPDATE users SET totp_enc=? WHERE id=?", (mgr_app.state.box.encrypt("JBSWY3DPEHPK3PXP"), u.id))
    conn.commit()
    login_cookie(u)
    n = len(_audit(mgr_app))
    monkeypatch.setattr(SessionStore, "delete_for_user", _locked)
    base = f"/api/v1/users/{u.id}"
    r = {"password": lambda: admin_client.post(f"{base}/password", json={"password": "Ein-ganz-neues-Passwort"}, headers=csrf),
         "reset-2fa": lambda: admin_client.post(f"{base}/reset-2fa", headers=csrf),
         "disable": lambda: admin_client.patch(base, json={"disabled": True}, headers=csrf)}[case]()
    assert r.status_code == 503
    after = store.get(u.id)
    assert store.check_password(u.id, TEST_PASSWORD) and after.totp_enabled and not after.disabled
    conn.close()
    assert _sessions(mgr_app, u.id) == 1 and len(_audit(mgr_app)) == n


def test_password_and_2fa_only_for_local(admin_client, csrf):
    uid = admin_client.post("/api/v1/users", json={"email": "o@c.de", "role": "viewer", "kind": "oidc"},
                            headers=csrf).json()["id"]
    assert admin_client.post(f"/api/v1/users/{uid}/password", json={"password": "Ein-ganz-neues-Passwort"},
                             headers=csrf).status_code == 422
    assert admin_client.post(f"/api/v1/users/{uid}/reset-2fa", headers=csrf).status_code == 422


def test_token_api_shows_plaintext_once(mgr_app, admin_client, csrf):
    r = admin_client.post("/api/v1/tokens", json={"name": "grafana", "expires_days": 30}, headers=csrf)
    assert r.status_code == 201
    plain = r.json()["token"]
    assert plain.startswith("moni_") and plain not in admin_client.get("/api/v1/tokens").text
    lst = admin_client.get("/api/v1/tokens").json()
    assert "hash" not in lst[0] and set(lst[0]) == {"id", "name", "scope", "created_at", "expires_at"}
    assert lst[0]["expires_at"] is not None and r.json()["scope"] == "read"
    e = _audit(mgr_app)[0]
    assert e["action"] == "token.created" and e["details"] == {"scope": "read"} and plain not in str(e)
    assert admin_client.delete(f"/api/v1/tokens/{lst[0]['id']}", headers=csrf).status_code == 204
    assert admin_client.delete(f"/api/v1/tokens/{lst[0]['id']}", headers=csrf).status_code == 404
    assert _audit(mgr_app)[0]["action"] == "token.revoked" and admin_client.get("/api/v1/tokens").json() == []


@pytest.mark.parametrize("body", [{"name": ""}, {"name": "x" * 65}, {"name": "a\nb"}, {"name": "   "},
                                  {"name": "a", "scope": "root"}, {"name": "a", "expires_days": 0},
                                  {"name": "a", "expires_days": 3651}, {"name": "a", "extra": 1}])
def test_token_validation(admin_client, csrf, body):
    assert admin_client.post("/api/v1/tokens", json=body, headers=csrf).status_code == 422


def test_audit_paging_and_limits(mgr_app, admin_client):
    conn = db.connect(mgr_app.state.settings.db_path)
    for i in range(5):
        audit.record(conn, "x", f"a{i}")
    conn.close()
    for q in ("limit=0", "limit=1001", "before=0"):
        assert admin_client.get(f"/api/v1/audit?{q}").status_code == 422
    p1 = admin_client.get("/api/v1/audit?limit=2").json()
    assert [e["action"] for e in p1["entries"]] == ["a4", "a3"] and p1["next_before"] == p1["entries"][-1]["id"]
    p3 = admin_client.get(f"/api/v1/audit?limit=2&before={admin_client.get('/api/v1/audit?limit=4').json()['entries'][-1]['id']}").json()
    assert [e["action"] for e in p3["entries"]] == ["a0"] and p3["next_before"] is None
    assert admin_client.get("/api/v1/audit").json()["next_before"] is None


def test_server_changes_are_audited(mgr_app, admin_client, csrf):
    body = {"name": "pi2", "url": "https://pi2.example", "token": "geheim-token", "headers": {"CF-Access-Client-Id": "id1"}}
    sid = admin_client.post("/api/v1/servers", json=body, headers=csrf).json()["id"]
    assert admin_client.patch(f"/api/v1/servers/{sid}", json={"enabled": False}, headers=csrf).status_code == 200
    assert admin_client.delete(f"/api/v1/servers/{sid}", headers=csrf).status_code == 204
    es = _audit(mgr_app)[:3]
    assert [e["action"] for e in es] == ["server.removed", "server.updated", "server.added"]
    assert es[2]["target"] == "pi2" and es[2]["details"] == {"url": "https://pi2.example", "header_names": ["CF-Access-Client-Id"]}
    assert es[1]["details"] == {"enabled": False} and es[2]["actor"].startswith("user:")
    assert "geheim-token" not in str(es) and "id1" not in str(es)
    # Bearer-Akteur
    tok, plain = mgr_app.state.tokens.create("adm", "admin")
    h = {"Authorization": f"Bearer {plain}"}
    c = TestClient(mgr_app, base_url="https://testserver")
    sid = c.post("/api/v1/servers", json=body, headers=h).json()["id"]
    c.delete(f"/api/v1/servers/{sid}", headers=h)
    assert {e["actor"] for e in _audit(mgr_app)[:2]} == {f"token:{tok.id}"}
