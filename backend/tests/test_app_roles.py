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
