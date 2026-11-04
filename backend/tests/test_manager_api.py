import json
import logging
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from moni import errors
from moni.auth.tokens import TokenStore
from moni.manager import db, tsdb
from moni.manager.api import router
from moni.secretbox import SecretBox, generate_key


@pytest.fixture
def app(settings, tmp_path):
    db.migrate(settings.db_path)
    a = FastAPI()
    a.state.settings = settings
    a.state.tokens = TokenStore(tmp_path / "tokens.json")
    a.state.box = SecretBox(generate_key())
    errors.install(a)
    a.include_router(router)
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def read_auth(app):
    return {"Authorization": f"Bearer {app.state.tokens.create('r', 'read')[1]}"}


@pytest.fixture
def admin_auth(app):
    return {"Authorization": f"Bearer {app.state.tokens.create('a', 'admin')[1]}"}


@pytest.fixture
def conn(app, client):
    c = db.connect(app.state.settings.db_path)
    yield c
    c.close()


@pytest.fixture
def local_id(conn, app):
    from moni.manager.servers import ServerStore
    return ServerStore(conn, app.state.box).ensure_local("lokal").id


def test_health_open(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_info(client, read_auth, local_id):
    i = client.get("/api/v1/info", headers=read_auth).json()
    assert i["role"] == "manager" and i["api_version"] == "v1" and i["server_count"] == 1 and i["tls"] == "off"
    assert i["hostname"] and i["version"]


def test_read_token_cannot_write(client, read_auth):
    assert client.post("/api/v1/servers", json={"name": "a", "url": "https://a", "token": "t"}, headers=read_auth).status_code == 403


def test_no_token_401(client):
    assert client.get("/api/v1/servers").status_code == 401


def test_create_list_never_leaks(client, admin_auth):
    r = client.post("/api/v1/servers", json={"name": "a", "url": "https://a", "token": "geheim-t",
                                             "headers": {"CF-Access-Client-Secret": "geheim-h"}}, headers=admin_auth)
    assert r.status_code == 201 and "geheim" not in r.text
    body = client.get("/api/v1/servers", headers=admin_auth).text
    assert "geheim-t" not in body and "geheim-h" not in body and "CF-Access-Client-Secret" in body
    item = json.loads(body)[0]
    assert item["warnings"] == 0 and item["agent_version"] is None and item["last_seen"] is None and item["status"]


def test_create_invalid_422_no_echo(client, admin_auth):
    r = client.post("/api/v1/servers", json={"name": "a", "url": "ftp://x", "token": "geheim-t"}, headers=admin_auth)
    assert r.status_code == 422 and "geheim" not in r.text and "URL" in r.json()["detail"]
    r = client.post("/api/v1/servers", json={"name": "a", "url": "https://a", "token": "geheim-t", "headers": {"X": 5}}, headers=admin_auth)
    assert r.status_code == 422 and "geheim" not in r.text
    r = client.post("/api/v1/servers", json={"name": "a", "url": "https://a", "token": 1234567}, headers=admin_auth)
    assert r.status_code == 422 and "1234567" not in r.text


def test_detail_snapshot_patch_delete(client, admin_auth, conn, app):
    sid = client.post("/api/v1/servers", json={"name": "a", "url": "https://a", "token": "t"}, headers=admin_auth).json()["id"]
    assert client.get(f"/api/v1/servers/{sid}/snapshot", headers=admin_auth).json()["detail"] == "Noch kein Snapshot"
    from moni.manager.servers import ServerStore
    ServerStore(conn, app.state.box).record_success(sid, 1762257600, 1762257600, json.dumps({"warnings": [1, 2], "x": 1}),
                                                    json.dumps({"version": "2.0.1"}))
    conn.commit()
    d = client.get(f"/api/v1/servers/{sid}", headers=admin_auth).json()
    assert d["warnings"] == 2 and d["agent_version"] == "2.0.1" and d["agent_info"] == {"version": "2.0.1"}
    assert d["last_seen"] == "2025-11-04T12:00:00Z"
    assert client.get(f"/api/v1/servers/{sid}/snapshot", headers=admin_auth).json()["x"] == 1
    p = client.patch(f"/api/v1/servers/{sid}", json={"enabled": False}, headers=admin_auth)
    assert p.status_code == 200 and p.json()["enabled"] is False
    assert client.patch(f"/api/v1/servers/{sid}", json={"enabled": "nein"}, headers=admin_auth).status_code == 422
    assert client.delete(f"/api/v1/servers/{sid}", headers=admin_auth).status_code == 204
    assert client.get(f"/api/v1/servers/{sid}", headers=admin_auth).status_code == 404


def test_series_and_history(client, read_auth, local_id, conn):
    snap = {"taken_at": "2026-11-04T12:00:00Z", "host": {"cpu": {"percent": 5.0}}}
    tsdb.write_snapshots(conn, local_id, [snap])
    conn.commit()
    assert isinstance(client.get(f"/api/v1/servers/{local_id}/series", headers=read_auth).json(), list)
    r = client.get(f"/api/v1/servers/{local_id}/history?metric=x&from=2026-11-04T11:00:00Z&to=2026-11-04T13:00:00Z&label=a:b:c&label=d:e",
                   headers=read_auth)
    assert r.status_code == 200 and "series" in r.json()
    assert client.get(f"/api/v1/servers/{local_id}/history?metric=x", headers=read_auth).status_code == 200


@pytest.mark.parametrize("qs", ["metric=x&from=2026-11-04T13:00:00Z&to=2026-11-04T12:00:00Z",
                                "metric=x&resolution=5m", "metric=x&from=gestern", "from=2026-11-04T12:00:00Z",
                                "metric=x&label=ohnedoppelpunkt"])
def test_history_422(client, read_auth, local_id, qs):
    assert client.get(f"/api/v1/servers/{local_id}/history?{qs}", headers=read_auth).status_code == 422


def test_unknown_server_404(client, read_auth):
    for p in ("snapshot", "series", "history?metric=x", ""):
        assert client.get(f"/api/v1/servers/999/{p}".rstrip("/"), headers=read_auth).status_code == 404


def test_unknown_patch_delete_404(client, admin_auth):
    assert client.patch("/api/v1/servers/999", json={"enabled": True}, headers=admin_auth).status_code == 404
    assert client.delete("/api/v1/servers/999", headers=admin_auth).status_code == 404


def test_local_not_deletable(client, admin_auth, local_id):
    assert client.delete(f"/api/v1/servers/{local_id}", headers=admin_auth).status_code == 422


def test_no_body_or_auth_logged(client, admin_auth, caplog):
    caplog.set_level(logging.DEBUG)
    client.post("/api/v1/servers", json={"name": "a", "url": "https://a", "token": "geheim-t"}, headers=admin_auth)
    assert "geheim-t" not in caplog.text and admin_auth["Authorization"].split()[1] not in caplog.text
