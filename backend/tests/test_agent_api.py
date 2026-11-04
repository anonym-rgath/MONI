import logging


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

def test_rate_limit(client, auth):
    codes = [client.get("/api/v1/info", headers=auth).status_code for _ in range(6)]
    assert codes[-1] == 429
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
