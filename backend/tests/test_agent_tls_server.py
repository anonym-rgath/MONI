"""Echter uvicorn-Server mit TLS, gebaut wie in __main__ (D3: hängt an uvicorn 0.48.0)."""
import base64
import hashlib
import http.client
import json
import ssl
from urllib.parse import urlsplit

import pytest

from moni import pki
from moni.timeutil import utcnow


def _client_cert(ca, cn="moni-manager"):
    key = pki.new_key()
    cert = pki.issue(*ca, pki.make_csr(key, cn), cn=cn, sans=[cn], usage="client", now=utcnow(), days=365)
    return pki.cert_pem(cert), pki.key_pem(key)


@pytest.fixture
def ca():
    return pki.create_ca(utcnow())


@pytest.fixture
def ca_pem(ca):
    return pki.cert_pem(ca[1])


@pytest.fixture
def manager_ctx(ca, ca_pem):
    return pki.client_context(ca_pem, *_client_cert(ca))


@pytest.fixture
def foreign_ctx(ca_pem):
    return pki.client_context(ca_pem, *_client_cert(pki.create_ca(utcnow())))  # vertraut dem Agent, Zertifikat von fremder CA


@pytest.fixture
def plain_ctx(ca_pem):
    return pki.client_context(ca_pem)


def _pair(server, ca):
    cert = pki.issue(*ca, server.state.new_csr(), cn="moni-agent", sans=["127.0.0.1"], usage="server", now=utcnow(), days=90)
    server.state.install(pki.cert_pem(cert), pki.cert_pem(ca[1]), utcnow())


def _conn(server, ctx):
    return http.client.HTTPSConnection("127.0.0.1", urlsplit(server.url).port, context=ctx, timeout=5)


def get(server, path, ctx):
    c = _conn(server, ctx)
    try:
        c.request("GET", path)
        r = c.getresponse()
        assert r.status == 200
        return json.loads(r.read())
    finally:
        c.close()


def _pinned_ctx():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # Pinning-Fall: Fingerabdruck wird selbst verglichen
    return ctx


def test_peer_cert_reaches_app_only_with_manager_cert(agent_tls_server, ca, ca_pem, manager_ctx, foreign_ctx, plain_ctx):
    _pair(agent_tls_server, ca)
    chain = get(agent_tls_server, "/_test/peer", manager_ctx)["client_cert_chain"]
    assert len(chain) == 1
    peer = pki.load_cert(chain[0].encode())
    assert pki.common_name(peer) == "moni-manager" and pki.has_eku(peer, "client")
    assert get(agent_tls_server, "/_test/peer", plain_ctx) is None  # ohne Zertifikat: Handshake ok, keine Erweiterung
    # fremde CA: Der Server bricht den Handshake ab. Unter TLS 1.3 merkt der Client das erst beim Lesen
    # (SSLError oder Verbindungsabbruch, beides OSError), unter TLS 1.2 schon im Handshake.
    with pytest.raises(OSError):
        get(agent_tls_server, "/_test/peer", foreign_ctx)
    foreign_ctx.maximum_version = manager_ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    with pytest.raises(ssl.SSLError):
        get(agent_tls_server, "/_test/peer", foreign_ctx)
    assert get(agent_tls_server, "/_test/peer", manager_ctx)["client_cert_chain"]  # TLS 1.2 selbst geht
    assert get(agent_tls_server, "/api/v1/health", plain_ctx) == {"status": "ok"}


def test_context_reload_after_install_affects_new_connections(agent_tls_server, ca, ca_pem, manager_ctx):
    ctx = agent_tls_server.state.context
    c = _conn(agent_tls_server, _pinned_ctx())
    c.connect()
    der = c.sock.getpeercert(binary_form=True)
    c.close()
    assert base64.urlsafe_b64encode(hashlib.sha256(der).digest()).rstrip(b"=").decode() == agent_tls_server.fp
    pinned_with_cert = _pinned_ctx()
    pki.load_chain_into(pinned_with_cert, *_client_cert(ca))
    assert get(agent_tls_server, "/_test/peer", pinned_with_cert) is None  # ungekoppelt: keine Client-Zertifikate (D3)
    with pytest.raises(ssl.SSLError):  # vorläufiges Zertifikat (selbstsigniert, SAN localhost) besteht keine CA-Prüfung
        get(agent_tls_server, "/api/v1/health", pki.client_context(ca_pem))
    _pair(agent_tls_server, ca)
    assert agent_tls_server.state.context is ctx
    assert get(agent_tls_server, "/api/v1/health", pki.client_context(ca_pem)) == {"status": "ok"}
    assert get(agent_tls_server, "/_test/peer", manager_ctx)["client_cert_chain"]


def _status(server, path, ctx):
    c = _conn(server, ctx)
    try:
        c.request("GET", path)
        return c.getresponse().status
    finally:
        c.close()


def test_manager_cert_reaches_snapshot_on_real_server(agent_tls_server, ca, manager_ctx, plain_ctx, monkeypatch):
    from conftest import raw
    from moni import collector as cmod
    monkeypatch.setattr(cmod, "read_raw", lambda s, c: raw(utcnow()))
    agent_tls_server.app.state.collector.collect_once()
    _pair(agent_tls_server, ca)
    assert _status(agent_tls_server, "/api/v1/snapshot", manager_ctx) == 200
    assert _status(agent_tls_server, "/api/v1/snapshot", plain_ctx) == 401  # ohne Zertifikat und ohne Bearer
