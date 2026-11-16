import json, os
from datetime import datetime, timezone
import pytest
from moni.auth.tokens import TokenStore
from moni import cli

def test_create_verify_revoke(tmp_path):
    st = TokenStore(tmp_path / "tokens.json")
    tok, plain = st.create("grafana")
    assert plain.startswith(f"moni_{tok.id}_") and plain not in (tmp_path / "tokens.json").read_text()
    assert oct(os.stat(tmp_path / "tokens.json").st_mode & 0o777) == "0o600"
    assert st.verify(plain).name == "grafana"
    assert st.verify(plain[:-1] + "x") is None and st.verify("Bearer quatsch") is None
    assert st.revoke(tok.id) is True and st.verify(plain) is None

def test_expired_token(tmp_path):
    st = TokenStore(tmp_path / "t.json")
    _, plain = st.create("x", expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert st.verify(plain, now=datetime(2026, 1, 2, tzinfo=timezone.utc)) is None

def test_unknown_scope(tmp_path):
    with pytest.raises(ValueError):
        TokenStore(tmp_path / "t.json").create("x", scope="root")

def test_corrupt_file_fails_closed_and_is_not_overwritten(tmp_path):
    p = tmp_path / "t.json"; p.write_text("{kaputt")
    st = TokenStore(p)
    assert st.verify("moni_deadbeef_abc") is None
    assert st.list() == []
    with pytest.raises(RuntimeError):
        st.create("x")
    with pytest.raises(RuntimeError):
        st.revoke("deadbeef")
    assert p.read_text() == "{kaputt"

def test_missing_file_is_empty(tmp_path):
    assert TokenStore(tmp_path / "nope.json").list() == []

def test_second_store_instance_sees_changes_immediately(tmp_path):
    p = tmp_path / "t.json"
    a, b = TokenStore(p), TokenStore(p)
    tok, plain = a.create("x")
    assert b.verify(plain) is not None
    assert a.revoke(tok.id) and b.verify(plain) is None

def test_secret_with_underscore_verifies(tmp_path, monkeypatch):
    monkeypatch.setattr("secrets.token_urlsafe", lambda n: "a_b-c_d")
    st = TokenStore(tmp_path / "t.json")
    _, plain = st.create("x")
    assert st.verify(plain) is not None

def test_cli_roundtrip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
    assert cli.main(["token", "create", "--name", "manager", "--expires-days", "30"]) == 0
    plain = capsys.readouterr().out.strip().splitlines()[-1]
    assert plain.startswith("moni_")
    assert cli.main(["token", "list"]) == 0
    out = capsys.readouterr().out
    assert "manager" in out and plain not in out
    assert json.loads((tmp_path / "tokens.json").read_text())["tokens"][0]["hash"] not in out
    tid = plain.split("_")[1]
    assert cli.main(["token", "revoke", tid]) == 0
    assert cli.main(["token", "revoke", tid]) == 1

def test_cli_unwritable_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path / "gibtsnicht" / "x"))
    assert cli.main(["token", "create", "--name", "a"]) == 2
    assert capsys.readouterr().err.strip()


def test_binary_garbage_fails_closed(tmp_path):
    p = tmp_path / "t.json"; p.write_bytes(b"\xff\xfe\x00garbage")
    st = TokenStore(p)
    assert st.verify("moni_deadbeef_abc") is None and st.list() == []
    with pytest.raises(RuntimeError):
        st.create("x")
    assert p.read_bytes() == b"\xff\xfe\x00garbage"

def test_unreadable_file_logs_error(tmp_path, caplog):
    p = tmp_path / "t.json"; p.write_text("{}"); p.chmod(0)
    try:
        if os.access(p, os.R_OK):
            pytest.skip("läuft als root")
        assert TokenStore(p).verify("moni_deadbeef_abc") is None
        assert "nicht lesbar" in caplog.text
    finally:
        p.chmod(0o600)

@pytest.mark.parametrize("patch", [{"expires_at": "kein-datum"}, {"hash": 5}, {"expires_at": 7}])
def test_semantically_corrupt_fails_closed(tmp_path, patch):
    st = TokenStore(tmp_path / "t.json")
    tok, plain = st.create("x")
    data = json.loads(st.path.read_text()); data["tokens"][0].update(patch)
    st.path.write_text(json.dumps(data))
    assert st.verify(plain) is None and st.list() == []

def test_lock_file_mode(tmp_path):
    st = TokenStore(tmp_path / "t.json"); st.create("x")
    assert oct(os.stat(tmp_path / "t.json.lock").st_mode & 0o777) == "0o600"

@pytest.mark.parametrize("days", ["0", "-3"])
def test_cli_rejects_nonpositive_expiry(tmp_path, monkeypatch, capsys, days):
    monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
    assert cli.main(["token", "create", "--name", "a", "--expires-days", days]) == 2
    assert not (tmp_path / "tokens.json").exists() and capsys.readouterr().err.strip()

def test_cli_invalid_env(monkeypatch, capsys):
    monkeypatch.setenv("MONI_ROLE", "quatsch")
    assert cli.main(["token", "list"]) == 2
    assert capsys.readouterr().err.strip()


def test_admin_scope_allowed(tmp_path):
    tok, _ = TokenStore(tmp_path / "t.json").create("x", scope="admin")
    assert tok.scope == "admin"


def test_last_used_written_at_most_hourly(tmp_path):
    from datetime import timedelta
    from moni.timeutil import utcnow
    s = TokenStore(tmp_path / "tokens.json")
    tok, plain = s.create("x")
    now = utcnow()
    assert s.verify(plain, now).last_used_at is None  # das zurückgegebene Token ist der Stand vor der Nutzung
    first = s.list()[0].last_used_at
    assert first is not None
    s.verify(plain, now + timedelta(minutes=30))
    assert s.list()[0].last_used_at == first
    s.verify(plain, now + timedelta(minutes=61))
    assert s.list()[0].last_used_at != first


def test_file_from_2_0_without_new_fields_loads(tmp_path):
    import json
    p = tmp_path / "tokens.json"
    s = TokenStore(p)
    _, plain = s.create("alt", "admin")
    data = json.loads(p.read_text())
    for t in data["tokens"]:
        del t["created_by"], t["last_used_at"]
    p.write_text(json.dumps(data))
    tok = s.verify(plain)
    assert tok and tok.created_by is None and tok.expires_at is None


def test_bad_created_by_fails_closed(tmp_path):
    import json
    p = tmp_path / "tokens.json"
    s = TokenStore(p)
    _, plain = s.create("x", created_by=1)
    data = json.loads(p.read_text())
    data["tokens"][0]["created_by"] = "1"
    p.write_text(json.dumps(data))
    assert s.verify(plain) is None


def test_unwritable_last_used_does_not_block_login(tmp_path, monkeypatch):
    s = TokenStore(tmp_path / "tokens.json")
    _, plain = s.create("x")
    monkeypatch.setattr(s, "_save", lambda tokens: (_ for _ in ()).throw(OSError("ro")))
    assert s.verify(plain) is not None
