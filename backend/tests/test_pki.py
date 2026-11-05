import base64
import os
import ssl
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

from moni import pki

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
PEM = serialization.Encoding.PEM


def name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def test_issue_ignores_csr_extensions_and_sets_own_san_and_eku():
    ca_key, ca = pki.create_ca(NOW)
    key = pki.new_key()
    csr = x509.CertificateSigningRequestBuilder().subject_name(name("evil")).add_extension(
        x509.SubjectAlternativeName([x509.DNSName("bank.example")]), False).add_extension(
        x509.BasicConstraints(ca=True, path_length=None), True).sign(key, hashes.SHA256())
    cert = pki.issue(ca_key, ca, csr.public_bytes(PEM), cn="agent", sans=["10.0.0.5"], usage="server", now=NOW, days=90)
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert [str(ip) for ip in san.get_values_for_type(x509.IPAddress)] == ["10.0.0.5"]
    assert san.get_values_for_type(x509.DNSName) == []
    assert pki.common_name(cert) == "agent"
    assert pki.has_eku(cert, "server") and not pki.has_eku(cert, "client")
    assert pki.matches_key(cert, key)
    assert not pki.matches_key(cert, pki.new_key())
    assert cert.not_valid_before_utc == NOW - timedelta(minutes=5)


def _bad_csr(kind):
    if kind == "rsa_csr":
        k = rsa.generate_private_key(65537, 2048)
        return x509.CertificateSigningRequestBuilder().subject_name(name("x")).sign(k, hashes.SHA256()).public_bytes(PEM)
    if kind == "p384_csr":
        k = ec.generate_private_key(ec.SECP384R1())
        return x509.CertificateSigningRequestBuilder().subject_name(name("x")).sign(k, hashes.SHA256()).public_bytes(PEM)
    if kind == "tampered_signature":
        csr = x509.load_pem_x509_csr(pki.make_csr(pki.new_key(), "x"))
        raw = bytearray(csr.public_bytes(serialization.Encoding.DER))
        raw[-1] ^= 0xFF
        b64 = base64.encodebytes(bytes(raw))
        return b"-----BEGIN CERTIFICATE REQUEST-----\n" + b64 + b"-----END CERTIFICATE REQUEST-----\n"
    return b"garbage"


@pytest.mark.parametrize("bad", ["rsa_csr", "tampered_signature", "garbage", "p384_csr"])
def test_issue_rejects_bad_csr(bad):
    ca_key, ca = pki.create_ca(NOW)
    with pytest.raises(ValueError, match="CSR ist ungültig") as ei:
        pki.issue(ca_key, ca, _bad_csr(bad), cn="a", sans=["localhost"], usage="server", now=NOW, days=1)
    assert ei.value.__context__ is None


def test_fingerprint_format():
    key = pki.new_key()
    c = pki.self_signed(key, "a", ["localhost"], NOW, 1)
    fp = pki.fingerprint(c)
    assert len(fp) == 43 and "=" not in fp and fp == pki.fingerprint(pki.load_cert(pki.cert_pem(c)))


def test_key_roundtrip_and_san_for_host():
    key = pki.new_key()
    assert pki.key_pem(key) == pki.key_pem(pki.load_key(pki.key_pem(key)))
    assert pki.key_pem(key).startswith(b"-----BEGIN PRIVATE KEY-----")
    for h in ("10.0.0.5", "::1", "agent.example.org", "localhost"):
        assert pki.san_for_host(h) == [h]
    for h in ("", "a b", "-x", "x-", "a/b", "a" * 254, "[::1]"):
        with pytest.raises(ValueError, match="Ungültiger Hostname"):
            pki.san_for_host(h)


def _pump(*pairs):
    # Schiebt Bytes zwischen zwei SSLObjects, bis beide fertig sind oder einer scheitert.
    (c, c_in, c_out), (s, s_in, s_out) = pairs
    done = {"c": False, "s": False}
    for _ in range(50):
        for k, obj, inb, outb in (("c", c, c_in, c_out), ("s", s, s_in, s_out)):
            if not done[k]:
                try:
                    obj.do_handshake()
                    done[k] = True
                except ssl.SSLWantReadError:
                    pass
            data = outb.read()
            if data:
                (s_in if k == "c" else c_in).write(data)
        if all(done.values()):
            return


def _handshake(server_ctx, client_ctx, hostname):
    c_in, c_out, s_in, s_out = ssl.MemoryBIO(), ssl.MemoryBIO(), ssl.MemoryBIO(), ssl.MemoryBIO()
    c = client_ctx.wrap_bio(c_in, c_out, server_hostname=hostname)
    s = server_ctx.wrap_bio(s_in, s_out, server_side=True)
    _pump((c, c_in, c_out), (s, s_in, s_out))
    return c, s


