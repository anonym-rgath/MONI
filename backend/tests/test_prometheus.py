from moni.agent import prometheus

SNAP = {"timestamp": "2026-11-04T12:00:00Z",
        "host": {"cpu": {"usage_percent": 12.5}, "load": {"load1": 0.5, "load5": None, "load15": 1.0},
                 "memory": {"total_bytes": 100, "used_bytes": 50, "usage_percent": 50.0, "swap_used_bytes": 0},
                 "uptime_seconds": 10.0, "process_count": 3,
                 "temperature": {"zones": [{"name": "cpu", "celsius": 50.0}]},
                 "filesystems": [{"mountpoint": "/", "device": "/dev/root", "fstype": "ext4",
                                  "total_bytes": 4096000, "used_bytes": 1, "usage_percent": 66.7}],
                 "network": None},
        "containers": [{"name": 'we"b', "image": "nginx", "status": "running", "health": "none",
                        "cpu_percent": None, "memory_usage_bytes": 5, "restart_count": 0}],
        "docker": {"reachable": True}}


def test_render():
    out = prometheus.render(SNAP)
    assert "# TYPE moni_host_cpu_usage_percent gauge" in out
    assert "moni_host_cpu_usage_percent 12.5\n" in out
    assert "moni_host_load5" not in out
    assert 'moni_filesystem_size_bytes{mountpoint="/",device="/dev/root",fstype="ext4"} 4096000' in out
    assert 'moni_container_up{name="we\\"b",image="nginx",status="running"} 1' in out
    assert 'moni_container_healthy{' not in out
    assert "moni_container_cpu_usage_percent" not in out
    assert "moni_docker_up 1" in out
    assert "moni_snapshot_timestamp_seconds 1793793600" in out


def test_render_tolerates_missing_blocks():
    out = prometheus.render({"timestamp": "2026-11-04T12:00:00Z",
                             "host": {"filesystems": None, "network": None, "temperature": {"available": False},
                                      "memory": {"available": False}, "load": {"available": False}},
                             "containers": [{"name": "a", "image": None, "status": None, "health": "healthy"}],
                             "docker": {"reachable": False}})
    assert "moni_docker_up 0" in out and 'moni_container_healthy{name="a"} 1' in out


def test_network_and_unhealthy():
    s = {**SNAP, "host": {**SNAP["host"], "network": [{"interface": "eth0", "rx_bytes": 7, "tx_bytes": 8}]},
         "containers": [{"name": "a", "image": "i", "status": "exited", "health": "unhealthy"}]}
    out = prometheus.render(s)
    assert 'moni_network_receive_bytes{interface="eth0"} 7' in out
    assert 'moni_container_up{name="a",image="i",status="exited"} 0' in out
    assert 'moni_container_healthy{name="a"} 0' in out


def test_metrics_endpoint(client, auth, measured):
    r = client.get("/metrics", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert client.get("/metrics").status_code == 401


def test_metrics_503_before_measurement(client, auth):
    r = client.get("/metrics", headers=auth)
    assert r.status_code == 503 and r.headers["content-type"].startswith("application/problem+json")
