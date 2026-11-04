from datetime import datetime, timedelta, timezone
from moni import collector
from moni.config import Settings

T0 = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)

def raw(t, idle_total=(700, 900), net={"eth0": (1000, 500)}, containers=None, **host_over):
    host = {"hostname": "pi", "uptime_seconds": 1.0, "process_count": 1,
            "cpu_info": {"cores": 4, "frequency_mhz": None},
            "memory": {"available": True}, "load": {"available": True},
            "filesystems": [], "temperature": {"available": True, "max_celsius": 50.0, "zones": []}}
    host.update(host_over)
    return {"taken_at": t, "cpu_times": idle_total, "net": net,
            "host": host, "containers": containers or [],
            "docker": {"reachable": True}, "warnings": []}

def test_first_snapshot_has_no_rates_and_warns():
    s = collector.derive(None, raw(T0))
    assert s["schema_version"] == 1 and s["timestamp"] == "2026-11-04T12:00:00Z"
    assert s["host"]["cpu"]["usage_percent"] is None and s["host"]["cpu"]["available"] is False
    assert s["host"]["network"][0]["rx_bytes_per_second"] is None
    assert "CPU-Last noch nicht berechenbar" in s["warnings"]

def test_rates_and_cpu_from_delta():
    s = collector.derive(raw(T0), raw(T0 + timedelta(seconds=10), (1400, 1800), {"eth0": (2000, 1500)}))
    assert s["host"]["cpu"]["usage_percent"] == 22.2
    assert s["host"]["network"][0] == {"interface": "eth0", "rx_bytes": 2000, "tx_bytes": 1500,
                                       "rx_bytes_per_second": 100.0, "tx_bytes_per_second": 100.0}

def test_counter_reset_gives_none_not_negative():
    c0 = [{"full_id": "x", "rx_bytes": 5000, "tx_bytes": 5000}]
    c1 = [{"full_id": "x", "rx_bytes": 10, "tx_bytes": 10}]
    s = collector.derive(raw(T0, containers=c0), raw(T0 + timedelta(seconds=15), containers=c1))
    assert s["containers"][0]["rx_bytes_per_second"] is None
    assert "full_id" not in s["containers"][0]

def test_container_without_counters_gives_none_rates():
    c0 = [{"full_id": "x", "rx_bytes": None, "tx_bytes": None}]
    c1 = [{"full_id": "x", "rx_bytes": 10, "tx_bytes": None}]
    s = collector.derive(raw(T0, containers=c0), raw(T0 + timedelta(seconds=15), containers=c1))
    assert s["containers"][0]["rx_bytes_per_second"] is None
    assert s["containers"][0]["tx_bytes_per_second"] is None

def test_unreadable_blocks_produce_warnings():
    s = collector.derive(None, raw(T0, net=None, filesystems=None, memory={"available": False}))
    assert s["host"]["network"] is None and s["host"]["filesystems"] is None
    assert {"Netzwerk nicht lesbar", "Dateisysteme nicht lesbar", "Host-Speicher nicht lesbar"} <= set(s["warnings"])

def test_ring_buffer_and_history(monkeypatch):
    c = collector.Collector(Settings.from_env({"MONI_COLLECT_INTERVAL": "15", "MONI_BUFFER_SECONDS": "60"}), client=object())
    times = iter([T0 + timedelta(seconds=15 * i) for i in range(6)])
    monkeypatch.setattr(collector, "read_raw", lambda s, cl: raw(next(times)))
    for _ in range(6): c.collect_once()
    pts, oldest = c.history(T0)
    assert len(pts) == 4 and oldest == "2026-11-04T12:00:30Z"
    assert [p["timestamp"] for p in c.history(T0 + timedelta(seconds=60))[0]] == ["2026-11-04T12:01:15Z"]
    assert c.history(T0 + timedelta(days=1))[0] == []
    assert c.latest()["timestamp"] == "2026-11-04T12:01:15Z"

def test_loop_survives_exception(monkeypatch):
    c = collector.Collector(Settings.from_env({"MONI_COLLECT_INTERVAL": "1", "MONI_BUFFER_SECONDS": "10"}), client=object())
    calls = {"n": 0}
    def flaky(s, cl):
        calls["n"] += 1
        if calls["n"] == 1: raise OSError("kaputt")
        return raw(T0 + timedelta(seconds=calls["n"]))
    monkeypatch.setattr(collector, "read_raw", flaky)
    c._run_one_cycle(); c._run_one_cycle()   # erster wirft, zweiter misst
    assert c.latest() is not None

def test_stop_ends_thread(monkeypatch):
    c = collector.Collector(Settings.from_env({"MONI_COLLECT_INTERVAL": "15", "MONI_BUFFER_SECONDS": "60"}), client=object())
    monkeypatch.setattr(collector, "read_raw", lambda s, cl: raw(T0))
    c.start()
    c.stop()
    assert not c._thread.is_alive()
    assert c.latest() is not None

def test_history_ignores_subsecond_part(monkeypatch):
    c = collector.Collector(Settings.from_env({"MONI_COLLECT_INTERVAL": "15", "MONI_BUFFER_SECONDS": "60"}), client=object())
    monkeypatch.setattr(collector, "read_raw", lambda s, cl: raw(T0 + timedelta(seconds=15, milliseconds=400)))
    c.collect_once()
    assert c.history(T0 + timedelta(seconds=15))[0] == []
    assert len(c.history(T0 + timedelta(seconds=14))[0]) == 1

def test_start_is_idempotent(monkeypatch):
    c = collector.Collector(Settings.from_env({"MONI_COLLECT_INTERVAL": "15", "MONI_BUFFER_SECONDS": "60"}), client=object())
    monkeypatch.setattr(collector, "read_raw", lambda s, cl: raw(T0))
    c.start(); t = c._thread; c.start()
    assert c._thread is t
    c.stop()


def test_real_read_raw_with_unreachable_docker_and_two_cycles(tmp_path):
    proc, sys_ = tmp_path / "proc", tmp_path / "sys"
    proc.mkdir(); sys_.mkdir()
    (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 400 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    (proc / "stat").write_text("cpu  10 0 10 80 0 0 0 0 0 0\n")
    (proc / "loadavg").write_text("0.5 0.75 1.0 1/100 123\n")
    (proc / "uptime").write_text("100.5 90.0\n")
    s = Settings.from_env({"HOST_PROC": str(proc), "HOST_SYS": str(sys_), "HOST_ETC": str(tmp_path),
                           "HOST_ROOT": str(tmp_path), "MONI_DATA_DIR": str(tmp_path)})

    class NoDocker:
        def get(self, endpoint, timeout): return None
    r = collector.read_raw(s, NoDocker())
    assert r["host"]["memory"]["available"] is True and r["host"]["memory"]["total_bytes"] == 1000 * 1024
    assert r["docker"]["reachable"] is False and "Docker API nicht erreichbar" in r["warnings"]
    c = collector.Collector(s, client=NoDocker())
    c._run_one_cycle(); c._run_one_cycle()
    assert len(c._buffer) == 2 and c.latest()["docker"]["reachable"] is False

def test_failing_cycle_is_logged_and_survives(monkeypatch, caplog):
    def boom(s, c): raise RuntimeError("kaputt")
    monkeypatch.setattr(collector, "read_raw", boom)
    c = collector.Collector(Settings.from_env({}), client=object())
    with caplog.at_level("ERROR", logger="moni.collector"):
        c._run_one_cycle()
    assert "Messzyklus fehlgeschlagen" in caplog.text and c.latest() is None
