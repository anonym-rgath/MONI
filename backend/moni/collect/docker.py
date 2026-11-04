"""Rein lesende Docker-Messung über die Engine-API (Socket-Proxy). Rohwerte in Bytes."""
import json
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib import request as urlrequest

from moni.config import Settings

log = logging.getLogger(__name__)

HEALTH_VALUES = frozenset({"healthy", "unhealthy", "starting", "none"})
NO_LIMIT = 10**14  # Docker meldet "kein Limit" teils als riesigen Wert


class DockerClient:
    def __init__(self, base: str):
        self.base = base

    def get(self, endpoint: str, timeout: float) -> Any | None:
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"
        try:
            if self.base.startswith(("http://", "https://")):
                with urlrequest.urlopen(f"{self.base.rstrip('/')}{endpoint}", timeout=timeout) as resp:
                    return json.loads(resp.read())
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect(self.base.removeprefix("unix://"))
                sock.sendall(f"GET {endpoint} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
                response = b""
                while chunk := sock.recv(4096):
                    response += chunk
            if b"\r\n\r\n" in response:
                return json.loads(response.split(b"\r\n\r\n", 1)[1])
        except (OSError, ValueError) as e:  # Socket-, Timeout- und JSON-Fehler
            log.warning("Docker API Fehler: %s", e)
        return None


def _health(value: Any) -> str:
    if value is None:
        return "none"
    return value if value in HEALTH_VALUES else "unknown"


def _cpu_percent(stats: dict) -> float | None:
    cpu, pre = stats.get("cpu_stats", {}), stats.get("precpu_stats", {})
    cpu_usage = cpu.get("cpu_usage", {})
    cpu_delta = cpu_usage.get("total_usage", 0) - pre.get("cpu_usage", {}).get("total_usage", 0)
    system_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    num_cpus = cpu.get("online_cpus") or len(cpu_usage.get("percpu_usage") or []) or 1
    if cpu_delta >= 0 and system_delta > 0:
        return round(cpu_delta / system_delta * num_cpus * 100, 1)
    return None


def _memory(stats: dict, host_total_mem_bytes: int | None) -> tuple[int | None, int | None, float | None]:
    mem = stats.get("memory_stats", {})
    detail = mem.get("stats", {})
    usage = mem.get("usage")
    if usage is None:
        usage = mem.get("rss") or detail.get("anon") or detail.get("total_rss")
    else:
        inactive = detail.get("inactive_file") or detail.get("total_inactive_file") or 0
        if usage - inactive > 0:
            usage -= inactive
    limit = mem.get("limit", 0)
    if limit == 0 or limit > NO_LIMIT:
        limit = host_total_mem_bytes or None  # ohne eigenes Limit darf der Container den Host nutzen
    if usage is None:
        return None, None, None
    return usage, limit, round(usage / limit * 100, 1) if limit else None


def container_metrics(client: DockerClient, c: dict, host_total_mem_bytes: int | None,
                      stats_timeout: float, inspect_timeout: float) -> dict:
    full_id = c.get("Id", "")
    state = c.get("State", "unknown")
    names = c.get("Names") or ["/unknown"]
    list_health = c.get("Health")
    m = {
        "id": full_id[:12], "full_id": full_id, "name": names[0].lstrip("/"),
        "image": c.get("Image"), "status": state,
        "health": _health(list_health.get("Status") if isinstance(list_health, dict) else None),
        "stats_available": False, "cpu_percent": None,
        "memory_usage_bytes": None, "memory_limit_bytes": None, "memory_percent": None,
        "rx_bytes": None, "tx_bytes": None, "restart_count": None,
        "uptime_seconds": int(time.time() - c["Created"]) if state == "running" and c.get("Created") else None,
    }
    if state != "running":
        return m

    base = f"/containers/{full_id}/stats?stream=false"
    stats = client.get(base, stats_timeout) or client.get(base + "&one-shot=true", stats_timeout)
    if stats:
        m["stats_available"] = True
        m["cpu_percent"] = _cpu_percent(stats)
        m["memory_usage_bytes"], m["memory_limit_bytes"], m["memory_percent"] = _memory(stats, host_total_mem_bytes)
        nets = (stats.get("networks") or {}).values()
        m["rx_bytes"] = sum(n.get("rx_bytes", 0) for n in nets)
        m["tx_bytes"] = sum(n.get("tx_bytes", 0) for n in nets)

    inspect = client.get(f"/containers/{full_id}/json", inspect_timeout)
    if inspect:
        m["restart_count"] = inspect.get("RestartCount")
        m["health"] = _health((inspect.get("State", {}).get("Health") or {}).get("Status"))
    return m


def collect_containers(client: DockerClient, settings: Settings,
                       host_total_mem_bytes: int | None) -> tuple[list[dict], dict, list[str]]:
    """Wirft nie: Fehler einzelner Container werden geloggt, der Rest bleibt erhalten."""
    status = {"reachable": False, "container_count": 0, "running_count": 0, "stats_available_count": 0,
              "health": {"healthy": 0, "unhealthy": 0, "starting": 0, "none": 0}}
    data = client.get("/containers/json?all=true", settings.docker_stats_timeout)
    if data is None:
        return [], status, ["Docker API nicht erreichbar"]
    status["reachable"] = True

    containers: list[dict] = []
    if data:
        def work(c: dict) -> dict | None:
            try:
                return container_metrics(client, c, host_total_mem_bytes,
                                         settings.docker_stats_timeout, settings.docker_inspect_timeout)
            except Exception as e:  # ein defekter Container darf die Messung nicht kippen
                log.warning("Container-Messung fehlgeschlagen: %s", e)
                return None
        with ThreadPoolExecutor(max_workers=max(1, min(settings.max_docker_workers, len(data)))) as ex:
            containers = [m for m in ex.map(work, data) if m is not None]

    running = [c for c in containers if c["status"] == "running"]
    status["container_count"] = len(containers)
    status["running_count"] = len(running)
    status["stats_available_count"] = sum(c["stats_available"] for c in running)
    for c in containers:
        if c["health"] in status["health"]:
            status["health"][c["health"]] += 1

    warnings = []
    if status["stats_available_count"] < status["running_count"]:
        warnings.append("Docker-Stats teilweise nicht verfügbar")
    if status["health"]["unhealthy"]:
        warnings.append("Mindestens ein Container ist unhealthy")
    return containers, status, warnings
