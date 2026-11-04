import json
import logging
from datetime import datetime, timezone

import pytest

from moni import logs, timeutil
from moni.config import Settings


def test_defaults():
    s = Settings.from_env({})
    assert s.role == "agent" and s.port == 9810
    assert s.collect_interval_seconds == 15 and s.buffer_seconds == 7200
    assert s.tls == "off" and s.rate_limit_per_minute == 120
    assert s.docker_api_base == "unix:///var/run/docker.sock"
    assert s.cors_origins == ()


def test_env_overrides():
    s = Settings.from_env({"MONI_COLLECT_INTERVAL": "5", "MONI_BUFFER_SECONDS": "60",
                           "API_CORS_ORIGINS": "https://a, https://b"})
    assert s.collect_interval_seconds == 5 and s.buffer_seconds == 60
    assert s.cors_origins == ("https://a", "https://b")


@pytest.mark.parametrize("env", [
    {"MONI_ROLE": "x"}, {"MONI_TLS": "on"}, {"MONI_COLLECT_INTERVAL": "0"},
    {"MONI_COLLECT_INTERVAL": "30", "MONI_BUFFER_SECONDS": "10"},
])
def test_invalid_env_raises(env):
    with pytest.raises(ValueError):
        Settings.from_env(env)


def test_iso_roundtrip():
    dt = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
    assert timeutil.iso(dt) == "2026-11-04T12:00:00Z"
    assert timeutil.parse_iso("2026-11-04T12:00:00Z") == dt
    assert timeutil.parse_iso("2026-11-04T13:00:00+01:00") == dt
    with pytest.raises(ValueError):
        timeutil.parse_iso("gestern")
    with pytest.raises(ValueError):
        timeutil.parse_iso("2026-11-04T12:00:00")  # ohne Zeitzone


def test_json_formatter():
    rec = logging.LogRecord("moni", logging.WARNING, __file__, 1, "Hallo %s", ("Welt",), None)
    out = json.loads(logs.JsonFormatter().format(rec))
    assert out["level"] == "WARNING" and out["msg"] == "Hallo Welt" and out["logger"] == "moni"
    assert out["ts"].endswith("Z")


def test_manager_requires_secret_key():
    with pytest.raises(ValueError, match="MONI_SECRET_KEY"):
        Settings.from_env({"MONI_ROLE": "manager"})


def test_secret_key_not_in_repr():
    s = Settings.from_env({"MONI_ROLE": "manager", "MONI_SECRET_KEY": "geheim123"})
    assert "geheim123" not in repr(s)


def test_manager_defaults_and_db_path():
    s = Settings.from_env({"MONI_ROLE": "manager", "MONI_SECRET_KEY": "x", "MONI_DATA_DIR": "/d"})
    assert (s.poll_timeout_seconds, s.retention_raw_hours, s.retention_minute_days, s.retention_hour_days) == (10.0, 48, 30, 365)
    assert str(s.db_path) == "/d/moni.db"


@pytest.mark.parametrize("env,name", [({"MONI_PORT": "abc"}, "MONI_PORT"), ({"MONI_PORT": "70000"}, "MONI_PORT"),
                                      ({"MONI_RETENTION_RAW_HOURS": "0"}, "MONI_RETENTION_RAW_HOURS"),
                                      ({"DOCKER_STATS_TIMEOUT": "0"}, "DOCKER_STATS_TIMEOUT"),
                                      ({"MONI_POLL_TIMEOUT": "nan"}, "MONI_POLL_TIMEOUT"),
                                      ({"DOCKER_INSPECT_TIMEOUT": "inf"}, "DOCKER_INSPECT_TIMEOUT")])
def test_invalid_values_name_the_variable(env, name):
    with pytest.raises(ValueError, match=name):
        Settings.from_env(env)
