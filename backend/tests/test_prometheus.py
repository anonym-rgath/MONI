from moni.agent import prometheus

SNAP = {"timestamp": "2026-11-04T12:00:00Z",
        "host": {"cpu": {"usage_percent": 12.5}, "load": {"load1": 0.5, "load5": None, "load15": 1.0},
                 "memory": {"total_bytes": 100, "used_bytes": 50, "usage_percent": 50.0, "swap_used_bytes": 0},
                 "uptime_seconds": 10.0, "process_count": 3,
                 "temperature": {"zones": [{"id": "thermal_zone0", "name": "cpu", "celsius": 50.0}]},
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
    assert 'moni_host_temperature_celsius{zone="cpu",id="thermal_zone0"} 50.0' in out
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


def test_unknown_health_not_exported():
    s = {**SNAP, "containers": [{"name": "a", "image": "i", "status": "running", "health": "unknown"}]}
    assert "moni_container_healthy" not in prometheus.render(s)


FULL = {'timestamp': '2026-11-04T12:00:00Z',
 'host': {'cpu': {'usage_percent': 12.5},
          'load': {'load1': 0.5, 'load5': None, 'load15': 1.0},
          'memory': {'total_bytes': 100, 'used_bytes': 50, 'usage_percent': 50.0, 'swap_used_bytes': 0},
          'uptime_seconds': 10.0,
          'process_count': 3,
          'temperature': {'zones': [{'id': 'thermal_zone0', 'name': 'cpu', 'celsius': 50.0},
                                    {'id': 'hwmon1', 'name': 'gp"u', 'celsius': 41}]},
          'filesystems': [{'mountpoint': '/',
                           'device': '/dev/root',
                           'fstype': 'ext4',
                           'total_bytes': 4096000,
                           'used_bytes': 1,
                           'usage_percent': 66.7},
                          {'mountpoint': '/data',
                           'device': '/dev/sda1',
                           'fstype': 'xfs',
                           'total_bytes': 10,
                           'used_bytes': 2,
                           'usage_percent': None}],
          'network': [{'interface': 'eth0', 'rx_bytes': 7, 'tx_bytes': 8},
                      {'interface': 'lo', 'rx_bytes': 1, 'tx_bytes': None}]},
 'containers': [{'name': 'a',
                 'image': 'i',
                 'status': 'running',
                 'health': 'healthy',
                 'cpu_percent': 1.5,
                 'memory_usage_bytes': 5,
                 'restart_count': 2},
                {'name': 'b',
                 'image': 'j',
                 'status': 'exited',
                 'health': 'unhealthy',
                 'cpu_percent': None,
                 'memory_usage_bytes': None,
                 'restart_count': 0},
                {'name': 'c',
                 'image': None,
                 'status': 'running',
                 'health': 'unknown',
                 'cpu_percent': 0.0,
                 'memory_usage_bytes': 1,
                 'restart_count': 1}],
 'docker': {'reachable': True}}

GOLDEN = (
    '# HELP moni_host_cpu_usage_percent CPU-Auslastung des Hosts in Prozent\n'
    '# TYPE moni_host_cpu_usage_percent gauge\n'
    'moni_host_cpu_usage_percent 12.5\n'
    '# HELP moni_host_load1 Load Average 1 Minute\n'
    '# TYPE moni_host_load1 gauge\n'
    'moni_host_load1 0.5\n'
    '# HELP moni_host_load15 Load Average 15 Minuten\n'
    '# TYPE moni_host_load15 gauge\n'
    'moni_host_load15 1.0\n'
    '# HELP moni_host_memory_total_bytes Gesamter Arbeitsspeicher in Bytes\n'
    '# TYPE moni_host_memory_total_bytes gauge\n'
    'moni_host_memory_total_bytes 100\n'
    '# HELP moni_host_memory_used_bytes Belegter Arbeitsspeicher in Bytes\n'
    '# TYPE moni_host_memory_used_bytes gauge\n'
    'moni_host_memory_used_bytes 50\n'
    '# HELP moni_host_memory_usage_percent Arbeitsspeicher-Auslastung in Prozent\n'
    '# TYPE moni_host_memory_usage_percent gauge\n'
    'moni_host_memory_usage_percent 50.0\n'
    '# HELP moni_host_swap_used_bytes Belegter Swap in Bytes\n'
    '# TYPE moni_host_swap_used_bytes gauge\n'
    'moni_host_swap_used_bytes 0\n'
    '# HELP moni_host_uptime_seconds Laufzeit des Hosts in Sekunden\n'
    '# TYPE moni_host_uptime_seconds gauge\n'
    'moni_host_uptime_seconds 10.0\n'
    '# HELP moni_host_processes Anzahl Prozesse auf dem Host\n'
    '# TYPE moni_host_processes gauge\n'
    'moni_host_processes 3\n'
    '# HELP moni_host_temperature_celsius Temperatur je Zone in Grad Celsius\n'
    '# TYPE moni_host_temperature_celsius gauge\n'
    'moni_host_temperature_celsius{zone="cpu",id="thermal_zone0"} 50.0\n'
    'moni_host_temperature_celsius{zone="gp\\"u",id="hwmon1"} 41\n'
    '# HELP moni_filesystem_size_bytes Größe des Dateisystems in Bytes\n'
    '# TYPE moni_filesystem_size_bytes gauge\n'
    'moni_filesystem_size_bytes{mountpoint="/",device="/dev/root",fstype="ext4"} 4096000\n'
    'moni_filesystem_size_bytes{mountpoint="/data",device="/dev/sda1",fstype="xfs"} 10\n'
    '# HELP moni_filesystem_used_bytes Belegter Platz des Dateisystems in Bytes\n'
    '# TYPE moni_filesystem_used_bytes gauge\n'
    'moni_filesystem_used_bytes{mountpoint="/",device="/dev/root",fstype="ext4"} 1\n'
    'moni_filesystem_used_bytes{mountpoint="/data",device="/dev/sda1",fstype="xfs"} 2\n'
    '# HELP moni_filesystem_usage_percent Auslastung des Dateisystems in Prozent\n'
    '# TYPE moni_filesystem_usage_percent gauge\n'
    'moni_filesystem_usage_percent{mountpoint="/",device="/dev/root",fstype="ext4"} 66.7\n'
    '# HELP moni_network_receive_bytes Empfangene Bytes je Schnittstelle (kumulativ)\n'
    '# TYPE moni_network_receive_bytes gauge\n'
    'moni_network_receive_bytes{interface="eth0"} 7\n'
    'moni_network_receive_bytes{interface="lo"} 1\n'
    '# HELP moni_network_transmit_bytes Gesendete Bytes je Schnittstelle (kumulativ)\n'
    '# TYPE moni_network_transmit_bytes gauge\n'
    'moni_network_transmit_bytes{interface="eth0"} 8\n'
    '# HELP moni_container_up 1, wenn der Container läuft\n'
    '# TYPE moni_container_up gauge\n'
    'moni_container_up{name="a",image="i",status="running"} 1\n'
    'moni_container_up{name="b",image="j",status="exited"} 0\n'
    'moni_container_up{name="c",image="None",status="running"} 1\n'
    '# HELP moni_container_cpu_usage_percent CPU-Auslastung des Containers in Prozent\n'
    '# TYPE moni_container_cpu_usage_percent gauge\n'
    'moni_container_cpu_usage_percent{name="a"} 1.5\n'
    'moni_container_cpu_usage_percent{name="c"} 0.0\n'
    '# HELP moni_container_memory_usage_bytes Speichernutzung des Containers in Bytes\n'
    '# TYPE moni_container_memory_usage_bytes gauge\n'
    'moni_container_memory_usage_bytes{name="a"} 5\n'
    'moni_container_memory_usage_bytes{name="c"} 1\n'
    '# HELP moni_container_restarts Anzahl Neustarts des Containers\n'
    '# TYPE moni_container_restarts gauge\n'
    'moni_container_restarts{name="a"} 2\n'
    'moni_container_restarts{name="b"} 0\n'
    'moni_container_restarts{name="c"} 1\n'
    '# HELP moni_container_healthy 1, wenn der Healthcheck des Containers gesund meldet\n'
    '# TYPE moni_container_healthy gauge\n'
    'moni_container_healthy{name="a"} 1\n'
    'moni_container_healthy{name="b"} 0\n'
    '# HELP moni_docker_up 1, wenn die Docker-API erreichbar ist\n'
    '# TYPE moni_docker_up gauge\n'
    'moni_docker_up 1\n'
    '# HELP moni_snapshot_timestamp_seconds Zeitpunkt des Snapshots (Unix-Sekunden)\n'
    '# TYPE moni_snapshot_timestamp_seconds gauge\n'
    'moni_snapshot_timestamp_seconds 1793793600\n'
)


def test_render_unchanged_after_refactor():
    # Golden aus dem Stand vor dem Herausloesen von flatten erzeugt
    assert prometheus.render(FULL) == GOLDEN
