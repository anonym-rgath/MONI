import logging
import re
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_PASSWORD, Dummy, code_at
from moni.auth import audit, passwords
from moni.auth.sessions import COOKIE
from moni.auth.users import UserStore
from moni.manager import db

SETUP = {"email": "admin@example.org", "name": "Admin", "password": "Ein-sicheres-Passwort-1"}
FAIL = {"type": "about:blank", "title": "Unauthorized", "status": 401, "detail": "Anmeldung fehlgeschlagen"}
NEW_PW = "Ganz-neues-Passwort-42"


@pytest.fixture(autouse=True)
def _keep_caplog(monkeypatch):
    # setup_logging() ersetzt per basicConfig(force=True) alle Root-Handler, auch den von caplog
    monkeypatch.setattr("moni.app.setup_logging", lambda: None)


def setup_token_from(caplog):
    text = "\n".join(r.getMessage() for r in caplog.get_records("setup") + caplog.records)
    return re.search(r"setup_[A-Za-z0-9_-]+", text).group(0)


def _conn(app):
    return db.connect(app.state.settings.db_path)


def _store(app, fn):
    conn = _conn(app)
    try:
        return fn(UserStore(conn, app.state.box), conn)
    finally:
        conn.close()


def _actions(app):
    return _store(app, lambda s, c: [e["action"] for e in audit.entries(c, 1000)])


def _audit(app):
    return _store(app, lambda s, c: audit.entries(c, 1000))


def totp_user(app, make_user, role="admin", email=None):
    """Lokaler Benutzer mit aktivem TOTP. Bestätigt 60 s in der Vergangenheit, damit jetzt und +30 s frei sind."""
    u = make_user(role, email=email)

    def enrol(s, c):
        secret, _ = s.begin_totp(u.id)
        t0 = time.time() - 60
        return secret, s.confirm_totp(u.id, code_at(secret, t0), int(t0))
    secret, codes = _store(app, enrol)
    return u, secret, codes


def use(c, cookie):
    c.cookies.clear()
    if cookie:
        c.cookies.set(COOKIE, cookie)


def login(c, email, pw=TEST_PASSWORD):
    return c.post("/api/v1/auth/login", json={"email": email, "password": pw})


def csrf(c):
    return c.get("/api/v1/auth/session").json()["csrf_token"]


def mfa(c, code):
    return c.post("/api/v1/auth/login/mfa", json={"code": code}, headers={"X-CSRF-Token": csrf(c)})


def full_login(c, u, secret, t=None):
    assert login(c, u.email).json() == {"stage": "mfa"}
    assert mfa(c, code_at(secret, time.time() if t is None else t)).json() == {"stage": "full"}
    return csrf(c)


def test_full_first_run_flow(mgr_client, mgr_app, caplog):
    tok = setup_token_from(caplog)
    assert mgr_client.get("/api/v1/setup").json() == {"required": True}
    assert mgr_client.post("/api/v1/setup", json=SETUP | {"setup_token": "setup_falsch"}).status_code == 403
    r = mgr_client.post("/api/v1/setup", json=SETUP | {"setup_token": tok})
    assert r.status_code == 201 and r.json() == {"stage": "enroll"}
    set_cookie = r.headers["set-cookie"].lower()
    assert all(x in set_cookie for x in ("httponly", "secure", "samesite=strict", "path=/"))
    assert mgr_client.get("/api/v1/servers").status_code == 401  # enroll ≠ full
    csrf_tok = mgr_client.get("/api/v1/auth/session").json()["csrf_token"]
    secret = mgr_client.post("/api/v1/auth/totp/setup", json={}, headers={"X-CSRF-Token": csrf_tok}).json()["secret"]
    codes = mgr_client.post("/api/v1/auth/totp/confirm", json={"code": code_at(secret, time.time())},
                            headers={"X-CSRF-Token": csrf_tok}).json()["recovery_codes"]
    assert len(codes) == 10 and mgr_client.get("/api/v1/servers").status_code == 200
    assert mgr_client.get("/api/v1/setup").json() == {"required": False}
    assert mgr_client.post("/api/v1/setup", json=SETUP | {"setup_token": tok}).status_code == 409
    assert mgr_app.state.setup_hash is None
    entries = _audit(mgr_app)
    created = next(e for e in entries if e["action"] == "setup.admin_created")
    assert created["actor"] == "setup" and created["target"] == SETUP["email"]
    assert {"totp.enabled", "login.success"} <= {e["action"] for e in entries}
    me = mgr_client.get("/api/v1/auth/session").json()
    assert me["stage"] == "full" and me["user"]["role"] == "admin" and me["user"]["totp_enabled"] is True
    assert set(me["user"]) == {"id", "email", "name", "role", "kind", "totp_enabled"}


