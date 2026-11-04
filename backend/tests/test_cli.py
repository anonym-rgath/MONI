import io
import pytest
from moni import cli
from moni.manager import db
from moni.secretbox import generate_key


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MONI_SECRET_KEY", generate_key())
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


def test_cli_token_scope(env, capsys):
    from moni.auth.tokens import TokenStore
    assert cli.main(["token", "create", "--name", "a", "--scope", "admin"]) == 0
    assert cli.main(["token", "create", "--name", "r"]) == 0
    assert [t.scope for t in TokenStore(env / "tokens.json").list()] == ["admin", "read"]
