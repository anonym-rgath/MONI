"""Alarmauswertung je Poller-Zyklus: Werte bestimmen, Zustandsautomat anwenden, Verlauf und Outbox schreiben.

run, drop_rule und prune öffnen keine Transaktion und committen nie: der Aufrufer hält BEGIN IMMEDIATE.
Kein Netzwerk; den Versand übernimmt der Notifier aus alert_outbox.
"""
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace

from ..metrics_model import Sample, flatten
from .alert_eval import Instance, Transition, evaluate, increase, instance_key, values_for
from .alert_rules import Rule, RuleStore

_RANK = {"warning": 0, "critical": 1}
_REMIND_DEFAULT = 240
_REMIND_MAX = 10080
EVENTS_KEEP = 365 * 86400
OUTBOX_KEEP = 30 * 86400


@contextmanager
def _tx(conn: sqlite3.Connection):
    """Committet nur, wenn die Transaktion hier begonnen wurde; sonst entscheidet der Aufrufer (P2-R1)."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    with conn:
        yield


def settings(conn: sqlite3.Connection) -> dict:
    r = conn.execute("SELECT value FROM meta WHERE key='alerting'").fetchone()
    return {"remind_minutes": json.loads(r[0]).get("remind_minutes", _REMIND_DEFAULT) if r else _REMIND_DEFAULT}


def save_settings(conn: sqlite3.Connection, remind_minutes) -> dict:
    if not (isinstance(remind_minutes, int) and not isinstance(remind_minutes, bool)
            and 0 <= remind_minutes <= _REMIND_MAX):
        raise ValueError("Erinnerung muss zwischen 0 und 10080 Minuten liegen")
    value = {"remind_minutes": remind_minutes}
    with _tx(conn):
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('alerting', ?)", (json.dumps(value),))
    return value


def _event(conn, now: int, rule_id, rule_name, server_id, server_name, key, event, severity, value, threshold,
           reason, muted: bool) -> int:
    return conn.execute(
        "INSERT INTO alert_events (ts, rule_id, rule_name, server_id, server_name, key, event, severity, value, "
        "threshold, reason, muted) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (now, rule_id, rule_name, server_id, server_name, key, event, severity, value, threshold, reason,
         int(muted))).lastrowid


def _queue(conn, event_id: int, severity: str, channels, now: int) -> None:
    for c in channels:  # D9
        if _RANK[severity] >= _RANK[c["min_severity"]]:
            conn.execute("INSERT INTO alert_outbox (event_id, channel_id, status, attempts, next_try, created_at) "
                         "VALUES (?, ?, 'pending', 0, ?, ?)", (event_id, c["id"], now, now))


def _muted(muted_until, now: int) -> bool:
    return muted_until is not None and muted_until > now


def _instance(r: sqlite3.Row) -> Instance:
    return Instance(r["key"], r["state"], r["severity"], r["value"], r["pending_since"], r["fired_at"],
                    r["last_notified"], bool(r["seen_ok"]))


_INSTANCES = ("SELECT i.*, r.name AS rule_name, s.name AS server_name, s.muted_until, s.enabled AS server_enabled "
              "FROM alert_instances i "
              "JOIN alert_rules r ON r.id = i.rule_id JOIN servers s ON s.id = i.server_id")


def _drop(conn, r: sqlite3.Row, reason: str, now: int) -> int:
    """Instanz ohne Benachrichtigung entfernen (D11); firing hinterlässt ein resolved im Verlauf."""
    conn.execute("DELETE FROM alert_instances WHERE rule_id=? AND server_id=? AND key=?",
                 (r["rule_id"], r["server_id"], r["key"]))
    if r["state"] != "firing":
        return 0
    _event(conn, now, r["rule_id"], r["rule_name"], r["server_id"], r["server_name"], r["key"], "resolved",
           r["severity"], r["value"], None, reason, _muted(r["muted_until"], now))
    return 1


def drop_rule(conn: sqlite3.Connection, rule_id: int, now: int) -> int:
    """Vor RuleStore.delete in derselben Transaktion aufrufen: die FK-Kaskade löscht sonst ohne Verlauf."""
    rows = conn.execute(_INSTANCES + " WHERE i.rule_id=?", (rule_id,)).fetchall()
    return sum(_drop(conn, r, "regel gelöscht", now) for r in rows)


def _increase(conn, rule: Rule, server_id: int, now: int) -> dict[str, float]:
    out: dict[str, float] = {}
    for sid, labels in conn.execute("SELECT id, labels FROM series WHERE server_id=? AND name=?",
                                    (server_id, rule.metric)).fetchall():
        lab = dict(json.loads(labels))
        if any(lab.get(k) != v for k, v in rule.label_filter.items()):
            continue
        pts = conn.execute("SELECT ts, value FROM samples_raw WHERE series_id=? AND ts BETWEEN ? AND ? ORDER BY ts",
                           (sid, now - rule.window_seconds, now)).fetchall()
        if pts:  # ohne Punkte im Fenster entfällt die Serie (D10)
            k = instance_key(lab, rule.key_labels)
            out[k] = out.get(k, 0.0) + increase([tuple(p) for p in pts])
    return out


def _values(conn, rule: Rule, srv: sqlite3.Row, now: int) -> dict[str, float] | None:
    """D2; None heißt eingefroren."""
    if rule.metric == "moni_server_up":
        if srv["status"] == "unknown":
            return None
        return values_for(rule, [Sample("moni_server_up", (), 1 if srv["status"] == "ok" else 0)])
    if srv["status"] != "ok" or srv["last_snapshot"] is None:
        return None
    if rule.func == "increase":
        return _increase(conn, rule, srv["id"], now)
    return values_for(rule, flatten(json.loads(srv["last_snapshot"])))


def _apply(conn, rule: Rule, srv: sqlite3.Row, old: Instance | None, t: Transition, now: int, muted: bool,
           channels) -> int:
    inst, event = t.instance, t.event
    if muted and event == "reminder":  # P5-R2: im Mute verworfen
        event = None
    if not muted and event in (None, "changed") and inst is not None and inst.state == "firing" \
            and inst.last_notified is None:
        event = "firing"  # D5: nach dem Mute nachholen (auch statt eines changed, das nie gemeldet würde)
    n = 0
    if event is not None:
        eid = _event(conn, now, rule.id, rule.name, srv["id"], srv["name"], t.key, event, t.severity, t.value,
                     t.threshold, t.reason, muted)
        n = 1
        if not muted and (event in ("firing", "reminder") or (old is not None and old.last_notified is not None)):
            _queue(conn, eid, t.severity, channels, now)
            if inst is not None and inst.state == "firing":
                inst = replace(inst, last_notified=now)
    if inst is None:
        if old is not None:
            conn.execute("DELETE FROM alert_instances WHERE rule_id=? AND server_id=? AND key=?",
                         (rule.id, srv["id"], t.key))
    elif inst != old:
        conn.execute("INSERT OR REPLACE INTO alert_instances (rule_id, server_id, key, state, severity, value, "
                     "pending_since, fired_at, last_notified, seen_ok) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (rule.id, srv["id"], t.key, inst.state, inst.severity, inst.value, inst.pending_since,
                      inst.fired_at, inst.last_notified, int(inst.seen_ok)))
    return n


def run(conn: sqlite3.Connection, now: int) -> int:
    """Wertet alle aktiven Regeln aus; liefert die Anzahl neuer Ereignisse."""
    remind = settings(conn)["remind_minutes"] * 60
    rules = RuleStore(conn).list()
    by_id = {r.id: r for r in rules}
    srvs = conn.execute("SELECT id, name, status, last_snapshot, muted_until FROM servers "
                        "WHERE enabled=1 ORDER BY id").fetchall()
    channels = conn.execute("SELECT id, min_severity FROM alert_channels WHERE enabled=1 ORDER BY id").fetchall()
    n = 0
    current: dict[tuple[int, int], dict[str, Instance]] = {}
    for r in conn.execute(_INSTANCES).fetchall():  # D11
        rule = by_id[r["rule_id"]]
        enabled = rule.enabled and r["server_enabled"]
        since = r["fired_at"] if r["state"] == "firing" else r["pending_since"]
        changed = rule.server_id not in (None, r["server_id"]) or (since is not None and rule.updated_at > since)
        if not enabled or changed:
            n += _drop(conn, r, "deaktiviert" if not enabled else "regel geändert", now)
            continue
        current.setdefault((r["rule_id"], r["server_id"]), {})[r["key"]] = _instance(r)
    for rule in rules:
        if not rule.enabled:
            continue
        for srv in srvs:
            if rule.server_id not in (None, srv["id"]):
                continue
            cur = current.get((rule.id, srv["id"]), {})
            muted = _muted(srv["muted_until"], now)
            for t in evaluate(rule, _values(conn, rule, srv, now), cur, now, remind):
                n += _apply(conn, rule, srv, cur.get(t.key), t, now, muted, channels)
    return n


def prune(conn: sqlite3.Connection, now: int) -> None:
    conn.execute("DELETE FROM alert_events WHERE ts < ?", (now - EVENTS_KEEP,))
    conn.execute("DELETE FROM alert_outbox WHERE created_at < ?", (now - OUTBOX_KEEP,))
