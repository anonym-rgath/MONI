"""Inventar aller schreibenden Routen: jede neue Route ist automatisch auf Rolle, CSRF und Anmeldung geprüft."""
import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from conftest import TEST_PASSWORD
from moni.auth.sessions import COOKIE

WRITE = {"POST", "PUT", "PATCH", "DELETE"}
# Vor der Anmeldung erreichbare Abläufe; jede Ausnahme steht hier ausdrücklich
PRE_AUTH = {
    ("POST", "/api/v1/setup"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/login/mfa"),
    ("POST", "/api/v1/auth/totp/setup"),
    ("POST", "/api/v1/auth/totp/confirm"),
    ("POST", "/api/v1/auth/logout"),
    ("GET", "/api/v1/auth/oidc/start"),
    ("GET", "/api/v1/auth/oidc/callback/{pid}"),
}
BODIES = {
    ("POST", "/api/v1/servers"): {"name": "a", "url": "https://a", "token": "t"},
    ("PATCH", "/api/v1/servers/{server_id}"): {"enabled": False},
    ("POST", "/api/v1/account/password"): {"current_password": "falsch-falsch-1", "new_password": "Ganz-neues-Passwort-42"},
    ("POST", "/api/v1/account/recovery-codes"): {"code": "123456"},
    ("POST", "/api/v1/users"): {"email": "neu@example.org", "role": "viewer", "password": TEST_PASSWORD},
    ("PATCH", "/api/v1/users/{user_id}"): {"name": "x"},
    ("POST", "/api/v1/users/{user_id}/password"): {"password": "Ein-ganz-neues-Passwort"},
    ("POST", "/api/v1/tokens"): {"name": "t"},
    ("PUT", "/api/v1/auth/oidc/public-url"): {"public_url": "https://moni.example.org"},
    ("PUT", "/api/v1/auth/oidc/providers/{pid}"): {"issuer": "https://idp.example.org", "client_id": "moni"},
    ("POST", "/api/v1/alert-rules"): {"name": "r", "metric": "moni_host_cpu_usage_percent", "op": ">", "critical": 90},
    ("PATCH", "/api/v1/alert-rules/{rule_id}"): {"enabled": False},
    ("POST", "/api/v1/alert-channels"): {"name": "c", "kind": "webhook", "url": "https://example.org/h"},
    ("PATCH", "/api/v1/alert-channels/{channel_id}"): {"enabled": False},
    ("PUT", "/api/v1/servers/{server_id}/mute"): {"until": "2999-01-01T00:00:00Z"},
    ("PUT", "/api/v1/alert-settings"): {"remind_minutes": 60},
}
NO_ROLE = "Keine Berechtigung"
NO_CSRF = "CSRF-Token fehlt oder ist ungültig"


def _routes(app):
    return [(m, r.path) for r in app.routes if isinstance(r, APIRoute) for m in sorted(r.methods)]


def _url(path):
    return re.sub(r"\{(\w+)\}", lambda m: "google" if m[1] == "pid" else "999999", path)


def test_pre_auth_allowlist_is_current(mgr_app):
    assert PRE_AUTH <= set(_routes(mgr_app))  # veraltete Ausnahmen fallen auf


@pytest.mark.parametrize("case", ["viewer_with_csrf", "admin_without_csrf", "anonymous"])
def test_every_write_route_checks_role_csrf_and_login(mgr_app, make_user, login_cookie, case):
    if case == "anonymous":
        client, headers = TestClient(mgr_app, base_url="https://testserver"), {}
    else:
        cookie, csrf = login_cookie(make_user("viewer" if case == "viewer_with_csrf" else "admin"))
        client = TestClient(mgr_app, base_url="https://testserver")
        client.cookies.set(COOKIE, cookie)
        headers = {"X-CSRF-Token": csrf} if case == "viewer_with_csrf" else {}
    checked = []
    for method, path in _routes(mgr_app):
        if method not in WRITE or (method, path) in PRE_AUTH:
            continue
        r = client.request(method, _url(path), json=BODIES.get((method, path)), headers=headers)
        got = (r.status_code, r.json().get("detail"))
        if case == "anonymous":
            assert got == (401, "Nicht angemeldet"), (method, path)
        elif case == "admin_without_csrf":
            assert got == (403, NO_CSRF), (method, path)
        elif path.startswith("/api/v1/account/"):  # Konto-Routen gelten auch für Betrachter
            assert r.status_code != 403, (method, path, got)
        else:
            assert got == (403, NO_ROLE), (method, path)
        checked.append((method, path))
    assert len(checked) >= 16  # das Inventar greift wirklich


@pytest.mark.parametrize("path", ["/api/v1/users", "/api/v1/tokens", "/api/v1/audit", "/api/v1/auth/oidc/providers",
                                  "/api/v1/alert-channels", "/api/v1/alert-settings",
                                  "/api/v1/servers/{id}/agent-tokens"])
def test_viewer_cannot_read_admin_lists(mgr_client, make_user, login_cookie, path):
    path = path.replace("{id}", "999999")
    mgr_client.cookies.set(COOKIE, login_cookie(make_user("viewer"))[0])
    r = mgr_client.get(path)
    assert (r.status_code, r.json()["detail"]) == (403, NO_ROLE)


# Öffentliche Lese-Routen ohne Anmeldung; jede Ausnahme steht hier ausdrücklich
PUBLIC_GET = {"/api/v1/health", "/api/v1/ready"}


def test_public_ready_route_is_deliberate(mgr_app, mgr_client):
    assert ("GET", "/api/v1/ready") in _routes(mgr_app)
    assert mgr_client.get("/api/v1/ready").status_code != 401
    assert mgr_client.get("/api/v1/metrics").status_code == 401  # Kennzahlen bleiben geschützt
