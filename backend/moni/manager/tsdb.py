"""Zeitreihen-Speicher des Managers: Schreiben, Verdichten, Aufbewahren, Abfragen. Commit liegt beim Aufrufer."""
import json
import logging
import math
import sqlite3
from datetime import datetime, timezone

from ..config import Settings
from ..metrics_model import flatten
from ..timeutil import iso, parse_iso


log = logging.getLogger(__name__)

_LOOKBACK = 600  # Nachzügler im normalen Zyklus; größere Rückfüllung senkt die Wasserzeichen in write_snapshots
_TABLES = {"raw": "samples_raw", "1m": "samples_1m", "1h": "samples_1h"}


def _labels_json(labels) -> str:
    return json.dumps(list(labels), separators=(",", ":"))


def write_snapshots(conn: sqlite3.Connection, server_id: int, snapshots: list[dict]) -> int | None:
    ids: dict[tuple, int] = {}
    newest = oldest = None
    for snap in snapshots:
        try:
            ts = int(parse_iso(snap["timestamp"]).timestamp())
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            log.warning("Snapshot ohne gültigen Zeitstempel übersprungen (Server %s)", server_id)
            continue
        for s in flatten(snap):
            try:
                value = float(s.value)
            except OverflowError:
                continue
            # > 2**63: kein plausibler Messwert; Summen in der Verdichtung könnten sonst zu ±inf/NaN überlaufen
            if s.name == "moni_snapshot_timestamp_seconds" or not math.isfinite(value) or abs(value) > 2 ** 63:
                continue
            key = (s.name, _labels_json(s.labels))
            if key not in ids:
                conn.execute("INSERT OR IGNORE INTO series (server_id, name, labels) VALUES (?, ?, ?)", (server_id, *key))
                ids[key] = conn.execute("SELECT id FROM series WHERE server_id=? AND name=? AND labels=?",
                                        (server_id, *key)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO samples_raw (series_id, ts, value) VALUES (?, ?, ?)",
                         (ids[key], ts, value))
        newest = ts if newest is None else max(newest, ts)
        oldest = ts if oldest is None else min(oldest, ts)
    if oldest is not None:  # Rückfüllung hinter dem Wasserzeichen: betroffene Buckets neu verdichten
        for key, size in (("rollup_1m_until", 60), ("rollup_1h_until", 3600)):
            conn.execute("UPDATE meta SET value = min(CAST(value AS INTEGER), ?) WHERE key=?", (oldest // size * size, key))
    return newest


def _until(conn, key: str) -> int:
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return int(r[0]) if r else 0


_ROLLUPS = (
    ("rollup_1m_until", 60, "samples_raw", "samples_1m", "MIN(r.value), AVG(r.value), MAX(r.value), COUNT(*)"),
    ("rollup_1h_until", 3600, "samples_1m", "samples_1h", "MIN(r.min), SUM(r.avg * r.count) / SUM(r.count), MAX(r.max), SUM(r.count)"),
)


def _rollup_sql(size: int, src: str, dst: str, agg: str) -> str:
    # Über series treiben: Bereichszugriff per Primärschlüssel statt Vollscan (kein Index auf ts allein)
    return (f"INSERT OR REPLACE INTO {dst} (series_id, ts, min, avg, max, count) "
            f"SELECT r.series_id, r.ts / {size} * {size}, {agg} FROM series s CROSS JOIN {src} r "
            f"ON r.series_id = s.id AND r.ts >= ? AND r.ts < ? GROUP BY r.series_id, r.ts / {size}")


def _prune_sql(table: str) -> str:
    return f"DELETE FROM {table} WHERE series_id IN (SELECT id FROM series) AND ts < ?"


def rollup(conn: sqlite3.Connection, now: int) -> None:
    for key, size, src, dst, agg in _ROLLUPS:
        limit = now // size * size  # nur abgeschlossene Buckets
        since = max(_until(conn, key) - _LOOKBACK, 0) // size * size
        conn.execute(_rollup_sql(size, src, dst, agg), (since, limit))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(limit)))


def prune(conn: sqlite3.Connection, now: int, settings: Settings) -> None:
    for table, keep in (("samples_raw", settings.retention_raw_hours * 3600),
                        ("samples_1m", settings.retention_minute_days * 86400),
                        ("samples_1h", settings.retention_hour_days * 86400)):
        conn.execute(_prune_sql(table), (now - keep,))
    conn.execute("DELETE FROM series WHERE " + " AND ".join(
        f"NOT EXISTS (SELECT 1 FROM {t} WHERE series_id = series.id)" for t in _TABLES.values()))


def list_series(conn: sqlite3.Connection, server_id: int) -> list[dict]:
    rows = conn.execute("SELECT name, labels FROM series WHERE server_id=? ORDER BY name, labels", (server_id,))
    return [{"metric": r["name"], "labels": dict(json.loads(r["labels"]))} for r in rows]


def pick_resolution(start: int, end: int) -> str:
    span = end - start
    return "raw" if span <= 6 * 3600 else "1m" if span <= 7 * 86400 else "1h"


def _finite(v):
    return v if v is None or math.isfinite(v) else None  # JSON kennt kein inf/NaN


def _query(conn, server_id, metric, start, end, res, labels) -> dict:
    cols = "value" if res == "raw" else "min, avg, max"
    out = []
    rows = conn.execute("SELECT id, labels FROM series WHERE server_id=? AND name=? ORDER BY labels",
                        (server_id, metric)).fetchall()
    for r in rows:
        lab = dict(json.loads(r["labels"]))
        if labels and any(lab.get(k) != v for k, v in labels.items()):
            continue
        pts = conn.execute(f"SELECT ts, {cols} FROM {_TABLES[res]} WHERE series_id=? AND ts BETWEEN ? AND ? ORDER BY ts",
                           (r["id"], start, end)).fetchall()
        out.append({"labels": lab, "points": [[iso(datetime.fromtimestamp(p[0], timezone.utc)), *map(_finite, p[1:])]
                                              for p in pts]})
    return {"resolution": res, "series": out}


def query(conn: sqlite3.Connection, server_id: int, metric: str, start: int, end: int,
          resolution: str = "auto", labels: dict[str, str] | None = None) -> dict:
    if start > end:
        raise ValueError("Beginn liegt nach dem Ende des Zeitraums")
    if resolution not in ("auto", *_TABLES):
        raise ValueError("Unbekannte Auflösung (erlaubt: auto, raw, 1m, 1h)")
    if resolution != "auto":
        return _query(conn, server_id, metric, start, end, resolution, labels)
    order = list(_TABLES)
    first = _query(conn, server_id, metric, start, end, pick_resolution(start, end), labels)
    for res in order[order.index(first["resolution"]):]:  # leer -> nächst gröbere Auflösung (Rohdaten sind evtl. verdichtet)
        r = first if res == first["resolution"] else _query(conn, server_id, metric, start, end, res, labels)
        if any(s["points"] for s in r["series"]):
            return r
    return first
