"""Manager-CA und Client-Zertifikat (D1, D4, D11)."""
import ssl
import stat
from datetime import timedelta

import pytest
from cryptography import x509

from conftest import Dummy
from moni import pki
from moni.manager.ca import ManagerPki
from moni.secretbox import SecretBox, SecretError, generate_key
from moni.timeutil import utcnow

NOW = utcnow()


def _mode(p):
    return stat.S_IMODE(p.stat().st_mode)


@pytest.fixture
def key():
    return generate_key()


@pytest.fixture
def mp(tmp_path, key):
    p = ManagerPki(tmp_path, SecretBox(key))
    p.ensure(NOW)
    return p


def _files(d):
    return {f.name: f.read_bytes() for f in d.iterdir()}


def test_ca_files_encrypted_and_0600(mp, tmp_path, key):
    d = tmp_path / "tls"
    assert set(_files(d)) == {"ca.crt", "ca.key.enc", "client.crt", "client.key.enc"}
    assert _mode(d) == 0o700 and all(_mode(f) == 0o600 for f in d.iterdir())
    box = SecretBox(key)
    ca = pki.load_cert((d / "ca.crt").read_bytes())
    assert mp.ca_pem == (d / "ca.crt").read_bytes()
    for name, cert in (("ca.key.enc", ca), ("client.key.enc", pki.load_cert((d / "client.crt").read_bytes()))):
        raw = (d / name).read_bytes()
        assert b"PRIVATE KEY" not in raw
        assert pki.matches_key(cert, pki.load_key(box.decrypt(raw.decode()).encode()))
    assert pki.common_name(ca) == "Moni Manager CA" and 3649 < pki.days_left(ca, NOW) <= 3650
    client = pki.load_cert((d / "client.crt").read_bytes())
    client.verify_directly_issued_by(ca)
    assert pki.common_name(client) == "moni-manager" and pki.has_eku(client, "client")
    assert not pki.has_eku(client, "server") and 364 < pki.days_left(client, NOW) <= 365


def test_ensure_is_idempotent_and_reloads(mp, tmp_path, key):
    before = _files(tmp_path / "tls")
    mp.ensure(NOW)
    again = ManagerPki(tmp_path, SecretBox(key))
    again.ensure(NOW)
    assert _files(tmp_path / "tls") == before and again.ca_pem == mp.ca_pem


def test_client_cert_renewed_from_60_days_and_context_rebuilt(mp, tmp_path):
    d = tmp_path / "tls"
    ctx = mp.client_context()
    assert mp.client_context() is ctx
    before = _files(d)
    mp.ensure(NOW + timedelta(days=304))  # noch 61 Tage
    assert _files(d) == before and mp.client_context() is ctx
    mp.ensure(NOW + timedelta(days=306))  # noch 59 Tage
    after = _files(d)
    assert after["client.crt"] != before["client.crt"] and after["client.key.enc"] != before["client.key.enc"]
    assert after["ca.crt"] == before["ca.crt"] and after["ca.key.enc"] == before["ca.key.enc"]
    assert mp.client_context() is not ctx


def test_client_context_is_strict(mp):
    ctx = mp.client_context()
    assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
    assert [c["subject"] for c in ctx.get_ca_certs()] == [((("commonName", "Moni Manager CA"),),)]


def test_wrong_secret_key_never_replaces_ca(mp, tmp_path):
    before = _files(tmp_path / "tls")
    with pytest.raises(SecretError):
        ManagerPki(tmp_path, SecretBox(generate_key())).ensure(NOW)
    assert _files(tmp_path / "tls") == before


@pytest.mark.parametrize("missing", ["ca.key.enc", "ca.crt"])
def test_half_ca_is_refused_not_replaced(mp, tmp_path, key, missing):  # P4-R5
    (tmp_path / "tls" / missing).unlink()
    before = _files(tmp_path / "tls")
    with pytest.raises(RuntimeError) as e:
        ManagerPki(tmp_path, SecretBox(key)).ensure(NOW)
    assert str(e.value) == ("CA-Dateien unvollständig (ca.crt oder ca.key.enc fehlt) – Sicherung zurückspielen oder "
                            "beide löschen, um eine neue CA zu erzeugen (alle Kopplungen gehen verloren)")
    assert _files(tmp_path / "tls") == before


def test_new_ca_only_when_both_missing(mp, tmp_path, key):
    d = tmp_path / "tls"
    old = mp.ca_pem
    (d / "ca.crt").unlink()
    (d / "ca.key.enc").unlink()
    ManagerPki(tmp_path, SecretBox(key)).ensure(NOW)
    assert (d / "ca.crt").read_bytes() != old
    pki.load_cert((d / "client.crt").read_bytes()).verify_directly_issued_by(pki.load_cert((d / "ca.crt").read_bytes()))


HOSTS = ("moni.example.org", "localhost", "127.0.0.1")


def _https(d):
    return pki.load_cert((d / "https.crt").read_bytes())


def _sans(cert):
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return [str(v.value) for v in san]


