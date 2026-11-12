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
    assert s.tls == "on" and s.public_url == "" and s.rate_limit_per_minute == 120
    assert s.docker_api_base == "unix:///var/run/docker.sock"
    assert s.cors_origins == ()


def test_env_overrides():
    s = Settings.from_env({"MONI_COLLECT_INTERVAL": "5", "MONI_BUFFER_SECONDS": "60",
                           "API_CORS_ORIGINS": "https://a, https://b"})
    assert s.collect_interval_seconds == 5 and s.buffer_seconds == 60
    assert s.cors_origins == ("https://a", "https://b")


@pytest.mark.parametrize("env", [
    {"MONI_ROLE": "x"}, {"MONI_TLS": "yes"}, {"MONI_COLLECT_INTERVAL": "0"},
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


@pytest.mark.parametrize("v,exp", [("", False), ("0", False), ("1", True), ("true", True), ("TRUE", True), ("False", False)])
def test_trust_local_proxy_values(v, exp):
    assert Settings.from_env({"MONI_TRUST_LOCAL_PROXY": v}).trust_local_proxy is exp


@pytest.mark.parametrize("v", ["yes", "on", "ture", "2"])
def test_trust_local_proxy_invalid(v):
    with pytest.raises(ValueError, match="MONI_TRUST_LOCAL_PROXY"):
        Settings.from_env({"MONI_TRUST_LOCAL_PROXY": v})


@pytest.mark.parametrize("v", ["0.0.0.0", "127.0.0.1", "::1", "localhost", "moni-1.example.com"])
def test_bind_host_valid(v):
    assert Settings.from_env({"MONI_BIND_HOST": v}).bind_host == v


@pytest.mark.parametrize("v", ["", "a b", "bad_host", "-x", "x-", "1.2.3.999;", "host/"])
def test_bind_host_invalid(v):
    with pytest.raises(ValueError, match="MONI_BIND_HOST"):
        Settings.from_env({"MONI_BIND_HOST": v})


def test_session_settings():
    s = Settings.from_env({})
    assert (s.session_idle_minutes, s.session_max_hours) == (60, 12)
    s = Settings.from_env({"MONI_SESSION_IDLE_MINUTES": "5", "MONI_SESSION_MAX_HOURS": "2"})
    assert (s.session_idle_minutes, s.session_max_hours) == (5, 2)
    for name in ("MONI_SESSION_IDLE_MINUTES", "MONI_SESSION_MAX_HOURS"):
        with pytest.raises(ValueError, match=name):
            Settings.from_env({name: "0"})


# --- Paket 4: vertrauenswürdige Proxys und Manager-Hostnamen (D7, D8) ----------

@pytest.mark.parametrize("value,exp", [
    ("10.0.0.0/8", ("10.0.0.0/8",)), ("172.18.0.1", ("172.18.0.1/32",)), ("fd00::/8", ("fd00::/8",)),
    ("10.0.0.0/8, 192.168.1.10", ("10.0.0.0/8", "192.168.1.10/32")),
    ("10.1.2.3/8", ("10.0.0.0/8",)),  # wie uvicorn (strikt) es braucht
])
def test_trusted_proxies_valid(value, exp):
    assert Settings.from_env({"MONI_TRUSTED_PROXIES": value}).trusted_proxies == exp


@pytest.mark.parametrize("value", ["kein-ip", "10.0.0.0/33", "*", "10.0.0.0/8;"])
def test_trusted_proxies_invalid(value):
    with pytest.raises(ValueError, match="MONI_TRUSTED_PROXIES enthält eine ungültige Adresse: "):
        Settings.from_env({"MONI_TRUSTED_PROXIES": value})


def test_trusted_proxies_default_empty():
    assert Settings.from_env({}).trusted_proxies == ()


def test_manager_hostnames_default_from_host(tmp_path):
    (tmp_path / "hostname").write_text("pi5\n")
    assert Settings.from_env({"HOST_ETC": str(tmp_path)}).manager_hostnames == ("pi5", "localhost", "127.0.0.1")
    (tmp_path / "hostname").write_text("localhost\n")
    assert Settings.from_env({"HOST_ETC": str(tmp_path)}).manager_hostnames == ("localhost", "127.0.0.1")
    (tmp_path / "hostname").write_text("kaputt_name\n")  # unbrauchbarer Hostname fällt aus dem Standard heraus
    assert Settings.from_env({"HOST_ETC": str(tmp_path)}).manager_hostnames == ("localhost", "127.0.0.1")


def test_manager_hostnames_explicit():
    s = Settings.from_env({"MONI_MANAGER_HOSTNAMES": "Moni.Example.org, 10.0.0.5,,::1"})
    assert s.manager_hostnames == ("moni.example.org", "10.0.0.5", "::1")


@pytest.mark.parametrize("value", ["a b", "bad_host", "x;", "-x", "a" * 254, "*.example.org"])
def test_manager_hostnames_invalid(value):
    with pytest.raises(ValueError, match="MONI_MANAGER_HOSTNAMES enthält einen ungültigen Namen"):
        Settings.from_env({"MONI_MANAGER_HOSTNAMES": value})


def test_https_port():
    assert Settings.from_env({}).https_port == 8443
    assert Settings.from_env({"MONI_HTTPS_PORT": "443"}).https_port == 443


@pytest.mark.parametrize("value", ["0", "65536", "-1"])
def test_https_port_out_of_range(value):
    with pytest.raises(ValueError) as e:
        Settings.from_env({"MONI_HTTPS_PORT": value})
    assert str(e.value) == "MONI_HTTPS_PORT muss zwischen 1 und 65535 liegen"


@pytest.mark.parametrize("value", ["abc", "443;", "8443.0"])
def test_https_port_not_a_number(value):
    with pytest.raises(ValueError, match="MONI_HTTPS_PORT"):
        Settings.from_env({"MONI_HTTPS_PORT": value})


def test_trusted_proxies_with_tls_on_warns(caplog):
    with caplog.at_level(logging.WARNING):
        Settings.from_env({"MONI_TLS": "on", "MONI_TRUSTED_PROXIES": "10.0.0.1"})
    assert any("MONI_TRUSTED_PROXIES" in r.message and "ignoriert" in r.message for r in caplog.records)
    caplog.clear()
    Settings.from_env({"MONI_TLS": "off", "MONI_TRUSTED_PROXIES": "10.0.0.1"})
    assert not caplog.records
