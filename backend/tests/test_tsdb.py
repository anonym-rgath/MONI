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


def _count(conn, table, sid_):
    return conn.execute(f"SELECT COUNT(*) FROM {table} s JOIN series x ON x.id=s.series_id WHERE x.server_id=? AND x.name=?", (sid_, M)).fetchone()[0]


def test_backfill_after_long_outage_is_rolled_up(conn, sid):
    tsdb.write_snapshots(conn, sid, [snap(T, 1.0)])
    tsdb.rollup(conn, now=T + 120)
    for now in range(T + 420, T + 110 * 60 + 300, 300):  # Ausfall: Verdichtung läuft leer weiter
        tsdb.rollup(conn, now=now)
    tsdb.write_snapshots(conn, sid, [snap(T + 60 * i, float(i)) for i in range(1, 111)])  # Rückfüllung
    tsdb.rollup(conn, now=T + 7200 + 5)
    assert _count(conn, "samples_1m", sid) == 111
    h = conn.execute("SELECT h.ts, h.count FROM samples_1h h JOIN series x ON x.id=h.series_id WHERE x.name=? ORDER BY h.ts", (M,)).fetchall()
    assert [tuple(r) for r in h] == [(T, 60), (T + 3600, 51)]


def test_rollup_and_prune_use_no_table_scan(conn, sid, settings):
    stmts = [(tsdb._rollup_sql(size, src, dst, agg), (0, 10)) for _, size, src, dst, agg in tsdb._ROLLUPS]
    stmts += [(tsdb._prune_sql(t), (0,)) for t in ("samples_raw", "samples_1m", "samples_1h")]
    for sql, params in stmts:
        plan = " | ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))
        print(plan)
        assert "SCAN samples_" not in plan and "SEARCH" in plan


def test_auto_falls_back_to_coarser_resolution(conn, sid, settings):
    tsdb.write_snapshots(conn, sid, [snap(T, 5.0)])
    tsdb.rollup(conn, now=T + 120)
    tsdb.prune(conn, now=T + 3 * 86400, settings=settings)  # Rohdaten weg, Minutenwerte da
    r = tsdb.query(conn, sid, M, T - 100, T + 3600, "auto")
    assert r["resolution"] == "1m" and r["series"][0]["points"] == [["2026-11-04T12:00:00Z", 5.0, 5.0, 5.0]]


def test_prune_removes_orphan_series(conn, sid, settings):
    a = snap(T, 1.0)
    a["containers"] = [{"name": "alt", "cpu_percent": 3.0}]
    tsdb.write_snapshots(conn, sid, [a, snap(T + 49 * 3600, 2.0)])
    tsdb.prune(conn, now=T + 49 * 3600, settings=settings)
    assert [s["metric"] for s in tsdb.list_series(conn, sid)] == ["moni_docker_up", M]


def test_prune_minute_and_hour_boundaries(conn, sid, settings):
    tsdb.write_snapshots(conn, sid, [snap(T, 1.0)])
    sr = conn.execute("SELECT id FROM series").fetchone()[0]
    now = T + 400 * 86400
    for table, days in (("samples_1m", 30), ("samples_1h", 365)):
        for ts in (now - days * 86400 - 1, now - days * 86400):
            conn.execute(f"INSERT INTO {table} VALUES (?, ?, 1, 1, 1, 1)", (sr, ts))
    tsdb.prune(conn, now=now, settings=settings)
    assert conn.execute("SELECT ts FROM samples_1m").fetchall()[0][0] == now - 30 * 86400
    assert conn.execute("SELECT ts FROM samples_1h").fetchall()[0][0] == now - 365 * 86400
    assert _count(conn, "samples_1m", sid) == 1 and _count(conn, "samples_1h", sid) == 1


def test_non_finite_and_huge_values_skipped(conn, sid):
    s = snap(T, float("nan"))
    s["host"]["load"] = {"load1": float("inf"), "load5": 10 ** 400, "load15": 2.0}
    assert tsdb.write_snapshots(conn, sid, [s]) == T
    assert [x["metric"] for x in tsdb.list_series(conn, sid)] == ["moni_docker_up", "moni_host_load15"]


def test_values_beyond_int64_skipped_and_query_never_non_finite(conn, sid):  # Final 1
    assert tsdb.write_snapshots(conn, sid, [snap(T, 1.7e308), snap(T + 15, -2.0 ** 64), snap(T + 30, 2.0 ** 63)]) == T + 30
    assert [p[1] for p in tsdb.query(conn, sid, M, T, T + 60, "raw")["series"][0]["points"]] == [2.0 ** 63]
    sr = conn.execute("SELECT id FROM series WHERE server_id=? AND name=?", (sid, M)).fetchone()[0]
    conn.execute("INSERT INTO samples_1m VALUES (?, ?, ?, ?, ?, ?)", (sr, T, float("-inf"), float("inf"), float("inf"), 2))
    assert tsdb.query(conn, sid, M, T, T + 60, "1m")["series"][0]["points"] == [["2026-11-04T12:00:00Z", None, None, None]]
