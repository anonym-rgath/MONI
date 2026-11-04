import logging
import types
from pathlib import Path


def test_health_needs_no_token(client):
    r = client.get("/api/v1/health"); assert r.status_code == 200 and r.json() == {"status": "ok"}

def test_snapshot_requires_bearer(client):
    r = client.get("/api/v1/snapshot")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["status"] == 401

def test_wrong_scheme_and_garbage_are_401(client, auth):
    plain = auth["Authorization"].split()[1]
    for h in (f"Basic {plain}", plain, "Bearer", "Bearer  x y"):
        assert client.get("/api/v1/info", headers={"Authorization": h}).status_code == 401
    assert client.get("/api/v1/info", headers={"Authorization": f"bearer {plain}"}).status_code == 200

def test_snapshot_before_first_measurement_is_503(client, auth):
    r = client.get("/api/v1/snapshot", headers=auth)
    assert r.status_code == 503 and r.json()["detail"] == "Noch keine Messung vorhanden"

def test_snapshot_and_info(client, auth, measured):
    assert client.get("/api/v1/snapshot", headers=auth).json()["schema_version"] == 1
    info = client.get("/api/v1/info", headers=auth).json()
    assert info["role"] == "agent" and info["api_version"] == "v1" and info["paired"] is False

def test_history_validation(client, auth, measured):
    assert client.get("/api/v1/history", headers=auth).status_code == 422
    assert client.get("/api/v1/history?since=gestern", headers=auth).status_code == 422
    r = client.get("/api/v1/history?since=2000-01-01T00:00:00Z", headers=auth).json()
    assert len(r["points"]) >= 1 and r["oldest"] is not None

def test_revoked_token_401(client, auth, store, token):
    store.revoke(token.id)
    assert client.get("/api/v1/info", headers=auth).status_code == 401

def test_rate_limit(client, auth, monkeypatch):
    monkeypatch.setattr("moni.ratelimit.time", types.SimpleNamespace(time=lambda: 30.0))  # kein Fensterwechsel
    codes = [client.get("/api/v1/info", headers=auth).status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[-1] == 429
    r = client.get("/api/v1/info", headers=auth)
    assert int(r.headers["retry-after"]) >= 1 and r.headers["content-type"].startswith("application/problem+json")
    assert client.get("/api/v1/health").status_code == 200   # ausgenommen

def test_authorization_header_not_logged(client, auth, caplog):
    caplog.set_level(logging.DEBUG)
    client.get("/api/v1/info", headers=auth)
    assert auth["Authorization"].split()[1] not in caplog.text

def test_unhandled_error_is_500_without_details(settings, collector):
    from fastapi.testclient import TestClient
    from moni.app import create_app
    app = create_app(settings, collector)
    app.add_api_route("/boom", lambda: 1 / 0)
    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/problem+json")
    assert "division" not in r.text

def test_docs_paths(client):
    assert client.get("/api/v1/openapi.json").status_code == 200
    assert client.get("/api/v1/docs").status_code == 200
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404

def test_version_matches_package(client, auth):
    from moni import __version__
    assert client.get("/api/v1/info", headers=auth).json()["version"] == __version__
    assert client.get("/api/v1/openapi.json").json()["info"]["version"] == __version__

def test_missing_scope_is_403(client, auth, monkeypatch):
    from dataclasses import replace
    tokens = client.app.state.tokens
    real = tokens.verify
    monkeypatch.setattr(tokens, "verify", lambda p: replace(real(p), scope="root"))
    r = client.get("/api/v1/info", headers=auth)
    assert r.status_code == 403 and r.headers["content-type"].startswith("application/problem+json")

def test_verify_exception_is_500_without_details(client, auth, monkeypatch):
    def boom(p): raise RuntimeError("geheim-xyz")
    monkeypatch.setattr(client.app.state.tokens, "verify", boom)
    r = client.get("/api/v1/info", headers=auth)
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/problem+json")
    assert "geheim-xyz" not in r.text

def test_cors_preflight_allows_authorization(settings, collector):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from moni.app import create_app
    c = TestClient(create_app(replace(settings, cors_origins=("http://x.test",)), collector))
    r = c.options("/api/v1/info", headers={"Origin": "http://x.test", "Access-Control-Request-Method": "GET",
                                           "Access-Control-Request-Headers": "Authorization"})
    assert r.status_code == 200 and "authorization" in r.headers["access-control-allow-headers"].lower()


def test_corrupt_token_file_is_401(client, auth, settings):
    (Path(settings.data_dir) / "tokens.json").write_text("{kaputt")
    assert client.get("/api/v1/info", headers=auth).status_code == 401


def test_admin_token_can_read(client, store):
    _, plain = store.create("adm", scope="admin")
    assert client.get("/api/v1/info", headers={"Authorization": f"Bearer {plain}"}).status_code == 200


def test_info_tls_from_settings(settings, collector, auth):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from moni.app import create_app
    c = TestClient(create_app(replace(settings, tls="x"), collector))
    assert c.get("/api/v1/info", headers=auth).json()["tls"] == "x"


def test_admin_scope_boundary(client, store):
    from fastapi import Depends
    from moni.auth.deps import require_scope
    client.app.add_api_route("/api/v1/_admin_only", lambda: {"ok": True}, dependencies=[Depends(require_scope("admin"))])
    h = lambda p: {"Authorization": f"Bearer {p}"}  # noqa: E731
    assert client.get("/api/v1/_admin_only").status_code == 401
    assert client.get("/api/v1/_admin_only", headers=h(store.create("r", scope="read")[1])).status_code == 403
    assert client.get("/api/v1/_admin_only", headers=h(store.create("a", scope="admin")[1])).status_code == 200