def test_setup_wrong_token_is_throttled_and_403_text(mgr_client, caplog):
    assert mgr_client.post("/api/v1/setup", json=SETUP | {"setup_token": "setup_\udcff"}).status_code == 422
    r = mgr_client.post("/api/v1/setup", json=SETUP | {"setup_token": "setup_falsch"})
    assert r.status_code == 403 and r.json()["detail"] == "Setup-Token ist ungültig"
    for _ in range(19):
        mgr_client.post("/api/v1/setup", json=SETUP | {"setup_token": "x"})
    r = mgr_client.post("/api/v1/setup", json=SETUP | {"setup_token": setup_token_from(caplog)})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_setup_rejects_weak_password(mgr_client, caplog):
    r = mgr_client.post("/api/v1/setup", json=SETUP | {"setup_token": setup_token_from(caplog), "password": "kurz"})
    assert r.status_code == 422 and "mindestens 12" in r.json()["detail"]
    assert mgr_client.get("/api/v1/setup").json() == {"required": True}


def test_login_mfa_rotates_session(mgr_client, mgr_app, make_user):
    u, secret, _ = totp_user(mgr_app, make_user)
    r = login(mgr_client, u.email)
    assert r.status_code == 200 and r.json() == {"stage": "mfa"}
    old = r.cookies[COOKIE]
    assert mgr_client.get("/api/v1/servers").status_code == 401  # mfa ≠ full
    r = mfa(mgr_client, code_at(secret, time.time()))
    assert r.status_code == 200 and r.json() == {"stage": "full"}
    new = r.cookies[COOKIE]
    assert new != old and mgr_client.get("/api/v1/servers").status_code == 200
    use(mgr_client, old)
    assert mgr_client.get("/api/v1/auth/session").status_code == 401
    success = next(e for e in _audit(mgr_app) if e["action"] == "login.success")
    assert success["actor"] == f"user:{u.email}"


def test_login_without_totp_goes_to_enroll(mgr_client, make_user):
    u = make_user("viewer")
    assert login(mgr_client, u.email).json() == {"stage": "enroll"}
    assert mgr_client.get("/api/v1/auth/session").json()["stage"] == "enroll"


def test_generic_failure_for_unknown_wrong_locked(mgr_client, mgr_app, make_user, monkeypatch):
    ok = make_user("admin")
    oidc = make_user("viewer", kind="oidc")
    disabled = make_user("viewer")
    locked = make_user("viewer")
    _store(mgr_app, lambda s, c: s.update(disabled.id, disabled=True))
    _store(mgr_app, lambda s, c: [s.register_failure(locked.id, int(time.time())) for _ in range(5)])
    real, calls = passwords.verify_password, []
    monkeypatch.setattr(passwords, "verify_password", lambda *a: calls.append(1) or real(*a))
    attempts = [("niemand@example.org", TEST_PASSWORD), ("kein-at", TEST_PASSWORD), (ok.email, "falsch-falsch-1"),
                (oidc.email, TEST_PASSWORD), (disabled.email, TEST_PASSWORD), (locked.email, TEST_PASSWORD)]
    for email, pw in attempts:
        calls.clear()
        r = login(mgr_client, email, pw)
        assert (r.status_code, r.json()) == (401, FAIL), email
        assert len(calls) == 1, email  # genau eine Argon2-Prüfung auf jedem Fehlerpfad (D8)
        assert COOKIE not in r.headers.get("set-cookie", "")
    failures = [e for e in _audit(mgr_app) if e["action"] == "login.failure"]
    assert len(failures) == 6
    assert failures[-1]["actor"] == "anonym" and failures[-1]["target"] == "niemand@example.org"
    assert failures[-3]["actor"] == f"user:{ok.email}"


def test_lockout_blocks_correct_password(mgr_client, mgr_app, make_user):
    u = make_user("admin")
    for _ in range(5):
        assert login(mgr_client, u.email, "falsch-falsch-1").status_code == 401
    r = login(mgr_client, u.email)
    assert (r.status_code, r.json()) == (401, FAIL)
    assert _actions(mgr_app).count("login.locked") == 1


