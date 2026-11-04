import pytest
from moni import cli
from moni.secretbox import SecretBox, SecretError, generate_key


def test_roundtrip_and_no_plaintext():
    box = SecretBox(generate_key())
    tok = box.encrypt("agent-token-123")
    assert "agent-token-123" not in tok and box.decrypt(tok) == "agent-token-123"


def test_key_rotation():
    old, new = generate_key(), generate_key()
    tok = SecretBox(old).encrypt("x")
    both = SecretBox(f" {new} , {old} ")
    assert both.decrypt(tok) == "x"
    assert SecretBox(new).decrypt(both.rotate(tok)) == "x"


def test_wrong_key_raises_secret_error():
    tok = SecretBox(generate_key()).encrypt("x")
    with pytest.raises(SecretError) as e:
        SecretBox(generate_key()).decrypt(tok)
    assert str(e.value) == "Geheimnis nicht entschlüsselbar" and e.value.__cause__ is None


@pytest.mark.parametrize("bad", ["not-a-token", "", 5, None])
def test_garbage_ciphertext_raises_secret_error(bad):
    with pytest.raises(SecretError) as e:
        SecretBox(generate_key()).decrypt(bad)
    assert str(e.value) == "Geheimnis nicht entschlüsselbar"
    assert "not-a-token" not in repr(e.value) and e.value.__suppress_context__


@pytest.mark.parametrize("spec", ["kein-schluessel-xyz", "", "   ", ",", f"{generate_key()},kaputt-xyz", f"{generate_key()},,"])
def test_invalid_key_message_hides_value(spec):
    with pytest.raises(ValueError) as e:
        SecretBox(spec)
    assert str(e.value) == "MONI_SECRET_KEY ist ungültig (erwartet Fernet-Schlüssel, ggf. kommagetrennt)"
    assert "xyz" not in str(e.value) and e.value.__suppress_context__


def test_cli_secret_generate(capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("MONI_SECRET_KEY", raising=False)
    monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path / "gibtsnicht"))
    assert cli.main(["secret", "generate"]) == 0
    out = capsys.readouterr().out
    assert len(out.splitlines()) == 1
    SecretBox(out.strip())
    assert not (tmp_path / "gibtsnicht").exists()


def test_encrypt_bad_text_leaks_nothing():
    with pytest.raises(ValueError) as e:
        SecretBox(generate_key()).encrypt("geheim\ud800")
    assert str(e.value) == "Geheimnis kann nicht gespeichert werden"
    assert e.value.__cause__ is None and e.value.__context__ is None and "geheim" not in repr(e.value)


def test_decrypt_error_has_no_context():
    with pytest.raises(SecretError) as e:
        SecretBox(generate_key()).decrypt(SecretBox(generate_key()).encrypt("x"))
    assert e.value.__context__ is None
