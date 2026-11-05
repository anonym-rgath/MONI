import dataclasses

import pytest
from fastapi.testclient import TestClient

from moni.app import create_app
from moni.secretbox import generate_key


class Dummy:
    def __init__(self):
        self.started = self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


def _mgr(settings, key=None):
    return dataclasses.replace(settings, role="manager", secret_key=key or generate_key())


def test_manager_info_and_no_metrics(settings):
    app = create_app(_mgr(settings), collector=Dummy(), poller=Dummy())
    with TestClient(app) as c:
        h = {"Authorization": f"Bearer {app.state.tokens.create('t', 'read')[1]}"}
        assert c.get("/api/v1/info", headers=h).json()["role"] == "manager"
        assert c.get("/metrics", headers=h).status_code == 404
        assert c.get("/api/v1/servers", headers=h).json()[0]["is_local"] is True


def test_passed_in_not_started(settings):
    col, pol = Dummy(), Dummy()
    with TestClient(create_app(_mgr(settings), collector=col, poller=pol)):
        pass
    assert (col.started, col.stopped, pol.started, pol.stopped) == (0, 0, 0, 0)


def test_bad_key_raises_without_key(settings):
    with pytest.raises(ValueError) as e:
        create_app(_mgr(settings, "kaputt-geheim"))
    assert "kaputt-geheim" not in str(e.value)


def test_self_owned_lifespan_starts_and_stops_threads(settings):
    app = create_app(_mgr(settings))
    with TestClient(app):
        assert app.state.collector._thread.is_alive()
        assert app.state.poller._thread.is_alive()
    assert not app.state.poller._thread.is_alive()


def test_migrate_error_message_kept(settings, monkeypatch):
    def boom(_):
        raise RuntimeError("Datenbank-Version 9 ist neuer als diese Moni-Version")
    monkeypatch.setattr("moni.app.db.migrate", boom)
    with pytest.raises(RuntimeError, match="Datenbank-Version 9 ist neuer"):
        create_app(_mgr(settings))


def test_local_name_clash_message(settings):
    from moni.manager import db
    from moni.manager.servers import ServerStore
    from moni.secretbox import SecretBox
    key = generate_key()
    s = _mgr(settings, key)
    db.migrate(s.db_path)
    c = db.connect(s.db_path)
    ServerStore(c, SecretBox(key)).add("pi", "https://a.example", "tok")
    c.close()
    import moni.app
    moni.app.read_hostname = lambda _: "pi"
    try:
        with pytest.raises(RuntimeError, match="'pi' ist bereits"):
            create_app(s)
    finally:
        import moni.collect.host as h
        moni.app.read_hostname = h.read_hostname


def test_rate_limit_keyed_on_forwarded_client_when_trusted(settings):
    from moni.__main__ import server_args
    from moni.config import Settings
    assert server_args(settings)["proxy_headers"] is False and server_args(settings)["host"] == "0.0.0.0"
    t = Settings.from_env({"MONI_ROLE": "manager", "MONI_SECRET_KEY": generate_key(), "MONI_BIND_HOST": "127.0.0.1",
                           "MONI_TRUST_LOCAL_PROXY": "1"})
    a = server_args(t)
    assert (a["host"], a["proxy_headers"], a["forwarded_allow_ips"]) == ("127.0.0.1", True, "127.0.0.1")
    # P3-R6: nginx loggt im Manager (ohne Query-String), uvicorn nicht; der Agent behält sein Zugriffslog
    assert a["access_log"] is False and server_args(settings)["access_log"] is True


def test_agent_forwarded_ips_only_from_trusted():
    """D8: Agent mit tls=off hinter einem Proxy; uvicorn 0.48 nimmt CIDRs an, Header gelten nur vom Proxy."""
    from uvicorn.middleware.proxy_headers import _TrustedHosts

    from moni.__main__ import server_args
    from moni.config import Settings
    a = server_args(Settings.from_env({"MONI_TLS": "off", "MONI_TRUSTED_PROXIES": "10.0.0.0/8, 192.168.1.10, fd00::/8"}))
    assert (a["proxy_headers"], a["forwarded_allow_ips"]) == (True, "10.0.0.0/8,192.168.1.10/32,fd00::/8")
    trusted = _TrustedHosts(a["forwarded_allow_ips"])
    assert all(ip in trusted for ip in ("10.1.2.3", "192.168.1.10", "fd00::1"))
    assert not any(ip in trusted for ip in ("11.0.0.1", "192.168.1.11", "127.0.0.1", "fe80::1"))
    # ohne Liste oder mit TLS (Proxys gelten nur bei tls=off): keine Weiterleitungs-Header
    for env in ({"MONI_TLS": "off"}, {"MONI_TLS": "on", "MONI_TRUSTED_PROXIES": "10.0.0.0/8"}):
        assert server_args(Settings.from_env(env))["proxy_headers"] is False
    # Manager: das Backend vertraut weiter nur nginx auf 127.0.0.1, auch mit Liste
    m = server_args(Settings.from_env({"MONI_ROLE": "manager", "MONI_SECRET_KEY": generate_key(), "MONI_TLS": "off",
                                       "MONI_TRUSTED_PROXIES": "10.0.0.0/8", "MONI_TRUST_LOCAL_PROXY": "1"}))
    assert (m["proxy_headers"], m["forwarded_allow_ips"]) == (True, "127.0.0.1")


def test_renamed_host_clashing_with_remote_keeps_local_name(settings, monkeypatch, caplog):  # Final 5
    from moni.manager import db
    from moni.manager.servers import ServerStore
    from moni.secretbox import SecretBox
    key = generate_key()
    s = _mgr(settings, key)
    db.migrate(s.db_path)
    c = db.connect(s.db_path)
    st = ServerStore(c, SecretBox(key))
    st.ensure_local("alt")
    st.add("neu", "https://a.example", "tok")
    c.close()
    monkeypatch.setattr("moni.app.read_hostname", lambda _: "neu")
    monkeypatch.setattr("moni.app.setup_logging", lambda: None)  # sonst entfernt basicConfig(force=True) caplog
    app = create_app(s, collector=Dummy(), poller=Dummy())
    with TestClient(app) as tc:
        h = {"Authorization": f"Bearer {app.state.tokens.create('t', 'read')[1]}"}
        names = {x["name"]: x["is_local"] for x in tc.get("/api/v1/servers", headers=h).json()}
    assert names == {"alt": True, "neu": False}
    assert any(r.levelname == "WARNING" and "'neu'" in r.getMessage() for r in caplog.records)
