from moni.metrics_model import Sample, flatten
from test_prometheus import FULL, SNAP


def test_flatten_matches_render():
    out = flatten(SNAP)
    assert Sample("moni_host_cpu_usage_percent", (), 12.5) in out
    assert Sample("moni_filesystem_size_bytes",
                  (("mountpoint", "/"), ("device", "/dev/root"), ("fstype", "ext4")), 4096000) in out
    assert Sample("moni_snapshot_timestamp_seconds", (), 1793793600) in out
    assert all(s.value is not None for s in out)


def test_flatten_bools_and_unknown_health():
    out = flatten(FULL)
    assert Sample("moni_container_up", (("name", "b"), ("image", "j"), ("status", "exited")), 0) in out
    assert Sample("moni_container_healthy", (("name", "b"),), 0) in out
    assert not [s for s in out if s.name == "moni_container_healthy" and s.labels == (("name", "c"),)]


def test_flatten_tolerates_missing_blocks():
    out = flatten({"timestamp": "2026-11-04T12:00:00Z", "host": None, "containers": None, "docker": None})
    assert [s.name for s in out] == ["moni_snapshot_timestamp_seconds"]
