"""Unit-Tests fuer das Pi-Monitor-Backend.

Schwerpunkt liegt auf der nicht-trivialen Parsing-/Rechen-Logik:
- /proc-Parsing (Memory, CPU-Delta, Load, Uptime, Temperatur)
- Docker-Stats-Auswertung (CPU-Prozent, Memory-Adjustment, Limit-Fallback)
- Aggregation in get_containers_with_status

Externe Abhaengigkeiten (Dateisystem, Docker-API) werden ueber monkeypatch
ersetzt, damit die Tests deterministisch und plattformunabhaengig laufen.
"""

import os

import pytest
from fastapi.testclient import TestClient

import server


# --------------------------------------------------------------------------
# Helfer
# --------------------------------------------------------------------------

def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


# --------------------------------------------------------------------------
# /proc- und /sys-Parsing
# --------------------------------------------------------------------------

def test_get_memory_info_parses_meminfo(tmp_path, monkeypatch):
    write(
        str(tmp_path / "meminfo"),
        "MemTotal:        8000000 kB\n"
        "MemAvailable:    6000000 kB\n"
        "SwapTotal:       1000000 kB\n"
        "SwapFree:         800000 kB\n",
    )
    monkeypatch.setattr(server, "HOST_PROC", str(tmp_path))

    mem = server.get_memory_info()

    assert mem["total_mb"] == 8000000 // 1024
    assert mem["available_mb"] == 6000000 // 1024
    assert mem["used_mb"] == mem["total_mb"] - mem["available_mb"]
    assert 0 < mem["usage_percent"] < 100
    assert mem["swap_total_mb"] == 1000000 // 1024
    assert mem["swap_used_mb"] == (1000000 - 800000) // 1024


def test_get_memory_info_handles_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "HOST_PROC", str(tmp_path))  # leeres Verzeichnis
    mem = server.get_memory_info()
    assert mem["total_mb"] == 0
    assert mem["usage_percent"] == 0


def test_get_cpu_usage_first_call_zero_then_delta(tmp_path, monkeypatch):
    stat = str(tmp_path / "stat")
    monkeypatch.setattr(server, "HOST_PROC", str(tmp_path))
    monkeypatch.setattr(server, "_prev_cpu", None)

    # 1. Messung: idle=700, total=900
    write(stat, "cpu  100 0 100 700 0 0 0 0\n")
    assert server.get_cpu_usage() == 0.0

    # 2. Messung: idle=1400, total=1800 -> idle_delta=700, total_delta=900
    # usage = (1 - 700/900) * 100 = 22.2
    write(stat, "cpu  200 0 200 1400 0 0 0 0\n")
    assert server.get_cpu_usage() == 22.2


def test_get_load_average(tmp_path, monkeypatch):
    write(str(tmp_path / "loadavg"), "0.50 0.75 1.00 1/234 5678\n")
    monkeypatch.setattr(server, "HOST_PROC", str(tmp_path))

    load = server.get_load_average()
    assert load == {"1min": 0.5, "5min": 0.75, "15min": 1.0}


def test_get_uptime_hours(tmp_path, monkeypatch):
    write(str(tmp_path / "uptime"), "7200.50 1000.00\n")
    monkeypatch.setattr(server, "HOST_PROC", str(tmp_path))
    assert server.get_uptime() == 2.0


def test_get_temperature(tmp_path, monkeypatch):
    zone = tmp_path / "class" / "thermal" / "thermal_zone0"
    write(str(zone / "temp"), "52123\n")
    monkeypatch.setattr(server, "HOST_SYS", str(tmp_path))
    assert server.get_temperature() == 52.1


# --------------------------------------------------------------------------
# Docker-Stats-Auswertung in build_container_metrics
# --------------------------------------------------------------------------

def _running_container():
    return {
        "Names": ["/web"],
        "State": "running",
        "Id": "abcdef1234567890",
        "Created": 0,
    }


def _stats(usage_bytes, limit_bytes, inactive_file=0, online_cpus=4):
    return {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 200000},
            "system_cpu_usage": 1000000,
            "online_cpus": online_cpus,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 100000},
            "system_cpu_usage": 500000,
        },
        "memory_stats": {
            "usage": usage_bytes,
            "limit": limit_bytes,
            "stats": {"inactive_file": inactive_file},
        },
        "networks": {"eth0": {"rx_bytes": 1000, "tx_bytes": 2000}},
    }


