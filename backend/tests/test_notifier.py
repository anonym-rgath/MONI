import logging
import threading

import pytest

from moni.manager import alert_channels, db
from moni.manager.alert_channels import SendError
from moni.manager.notifier import Notifier
from moni.secretbox import SecretBox, generate_key


@pytest.fixture
def box():
    return SecretBox(generate_key())


@pytest.fixture
def settings(tmp_path):
    from moni.config import Settings
    return Settings.from_env({"MONI_DATA_DIR": str(tmp_path), "MONI_TLS": "off"})


@pytest.fixture
def conn(settings):
    db.migrate(settings.db_path)
    c = db.connect(settings.db_path)
    yield c
    c.close()


def add(conn, box, name="hook", enabled=1, secret=True):
    enc = box.encrypt('{"url": "https://h.example.org/x"}') if secret else None
    cid = conn.execute("INSERT INTO alert_channels (name, kind, config, secret_enc, enabled, created_at) "
                       "VALUES (?, 'webhook', '{}', ?, ?, 0)", (name, enc, enabled)).lastrowid
    eid = conn.execute("INSERT INTO alert_events (ts, rule_name, server_name, key, event, severity, value, threshold, reason) "
                       "VALUES (5, 'CPU', 'web1', '{}', 'firing', 'critical', 90, 85, 'r')").lastrowid
    conn.execute("INSERT INTO alert_outbox (event_id, channel_id, next_try, created_at) VALUES (?, ?, 0, 0)", (eid, cid))
    conn.commit()
    return cid


def row(conn):
    return dict(conn.execute("SELECT * FROM alert_outbox ORDER BY id").fetchone())


def ok_deliver(calls):
    def f(channel, secrets, event, manager_name):
        calls.append((channel.name, secrets, event, manager_name))
    return f


def failing(channel, secrets, event, manager_name):
    raise SendError("Webhook nicht erreichbar")


def test_success_marks_sent_and_never_resends(conn, settings, box):
    add(conn, box)
    calls = []
    n = Notifier(settings, box, ok_deliver(calls))
    assert n.run_once(0) == 1
    assert row(conn)["status"] == "sent"
    assert n.run_once(10_000) == 0
    name, secrets, event, mgr = calls[0]
    assert (name, secrets["url"], event["rule_name"], event["severity"], mgr) == ("hook", "https://h.example.org/x", "CPU", "critical", "moni")
    assert len(calls) == 1


def test_manager_name_is_local_server(conn, settings, box):
    add(conn, box)
    from moni.manager.servers import ServerStore
    ServerStore(conn, box).ensure_local("mein-pi")
    calls = []
    Notifier(settings, box, ok_deliver(calls)).run_once(0)
    assert calls[0][3] == "mein-pi"


def test_retry_schedule_and_final_failure(conn, settings, box, caplog):
    add(conn, box)
    n = Notifier(settings, box, failing)
    for now, expected_next in [(0, 60), (60, 360), (360, 1260), (1260, 4860)]:
        n.run_once(now)
        r = row(conn)
        assert r["next_try"] == expected_next and r["status"] == "pending"
    assert n.run_once(1259) == 0  # noch nicht fällig
    with caplog.at_level(logging.WARNING, logger="moni.notifier"):
        n.run_once(4860)
    r = row(conn)
    assert r["status"] == "failed" and r["attempts"] == 5 and r["last_error"] == "Webhook nicht erreichbar"
    assert "Benachrichtigung über hook endgültig fehlgeschlagen" in caplog.text
    assert n.run_once(10**6) == 0


def test_no_db_transaction_during_delivery(conn, settings, box):
    add(conn, box)
    seen = []

    def d(channel, secrets, event, manager_name):
        other = db.connect(settings.db_path)
        try:
            other.execute("PRAGMA busy_timeout=100")
            other.execute("BEGIN IMMEDIATE")  # wirft bei gehaltener Schreibsperre
            other.rollback()
            seen.append(True)
        finally:
            other.close()

    Notifier(settings, box, d).run_once(0)
    assert seen == [True] and row(conn)["status"] == "sent"


def test_restart_does_not_resend_sent(conn, settings, box):
    add(conn, box)
    Notifier(settings, box, ok_deliver([])).run_once(0)
    calls = []
    assert Notifier(settings, box, ok_deliver(calls)).run_once(10) == 0 and calls == []


def test_error_texts_only_fixed(conn, settings, box, caplog):
    add(conn, box)

    def boom(*a):
        raise RuntimeError("geheim https://h.example.org/x")

    with caplog.at_level(logging.DEBUG):
        Notifier(settings, box, boom).run_once(0)
    assert row(conn)["last_error"] == "Versand fehlgeschlagen"
    assert "geheim" not in caplog.text and "h.example.org" not in caplog.text and "RuntimeError" in caplog.text
    conn.execute("UPDATE alert_outbox SET status='pending', next_try=0, attempts=0")
    conn.commit()
    Notifier(settings, box, failing).run_once(0)
    assert row(conn)["last_error"] == "Webhook nicht erreichbar"


def test_disabled_channel_marks_failed_without_delivery(conn, settings, box):
    add(conn, box, enabled=0)
    calls = []
    Notifier(settings, box, ok_deliver(calls)).run_once(0)
    r = row(conn)
    assert calls == [] and r["status"] == "failed" and r["last_error"] == "Kanal deaktiviert"


