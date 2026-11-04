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
        TokenStore(tmp_path / "t.json").create("x", scope="admin")

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
