"""Alarmregeln: Validierung und Speicher. Die Auswertung steht in alerting.py."""
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

from ..auth.users import Conflict
from ..metrics_model import HELP

KNOWN_METRICS = frozenset(HELP) | {"moni_server_up"}
_LABEL = re.compile(r"^[a-z_]+$")
_OPS = (">", ">=", "<", "<=")


@dataclass(frozen=True)
class Rule:
    id: int
    name: str
    metric: str
    func: str
    window_seconds: int | None
    op: str
    warning: float | None
    critical: float | None
    for_seconds: int
    key_labels: tuple[str, ...]
    label_filter: dict[str, str]
    require_prior_ok: bool
    server_id: int | None
    enabled: bool
    created_at: int
    updated_at: int


def _rule(r: sqlite3.Row) -> Rule:
    return Rule(r["id"], r["name"], r["metric"], r["func"], r["window_seconds"], r["op"], r["warning"], r["critical"],
                r["for_seconds"], tuple(json.loads(r["key_labels"])), json.loads(r["label_filter"]),
                bool(r["require_prior_ok"]), r["server_id"], bool(r["enabled"]), r["created_at"], r["updated_at"])


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate(data: dict) -> dict:
    """Prüft und normalisiert eine Regel (ohne id und Zeitstempel). Wirft ValueError mit deutschem Text."""
    name = data.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64 or any(not c.isprintable() for c in name):
        raise ValueError("Name muss 1 bis 64 Zeichen lang sein")
    if data.get("metric") not in KNOWN_METRICS:
        raise ValueError("Unbekannte Metrik")
    op = data.get("op")
    if op not in _OPS:
        raise ValueError("Vergleich muss >, >=, < oder <= sein")
    func, window = data.get("func", "value"), data.get("window_seconds")
    if func not in ("value", "increase") or (func == "increase") != (window is not None) \
            or (window is not None and not (_int(window) and 60 <= window <= 86400)):
        raise ValueError("Fenster nur bei increase (60 bis 86400 Sekunden)")
    warn, crit = data.get("warning"), data.get("critical")
    if warn is None and crit is None:
        raise ValueError("Mindestens eine Schwelle angeben")
    if any(v is not None and not _num(v) for v in (warn, crit)):
        raise ValueError("Schwellen müssen endliche Zahlen sein")
    if warn is not None and crit is not None and not (warn < crit if op in (">", ">=") else warn > crit):
        raise ValueError("Warnschwelle muss vor der kritischen Schwelle liegen")
    for_s = data.get("for_seconds", 0)
    if not _int(for_s) or not 0 <= for_s <= 86400:
        raise ValueError("Dauer muss zwischen 0 und 86400 Sekunden liegen")
    keys, filt = data.get("key_labels", []), data.get("label_filter", {})
    if not isinstance(keys, (list, tuple)) or not isinstance(filt, dict):
        raise ValueError("Labels: höchstens 5, nur [a-z_]+")
    keys = list(dict.fromkeys(keys))
    if len(keys) > 5 or not all(isinstance(k, str) and _LABEL.match(k) for k in (*keys, *filt)) \
            or not all(isinstance(v, str) for v in filt.values()):
        raise ValueError("Labels: höchstens 5, nur [a-z_]+")
    sid = data.get("server_id")
    if sid is not None and not _int(sid):
        raise ValueError("Server ist ungültig")
    return {"name": name.strip(), "metric": data["metric"], "func": func, "window_seconds": window, "op": op,
            "warning": warn, "critical": crit, "for_seconds": for_s, "key_labels": keys, "label_filter": dict(filt),
            "require_prior_ok": bool(data.get("require_prior_ok", False)), "server_id": sid,
            "enabled": bool(data.get("enabled", True))}


class RuleStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @contextmanager
    def _tx(self):
        """Committet nur, wenn die Transaktion hier begonnen wurde; sonst entscheidet der Aufrufer."""
        if self._conn.in_transaction:
            yield
            return
        self._conn.execute("BEGIN IMMEDIATE")
        with self._conn:
            yield

    def list(self) -> list[Rule]:
        return [_rule(r) for r in self._conn.execute("SELECT * FROM alert_rules ORDER BY id")]

    def get(self, rule_id: int) -> Rule | None:
        r = self._conn.execute("SELECT * FROM alert_rules WHERE id=?", (rule_id,)).fetchone()
        return _rule(r) if r else None

    def _check_server(self, sid) -> None:
        if sid is not None and not self._conn.execute("SELECT 1 FROM servers WHERE id=?", (sid,)).fetchone():
            raise ValueError("Server existiert nicht")

    @staticmethod
    def _values(v: dict) -> tuple:
        return (v["name"], v["metric"], v["func"], v["window_seconds"], v["op"], v["warning"], v["critical"],
                v["for_seconds"], json.dumps(v["key_labels"]), json.dumps(v["label_filter"], sort_keys=True),
                int(v["require_prior_ok"]), v["server_id"], int(v["enabled"]))

    _COLS = ("name=?, metric=?, func=?, window_seconds=?, op=?, warning=?, critical=?, for_seconds=?, key_labels=?, "
             "label_filter=?, require_prior_ok=?, server_id=?, enabled=?")

    def create(self, data: dict, now: int) -> Rule:
        v = validate(data)
        try:
            with self._tx():
                self._check_server(v["server_id"])
                cur = self._conn.execute(
                    "INSERT INTO alert_rules (name, metric, func, window_seconds, op, warning, critical, for_seconds, "
                    "key_labels, label_filter, require_prior_ok, server_id, enabled, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (*self._values(v), now, now))
        except sqlite3.IntegrityError:
            raise Conflict("Regelname ist bereits vergeben") from None
        return self.get(cur.lastrowid)

    def update(self, rule_id: int, data: dict, now: int) -> Rule | None:
        v = validate(data)
        try:
            with self._tx():
                self._check_server(v["server_id"])
                cur = self._conn.execute(f"UPDATE alert_rules SET {self._COLS}, updated_at=? WHERE id=?",
                                         (*self._values(v), now, rule_id))
        except sqlite3.IntegrityError:
            raise Conflict("Regelname ist bereits vergeben") from None
        return self.get(rule_id) if cur.rowcount else None

    def delete(self, rule_id: int) -> bool:
        with self._tx():
            return self._conn.execute("DELETE FROM alert_rules WHERE id=?", (rule_id,)).rowcount > 0
