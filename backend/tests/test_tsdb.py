from datetime import datetime, timezone

import pytest

from moni.config import Settings
from moni.manager import db, tsdb
from moni.manager.servers import ServerStore
from moni.secretbox import SecretBox, generate_key
from moni.timeutil import iso

T = 1793793600  # 2026-11-04T12:00:00Z


@pytest.fixture
def settings(tmp_path):
    return Settings.from_env({"MONI_ROLE": "manager", "MONI_SECRET_KEY": generate_key(), "MONI_DATA_DIR": str(tmp_path)})


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "moni.db"
    db.migrate(p)
    c = db.connect(p)
    yield c
    c.close()


@pytest.fixture
def sid(conn):
    return ServerStore(conn, SecretBox(generate_key())).add("sau", "https://moni.example.org", "tok").id


def snap(ts, cpu):
    return {"schema_version": 1, "timestamp": iso(datetime.fromtimestamp(ts, timezone.utc)),
            "host": {"cpu": {"usage_percent": cpu}}, "containers": [], "docker": {"reachable": True}}


M = "moni_host_cpu_usage_percent"


def test_write_is_idempotent(conn, sid):
    assert tsdb.write_snapshots(conn, sid, [snap(T, 10.0), snap(T + 15, 20.0)]) == T + 15
    tsdb.write_snapshots(conn, sid, [snap(T + 15, 99.0)])  # Doppelt -> ignoriert
    r = tsdb.query(conn, sid, M, T, T + 60, "raw")
    assert r["series"][0]["points"] == [["2026-11-04T12:00:00Z", 10.0], ["2026-11-04T12:00:15Z", 20.0]]


def test_write_skips_bad_timestamp_and_timestamp_metric(conn, sid):
    bad = [{"host": {}}, {"timestamp": "unsinn"}, {"timestamp": "2026-11-04T12:00:00"}]
    assert tsdb.write_snapshots(conn, sid, bad) is None
    tsdb.write_snapshots(conn, sid, [snap(T, 1.0)])
    assert tsdb.query(conn, sid, "moni_snapshot_timestamp_seconds", T, T + 10, "raw")["series"] == []


def test_write_duplicate_labels_first_wins(conn, sid):
    s = snap(T, 1.0)
    s["containers"] = [{"name": "web", "cpu_percent": 5.0}, {"name": "web", "cpu_percent": 9.0}]
    tsdb.write_snapshots(conn, sid, [s])
    r = tsdb.query(conn, sid, "moni_container_cpu_usage_percent", T, T + 10, "raw")
    assert r["series"][0]["points"][0][1] == 5.0


def test_rollup_only_complete_buckets(conn, sid):
    tsdb.write_snapshots(conn, sid, [snap(T + i * 15, v) for i, v in enumerate([10.0, 20.0, 30.0, 40.0, 50.0])])
    tsdb.rollup(conn, now=T + 70)  # Minute T ist abgeschlossen, Minute T+60 nicht
    r = tsdb.query(conn, sid, M, T, T + 120, "1m")
    assert r["series"][0]["points"] == [["2026-11-04T12:00:00Z", 10.0, 25.0, 40.0]]
    tsdb.rollup(conn, now=T + 3600 + 5)  # Stunde T abgeschlossen
    h = tsdb.query(conn, sid, M, T, T + 7200, "1h")
    assert h["series"][0]["points"] == [["2026-11-04T12:00:00Z", 10.0, 30.0, 50.0]]


def test_rollup_late_point_updates_bucket(conn, sid):
    tsdb.write_snapshots(conn, sid, [snap(T, 10.0)])
    tsdb.rollup(conn, now=T + 3600 + 5)
    tsdb.write_snapshots(conn, sid, [snap(T + 30, 30.0)])  # Nachzügler
    tsdb.rollup(conn, now=T + 3600 + 10)
    assert tsdb.query(conn, sid, M, T, T + 60, "1m")["series"][0]["points"] == [["2026-11-04T12:00:00Z", 10.0, 20.0, 30.0]]
    assert tsdb.query(conn, sid, M, T, T + 3600, "1h")["series"][0]["points"] == [["2026-11-04T12:00:00Z", 10.0, 20.0, 30.0]]


def test_hour_avg_is_count_weighted(conn, sid):
    # Minute 0: 3 Punkte (avg 10), Minute 1: 1 Punkt (50) -> gewichtet 20, nicht 30
    tsdb.write_snapshots(conn, sid, [snap(T, 10.0), snap(T + 15, 10.0), snap(T + 30, 10.0), snap(T + 60, 50.0)])
    tsdb.rollup(conn, now=T + 3600 + 5)
    assert tsdb.query(conn, sid, M, T, T + 60, "1h")["series"][0]["points"] == [["2026-11-04T12:00:00Z", 10.0, 20.0, 50.0]]


def test_prune(conn, sid, settings):
    tsdb.write_snapshots(conn, sid, [snap(T, 1.0), snap(T + 49 * 3600, 2.0)])
    tsdb.prune(conn, now=T + 49 * 3600, settings=settings)  # 48 h Rohdaten
    r = tsdb.query(conn, sid, M, T - 10, T + 50 * 3600, "raw")
    assert [p[1] for p in r["series"][0]["points"]] == [2.0]


@pytest.mark.parametrize("span,res", [(6 * 3600, "raw"), (6 * 3600 + 1, "1m"), (7 * 86400, "1m"), (7 * 86400 + 1, "1h")])
def test_pick_resolution(span, res):
    assert tsdb.pick_resolution(T, T + span) == res


def test_query_errors_and_empty(conn, sid):
    with pytest.raises(ValueError):
        tsdb.query(conn, sid, "x", T + 10, T)
    with pytest.raises(ValueError):
        tsdb.query(conn, sid, "x", T, T + 10, "5m")
    assert tsdb.query(conn, sid, "unbekannt", T, T + 10)["series"] == []


def test_label_filter_and_list_series(conn, sid):
    s = snap(T, 1.0)
    s["containers"] = [{"name": "web", "image": "i", "status": "running", "health": "none", "cpu_percent": 5.0},
                       {"name": "db", "image": "i", "status": "running", "health": "none", "cpu_percent": 7.0}]
    tsdb.write_snapshots(conn, sid, [s])
    r = tsdb.query(conn, sid, "moni_container_cpu_usage_percent", T, T + 10, "raw", {"name": "db"})
    assert [x["labels"]["name"] for x in r["series"]] == ["db"] and r["series"][0]["points"][0][1] == 7.0
    assert {"metric": "moni_container_cpu_usage_percent", "labels": {"name": "web"}} in tsdb.list_series(conn, sid)