def test_https_cert_sans_and_renewal(mp, tmp_path):
    d = tmp_path / "tls"
    assert mp.ensure_https(HOSTS, NOW) is True
    assert _mode(d / "https.crt") == 0o600 and _mode(d / "https.key") == 0o600
    cert = _https(d)
    cert.verify_directly_issued_by(pki.load_cert(mp.ca_pem))
    assert pki.matches_key(cert, pki.load_key((d / "https.key").read_bytes()))  # Klartext für nginx (D4)
    assert _sans(cert) == list(HOSTS)
    assert pki.has_eku(cert, "server") and not pki.has_eku(cert, "client")
    assert pki.common_name(cert) != "moni-manager" and 396 < pki.days_left(cert, NOW) <= 397
    before = _files(d)
    assert mp.ensure_https(HOSTS, NOW) is False and _files(d) == before
    assert mp.ensure_https(HOSTS, NOW + timedelta(days=366)) is False and _files(d) == before  # noch 31 Tage
    assert mp.ensure_https(HOSTS, NOW + timedelta(days=368)) is True  # noch 29 Tage
    renewed = _files(d)
    assert renewed["https.crt"] != before["https.crt"] and renewed["https.key"] != before["https.key"]
    assert 396 < pki.days_left(_https(d), NOW + timedelta(days=368)) <= 397
    assert {k: v for k, v in renewed.items() if not k.startswith("https")} == \
        {k: v for k, v in before.items() if not k.startswith("https")}


def test_https_cert_renewed_on_hostname_change_or_damage(mp, tmp_path):
    d = tmp_path / "tls"
    assert mp.ensure_https(HOSTS, NOW) is True
    assert mp.ensure_https(("neu.example.org", "localhost"), NOW) is True
    assert _sans(_https(d)) == ["neu.example.org", "localhost"]
    (d / "https.key").write_bytes(pki.key_pem(pki.new_key()))  # Schlüssel passt nicht mehr zum Zertifikat
    assert mp.ensure_https(("neu.example.org", "localhost"), NOW) is True
    (d / "https.crt").unlink()
    assert mp.ensure_https(("neu.example.org", "localhost"), NOW) is True
    assert mp.ensure_https(("neu.example.org", "localhost"), NOW) is False


def test_sign_agent_uses_host_not_csr(mp):
    csr = pki.make_csr(pki.new_key(), "evil.example.org")
    cert = pki.load_cert(mp.sign_agent(csr, "127.0.0.1", NOW))
    cert.verify_directly_issued_by(pki.load_cert(mp.ca_pem))
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert [str(ip) for ip in san.get_values_for_type(x509.IPAddress)] == ["127.0.0.1"]
    assert san.get_values_for_type(x509.DNSName) == [] and pki.common_name(cert) == "moni-agent"
    assert pki.has_eku(cert, "server") and not pki.has_eku(cert, "client") and 89 < pki.days_left(cert, NOW) <= 90
    with pytest.raises(ValueError):
        mp.sign_agent(csr, "bad host!", NOW)
    with pytest.raises(ValueError):
        mp.sign_agent(b"kein csr", "127.0.0.1", NOW)


def test_prepare_reencrypt_commit_and_abort(mp, tmp_path, key):
    d = tmp_path / "tls"
    before = _files(d)
    new = generate_key()
    rot = ManagerPki(tmp_path, SecretBox(f"{new},{key}"))
    n, commit, abort = rot.prepare_reencrypt()
    tmps = list(d.glob("*.tmp"))
    assert n == 2 and len(tmps) == 2 and all(_mode(t) == 0o600 for t in tmps)
    assert {k: v for k, v in _files(d).items() if not k.endswith(".tmp")} == before
    abort()
    assert _files(d) == before
    n, commit, abort = rot.prepare_reencrypt()
    commit()
    after = _files(d)
    assert set(after) == set(before) and after["ca.crt"] == before["ca.crt"]
    assert after["ca.key.enc"] != before["ca.key.enc"] and after["client.key.enc"] != before["client.key.enc"]
    only_new = ManagerPki(tmp_path, SecretBox(new))
    only_new.ensure(NOW)  # entschlüsselt beide Schlüssel nur mit dem neuen Schlüssel
    assert only_new.client_context() is not None


def test_prepare_reencrypt_failure_leaves_no_tmp(mp, tmp_path):
    before = _files(tmp_path / "tls")
    with pytest.raises(SecretError):
        ManagerPki(tmp_path, SecretBox(generate_key())).prepare_reencrypt()
    assert _files(tmp_path / "tls") == before


def test_ca_crt_public(mgr_client, mgr_app, mgr_settings):
    r = mgr_client.get("/api/v1/ca.crt")  # ohne Anmeldung (D11)
    assert r.status_code == 200 and r.headers["content-type"] == "application/x-pem-file"
    assert r.content == mgr_app.state.pki.ca_pem == (mgr_settings.db_path.parent / "tls" / "ca.crt").read_bytes()
    assert b"PRIVATE" not in r.content


def test_app_start_wires_pki_into_poller(mgr_settings):
    from moni.app import create_app
    app = create_app(mgr_settings, collector=Dummy())
    assert isinstance(app.state.pki, ManagerPki) and app.state.poller._pki is app.state.pki
