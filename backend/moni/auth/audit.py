"""Audit-Log (D10). Details enthalten nie Geheimnisse (Aufrufer-Pflicht)."""
import json
import sqlite3
import time
from datetime import datetime, timezone

from .. import timeutil

RETENTION_DAYS = 365
_MAX = 256


def _cut(v: str | None) -> str | None:
    return None if v is None else v[:_MAX]


def record(conn: sqlite3.Connection, actor: str, action: str, *, target: str | None = None, ip: str | None = None,
           details: dict | None = None, now: int | None = None) -> None:
    """Schreibt einen Eintrag; committet nur, wenn der Aufrufer keine Transaktion offen hat."""
    d = None if details is None else json.dumps(details, allow_nan=False)
    own = not conn.in_transaction
    conn.execute("INSERT INTO audit_log (ts, actor, action, target, ip, details) VALUES (?,?,?,?,?,?)",
                 (int(time.time()) if now is None else now, _cut(actor), _cut(action), _cut(target), _cut(ip), d))
    if own:
        conn.commit()


def entries(conn: sqlite3.Connection, limit: int = 100, before: int | None = None) -> list[dict]:
    rows = conn.execute("SELECT * FROM audit_log WHERE (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
                        (before, before, limit)).fetchall()
    return [{"id": r["id"], "ts": timeutil.iso(datetime.fromtimestamp(r["ts"], timezone.utc)), "actor": r["actor"],
             "action": r["action"], "target": r["target"], "ip": r["ip"],
             "details": None if r["details"] is None else json.loads(r["details"])} for r in rows]


def prune(conn: sqlite3.Connection, cutoff_ts: int) -> int:
    own = not conn.in_transaction
    n = conn.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff_ts,)).rowcount
    if own:
        conn.commit()
    return n
