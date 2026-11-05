import json
from datetime import datetime, timezone

import pytest

from moni.manager import alerting, db
from moni.manager.alert_rules import RuleStore
from moni.manager.servers import ServerStore
from moni.secretbox import SecretBox, generate_key
from moni.timeutil import iso

T = 4_000_000_000  # nach dem updated_at der Standardregeln (Migration), sonst gälte jede Regel als geändert


@pytest.fixture
def db_(tmp_path):
    p = tmp_path / "moni.db"
    db.migrate(p)
    c = db.connect(p)
    yield c
    c.close()


@pytest.fixture
def servers(db_):
    return ServerStore(db_, SecretBox(generate_key()))


@pytest.fixture
def server(servers):
    return servers.add("web1", "https://web1.example.org", "tok").id


def add_channel(conn, name="hook", min_severity="warning", enabled=True) -> int:
    return conn.execute("INSERT INTO alert_channels (name, kind, config, min_severity, enabled, created_at) "
                        "VALUES (?, 'webhook', '{}', ?, ?, 0)", (name, min_severity, int(enabled))).lastrowid


@pytest.fixture
def channel(db_):
    return add_channel(db_)


def set_snapshot(conn, servers, sid, cpu=10.0, filesystems=(), ts=T):
    snap = {"schema_version": 1, "timestamp": iso(datetime.fromtimestamp(ts, timezone.utc)),
            "host": {"cpu": {"usage_percent": cpu},
                     "filesystems": [{"mountpoint": m, "device": "sda", "fstype": "ext4", "usage_percent": u}
                                     for m, u in filesystems]},
            "containers": [], "docker": {"reachable": True}}
    servers.record_success(sid, ts, ts, json.dumps(snap), None)