def _patch_docker(monkeypatch, stats, inspect=None):
    def fake(endpoint, timeout=2.0):
        if "/stats" in endpoint:
            return stats
        if endpoint.endswith("/json"):
            return inspect
        return None
    monkeypatch.setattr(server, "docker_request", fake)


def test_build_container_metrics_cpu_percent(monkeypatch):
    # cpu_delta=100000, system_delta=500000, num_cpus=4
    # usage = 100000/500000 * 4 * 100 = 80.0
    _patch_docker(monkeypatch, _stats(200 * 1024**2, 1000 * 1024**2))
    result = server.build_container_metrics(_running_container())

    assert result["cpu"]["available"] is True
    assert result["cpu"]["usage_percent"] == 80.0
    assert result["network"]["rx_bytes"] == 1000
    assert result["network"]["tx_bytes"] == 2000


def test_build_container_metrics_memory_adjustment(monkeypatch):
    # 200 MiB usage minus 50 MiB inactive_file = 150 MiB effektiv
    _patch_docker(
        monkeypatch,
        _stats(200 * 1024**2, 1000 * 1024**2, inactive_file=50 * 1024**2),
    )
    result = server.build_container_metrics(_running_container())

    assert result["memory"]["available"] is True
    assert result["memory"]["usage_mb"] == 150
    assert result["memory"]["limit_mb"] == 1000
    assert result["memory"]["usage_percent"] == 15.0


def test_build_container_metrics_limit_fallback_uses_host_total(monkeypatch):
    # Container ohne eigenes Limit (limit=0) -> Fallback auf host_total_mem_bytes.
    # get_memory_info darf dabei NICHT aufgerufen werden (Regression fuer #6).
    def boom():
        raise AssertionError("get_memory_info darf je Container nicht laufen")
    monkeypatch.setattr(server, "get_memory_info", boom)
    _patch_docker(monkeypatch, _stats(100 * 1024**2, 0))

    result = server.build_container_metrics(
        _running_container(), host_total_mem_bytes=1000 * 1024**2
    )

    assert result["memory"]["usage_mb"] == 100
    assert result["memory"]["limit_mb"] == 1000
    assert result["memory"]["usage_percent"] == 10.0


def test_build_container_metrics_reads_inspect(monkeypatch):
    inspect = {"RestartCount": 3, "State": {"Health": {"Status": "healthy"}}}
    _patch_docker(monkeypatch, _stats(100 * 1024**2, 1000 * 1024**2), inspect=inspect)
    result = server.build_container_metrics(_running_container())

    assert result["restart_count"] == 3
    assert result["health"]["status"] == "healthy"


def test_build_container_metrics_stopped_returns_early(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("docker_request darf fuer gestoppte Container nicht laufen")
    monkeypatch.setattr(server, "docker_request", fail)

    c = {"Names": ["/db"], "State": "exited", "Id": "deadbeef", "Created": 0}
    result = server.build_container_metrics(c)

    assert result["status"] == "exited"
    assert result["stats"]["available"] is False
    assert result["cpu"]["available"] is False


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def test_get_containers_with_status_aggregates(monkeypatch):
    listing = [
        {"Names": ["/web"], "State": "running", "Id": "a" * 16, "Created": 0},
        {"Names": ["/db"], "State": "exited", "Id": "b" * 16, "Created": 0},
    ]

    def fake(endpoint, timeout=2.0):
        if endpoint.startswith("/containers/json"):
            return listing
        if "/stats" in endpoint:
            return _stats(100 * 1024**2, 1000 * 1024**2)
        if endpoint.endswith("/json"):
            return {"RestartCount": 0, "State": {"Health": {"Status": "healthy"}}}
        return None
    monkeypatch.setattr(server, "docker_request", fake)

    containers, status = server.get_containers_with_status()

    assert status["reachable"] is True
    assert status["container_count"] == 2
    assert status["running_count"] == 1
    assert status["health_healthy_count"] == 1


def test_get_containers_with_status_unreachable(monkeypatch):
    monkeypatch.setattr(server, "docker_request", lambda *a, **k: None)
    containers, status = server.get_containers_with_status()

    assert containers == []
    assert status["reachable"] is False
    assert "Docker API nicht erreichbar" in status["warnings"]


# --------------------------------------------------------------------------
# API-Ebene (TestClient) - Struktur, plattformunabhaengig
# --------------------------------------------------------------------------

client = TestClient(server.app)


def test_api_root_is_live():
    resp = client.get("/api/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "live"


def test_api_host_metrics_structure():
    resp = client.get("/api/metrics/host")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("cpu", "memory", "disk", "load_average", "temperature", "uptime_hours"):
        assert key in body
