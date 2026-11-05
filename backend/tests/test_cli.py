import io
import os
import pytest
from moni import cli
from moni.auth.tokens import TokenStore
from moni.auth.users import UserStore
from moni.secretbox import SecretBox
from moni.manager import db
from moni.secretbox import generate_key


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MONI_SECRET_KEY", generate_key())
    monkeypatch.setenv("MONI_ROLE", "manager")
    return tmp_path


def add(env, *extra, tok="tok-abc\n"):
    (env / "tok").write_text(tok)
    return cli.main(["server", "add", "--name", "sau", "--url", "https://m.example", "--token-file", str(env / "tok"), *extra])


def test_cli_server_add_list_remove(env, capsys):
    assert add(env, "--header", "CF-Access-Client-Id=id1") == 0
    assert cli.main(["server", "list"]) == 0
    out = capsys.readouterr().out
    assert "sau" in out and "CF-Access-Client-Id" in out and "tok-abc" not in out and "id1" not in out
    assert cli.main(["server", "remove", "1"]) == 0
    assert cli.main(["server", "remove", "1"]) == 1


def test_cli_token_strips_one_newline_and_stdin(env, monkeypatch):
    assert add(env, tok="tok-abc\n\n") == 2  # zweiter Zeilenumbruch bleibt -> ungültig
    monkeypatch.setattr("sys.stdin", io.StringIO("tok-stdin\n"))
    assert cli.main(["server", "add", "--name", "b", "--url", "https://b.example", "--token-file", "-"]) == 0


def test_cli_errors(env, capsys, monkeypatch):
    assert add(env, "--header", "kaputt") == 2
    assert add(env, "--header", "Authorization=x") == 2
    assert cli.main(["server", "add", "--name", "s", "--url", "https://a", "--token-file", str(env / "fehlt")]) == 2
    assert capsys.readouterr().err.count("Fehler") == 3
    monkeypatch.delenv("MONI_SECRET_KEY")
    assert cli.main(["server", "list"]) == 2


def test_cli_rejects_token_argument(env):
    with pytest.raises(SystemExit):
        cli.main(["server", "add", "--name", "a", "--url", "https://a", "--token", "x"])


def test_cli_secret_reencrypt(env, capsys):
    assert add(env) == 0
    assert cli.main(["secret", "reencrypt"]) == 0
    assert capsys.readouterr().out.strip().endswith("1")


def test_cli_secret_reencrypt_counts_users_and_is_atomic(env, capsys, monkeypatch):
    import os
    from moni.auth.users import UserStore
    from moni.manager.servers import ServerStore
    from moni.secretbox import SecretBox
    assert add(env) == 0
    conn = db.connect(env / "moni.db")
    box = SecretBox(os.environ["MONI_SECRET_KEY"])
    u = UserStore(conn, box)
    uid = u.create("a@b.de", "", "admin", "local", password="x" * 12).id
    u.begin_totp(uid)
    assert cli.main(["secret", "reencrypt"]) == 0
    assert capsys.readouterr().out.strip().endswith("2")
    # Nutzer-Secret mit fremdem Schlüssel: Rotation scheitert, auch der Server bleibt unverändert
    UserStore(conn, SecretBox(generate_key())).begin_totp(uid)
    before = [tuple(r) for r in conn.execute("SELECT token_enc, headers_enc FROM servers")]
    assert cli.main(["secret", "reencrypt"]) == 2
    assert before == [tuple(r) for r in conn.execute("SELECT token_enc, headers_enc FROM servers")]
    assert not conn.in_transaction
    conn.close()


def test_cli_token_scope(env, capsys):
    from moni.auth.tokens import TokenStore
    assert cli.main(["token", "create", "--name", "a", "--scope", "admin"]) == 0
    assert cli.main(["token", "create", "--name", "r"]) == 0
    assert [t.scope for t in TokenStore(env / "tokens.json").list()] == ["admin", "read"]


def _audit_rows(env):
    from moni.auth import audit
    conn = db.connect(env / "moni.db")
    try:
        return audit.entries(conn)
    finally:
        conn.close()


def _mk_user(env, kind="local"):
    db.migrate(env / "moni.db")
    conn = db.connect(env / "moni.db")
    try:
        return UserStore(conn, SecretBox(os.environ["MONI_SECRET_KEY"])).create(
            "A@b.de", "", "viewer", kind, "x" * 12 if kind == "local" else None)
    finally:
        conn.close()


