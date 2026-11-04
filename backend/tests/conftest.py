import os
import sys

# backend/ in den Importpfad legen, damit `import moni` funktioniert,
# egal von wo pytest gestartet wird.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# --- gemeinsame Fixtures fuer API-Tests -----------------------------------
from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402


def raw(t, idle_total=(700, 900), net={"eth0": (1000, 500)}, containers=None, **host_over):
    host = {"hostname": "pi", "uptime_seconds": 1.0, "process_count": 1,
            "cpu_info": {"cores": 4, "frequency_mhz": None},
            "memory": {"available": True}, "load": {"available": True},
            "filesystems": [], "temperature": {"available": True, "max_celsius": 50.0, "zones": []}}
    host.update(host_over)
    return {"taken_at": t, "cpu_times": idle_total, "net": net,
            "host": host, "containers": containers or [],
            "docker": {"reachable": True}, "warnings": []}


@pytest.fixture
def settings(tmp_path):
    from moni.config import Settings
    return Settings.from_env({"MONI_DATA_DIR": str(tmp_path), "MONI_RATE_LIMIT_PER_MINUTE": "5"})


@pytest.fixture
def store(tmp_path):
    from moni.auth.tokens import TokenStore
    return TokenStore(tmp_path / "tokens.json")


@pytest.fixture
def _token_pair(store):
    return store.create("t")


@pytest.fixture
def token(_token_pair):
    return _token_pair[0]


@pytest.fixture
def auth(_token_pair):
    return {"Authorization": f"Bearer {_token_pair[1]}"}


@pytest.fixture
def collector(settings):
    from moni.collector import Collector
    return Collector(settings, client=object())  # nicht gestartet


@pytest.fixture
def measured(collector, monkeypatch):
    from moni import collector as cmod
    monkeypatch.setattr(cmod, "read_raw", lambda s, c: raw(datetime.now(timezone.utc)))
    collector.collect_once()
    return collector


@pytest.fixture
def client(settings, collector, token):  # token: stellt sicher, dass `auth` und die Token-Datei zusammenpassen
    from fastapi.testclient import TestClient
    from moni.app import create_app
    return TestClient(create_app(settings, collector))
