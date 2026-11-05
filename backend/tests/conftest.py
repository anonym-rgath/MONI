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


def code_at(secret, t):
    """TOTP-Code (6 Stellen, 30 s, SHA-1) für ein Base32-Secret zum Zeitpunkt t; für alle Auth-Tests."""
    import base64

    from cryptography.hazmat.primitives.hashes import SHA1
    from cryptography.hazmat.primitives.twofactor.totp import TOTP
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    return TOTP(key, 6, SHA1(), 30, enforce_key_length=False).generate(t).decode()


# --- Manager-Fixtures (Benutzer, Sessions) ----------------------------------
TEST_PASSWORD = "Test-Passwort-123"


class Dummy:
    """Ersatz für Collector/Poller: wird nicht gestartet."""

    def start(self):
        pass

    def stop(self):
        pass


@pytest.fixture
def mgr_settings(tmp_path):
    from moni.config import Settings
    from moni.secretbox import generate_key
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "hostname").write_text("moni-test\n")
    return Settings.from_env({"MONI_DATA_DIR": str(tmp_path), "MONI_ROLE": "manager", "MONI_SECRET_KEY": generate_key(),
                              "MONI_RATE_LIMIT_PER_MINUTE": "10000", "HOST_ETC": str(etc)})


@pytest.fixture
def mgr_app(mgr_settings):
    from moni.app import create_app
    return create_app(mgr_settings, collector=Dummy(), poller=Dummy())


@pytest.fixture
def mgr_client(mgr_app):
    from fastapi.testclient import TestClient
    return TestClient(mgr_app, base_url="https://testserver")  # sonst schickt der Client Secure-Cookies nicht mit


@pytest.fixture
def make_user(mgr_app):
    import itertools

    from moni.auth.users import UserStore
    from moni.manager import db
    n = itertools.count(1)

    def make(role, kind="local", email=None):
        conn = db.connect(mgr_app.state.settings.db_path)
        try:
            return UserStore(conn, mgr_app.state.box).create(email or f"u{next(n)}@example.org", "Test", role, kind,
                                                             TEST_PASSWORD if kind == "local" else None)
        finally:
            conn.close()
    return make


@pytest.fixture
def login_cookie(mgr_app):
    import time

    from moni.auth.sessions import SessionStore
    from moni.manager import db

    def login(user, stage="full"):
        conn = db.connect(mgr_app.state.settings.db_path)
        try:
            s, plain = SessionStore(conn, mgr_app.state.settings).create(user.id, stage, int(time.time()), None)
        finally:
            conn.close()
        return plain, s.csrf
    return login