def test_cli_manager_actions_are_audited(env):
    assert add(env) == 0
    assert cli.main(["server", "remove", "1"]) == 0
    assert cli.main(["token", "create", "--name", "g", "--scope", "admin"]) == 0
    tid = TokenStore(env / "tokens.json").list()[0].id
    assert cli.main(["token", "revoke", tid]) == 0
    assert cli.main(["secret", "reencrypt"]) == 0
    es = _audit_rows(env)
    assert [e["action"] for e in es] == ["secret.reencrypted", "token.revoked", "token.created", "server.removed", "server.added"]
    assert {e["actor"] for e in es} == {"cli"} and "tok-abc" not in str(es)
    assert es[2]["details"] == {"scope": "admin"} and es[4]["details"]["url"] == "https://m.example"


def test_cli_token_in_agent_role_has_no_audit(env, monkeypatch):
    monkeypatch.setenv("MONI_ROLE", "agent")
    assert cli.main(["token", "create", "--name", "g"]) == 0
    assert not (env / "moni.db").exists()


def test_cli_user_list_and_unknown_email(env, capsys):
    _mk_user(env)
    assert cli.main(["user", "list"]) == 0
    assert "a@b.de" in capsys.readouterr().out
    for cmd in (["reset-2fa"], ["unlock"], ["reset-password", "--password-file", "-"]):
        assert cli.main(["user", cmd[0], "no@x.de", *cmd[1:]]) == 1
        assert capsys.readouterr().err.strip() == "Kein Benutzer mit der E-Mail no@x.de."


def test_cli_reset_2fa_and_unlock_audited(env, capsys):
    import time
    from moni.auth.sessions import SessionStore
    from moni.config import Settings
    u = _mk_user(env)
    conn = db.connect(env / "moni.db")
    users = UserStore(conn, SecretBox(os.environ["MONI_SECRET_KEY"]))
    users.begin_totp(u.id)
    SessionStore(conn, Settings.from_env()).create(u.id, "full", int(time.time()), None)
    conn.execute("UPDATE users SET locked_until=?, failed_logins=5 WHERE id=?", (int(time.time()) + 600, u.id))
    conn.commit()
    assert cli.main(["user", "unlock", "A@B.de"]) == 0
    assert cli.main(["user", "reset-2fa", "a@b.de"]) == 0
    assert users.get(u.id).locked_until is None and not users.get(u.id).totp_enabled
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    conn.close()
    es = _audit_rows(env)
    assert [(e["actor"], e["action"], e["target"]) for e in es] == [("cli", "totp.reset", "a@b.de"), ("cli", "user.unlocked", "a@b.de")]


def test_cli_reset_password_from_stdin(env, capsys, monkeypatch):
    u = _mk_user(env)
    monkeypatch.setattr("sys.stdin", io.StringIO("kurz\n"))
    assert cli.main(["user", "reset-password", "a@b.de", "--password-file", "-"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("Fehler: ") and "kurz" not in err
    monkeypatch.setattr("sys.stdin", io.StringIO("Ein-ganz-neues-Passwort\n"))
    assert cli.main(["user", "reset-password", "a@b.de", "--password-file", "-"]) == 0
    conn = db.connect(env / "moni.db")
    assert UserStore(conn, SecretBox(os.environ["MONI_SECRET_KEY"])).check_password(u.id, "Ein-ganz-neues-Passwort")
    conn.close()
    es = _audit_rows(env)
    assert es[0]["action"] == "password.reset" and es[0]["actor"] == "cli" and "Passwort" not in str(es)


@pytest.mark.parametrize("cmd", [["reset-2fa"], ["reset-password", "--password-file", "-"]])
def test_cli_credential_change_and_session_end_are_atomic(env, monkeypatch, cmd):
    import sqlite3
    import time
    from moni.auth.sessions import SessionStore
    from moni.config import Settings

    def locked(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    u = _mk_user(env)
    conn = db.connect(env / "moni.db")
    users = UserStore(conn, SecretBox(os.environ["MONI_SECRET_KEY"]))
    conn.execute("UPDATE users SET totp_enc=? WHERE id=?",
                 (SecretBox(os.environ["MONI_SECRET_KEY"]).encrypt("JBSWY3DPEHPK3PXP"), u.id))
    conn.commit()
    SessionStore(conn, Settings.from_env()).create(u.id, "full", int(time.time()), None)
    monkeypatch.setattr(SessionStore, "delete_for_user", locked)
    monkeypatch.setattr("sys.stdin", io.StringIO("Ein-ganz-neues-Passwort\n"))
    with pytest.raises(sqlite3.OperationalError):
        cli.main(["user", cmd[0], "a@b.de", *cmd[1:]])
    assert users.check_password(u.id, "x" * 12) and users.get(u.id).totp_enabled
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    conn.close()
    assert _audit_rows(env) == []


def test_cli_reset_password_rejects_argument(env):
    with pytest.raises(SystemExit):
        cli.main(["user", "reset-password", "a@b.de", "--password", "x"])
