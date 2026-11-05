import pytest
from conftest import code_at

from moni.auth.users import Conflict, UserStore
from moni.manager import db
from moni.secretbox import SecretBox, generate_key

PW = "x" * 12


@pytest.fixture
def users(tmp_path):
    p = tmp_path / "moni.db"
    db.migrate(p)
    s = UserStore(db.connect(p), SecretBox(generate_key()))
    yield s
    s._conn.close()


def test_create_local_and_no_secrets_in_repr(users):
    u = users.create(" Admin@Example.org ", "Ada", "admin", "local", password="ein-langes-passwort")
    assert u.email == "admin@example.org" and u.totp_enabled is False and u.oidc_bound is False
    assert "argon2" not in repr(u) and users.check_password(u.id, "ein-langes-passwort")
    assert not users.check_password(u.id, "falsches-passwort") and not users.check_password(u.id, "\ud800")


def test_duplicate_email_conflict(users):
    users.create("a@b.de", "", "admin", "local", password=PW)
    with pytest.raises(Conflict, match="bereits vergeben"):
        users.create("A@B.DE", "", "viewer", "local", password="y" * 12)


@pytest.mark.parametrize("email", ["", "kein-at", "a@b", "a b@c.de", "a@b.de\n", "x" * 250 + "@b.de"])
def test_invalid_email(users, email):
    with pytest.raises(ValueError, match="E-Mail"):
        users.create(email, "", "viewer", "local", password=PW)


def test_oidc_has_no_password(users):
    with pytest.raises(ValueError, match="OIDC"):
        users.create("o@b.de", "", "viewer", "oidc", password=PW)
    o = users.create("o@b.de", "", "viewer", "oidc")
    assert not users.check_password(o.id, PW)
    with pytest.raises(ValueError, match="OIDC"):
        users.set_password(o.id, PW)


def test_create_validates_password_and_name(users):
    with pytest.raises(ValueError, match="mindestens"):
        users.create("a@b.de", "", "viewer", "local", password="kurz")
    with pytest.raises(ValueError, match="Passwort enthält ungültige Zeichen"):
        users.create("a@b.de", "", "viewer", "local", password="\ud800" * 12)
    for name in ("x" * 101, "a\nb"):
        with pytest.raises(ValueError, match="Name"):
            users.create("a@b.de", name, "viewer", "local", password=PW)
    assert users.count() == 0


def test_set_password(users):
    u = users.create("a@b.de", "", "admin", "local", password=PW)
    users.set_password(u.id, "ein-neues-passwort")
    assert users.check_password(u.id, "ein-neues-passwort") and not users.check_password(u.id, PW)
    with pytest.raises(ValueError):
        users.set_password(u.id, "kurz")


def test_last_admin_protected(users):
    a = users.create("a@b.de", "", "admin", "local", password=PW)
    for op in (lambda: users.delete(a.id), lambda: users.update(a.id, role="viewer"), lambda: users.update(a.id, disabled=True)):
        with pytest.raises(Conflict, match="letzte aktive Admin"):
            op()
    assert users.update(a.id, name="Ada").name == "Ada"
    b = users.create("b@b.de", "", "admin", "local", password=PW)
    users.update(b.id, disabled=True)
    with pytest.raises(Conflict):        # b ist deaktiviert, zählt nicht
        users.delete(a.id)
    users.update(b.id, disabled=False)
    assert users.delete(a.id) is True and users.delete(a.id) is False


def test_update_unknown_and_noop(users):
    assert users.update(99, name="x") is None
    a = users.create("a@b.de", "n", "viewer", "local", password=PW)
    assert users.update(a.id) == a


def test_lockout_after_five(users):
    u = users.create("a@b.de", "", "admin", "local", password=PW)
    assert [users.register_failure(u.id, 1000) for _ in range(5)] == [False] * 4 + [True]
    assert users.is_locked(users.get(u.id), 1000 + 899) and not users.is_locked(users.get(u.id), 1000 + 900)
    assert users.get(u.id).failed_logins == 0
    users.register_success(u.id, 2000)
    assert users.get(u.id).failed_logins == 0 and users.get(u.id).last_login == 2000
    assert users.get(u.id).locked_until is None
    users.register_failure(u.id, 3000)
    assert users.unlock(u.id) and users.get(u.id).failed_logins == 0 and not users.unlock(99)


def test_totp_enrolment_and_second_factor(users):
    u = users.create("a@b.de", "", "admin", "local", password=PW)
    secret, uri = users.begin_totp(u.id)
    assert uri.startswith("otpauth://totp/Moni:a%40b.de?secret=" + secret)
    t = 1_800_000_000
    assert users.confirm_totp(u.id, "000000" if code_at(secret, t) != "000000" else "111111", t) is None
    codes = users.confirm_totp(u.id, code_at(secret, t), t)
    assert len(codes) == 10 and users.get(u.id).totp_enabled
    assert users.verify_second_factor(u.id, code_at(secret, t), t) is None          # derselbe Schritt: Replay
    assert users.verify_second_factor(u.id, code_at(secret, t + 30), t + 30) == "totp"
    assert users.verify_second_factor(u.id, codes[0].upper(), t) == "recovery"
    assert users.verify_second_factor(u.id, codes[0], t) is None                    # nur einmal
    assert users.verify_second_factor(u.id, "\ud800" * 10, t) is None
    row = users._conn.execute("SELECT totp_enc FROM users").fetchone()[0]
    assert secret not in row
    hashes = [r[0] for r in users._conn.execute("SELECT code_hash FROM recovery_codes")]
    assert len(hashes) == 10 and not any(c in h for c in codes for h in hashes)


