from moni.collect import docker
from moni.config import Settings

RUNNING = {"Id": "a" * 64, "Names": ["/web"], "Image": "nginx:1", "State": "running", "Created": 0}

class Fake:
    def __init__(self, routes): self.routes = routes
    def get(self, endpoint, timeout):
        for prefix, val in self.routes.items():
            if endpoint.startswith(prefix): return val
        return None

def stats(mem_usage=500 * 2**20, inactive=100 * 2**20, limit=2**30):
    return {"cpu_stats": {"cpu_usage": {"total_usage": 400}, "system_cpu_usage": 2000, "online_cpus": 2},
            "precpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 1000},
            "memory_stats": {"usage": mem_usage, "limit": limit, "stats": {"inactive_file": inactive}},
            "networks": {"eth0": {"rx_bytes": 10, "tx_bytes": 20}, "eth1": {"rx_bytes": 1, "tx_bytes": 2}}}

def test_running_container_metrics():
    c = docker.container_metrics(Fake({"/containers/" + "a" * 64 + "/stats": stats(),
                                       "/containers/" + "a" * 64 + "/json": {"RestartCount": 3, "State": {"Health": {"Status": "healthy"}}}}),
                                 RUNNING, 8 * 2**30, 2.5, 1.0)
    assert c["id"] == "a" * 12 and c["name"] == "web" and c["image"] == "nginx:1"
    assert c["cpu_percent"] == 40.0
    assert c["memory_usage_bytes"] == 400 * 2**20 and c["memory_limit_bytes"] == 2**30
    assert c["memory_percent"] == 39.1
    assert (c["rx_bytes"], c["tx_bytes"]) == (11, 22)
    assert c["restart_count"] == 3 and c["health"] == "healthy" and c["stats_available"] is True

def test_unlimited_container_uses_host_total():
    c = docker.container_metrics(Fake({"/containers/" + "a" * 64 + "/stats": stats(limit=0)}), RUNNING, 4 * 2**30, 2.5, 1.0)
    assert c["memory_limit_bytes"] == 4 * 2**30 and c["memory_percent"] == 9.8

def test_stopped_container_has_no_stats():
    c = docker.container_metrics(Fake({}), RUNNING | {"State": "exited"}, None, 2.5, 1.0)
    assert c["status"] == "exited" and c["stats_available"] is False
    assert c["cpu_percent"] is None and c["memory_usage_bytes"] is None and c["uptime_seconds"] is None

def test_collect_unreachable():
    cs, st, warn = docker.collect_containers(Fake({}), Settings.from_env({}), None)
    assert cs == [] and st["reachable"] is False and warn == ["Docker API nicht erreichbar"]

def test_collect_aggregates():
    routes = {"/containers/json": [RUNNING, RUNNING | {"Id": "b" * 64, "Names": ["/db"], "State": "exited"}],
              "/containers/" + "a" * 64 + "/json": {"State": {"Health": {"Status": "unhealthy"}}}}
    cs, st, warn = docker.collect_containers(Fake(routes), Settings.from_env({}), None)
    assert st["container_count"] == 2 and st["running_count"] == 1
    assert st["stats_available_count"] == 0 and st["health"]["unhealthy"] == 1
    assert "Docker-Stats teilweise nicht verfügbar" in warn
    assert "Mindestens ein Container ist unhealthy" in warn

def test_unknown_health_and_list_fallback():
    r = RUNNING | {"Health": {"Status": "healthy"}}
    c = docker.container_metrics(Fake({}), r, None, 2.5, 1.0)  # inspect fails -> list value
    assert c["health"] == "healthy"
    c = docker.container_metrics(Fake({"/containers/" + "a" * 64 + "/json": {"State": {"Health": {"Status": "weird"}}}}), RUNNING, None, 2.5, 1.0)
    assert c["health"] == "unknown"
    c = docker.container_metrics(Fake({"/containers/" + "a" * 64 + "/json": {"State": {}}}), RUNNING, None, 2.5, 1.0)
    assert c["health"] == "none"

def test_worker_exception_drops_only_that_container():
    class Boom(Fake):
        def get(self, endpoint, timeout):
            if "b" * 64 in endpoint: raise RuntimeError("kaputt")
            return super().get(endpoint, timeout)
    routes = {"/containers/json": [RUNNING, RUNNING | {"Id": "b" * 64, "Names": ["/bad"]}]}
    cs, st, _ = docker.collect_containers(Boom(routes), Settings.from_env({}), None)
    assert [c["name"] for c in cs] == ["web"] and st["container_count"] == 1

def _inspect(**state): return {"/containers/" + "a" * 64 + "/json": {"State": state}}

def test_uptime_from_started_at_with_nanoseconds():
    from datetime import datetime, timedelta, timezone
    t = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")
    c = docker.container_metrics(Fake(_inspect(StartedAt=t)), RUNNING | {"Created": 0}, None, 2.5, 1.0)
    assert isinstance(c["uptime_seconds"], int) and 55 <= c["uptime_seconds"] <= 65

def test_uptime_none_without_inspect_or_valid_start():
    assert docker.container_metrics(Fake({}), RUNNING, None, 2.5, 1.0)["uptime_seconds"] is None
    for bad in ("0001-01-01T00:00:00Z", "quatsch", None):
        c = docker.container_metrics(Fake(_inspect(StartedAt=bad)), RUNNING, None, 2.5, 1.0)
        assert c["uptime_seconds"] is None

def test_inspect_failure_gives_unknown_health_unless_list_has_it():
    assert docker.container_metrics(Fake({}), RUNNING, None, 2.5, 1.0)["health"] == "unknown"

def test_one_shot_stats_have_no_cpu_percent():
    s = stats(); s["precpu_stats"] = {}
    c = docker.container_metrics(Fake({"/containers/" + "a" * 64 + "/stats": s}), RUNNING, None, 2.5, 1.0)
    assert c["cpu_percent"] is None and c["stats_available"] is True

def _unix_get(monkeypatch, payload):
    class Sock:
        def __init__(self, *a): self.chunks = [payload, b""]
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def settimeout(self, t): pass
        def connect(self, p): pass
        def sendall(self, d): pass
        def recv(self, n): return self.chunks.pop(0)
    monkeypatch.setattr(docker.socket, "socket", Sock)
    return docker.DockerClient("unix:///x").get("/containers/json", 1.0)

def test_unix_socket_non_200_is_none(monkeypatch):
    assert _unix_get(monkeypatch, b"HTTP/1.0 404 Not Found\r\n\r\n{\"message\": \"x\"}") is None
    assert _unix_get(monkeypatch, b"HTTP/1.0 200 OK\r\n\r\n[1]") == [1]

def test_non_list_payload_is_unreachable():
    for payload in ({"message": "x"}, 5):
        cs, st, warn = docker.collect_containers(Fake({"/containers/json": payload}), Settings.from_env({}), None)
        assert cs == [] and st["reachable"] is False and warn == ["Docker API nicht erreichbar"]

def test_client_get_swallows_http_exception(monkeypatch):
    import http.client
    def boom(*a, **k): raise http.client.BadStatusLine("x")
    monkeypatch.setattr(docker.urlrequest, "urlopen", boom)
    assert docker.DockerClient("http://x").get("/containers/json", 1.0) is None

def test_no_networks_and_null_limit():
    s = stats(); del s["networks"]; s["memory_stats"]["limit"] = None
    c = docker.container_metrics(Fake({"/containers/" + "a" * 64 + "/stats": s}), RUNNING, None, 2.5, 1.0)
    assert c["rx_bytes"] is None and c["tx_bytes"] is None and c["memory_limit_bytes"] is None
