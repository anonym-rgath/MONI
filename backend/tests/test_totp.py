import re

import pytest
from conftest import code_at

from moni.auth import totp


def test_match_window_and_replay():
    s, t = totp.new_secret(), 1_800_000_000
    step = totp.match_step(s, code_at(s, t - 30), t, None)
    assert step == t // 30 - 1
    assert totp.match_step(s, code_at(s, t - 30), t, step) is None          # Wiederverwendung
    assert totp.match_step(s, code_at(s, t - 90), t, None) is None          # außerhalb ±1
    assert totp.match_step(s, " " + code_at(s, t)[:3] + " " + code_at(s, t)[3:], t, None) == t // 30


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "١٢٣٤٥٦"])
def test_rejects_non_six_ascii_digits(bad):
    assert totp.match_step(totp.new_secret(), bad, 1_800_000_000, None) is None


def test_uri():
    u = totp.provisioning_uri("ABC", "a@b.de")
    assert u.startswith("otpauth://totp/Moni:a%40b.de?") and "secret=ABC" in u and "issuer=Moni" in u


def test_recovery_codes():
    codes = totp.new_recovery_codes()
    assert len(codes) == len(set(codes)) == 10
    assert all(re.fullmatch(r"[a-z2-7]{5}-[a-z2-7]{5}", c) for c in codes)
    assert totp.hash_code(codes[0].upper().replace("-", " ")) == totp.hash_code(codes[0])