def test_mfa_failures_count_towards_lockout(mgr_client, mgr_app, make_user):
    u, secret, _ = totp_user(mgr_app, make_user)
    assert login(mgr_client, u.email).json() == {"stage": "mfa"}
    for i in range(5):
        r = mfa(mgr_client, "000000" if code_at(secret, time.time()) != "000000" else "111111")
        assert (r.status_code, r.json()) == (401, FAIL)
    assert mgr_client.get("/api/v1/auth/session").status_code == 401  # bei Sperre wird die Session gelöscht
    acts = _actions(mgr_app)
    assert acts.count("mfa.failure") == 5 and acts.count("login.locked") == 1
    assert login(mgr_client, u.email).status_code == 401


def test_locked_account_cannot_complete_mfa(mgr_client, mgr_app, make_user):
    u, secret, _ = totp_user(mgr_app, make_user)
    assert login(mgr_client, u.email).json() == {"stage": "mfa"}
    token = csrf(mgr_client)
    _store(mgr_app, lambda s, c: [s.register_failure(u.id, int(time.time())) for _ in range(5)])
    r = mgr_client.post("/api/v1/auth/login/mfa", json={"code": code_at(secret, time.time())}, headers={"X-CSRF-Token": token})
    assert (r.status_code, r.json()) == (401, FAIL)
    assert mgr_client.get("/api/v1/auth/session").status_code == 401


def test_password_failures_then_mfa_failures_lock(mgr_client, mgr_app, make_user):
    u, secret, _ = totp_user(mgr_app, make_user)
    for _ in range(3):
        login(mgr_client, u.email, "falsch-falsch-1")
    assert login(mgr_client, u.email).json() == {"stage": "mfa"}  # setzt den Zähler noch nicht zurück
    assert mfa(mgr_client, "abcdef").status_code == 401  # 4. Fehlversuch
    assert mgr_client.get("/api/v1/auth/session").status_code == 200
    assert mfa(mgr_client, "abcdef").status_code == 401  # 5. Fehlversuch sperrt und beendet die Session
    assert mgr_client.get("/api/v1/auth/session").status_code == 401
    assert _actions(mgr_app).count("login.locked") == 1


def test_ip_throttle_429(mgr_client, make_user):
    u = make_user("admin")
    for i in range(20):
        assert login(mgr_client, f"x{i}@example.org").status_code == 401
    r = login(mgr_client, u.email)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert r.json()["detail"] == "Zu viele Fehlversuche, bitte später erneut versuchen"


@pytest.mark.parametrize("ip,key", [
    ("192.0.2.7", "192.0.2.7"),
    ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
    ("2001:db8:1:2:ffff:ffff:ffff:ffff", "2001:db8:1:2::/64"),
    ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
    ("fe80::1%eth0", "fe80::/64"),
    ("::ffff:192.0.2.7", "192.0.2.7"),
    ("testclient", "testclient"),
])
def test_throttle_key(ip, key):
    from moni.auth.throttle import throttle_key
    assert throttle_key(SimpleNamespace(client=SimpleNamespace(host=ip))) == key


def test_throttle_key_without_client():
    from moni.auth.throttle import throttle_key
    assert throttle_key(SimpleNamespace(client=None)) == ""


def test_recovery_code_login_once(mgr_client, mgr_app, make_user):
    u, secret, codes = totp_user(mgr_app, make_user)
    assert login(mgr_client, u.email).json() == {"stage": "mfa"}
    assert mfa(mgr_client, codes[0].upper()).json() == {"stage": "full"}
    acts = _actions(mgr_app)
    assert "recovery_code.used" in acts and "login.success" in acts
    use(mgr_client, None)
    assert login(mgr_client, u.email).json() == {"stage": "mfa"}
    assert mfa(mgr_client, codes[0]).status_code == 401


