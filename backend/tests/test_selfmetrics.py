import types

import pytest
from fastapi.testclient import TestClient

from moni import __version__
from moni.agent import prometheus
from moni.manager import db
from moni.manager.servers import ServerStore


@pytest.fixture
def mclient(mgr_app):
    return TestClient(mgr_app, base_url="https://testserver")


@pytest.fixture
def mauth(mgr_app):
    return {"Authorization": f"Bearer {mgr_app.state.tokens.create('r', 'read')[1]}"}


def _servers(mgr_app, *names, url="https://b.example.org", headers=None):
    conn = db.connect(mgr_app.state.settings.db_path)
    try:
        store = ServerStore(conn, mgr_app.state.box)
        out = [store.ensure_local("lokal")]
        store.record_success(out[0].id, 1, 1, "{}", None)
        out += [store.add(n, url, "tok", headers) for n in names]
        return store, out
    finally:
        conn.close()


def test_metrics_requires_auth(mclient):
    assert mclient.get("/api/v1/metrics").status_code == 401


def test_metrics_content(mgr_app, mclient, mauth):
    _servers(mgr_app, "b")
    st = mgr_app.state.poll_stats
    st.record("lokal", 0.25)
    st.record("b", 1.5)
    st.fail("b")
    st.cycle(2.0)
    r = mclient.get("/api/v1/metrics", headers=mauth)
    assert r.status_code == 200 and r.headers["content-type"] == prometheus.CONTENT_TYPE
    t = r.text
    assert f'moni_build_info{{role="manager",version="{__version__}"}} 1' in t
    assert 'moni_manager_server_up{server="lokal"} 1' in t
    assert 'moni_manager_server_up{server="b"} 0' in t
    assert 'moni_manager_poll_errors_total{server="b"} 1' in t
    assert 'moni_manager_poll_errors_total{server="lokal"} 0' in t
    assert 'moni_manager_poll_duration_seconds{server="b"} 1.5' in t
    assert "moni_manager_poll_cycle_duration_seconds 2.0" in t
    assert "# TYPE moni_manager_poll_errors_total counter" in t
    size = [ln for ln in t.splitlines() if ln.startswith("moni_manager_db_size_bytes ")][0]
    assert float(size.split()[1]) > 0


def test_metrics_skip_disabled_servers(mgr_app, mclient, mauth):
    store, (_, b) = _servers(mgr_app, "b")
    conn = db.connect(mgr_app.state.settings.db_path)
    try:
        ServerStore(conn, mgr_app.state.box).set_enabled(b.id, False)
    finally:
        conn.close()
    mgr_app.state.poll_stats.record("b", 1.5)
    t = mclient.get("/api/v1/metrics", headers=mauth).text
    assert 'moni_manager_server_up{server="lokal"} 1' in t
    assert 'server="b"' not in t


def test_metrics_missing_before_first_poll(mgr_app, mclient, mauth):
    _servers(mgr_app, "b")
    t = mclient.get("/api/v1/metrics", headers=mauth).text
    assert "moni_manager_poll_duration_seconds{" not in t and "poll_cycle_duration_seconds " not in t


def test_metrics_no_secrets(mgr_app, mclient, mauth):
    store, (_, b) = _servers(mgr_app, "b", url="https://geheim.example.org", headers={"X-Auth": "wert"})
    conn = db.connect(mgr_app.state.settings.db_path)
    try:
        ServerStore(conn, mgr_app.state.box).record_failure(b.id, 1, "Boom", fatal=True)
    finally:
        conn.close()
    t = mclient.get("/api/v1/metrics", headers=mauth).text
    for s in ("geheim.example.org", "X-Auth", "Boom"):
        assert s not in t


def test_metrics_label_escaping(mgr_app, mclient, mauth):
    _servers(mgr_app, 'a"b\\c')
    t = mclient.get("/api/v1/metrics", headers=mauth).text
    assert 'moni_manager_server_up{server="a\\"b\\\\c"} 0' in t


def test_ready_manager_ok(mgr_app, mclient):
    mgr_app.state.poller = types.SimpleNamespace(alive=lambda: True)
    r = mclient.get("/api/v1/ready")
    assert (r.status_code, r.json()) == (200, {"status": "ready"})


def test_ready_manager_poller_dead(mgr_app, mclient):
    mgr_app.state.poller = types.SimpleNamespace(alive=lambda: False)
    r = mclient.get("/api/v1/ready")
    assert r.status_code == 503 and r.json()["detail"] == "Noch nicht bereit"
    assert r.headers["content-type"].startswith("application/problem+json")


def test_ready_agent_before_and_after_first_snapshot(client, collector, monkeypatch):
    from datetime import datetime, timezone

    from conftest import raw
    from moni import collector as cmod
    r = client.get("/api/v1/ready")
    assert r.status_code == 503 and r.json()["detail"] == "Noch nicht bereit"
    monkeypatch.setattr(cmod, "read_raw", lambda s, c: raw(datetime.now(timezone.utc)))
    collector.collect_once()
    r = client.get("/api/v1/ready")
    assert (r.status_code, r.json()) == (200, {"status": "ready"})


def test_agent_metrics_build_info(client, auth, measured):
    t = client.get("/metrics", headers=auth).text
    assert f'moni_build_info{{role="agent",version="{__version__}"}} 1' in t


def test_metrics_viewer_may_read(mclient, make_user, login_cookie):
    from moni.auth.sessions import COOKIE
    mclient.cookies.set(COOKIE, login_cookie(make_user("viewer"))[0])
    assert mclient.get("/api/v1/metrics").status_code == 200
