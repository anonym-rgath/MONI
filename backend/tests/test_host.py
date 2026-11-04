import os

from moni.collect import host


def write(path, content):
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def test_cpu_times_and_percent(tmp_path):
    write(tmp_path / "stat", "cpu  100 0 100 700 0 0 0 0 0 0\n")
    assert host.read_cpu_times(str(tmp_path)) == (700, 900)
    assert host.cpu_percent((700, 900), (1400, 1800)) == 22.2
    assert host.cpu_percent(None, (1400, 1800)) is None
    assert host.cpu_percent((700, 900), (700, 900)) is None


def test_cpu_info_without_cpufreq_is_none(tmp_path):
    info = host.read_cpu_info(str(tmp_path))
    assert info["frequency_mhz"] is None and info["cores"] >= 1


def test_memory_bytes(tmp_path):
    write(tmp_path / "meminfo", "MemTotal: 8000000 kB\nMemAvailable: 6000000 kB\n"
                                "SwapTotal: 1000000 kB\nSwapFree: 800000 kB\n")
    m = host.read_memory(str(tmp_path))
    assert m["total_bytes"] == 8_192_000_000 and m["available_bytes"] == 6_144_000_000
    assert m["used_bytes"] == 2_048_000_000 and m["usage_percent"] == 25.0
    assert m["swap_total_bytes"] == 1_024_000_000 and m["swap_used_bytes"] == 204_800_000
    assert m["swap_percent"] == 20.0 and m["available"] is True


def test_memory_missing_is_none(tmp_path):
    m = host.read_memory(str(tmp_path))
    assert m["available"] is False and m["total_bytes"] is None and m["usage_percent"] is None


def test_load_uptime_processes(tmp_path):
    write(tmp_path / "loadavg", "0.50 0.75 1.00 1/234 5678\n")
    write(tmp_path / "uptime", "7200.55 100.0\n")
    (tmp_path / "1").mkdir(); (tmp_path / "42").mkdir(); (tmp_path / "self").mkdir()
    assert host.read_load(str(tmp_path)) == {"load1": 0.5, "load5": 0.75, "load15": 1.0, "available": True}
    assert host.read_uptime_seconds(str(tmp_path)) == 7200.55
    assert host.read_process_count(str(tmp_path)) == 2


def test_temperatures_all_zones(tmp_path):
    for i, (typ, milli) in enumerate([("cpu-thermal", "52500"), ("gpu", "61000")]):
        write(tmp_path / f"class/thermal/thermal_zone{i}/type", typ)
        write(tmp_path / f"class/thermal/thermal_zone{i}/temp", milli)
    t = host.read_temperatures(str(tmp_path))
    assert t["max_celsius"] == 61.0 and t["available"] is True
    assert {"name": "cpu-thermal", "celsius": 52.5} in t["zones"]


def test_temperatures_none(tmp_path):
    assert host.read_temperatures(str(tmp_path)) == {"max_celsius": None, "zones": [], "available": False}


def test_net_counters_filters_virtual(tmp_path):
    write(tmp_path / "1/net/dev",
          "Inter-|   Receive  |  Transmit\n face |bytes packets|bytes\n"
          "    lo: 100 1 0 0 0 0 0 0 100 1 0 0 0 0 0 0\n"
          "  eth0: 5000 10 0 0 0 0 0 0 3000 8 0 0 0 0 0 0\n"
          "vethab: 1 1 0 0 0 0 0 0 1 1 0 0 0 0 0 0\n"
          "docker0: 1 1 0 0 0 0 0 0 1 1 0 0 0 0 0 0\n")
    assert host.read_net_counters(str(tmp_path)) == {"eth0": (5000, 3000)}


def test_filesystems_filters_and_dedupes(tmp_path, monkeypatch):
    write(tmp_path / "1/mounts",
          "/dev/root / ext4 rw 0 0\nproc /proc proc rw 0 0\ntmpfs /run tmpfs rw 0 0\n"
          "/dev/sda1 /mnt/data ext4 rw 0 0\n"
          "overlay /var/lib/docker/overlay2/x/merged overlay rw 0 0\n"
          "/dev/root /home ext4 rw 0 0\n")
    class St: f_blocks, f_bfree, f_bavail, f_frsize = 1000, 400, 300, 4096
    seen = []
    monkeypatch.setattr(host.os, "statvfs", lambda p: seen.append(p) or St)
    fs = host.read_filesystems(str(tmp_path), "/host/root")
    assert [f["mountpoint"] for f in fs] == ["/", "/mnt/data"]
    assert seen == ["/host/root/", "/host/root/mnt/data"]
    assert fs[0] == {"mountpoint": "/", "device": "/dev/root", "fstype": "ext4",
                          "total_bytes": 4_096_000, "used_bytes": 2_457_600,
                          "free_bytes": 1_228_800, "usage_percent": 66.7}


def test_filesystems_unreadable_mounts_is_none(tmp_path):
    assert host.read_filesystems(str(tmp_path), "/") is None