def test_pending_totp_keeps_active_one_until_confirmed(users):
    u = users.create("a@b.de", "", "admin", "local", password=PW)
    s1, _ = users.begin_totp(u.id)
    t = 1_800_000_000
    old_codes = users.confirm_totp(u.id, code_at(s1, t), t)
    s2, _ = users.begin_totp(u.id)
    assert users.verify_second_factor(u.id, code_at(s1, t + 30), t + 30) == "totp"
    new_codes = users.confirm_totp(u.id, code_at(s2, t + 60), t + 60)
    assert users.verify_second_factor(u.id, old_codes[1], t + 90) is None          # alte Codes ersetzt
    assert users.verify_second_factor(u.id, new_codes[1], t + 90) == "recovery"
    assert users.regenerate_recovery_codes(u.id) != new_codes
    assert users.verify_second_factor(u.id, new_codes[2], t + 90) is None


def test_reset_totp_clears_codes(users):
    u = users.create("a@b.de", "", "admin", "local", password=PW)
    secret, _ = users.begin_totp(u.id)
    t = 1_800_000_000
    users.confirm_totp(u.id, code_at(secret, t), t)
    users.begin_totp(u.id)
    users.reset_totp(u.id)
    assert users.get(u.id).totp_enabled is False
    assert users._conn.execute("SELECT COUNT(*) FROM recovery_codes").fetchone()[0] == 0
    r = users._conn.execute("SELECT totp_pending_enc, totp_last_step FROM users").fetchone()
    assert tuple(r) == (None, None)
    assert users.verify_second_factor(u.id, "abcde-fghij", t) is None
    with pytest.raises(ValueError):
        users.regenerate_recovery_codes(u.id)


def test_oidc_binding(users):
    o = users.create("o@b.de", "", "viewer", "oidc")
    other = users.create("p@b.de", "", "viewer", "oidc")
    users.bind_oidc(o.id, "https://idp", "sub1")
    assert users.by_oidc("https://idp", "sub1").id == o.id and users.get(o.id).oidc_bound
    assert users.by_oidc("https://idp", "sub2") is None
    with pytest.raises(Conflict):
        users.bind_oidc(other.id, "https://idp", "sub1")
    assert users.by_email(" O@B.de ").id == o.id and users.by_email("kaputt") is None


def test_reencrypt_all_users(tmp_path):
    p = tmp_path / "moni.db"; db.migrate(p)
    conn = db.connect(p)
    old, new = generate_key(), generate_key()
    st = UserStore(conn, SecretBox(old))
    u = st.create("a@b.de", "", "admin", "local", password=PW)
    st.create("b@b.de", "", "viewer", "local", password=PW)           # ohne TOTP: wird nicht gezählt
    secret, _ = st.begin_totp(u.id)
    t = 1_800_000_000
    st.confirm_totp(u.id, code_at(secret, t), t)
    pending, _ = st.begin_totp(u.id)
    assert UserStore(conn, SecretBox(f"{new},{old}")).reencrypt_all() == 1
    st2 = UserStore(conn, SecretBox(new))
    assert st2.verify_second_factor(u.id, code_at(secret, t + 30), t + 30) == "totp"
    assert st2.confirm_totp(u.id, code_at(pending, t + 60), t + 60) is not None
    conn.close()


def test_migration_v2_on_v1_db_keeps_servers(tmp_path, monkeypatch):
    p = tmp_path / "moni.db"
    only_v1 = tmp_path / "mig"; only_v1.mkdir()
    (only_v1 / "0001_init.sql").write_text((db.MIGRATIONS_DIR / "0001_init.sql").read_text())
    monkeypatch.setattr(db, "MIGRATIONS_DIR", only_v1)
    assert db.migrate(p) == 1
    c = db.connect(p)
    c.execute("INSERT INTO servers (id, name, created_at) VALUES (1, 'alt', 0)"); c.commit(); c.close()
    monkeypatch.undo()
    assert db.migrate(p) == 4
    c = db.connect(p)
    assert c.execute("SELECT name FROM servers").fetchone()[0] == "alt"
    assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert c.execute("PRAGMA user_version").fetchone()[0] == 4
    c.close()


@pytest.mark.parametrize("email,ok", [("a@b.de", True), ("a@b..", True), ("a@.de", False), ("a@de.", False),
                                      ("a@b@c.de", False), ("@b.de", False), ("a@b", False), ("a b@c.de", False)])
def test_email_shape(email, ok):
    from moni.auth.users import _email_shape
    assert _email_shape(email) is ok


def test_email_check_is_linear():
    import time
    from moni.auth.users import normalize_email
    t = time.perf_counter()
    for bad in ("a@" + "!" * 250, "a@" + "!." * 120 + " x"):  # ohne Punkt in der Domain / mit Leerzeichen
        with pytest.raises(ValueError):
            normalize_email(bad)
    assert time.perf_counter() - t < 0.05