def test_undecryptable_secret_counts_as_failed_attempt(conn, settings, box):
    add(conn, box)
    calls = []
    Notifier(settings, SecretBox(generate_key()), ok_deliver(calls)).run_once(0)
    r = row(conn)
    assert calls == [] and r["attempts"] == 1 and r["last_error"] == "Kanal-Geheimnis nicht entschlüsselbar"
    assert r["next_try"] == 60


def test_batch_limit_20_in_id_order(conn, settings, box):
    add(conn, box)
    for _ in range(24):
        conn.execute("INSERT INTO alert_outbox (event_id, channel_id, next_try, created_at) VALUES (1, 1, 0, 0)")
    conn.commit()
    assert Notifier(settings, box, ok_deliver([])).run_once(0) == 20
    assert conn.execute("SELECT count(*) FROM alert_outbox WHERE status='pending'").fetchone()[0] == 5


def test_concurrent_status_change_not_clobbered(conn, settings, box):
    add(conn, box)

    def d(channel, secrets, event, manager_name):
        conn.execute("UPDATE alert_outbox SET status='failed', last_error='x'")
        conn.commit()

    Notifier(settings, box, d).run_once(0)
    assert row(conn)["status"] == "failed" and row(conn)["last_error"] == "x"


def test_thread_start_stop_and_survives_errors(conn, settings, box, monkeypatch):
    add(conn, box)
    n = Notifier(settings, box, ok_deliver([]), interval=0.01)
    calls = []

    def boom(now):
        calls.append(now)
        raise RuntimeError("x")

    monkeypatch.setattr(n, "run_once", boom)
    n.start()
    for _ in range(100):
        if len(calls) >= 2:
            break
        threading.Event().wait(0.02)
    n.stop()
    assert len(calls) >= 2 and not n._thread.is_alive()


def test_stop_between_deliveries_persists_first_and_leaves_rest(conn, settings, box):
    add(conn, box)
    for _ in range(2):
        conn.execute("INSERT INTO alert_outbox (event_id, channel_id, next_try, created_at) VALUES (1, 1, 0, 0)")
    conn.commit()
    calls = []
    n = Notifier(settings, box, lambda *a: (calls.append(1), n._stop.set()))
    assert n.run_once(0) == 1
    rows = [r["status"] for r in conn.execute("SELECT status FROM alert_outbox ORDER BY id")]
    assert rows == ["sent", "pending", "pending"] and len(calls) == 1


def test_persist_failure_stops_batch(conn, settings, box, monkeypatch):
    add(conn, box)
    conn.execute("INSERT INTO alert_outbox (event_id, channel_id, next_try, created_at) VALUES (1, 1, 0, 0)")
    conn.commit()
    calls = []
    n = Notifier(settings, box, ok_deliver(calls))
    monkeypatch.setattr(n, "_persist", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    assert n.run_once(0) == 0 and len(calls) == 1


def test_delivered_but_unsaved_is_saved_next_run_without_resend(conn, settings, box, caplog):  # P5-R19, D6
    add(conn, box)
    calls = []
    n = Notifier(settings, box, ok_deliver(calls))
    real, fails = n._persist, [RuntimeError("x")]

    def flaky(it, now):
        if fails:
            raise fails.pop()
        real(it, now)
    n._persist = flaky
    with caplog.at_level(logging.ERROR):
        assert n.run_once(0) == 0
    assert row(conn)["status"] == "pending" and "RuntimeError" in caplog.text
    assert n.run_once(10) == 1
    assert row(conn)["status"] == "sent" and len(calls) == 1
    assert n.run_once(20) == 0 and len(calls) == 1


def test_broken_channel_does_not_block_others(conn, settings, box, caplog):
    add(conn, box, name="kaputt")
    conn.execute("UPDATE alert_channels SET secret_enc=? WHERE id=1", (box.encrypt("kein json"),))
    add(conn, box, name="gut")
    calls = []
    Notifier(settings, box, ok_deliver(calls)).run_once(0)
    rows = [dict(r) for r in conn.execute("SELECT * FROM alert_outbox ORDER BY id")]
    assert [c[0] for c in calls] == ["gut"]
    assert rows[0]["status"] == "pending" and rows[0]["attempts"] == 1 and rows[0]["last_error"] == "Versand fehlgeschlagen"
    assert rows[1]["status"] == "sent"


def test_next_try_per_item(conn, settings, box, monkeypatch):
    from types import SimpleNamespace
    add(conn, box)
    conn.execute("INSERT INTO alert_outbox (event_id, channel_id, next_try, created_at) VALUES (1, 1, 0, 0)")
    conn.commit()
    clock = [0.0]
    monkeypatch.setattr("moni.manager.notifier.time", SimpleNamespace(monotonic=lambda: clock[0], time=lambda: clock[0]))

    def slow_fail(channel, secrets, event, manager_name):
        clock[0] += 100  # jeder Versuch dauert 100 s
        raise SendError("Webhook nicht erreichbar")

    Notifier(settings, box, slow_fail).run_once(1000)
    assert [r[0] for r in conn.execute("SELECT next_try FROM alert_outbox ORDER BY id")] == [1160, 1260]