@pytest.fixture
def world():
    now = datetime.now(timezone.utc)
    ca_key, ca = pki.create_ca(now)
    akey = pki.new_key()
    agent = pki.issue(ca_key, ca, pki.make_csr(akey, "x"), cn="agent", sans=["127.0.0.1"], usage="server", now=now, days=90)
    mkey = pki.new_key()
    mgr = pki.issue(ca_key, ca, pki.make_csr(mkey, "x"), cn="moni-manager", sans=["moni-manager"], usage="client", now=now, days=365)
    return dict(ca_key=ca_key, ca=ca, now=now, agent=agent, akey=akey, mgr=mgr, mkey=mkey)


def test_tls_roundtrip_mtls_in_memory(world):
    w = world
    ca_pem = pki.cert_pem(w["ca"])
    srv = pki.server_context(pki.cert_pem(w["agent"]), pki.key_pem(w["akey"]), ca_pem)
    assert srv.minimum_version == ssl.TLSVersion.TLSv1_2
    cli = pki.client_context(ca_pem, pki.cert_pem(w["mgr"]), pki.key_pem(w["mkey"]))
    c, s = _handshake(srv, cli, "127.0.0.1")
    cert = s.getpeercert()
    assert dict(x[0] for x in cert["subject"])["commonName"] == "moni-manager"
    c.write(b"ping")
    assert c.getpeercert()  # Client prüft Agent-Zertifikat

    # ohne Client-Zertifikat: Handshake klappt (CERT_OPTIONAL), kein Peer-Zertifikat
    c, s = _handshake(srv, pki.client_context(ca_pem), "127.0.0.1")
    assert not s.getpeercert()

    # Client-Zertifikat einer fremden CA → Handshake-Fehler
    fk, fca = pki.create_ca(w["now"])
    k = pki.new_key()
    foreign = pki.issue(fk, fca, pki.make_csr(k, "x"), cn="moni-manager", sans=["m"], usage="client", now=w["now"], days=1)
    with pytest.raises(ssl.SSLError):
        _handshake(srv, pki.client_context(ca_pem, pki.cert_pem(foreign), pki.key_pem(k)), "127.0.0.1")


def test_client_context_checks_hostname(world):
    w = world
    ca_pem = pki.cert_pem(w["ca"])
    srv = pki.server_context(pki.cert_pem(w["agent"]), pki.key_pem(w["akey"]), None)
    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(srv, pki.client_context(ca_pem), "10.0.0.6")


def test_load_chain_into_leaves_no_plain_key_in_tmp(tmp_path, monkeypatch):
    key = pki.new_key()
    cert = pki.self_signed(key, "a", ["localhost"], NOW, 1)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    seen = []
    orig = ssl.SSLContext.load_cert_chain

    def spy(self, certfile, keyfile=None, password=None):
        seen.extend(open(p, "rb").read() for p in (certfile, keyfile))
        return orig(self, certfile, keyfile, password)

    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", spy)
    pki.load_chain_into(ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER), pki.cert_pem(cert), pki.key_pem(key))
    assert len(seen) == 2 and all(b"BEGIN PRIVATE KEY" not in d for d in seen)
    assert b"ENCRYPTED PRIVATE KEY" in seen[1]
    assert list(tmp_path.iterdir()) == []


def test_write_private_mode_and_atomic(tmp_path):
    p = tmp_path / "k.pem"
    pki.write_private(p, b"one")
    pki.write_private(p, b"two")
    assert p.read_bytes() == b"two"
    assert os.stat(p).st_mode & 0o777 == 0o600
    assert [f.name for f in tmp_path.iterdir()] == ["k.pem"]


def test_days_left_and_create_ca_constraints():
    _, ca = pki.create_ca(NOW)
    assert pki.common_name(ca) == "Moni Manager CA"
    bc = ca.extensions.get_extension_for_class(x509.BasicConstraints)
    assert bc.critical and bc.value.ca and bc.value.path_length == 0
    ku = ca.extensions.get_extension_for_class(x509.KeyUsage)
    assert ku.critical and ku.value.key_cert_sign and ku.value.crl_sign and not ku.value.digital_signature
    assert ca.not_valid_after_utc - ca.not_valid_before_utc >= timedelta(days=3650)
    assert pki.days_left(ca, NOW) == pytest.approx(3650, abs=1)
    assert pki.days_left(ca, NOW + timedelta(days=3650 + 1)) < 0