def test_totp_change_requires_current_code(mgr_client, mgr_app, make_user):
    u, secret, codes = totp_user(mgr_app, make_user)
    token = full_login(mgr_client, u, secret)
    h = {"X-CSRF-Token": token}
    for body in ({}, {"current_code": "000000" if code_at(secret, time.time() + 30) != "000000" else "111111"},
                 {"current_code": codes[0]}):
        r = mgr_client.post("/api/v1/auth/totp/setup", json=body, headers=h)
        assert (r.status_code, r.json()) == (401, FAIL), body
    assert _store(mgr_app, lambda s, c: s.get(u.id).failed_logins) == 3
    used = _store(mgr_app, lambda s, c: c.execute("SELECT COUNT(*) FROM recovery_codes WHERE used_at IS NOT NULL").fetchone()[0])
    assert used == 0  # ein Wiederherstellungscode zählt hier nicht und wird nicht verbraucht
    r = mgr_client.post("/api/v1/auth/totp/setup", json={"current_code": code_at(secret, time.time() + 30)}, headers=h)
    assert r.status_code == 200 and set(r.json()) == {"secret", "otpauth_uri"}
    new = r.json()["secret"]
    assert r.json()["otpauth_uri"].startswith("otpauth://totp/") and new != secret
    r = mgr_client.post("/api/v1/auth/totp/confirm", json={"code": code_at(new, time.time())}, headers=h)
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10
    assert mgr_client.get("/api/v1/auth/session").json()["stage"] == "full"


def test_totp_setup_only_local(mgr_client, make_user, login_cookie):
    cookie, token = login_cookie(make_user("viewer", kind="oidc"))
    use(mgr_client, cookie)
    r = mgr_client.post("/api/v1/auth/totp/setup", json={}, headers={"X-CSRF-Token": token})
    assert r.status_code == 400


def test_totp_confirm_wrong_code(mgr_client, make_user):
    u = make_user("viewer")
    login(mgr_client, u.email)
    h = {"X-CSRF-Token": csrf(mgr_client)}
    mgr_client.post("/api/v1/auth/totp/setup", json={}, headers=h)
    r = mgr_client.post("/api/v1/auth/totp/confirm", json={"code": "abcdef"}, headers=h)
    assert (r.status_code, r.json()) == (401, FAIL)
    assert mgr_client.get("/api/v1/auth/session").json()["stage"] == "enroll"


def test_password_change_ends_other_sessions(mgr_client, mgr_app, make_user, login_cookie):
    u, secret, _ = totp_user(mgr_app, make_user)
    (a, csrf_a), (b, _) = login_cookie(u), login_cookie(u)
    use(mgr_client, a)
    url, h = "/api/v1/account/password", {"X-CSRF-Token": csrf_a}
    r = mgr_client.post(url, json={"current_password": "falsch-falsch-1", "new_password": NEW_PW}, headers=h)
    assert (r.status_code, r.json()) == (401, FAIL)
    assert _store(mgr_app, lambda s, c: s.get(u.id).failed_logins) == 1
    r = mgr_client.post(url, json={"current_password": TEST_PASSWORD, "new_password": "kurz"}, headers=h)
    assert r.status_code == 422
    r = mgr_client.post(url, json={"current_password": TEST_PASSWORD, "new_password": NEW_PW}, headers=h)
    assert r.status_code == 204
    assert mgr_client.get("/api/v1/auth/session").status_code == 200
    use(mgr_client, b)
    assert mgr_client.get("/api/v1/auth/session").status_code == 401
    use(mgr_client, None)
    assert login(mgr_client, u.email).status_code == 401
    assert login(mgr_client, u.email, NEW_PW).json() == {"stage": "mfa"}
    assert "password.changed" in _actions(mgr_app)


def test_password_change_and_session_end_are_atomic(mgr_client, mgr_app, make_user, login_cookie, monkeypatch):
    from moni.auth.sessions import SessionStore

    def locked(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    u = make_user("viewer")
    (a, csrf_a), _ = login_cookie(u), login_cookie(u)
    use(mgr_client, a)
    n = len(_actions(mgr_app))
    monkeypatch.setattr(SessionStore, "delete_for_user", locked)
    r = mgr_client.post("/api/v1/account/password", json={"current_password": TEST_PASSWORD, "new_password": NEW_PW},
                        headers={"X-CSRF-Token": csrf_a})
    assert r.status_code == 503
    assert _store(mgr_app, lambda s, c: s.check_password(u.id, TEST_PASSWORD))
    assert _store(mgr_app, lambda s, c: c.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (u.id,)).fetchone()[0]) == 2
    assert len(_actions(mgr_app)) == n


def test_account_endpoints_need_browser_session(mgr_client, mgr_app):
    h = {"Authorization": f"Bearer {mgr_app.state.tokens.create('a', 'admin')[1]}"}
    for url, body in (("/api/v1/account/password", {"current_password": "a", "new_password": "b"}),
                      ("/api/v1/account/recovery-codes", {"code": "123456"})):
        r = mgr_client.post(url, json=body, headers=h)
        assert r.status_code == 400 and r.json()["detail"] == "Nur mit Anmeldung im Browser möglich"


