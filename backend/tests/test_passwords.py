import pytest

from moni.auth.passwords import check_policy, dummy_verify, hash_password, verify_password


def test_hash_is_argon2id_phc_and_verifies():
    h = hash_password("korrekt-pferd-batterie")
    assert h.startswith("$argon2id$") and "m=19456" in h and "t=2" in h and "p=1" in h
    assert verify_password("korrekt-pferd-batterie", h) and not verify_password("falsch-falsch-falsch", h)


def test_same_password_different_salt():
    assert hash_password("x" * 12) != hash_password("x" * 12)


@pytest.mark.parametrize("phc", ["", "kaputt", "$argon2id$v=19$m=1$", "$2b$12$abcdefghijklmnopqrstuv"])
def test_verify_never_raises(phc):
    assert verify_password("irgendwas-langes", phc) is False


@pytest.mark.parametrize("pw,msg", [("kurz", "mindestens 12"), ("a" * 257, "höchstens 256"), ("Admin@Example.org", "E-Mail")])
def test_policy(pw, msg):
    with pytest.raises(ValueError, match=msg):
        check_policy(pw, "admin@example.org")


def test_dummy_verify_runs():
    dummy_verify("egal")  # darf nichts werfen


@pytest.mark.parametrize("pw", ["\ud800" + "x" * 12, "x" * 12 + "\udfff", "\ud800"])
def test_policy_rejects_lone_surrogates_without_echo(pw):
    with pytest.raises(ValueError, match="^Passwort enthält ungültige Zeichen$"):
        check_policy(pw, "a@b.de")
