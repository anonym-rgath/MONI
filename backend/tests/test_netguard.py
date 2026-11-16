import socket

import pytest

from moni import netguard
from moni.auth import oidc
from moni.config import Settings
from moni.manager.alert_channels import SendError, send_email, send_webhook
from moni.manager.poller import AgentClient, AgentError

BLOCKED = ["169.254.169.254", "169.254.0.1", "fe80::1", "fe80::1%eth0", "::ffff:169.254.169.254", "fd00:ec2::254"]
PRIVATE = ["127.0.0.1", "10.0.0.5", "192.168.1.10", "172.17.0.2", "100.64.0.1", "fd12::1", "::1", "0.0.0.0"]


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(netguard, "allow_private", True)


@pytest.fixture
def connects(monkeypatch):
    """Jeder Verbindungsversuch wird notiert und scheitert sofort; gesperrte Ziele dürfen hier nie auftauchen."""
    seen = []

    def spy(self, addr):
        seen.append(addr[0])
        raise ConnectionRefusedError
    monkeypatch.setattr(socket.socket, "connect", spy)
    return seen


@pytest.mark.parametrize("ip", BLOCKED)
def test_link_local_and_metadata_always_blocked(ip):
    with pytest.raises(netguard.Blocked):
        netguard.check(ip)


@pytest.mark.parametrize("ip", PRIVATE)
def test_private_allowed_by_default_blocked_with_switch(ip, monkeypatch):
    netguard.check(ip)
    monkeypatch.setattr(netguard, "allow_private", False)
    with pytest.raises(netguard.Blocked):
        netguard.check(ip)


def test_public_allowed_with_switch(monkeypatch):
    monkeypatch.setattr(netguard, "allow_private", False)
    netguard.check("93.184.215.14")
    netguard.check("2606:4700::1111")


def test_hostname_resolving_to_metadata_is_blocked(monkeypatch):
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                                                    ("169.254.169.254", a[1]))])
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: calls.append(a))
    with pytest.raises(netguard.Blocked):
        netguard.create_connection(("metadata.example.org", 80), 1)
    assert calls == []  # nie verbunden


def test_agent_client_blocked(connects):
    with pytest.raises(AgentError) as ei:
        AgentClient("http://169.254.169.254", "TOKEN", {}, 2).get("/api/v1/info")
    assert ei.value.message == "Agent nicht erreichbar"
    with pytest.raises(AgentError):
        AgentClient("https://[fe80::1]:9810", "TOKEN", {}, 2).get("/api/v1/info")
    assert connects == []


def test_webhook_blocked(connects):
    with pytest.raises(SendError) as ei:
        send_webhook("http://169.254.169.254/latest/meta-data/", {"a": 1}, None, timeout=2)
    assert str(ei.value) == "Webhook nicht erreichbar" and connects == []


def test_webhook_private_blocked_with_switch(monkeypatch, connects):
    monkeypatch.setattr(netguard, "allow_private", False)
    with pytest.raises(SendError):
        send_webhook("http://127.0.0.1:9/hook", {"a": 1}, None, timeout=2)
    assert connects == []
    monkeypatch.setattr(netguard, "allow_private", True)
    with pytest.raises(SendError):
        send_webhook("http://127.0.0.1:9/hook", {"a": 1}, None, timeout=2)
    assert connects == ["127.0.0.1"]  # Standard: privates Ziel wird versucht


@pytest.mark.parametrize("security", ["starttls", "tls"])
def test_smtp_blocked(security, connects):
    cfg = {"host": "169.254.169.254", "port": 25, "security": security, "username": None,
           "from_addr": "moni@example.org", "to": ["a@example.org"]}
    with pytest.raises(SendError) as ei:
        send_email(cfg, None, "S", "T", timeout=2)
    assert str(ei.value) == "SMTP-Server nicht erreichbar" and connects == []


def test_oidc_blocked(connects):
    with pytest.raises(oidc.OidcError) as ei:
        oidc._http("GET", "https://169.254.169.254/.well-known/openid-configuration")
    assert str(ei.value) == "OIDC-Anbieter nicht erreichbar" and connects == []


def test_setting_default_and_switch():
    from moni.secretbox import generate_key
    base = {"MONI_ROLE": "manager", "MONI_SECRET_KEY": generate_key()}
    assert Settings.from_env(base).allow_private_targets is True
    assert Settings.from_env({**base, "MONI_ALLOW_PRIVATE_TARGETS": "0"}).allow_private_targets is False
    with pytest.raises(ValueError, match="MONI_ALLOW_PRIVATE_TARGETS"):
        Settings.from_env({**base, "MONI_ALLOW_PRIVATE_TARGETS": "vielleicht"})


def test_create_app_applies_switch(mgr_settings):
    from dataclasses import replace
    from moni.app import create_app
    create_app(replace(mgr_settings, allow_private_targets=False))
    assert netguard.allow_private is False