def test_regenerate_recovery_codes(mgr_client, mgr_app, make_user):
    u, secret, codes = totp_user(mgr_app, make_user)
    token = full_login(mgr_client, u, secret)
    url, h = "/api/v1/account/recovery-codes", {"X-CSRF-Token": token}
    for code in (codes[0], "abcdef"):
        r = mgr_client.post(url, json={"code": code}, headers=h)
        assert (r.status_code, r.json()) == (401, FAIL)
    assert _store(mgr_app, lambda s, c: s.get(u.id).failed_logins) == 2
    r = mgr_client.post(url, json={"code": code_at(secret, time.time() + 30)}, headers=h)
    assert r.status_code == 200 and len(new := r.json()["recovery_codes"]) == 10 and not set(new) & set(codes)
    assert "recovery_codes.regenerated" in _actions(mgr_app)


def test_full_session_lock_ends_session(mgr_client, mgr_app, make_user):
    u, secret, _ = totp_user(mgr_app, make_user)
    token = full_login(mgr_client, u, secret)
    for _ in range(5):
        mgr_client.post("/api/v1/account/recovery-codes", json={"code": "abcdef"}, headers={"X-CSRF-Token": token})
    assert mgr_client.get("/api/v1/auth/session").status_code == 401
    assert "login.locked" in _actions(mgr_app)


@pytest.mark.parametrize("body", [{"code": 123456}, {"code": "1" * 1025}, {"code": "123456", "extra": 1}, {}])
def test_bodies_are_strict(mgr_client, mgr_app, make_user, body):
    assert login(mgr_client, totp_user(mgr_app, make_user)[0].email).json() == {"stage": "mfa"}
    r = mgr_client.post("/api/v1/auth/login/mfa", json=body, headers={"X-CSRF-Token": csrf(mgr_client)})
    assert r.status_code == 422


def test_login_body_limits(mgr_client):
    assert login(mgr_client, "a@example.org", "x" * 1025).status_code == 422
    assert mgr_client.post("/api/v1/auth/login", json={"email": 1, "password": "x"}).status_code == 422


def test_logout(mgr_client, mgr_app, make_user):
    u = make_user("admin")
    login(mgr_client, u.email)  # Stufe enroll genügt
    assert mgr_client.post("/api/v1/auth/logout").status_code == 403  # ohne CSRF
    r = mgr_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf(mgr_client)})
    assert r.status_code == 204
    sc = r.headers["set-cookie"].lower()
    assert sc.startswith(f"{COOKIE}=") and "max-age=0" in sc
    assert all(x in sc for x in ("httponly", "secure", "samesite=strict", "path=/"))
    assert mgr_client.get("/api/v1/auth/session").status_code == 401
    assert _store(mgr_app, lambda s, c: c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]) == 0
    assert "logout" in _actions(mgr_app)


def test_concurrent_setup_creates_one_admin(mgr_app, caplog):
    tok = setup_token_from(caplog)
    barrier, results = threading.Barrier(2), []

    def run(i):
        c = TestClient(mgr_app, base_url="https://testserver")
        barrier.wait()
        results.append(c.post("/api/v1/setup", json=SETUP | {"setup_token": tok, "email": f"a{i}@example.org"}).status_code)
    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [201, 409]
    assert _store(mgr_app, lambda s, c: s.count()) == 1


def test_no_secrets_in_logs(mgr_client, mgr_app, caplog):
    tok = setup_token_from(caplog)
    caplog.set_level(logging.DEBUG)
    secrets = [SETUP["password"], NEW_PW, tok]
    r = mgr_client.post("/api/v1/setup", json=SETUP | {"setup_token": tok})
    secrets.append(r.cookies[COOKIE])
    secrets.append(token := csrf(mgr_client))
    secret = mgr_client.post("/api/v1/auth/totp/setup", json={}, headers={"X-CSRF-Token": token}).json()["secret"]
    codes = mgr_client.post("/api/v1/auth/totp/confirm", json={"code": code_at(secret, time.time() - 30)},
                            headers={"X-CSRF-Token": token}).json()["recovery_codes"]
    secrets += [secret, *codes]
    mgr_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf(mgr_client)})
    login(mgr_client, SETUP["email"], "falsch-falsch-1")
    r = login(mgr_client, SETUP["email"], SETUP["password"])
    secrets.append(r.cookies[COOKIE])
    secrets.append(csrf(mgr_client))
    mfa(mgr_client, "abcdef")
    r = mfa(mgr_client, codes[0])
    secrets.append(r.cookies[COOKIE])
    secrets.append(token := csrf(mgr_client))
    h = {"X-CSRF-Token": token}
    mgr_client.post("/api/v1/account/password", json={"current_password": SETUP["password"], "new_password": NEW_PW}, headers=h)
    new = mgr_client.post("/api/v1/account/recovery-codes", json={"code": code_at(secret, time.time())}, headers=h).json()
    secrets += new["recovery_codes"]
    text = caplog.text
    stored = _store(mgr_app, lambda s, c: " ".join(str(tuple(r)) for r in c.execute("SELECT * FROM audit_log")))
    for s in secrets:
        assert s not in text and s not in stored
    assert "setup.admin_created" in stored and "recovery_codes.regenerated" in stored