def events(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM alert_events ORDER BY id")]


def outbox(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM alert_outbox ORDER BY id")]


def instances(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM alert_instances ORDER BY rule_id, key")]


def rule_id(conn, name):
    return conn.execute("SELECT id FROM alert_rules WHERE name=?", (name,)).fetchone()[0]


def test_cpu_alert_fires_after_duration_and_creates_outbox(db_, servers, server, channel):
    set_snapshot(db_, servers, server, cpu=90)
    assert alerting.run(db_, T) == 0 and alerting.run(db_, T + 299) == 0
    assert outbox(db_) == []
    assert alerting.run(db_, T + 300) == 1
    ev = events(db_)[-1]
    assert (ev["event"], ev["severity"], ev["rule_name"]) == ("firing", "critical", "CPU-Auslastung")
    assert (ev["server_name"], ev["server_id"], ev["value"], ev["threshold"], ev["muted"], ev["ts"], ev["key"]) == \
        ("web1", server, 90, 85, 0, T + 300, "{}")
    ob = outbox(db_)
    assert [o["channel_id"] for o in ob] == [channel]
    assert (ob[0]["event_id"], ob[0]["status"], ob[0]["attempts"], ob[0]["next_try"], ob[0]["created_at"]) == \
        (ev["id"], "pending", 0, T + 300, T + 300)
    inst = instances(db_)[0]
    assert (inst["state"], inst["fired_at"], inst["last_notified"]) == ("firing", T + 300, T + 300)
    assert alerting.run(db_, T + 315) == 0 and len(outbox(db_)) == 1


def test_unreachable_server_fires_server_rule_and_freezes_others(db_, servers, server, channel):
    assert alerting.run(db_, T) == 0  # Status unknown: auch moni_server_up eingefroren
    set_snapshot(db_, servers, server, cpu=90)
    alerting.run(db_, T)
    servers.record_failure(server, T + 10, "Agent lehnt Token ab", fatal=True)
    alerting.run(db_, T + 300)
    ev = events(db_)
    assert [(e["rule_name"], e["event"], e["severity"], e["value"]) for e in ev] == \
        [("Server nicht erreichbar", "firing", "critical", 0)]
    cpu = [i for i in instances(db_) if i["rule_id"] == rule_id(db_, "CPU-Auslastung")]
    assert cpu[0]["state"] == "pending" and cpu[0]["pending_since"] == T
    set_snapshot(db_, servers, server, cpu=90, ts=T + 400)
    alerting.run(db_, T + 400)
    assert [(e["rule_name"], e["event"]) for e in events(db_)[1:]] == \
        [("Server nicht erreichbar", "resolved"), ("CPU-Auslastung", "firing")]
    assert len(outbox(db_)) == 3


def test_disk_per_mountpoint_instances(db_, servers, server, channel):
    set_snapshot(db_, servers, server, filesystems=[("/", 90), ("/data", 80), ("/boot", 10)])
    alerting.run(db_, T)
    alerting.run(db_, T + 300)
    fired = {e["key"]: e["severity"] for e in events(db_) if e["event"] == "firing"}
    assert fired == {'{"mountpoint":"/"}': "critical", '{"mountpoint":"/data"}': "warning"}
    set_snapshot(db_, servers, server, filesystems=[("/", 90)], ts=T + 315)  # /data ausgehängt
    alerting.run(db_, T + 315)
    ev = events(db_)[-1]
    assert (ev["event"], ev["key"], ev["reason"], ev["severity"]) == ("resolved", '{"mountpoint":"/data"}', "entfallen",
                                                                      "warning")
    assert outbox(db_)[-1]["event_id"] == ev["id"]  # war benachrichtigt: Behebung geht raus
    assert [i["key"] for i in instances(db_)] == ['{"mountpoint":"/"}']


def _series(conn, sid, name, points, metric="moni_container_restarts"):
    sid_ = conn.execute("INSERT INTO series (server_id, name, labels) VALUES (?, ?, ?)",
                        (sid, metric, json.dumps([["name", name]], separators=(",", ":")))).lastrowid
    conn.executemany("INSERT INTO samples_raw VALUES (?, ?, ?)", [(sid_, ts, v) for ts, v in points])


def test_restart_loop_from_raw_points_and_reset_safe(db_, servers, server, channel):
    set_snapshot(db_, servers, server)
    now = T + 1000
    # vor dem Fenster 0 -> 5 zählt nicht; Reset 6 -> 0 zählt +0; im Fenster also 1 + 0 + 1 = 2
    _series(db_, server, "web", [(now - 700, 0), (now - 500, 5), (now - 400, 6), (now - 300, 0), (now - 200, 1)])
    alerting.run(db_, now)
    assert events(db_) == []
    db_.execute("INSERT INTO samples_raw SELECT id, ?, 2 FROM series WHERE name='moni_container_restarts'", (now + 10,))
    alerting.run(db_, now + 10)
    ev = events(db_)
    assert [(e["event"], e["key"], e["value"], e["severity"]) for e in ev] == \
        [("firing", '{"name":"web"}', 3, "critical")]


def test_increase_sums_series_per_key(db_, servers, server, channel):
    set_snapshot(db_, servers, server)
    RuleStore(db_).create({"name": "Neustarts gesamt", "metric": "moni_container_restarts", "func": "increase",
                           "window_seconds": 600, "op": ">=", "critical": 4}, now=T)
    _series(db_, server, "web", [(T + 100, 0), (T + 200, 2)])
    _series(db_, server, "db", [(T + 100, 1), (T + 200, 3)])
    alerting.run(db_, T + 300)
    assert [(e["rule_name"], e["value"]) for e in events(db_)] == [("Neustarts gesamt", 4)]


def test_muted_server_records_but_does_not_queue_then_notifies_once_after(db_, servers, server, channel):
    assert servers.set_muted(server, T + 1000) and servers.get(server).muted_until == T + 1000
    assert not servers.set_muted(9999, T)
    alerting.save_settings(db_, 10)
    set_snapshot(db_, servers, server, cpu=90)
    alerting.run(db_, T)
    alerting.run(db_, T + 300)
    alerting.run(db_, T + 900)  # wäre eine Erinnerung, aber nie benachrichtigt und stumm
    assert [(e["event"], e["muted"]) for e in events(db_)] == [("firing", 1)]
    assert outbox(db_) == [] and instances(db_)[0]["last_notified"] is None
    alerting.run(db_, T + 1000)  # Stummschaltung abgelaufen: genau ein firing (D5)
    alerting.run(db_, T + 1015)
    ev = events(db_)
    assert [(e["event"], e["muted"], e["severity"], e["threshold"]) for e in ev] == \
        [("firing", 1, "critical", 85), ("firing", 0, "critical", 85)]
    assert [o["event_id"] for o in outbox(db_)] == [ev[1]["id"]]
    assert instances(db_)[0]["last_notified"] == T + 1000


def test_resolved_during_mute_is_not_queued(db_, servers, server, channel):
    set_snapshot(db_, servers, server, cpu=90)
    alerting.run(db_, T)
    alerting.run(db_, T + 300)
    assert len(outbox(db_)) == 1
    servers.set_muted(server, T + 1000)
    set_snapshot(db_, servers, server, cpu=10, ts=T + 400)
    alerting.run(db_, T + 400)
    servers.set_muted(server, None)
    alerting.run(db_, T + 415)
    assert [(e["event"], e["muted"]) for e in events(db_)] == [("firing", 0), ("resolved", 1)]
    assert len(outbox(db_)) == 1 and instances(db_) == []


def test_reminder_during_mute_is_dropped(db_, servers, server, channel):
    alerting.save_settings(db_, 10)
    set_snapshot(db_, servers, server, cpu=90)
    alerting.run(db_, T)
    alerting.run(db_, T + 300)
    servers.set_muted(server, T + 1000)
    alerting.run(db_, T + 900)  # Erinnerung fällig, aber stumm: kein Verlauf, last_notified bleibt
    assert [e["event"] for e in events(db_)] == ["firing"] and instances(db_)[0]["last_notified"] == T + 300
    alerting.run(db_, T + 1000)
    assert [(e["event"], e["muted"]) for e in events(db_)] == [("firing", 0), ("reminder", 0)]
    assert len(outbox(db_)) == 2 and instances(db_)[0]["last_notified"] == T + 1000


def test_resolved_of_never_notified_firing_is_not_queued(db_, servers, server, channel):
    servers.set_muted(server, T + 400)
    set_snapshot(db_, servers, server, cpu=90)
    alerting.run(db_, T)
    alerting.run(db_, T + 300)
    set_snapshot(db_, servers, server, cpu=10, ts=T + 400)
    alerting.run(db_, T + 400)  # Stummschaltung zu Ende, im selben Lauf behoben
    assert [(e["event"], e["muted"]) for e in events(db_)] == [("firing", 1), ("resolved", 0)]
    assert outbox(db_) == []


def test_changed_right_after_mute_sends_one_firing(db_, servers, server, channel):
    servers.set_muted(server, T + 400)
    set_snapshot(db_, servers, server, cpu=80)
    alerting.run(db_, T)
    alerting.run(db_, T + 300)
    set_snapshot(db_, servers, server, cpu=90, ts=T + 400)
    alerting.run(db_, T + 400)
    ev = events(db_)
    assert [(e["event"], e["severity"], e["muted"]) for e in ev] == [("firing", "warning", 1), ("firing", "critical", 0)]
    assert [o["event_id"] for o in outbox(db_)] == [ev[1]["id"]]


def test_reminder_after_remind_minutes_only(db_, servers, server, channel):
    assert alerting.settings(db_) == {"remind_minutes": 240}
    assert alerting.save_settings(db_, 10) == {"remind_minutes": 10} and alerting.settings(db_) == {"remind_minutes": 10}
    set_snapshot(db_, servers, server, cpu=90)
    alerting.run(db_, T)
    alerting.run(db_, T + 300)
    alerting.run(db_, T + 899)
    assert len(outbox(db_)) == 1
    alerting.run(db_, T + 900)
    alerting.run(db_, T + 915)
    assert [e["event"] for e in events(db_)] == ["firing", "reminder"] and len(outbox(db_)) == 2
    alerting.save_settings(db_, 0)
    alerting.run(db_, T + 9000)
    assert len(events(db_)) == 2


@pytest.mark.parametrize("bad", [-1, 10081, True, "5", 1.5, None])
def test_settings_validation(db_, bad):
    with pytest.raises(ValueError, match="Erinnerung muss zwischen 0 und 10080 Minuten liegen"):
        alerting.save_settings(db_, bad)


def test_save_settings_commits_only_own_transaction(db_):
    alerting.save_settings(db_, 5)
    assert not db_.in_transaction
    db_.execute("BEGIN IMMEDIATE")
    alerting.save_settings(db_, 7)
    assert db_.in_transaction
    db_.rollback()
    assert alerting.settings(db_) == {"remind_minutes": 5}


def test_min_severity_routing(db_, servers, server, channel):
    crit = add_channel(db_, "crit", "critical")
    add_channel(db_, "aus", "warning", enabled=False)
    set_snapshot(db_, servers, server, cpu=80)
    alerting.run(db_, T)
    alerting.run(db_, T + 300)
    assert [o["channel_id"] for o in outbox(db_)] == [channel]  # Kanal critical bekommt keine Warnung
    set_snapshot(db_, servers, server, cpu=90, ts=T + 315)
    alerting.run(db_, T + 315)
    assert events(db_)[-1]["event"] == "changed"
    assert [o["channel_id"] for o in outbox(db_)[1:]] == [channel, crit]
    set_snapshot(db_, servers, server, cpu=10, ts=T + 330)
    alerting.run(db_, T + 330)
    ev = events(db_)[-1]
    assert (ev["event"], ev["severity"]) == ("resolved", "critical")
    assert [o["channel_id"] for o in outbox(db_)[3:]] == [channel, crit]


def _fire_cpu(db_, servers, server):
    set_snapshot(db_, servers, server, cpu=90)
    alerting.run(db_, T)
    alerting.run(db_, T + 300)
    assert len(outbox(db_)) == 1


def test_rule_change_resolves_silently(db_, servers, server, channel):
    _fire_cpu(db_, servers, server)
    store = RuleStore(db_)
    r = store.get(rule_id(db_, "CPU-Auslastung"))
    store.update(r.id, {"name": r.name, "metric": r.metric, "op": ">", "warning": 70, "critical": 95,
                        "for_seconds": 300}, now=T + 400)
    alerting.run(db_, T + 500)
    ev = events(db_)[-1]
    assert (ev["event"], ev["reason"], ev["severity"], ev["muted"]) == ("resolved", "regel geändert", "critical", 0)
    assert len(outbox(db_)) == 1
    inst = instances(db_)
    assert len(inst) == 1 and (inst[0]["state"], inst[0]["pending_since"]) == ("pending", T + 500)
    store.update(r.id, {"name": r.name, "metric": r.metric, "op": ">", "warning": 70, "critical": 95,
                        "for_seconds": 300, "enabled": False}, now=T + 600)
    alerting.run(db_, T + 600)
    assert instances(db_) == [] and len(events(db_)) == 2  # pending: ohne Ereignis entfernt


def test_rule_disabled_resolves_silently(db_, servers, server, channel):
    _fire_cpu(db_, servers, server)
    db_.execute("UPDATE alert_rules SET enabled=0 WHERE name='CPU-Auslastung'")  # ohne updated_at: Grund deaktiviert
    alerting.run(db_, T + 315)
    assert (events(db_)[-1]["event"], events(db_)[-1]["reason"]) == ("resolved", "deaktiviert")
    assert len(outbox(db_)) == 1 and instances(db_) == []


def test_disabled_server_resolves_silently(db_, servers, server, channel):
    _fire_cpu(db_, servers, server)
    servers.set_enabled(server, False)
    alerting.run(db_, T + 315)
    ev = events(db_)[-1]
    assert (ev["event"], ev["reason"], ev["server_name"]) == ("resolved", "deaktiviert", "web1")
    assert len(outbox(db_)) == 1 and instances(db_) == []
    alerting.run(db_, T + 330)
    assert len(events(db_)) == 2


def test_drop_rule_resolves_silently_before_delete(db_, servers, server, channel):
    _fire_cpu(db_, servers, server)
    rid = rule_id(db_, "CPU-Auslastung")
    assert alerting.drop_rule(db_, rid, T + 400) == 1
    assert RuleStore(db_).delete(rid)
    ev = events(db_)[-1]
    assert (ev["event"], ev["reason"], ev["rule_id"], ev["rule_name"], ev["ts"]) == \
        ("resolved", "regel gelöscht", rid, "CPU-Auslastung", T + 400)
    assert len(outbox(db_)) == 1 and instances(db_) == []
    assert alerting.drop_rule(db_, rid, T + 500) == 0


def test_run_does_not_commit(db_, servers, server):
    set_snapshot(db_, servers, server, cpu=90)
    db_.execute("BEGIN IMMEDIATE")
    alerting.run(db_, T)
    assert db_.in_transaction
    db_.rollback()
    assert instances(db_) == []


def test_prune_retention(db_, servers, server, channel):
    old, recent = T - 366 * 86400, T - 364 * 86400
    ids = [db_.execute("INSERT INTO alert_events (ts, rule_name, server_name, key, event) VALUES (?, 'r', 's', '{}', "
                       "'firing')", (ts,)).lastrowid for ts in (old, recent, T)]
    for eid, created in ((ids[1], T - 31 * 86400), (ids[2], T - 29 * 86400)):
        db_.execute("INSERT INTO alert_outbox (event_id, channel_id, next_try, created_at) VALUES (?, ?, 0, ?)",
                    (eid, channel, created))
    alerting.prune(db_, T)
    assert [e["ts"] for e in events(db_)] == [recent, T]
    assert [o["event_id"] for o in outbox(db_)] == [ids[2]]
