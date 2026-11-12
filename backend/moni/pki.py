"""PKI-Bausteine: CA, Schlüssel, CSR, Ausstellen, Fingerabdrücke, SSL-Kontexte (EC P-256)."""
import base64
import ipaddress
import os
import re
import secrets
import ssl
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from moni.fsutil import write_private  # noqa: F401  (Re-Export)

_HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*")
_EKU = {"server": ExtendedKeyUsageOID.SERVER_AUTH, "client": ExtendedKeyUsageOID.CLIENT_AUTH}


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def key_pem(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def load_key(pem: bytes):
    return serialization.load_pem_private_key(pem, password=None)


def cert_pem(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def load_cert(pem: bytes) -> x509.Certificate:
    return x509.load_pem_x509_certificate(pem)


def fingerprint(cert) -> str:
    digest = cert.fingerprint(hashes.SHA256())
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _builder(subject, issuer, pub, now, days, issuer_pub=None):
    # SKI/AKI: Python >= 3.13 prüft strikt (VERIFY_X509_STRICT) und verlangt den AKI.
    return (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(issuer).public_key(pub)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(pub), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_pub or pub), critical=False)
    )


def _san(sans: list[str]) -> x509.SubjectAlternativeName:
    names: list[x509.GeneralName] = []
    for s in sans:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(s)))
        except ValueError:
            names.append(x509.DNSName(s))
    return x509.SubjectAlternativeName(names)


def create_ca(now: datetime):
    key = new_key()
    n = _name("Moni Manager CA")
    cert = (
        _builder(n, n, key.public_key(), now, 3650)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def self_signed(key, cn: str, sans: list[str], now: datetime, days: int) -> x509.Certificate:
    n = _name(cn)
    return (
        _builder(n, n, key.public_key(), now, days)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_san(sans), critical=False)
        .add_extension(x509.ExtendedKeyUsage([_EKU["server"]]), critical=False)
        .sign(key, hashes.SHA256())
    )


def make_csr(key, cn: str) -> bytes:
    csr = x509.CertificateSigningRequestBuilder().subject_name(_name(cn)).sign(key, hashes.SHA256())
    return csr.public_bytes(serialization.Encoding.PEM)


def _load_csr(pem: bytes):
    try:
        csr = x509.load_pem_x509_csr(pem)
        pub = csr.public_key()
        ok = csr.is_signature_valid and isinstance(pub, ec.EllipticCurvePublicKey) and isinstance(pub.curve, ec.SECP256R1)
    except Exception:
        return None
    return csr if ok else None


def issue(ca_key, ca_cert, csr_pem: bytes, *, cn: str, sans: list[str],
          usage: Literal["server", "client"], now: datetime, days: int) -> x509.Certificate:
    csr = _load_csr(csr_pem)
    if not sans:
        raise ValueError("SAN fehlt")
    if csr is None:  # außerhalb von except, damit kein __context__ mitläuft
        raise ValueError("CSR ist ungültig")
    # Nur den öffentlichen Schlüssel aus der CSR übernehmen; Namen und Extensions sind fest.
    return (
        _builder(_name(cn), ca_cert.subject, csr.public_key(), now, days, ca_key.public_key())
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([_EKU[usage]]), critical=False)
        .add_extension(_san(sans), critical=False)
        .sign(ca_key, hashes.SHA256())
    )


def san_for_host(host: str) -> list[str]:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or not _HOST_RE.fullmatch(host):
            raise ValueError("Ungültiger Hostname") from None
    return [host]


def matches_key(cert, key) -> bool:
    fmt = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return cert.public_key().public_bytes(*fmt) == key.public_key().public_bytes(*fmt)


def days_left(cert, now: datetime) -> float:
    return (cert.not_valid_after_utc - now).total_seconds() / 86400


def has_eku(cert, usage: Literal["server", "client"]) -> bool:
    try:
        return _EKU[usage] in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound:
        return False


def common_name(cert) -> str | None:
    attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return str(attrs[0].value) if attrs else None


def load_chain_into(ctx: ssl.SSLContext, cert_pem: bytes, key_pem: bytes) -> None:
    """ssl kann nur aus Dateien laden: Schlüssel nur verschlüsselt und kurz auf die Platte (D4)."""
    password = secrets.token_bytes(32)
    enc = load_key(key_pem).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(password))
    paths: list[str] = []
    try:
        for data in (cert_pem, enc):
            fd, p = tempfile.mkstemp(dir=tempfile.gettempdir())  # mkstemp legt mit 0600 an
            paths.append(p)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
        ctx.load_cert_chain(paths[0], paths[1], password=password)
    finally:
        for p in paths:
            Path(p).unlink(missing_ok=True)


def server_context(cert_pem: bytes, key_pem: bytes, ca_pem: bytes | None) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    load_chain_into(ctx, cert_pem, key_pem)
    if ca_pem is not None:
        ctx.verify_mode = ssl.CERT_OPTIONAL
        ctx.load_verify_locations(cadata=ca_pem.decode())
    return ctx


def client_context(ca_pem: bytes | None, cert_pem: bytes | None = None, key_pem: bytes | None = None) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cadata=ca_pem.decode() if ca_pem else None)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if (cert_pem is None) != (key_pem is None):
        raise ValueError("Zertifikat und Schlüssel nur zusammen angeben")
    if cert_pem is not None:
        load_chain_into(ctx, cert_pem, key_pem)
    return ctx