def test_setup_token_not_logged_when_users_exist(mgr_settings, make_user, caplog):
    from moni.app import create_app
    make_user("admin")
    caplog.clear()
    app = create_app(mgr_settings, collector=Dummy(), poller=Dummy())
    assert "Ersteinrichtung offen" not in caplog.text and app.state.setup_hash is None
    c = TestClient(app, base_url="https://testserver")
    assert c.get("/api/v1/setup").json() == {"required": False}
    r = c.post("/api/v1/setup", json=SETUP | {"setup_token": "setup_x"})
    assert r.status_code == 409 and r.json()["detail"] == "Ersteinrichtung ist bereits abgeschlossen"


def _totp_state(app, uid):
    return _store(app, lambda s, c: (
        tuple(c.execute("SELECT totp_enc, totp_pending_enc, totp_last_step FROM users WHERE id=?", (uid,)).fetchone()),
        sorted(r[0] for r in c.execute("SELECT code_hash FROM recovery_codes WHERE user_id=?", (uid,)))))


def test_enroll_session_cannot_replace_active_totp(mgr_client, mgr_app, make_user, login_cookie):
    u, secret, _ = totp_user(mgr_app, make_user)
    before = _totp_state(mgr_app, u.id)
    cookie, token = login_cookie(u, "enroll")  # z. B. ein zweiter Passwort-Login, bevor die 2FA aktiv wurde
    use(mgr_client, cookie)
    h = {"X-CSRF-Token": token}
    r = mgr_client.post("/api/v1/auth/totp/setup", json={}, headers=h)
    assert (r.status_code, r.json()) == (401, FAIL)
    r = mgr_client.post("/api/v1/auth/totp/confirm", json={"code": code_at(secret, time.time())}, headers=h)
    assert (r.status_code, r.json()) == (401, FAIL)
    assert _totp_state(mgr_app, u.id) == before
    assert mgr_client.get("/api/v1/auth/session").json()["stage"] == "enroll"


def test_confirm_from_enroll_ends_other_pre_auth_sessions(mgr_app, make_user, login_cookie):
    u = make_user("admin")
    a, b = (TestClient(mgr_app, base_url="https://testserver") for _ in range(2))
    assert login(a, u.email).json() == login(b, u.email).json() == {"stage": "enroll"}
    full_cookie, _ = login_cookie(u, "full")  # kann es eigentlich noch nicht geben, bleibt aber erhalten
    h = {"X-CSRF-Token": csrf(a)}
    secret = a.post("/api/v1/auth/totp/setup", json={}, headers=h).json()["secret"]
    assert a.post("/api/v1/auth/totp/confirm", json={"code": code_at(secret, time.time())}, headers=h).status_code == 200
    assert a.get("/api/v1/auth/session").json()["stage"] == "full"
    assert b.get("/api/v1/auth/session").status_code == 401
    assert b.post("/api/v1/auth/totp/setup", json={}).status_code == 401
    use(b, full_cookie)
    assert b.get("/api/v1/auth/session").json()["stage"] == "full"


def test_invalid_login_email_not_in_audit(mgr_client, mgr_app):
    assert login(mgr_client, "kein-at-zeichen geheim").status_code == 401
    assert login(mgr_client, " Niemand@Example.org ").status_code == 401
    bad, ok = [e for e in _audit(mgr_app) if e["action"] == "login.failure"][::-1]
    assert bad["target"] is None and bad["actor"] == "anonym" and "geheim" not in str(bad)
    assert ok["target"] == "niemand@example.org"
