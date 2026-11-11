import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from moni.auth.sessions import COOKIE
from moni.manager import alert_api, alerting, db
from moni.manager.alerting import _event

P = "/api/v1"


@pytest.fixture(autouse=True)
def _reset_throttle():
    alert_api._sends.clear()


@pytest.fixture
def admin_client(mgr_app, make_user, login_cookie):
    c = TestClient(mgr_app, base_url="https://testserver")
    cookie, csrf = login_cookie(make_user("admin"))
    c.cookies.set(COOKIE, cookie)
    c.headers["X-CSRF-Token"] = csrf
    return c


@pytest.fixture
def viewer_client(mgr_app, make_user, login_cookie):
    c = TestClient(mgr_app, base_url="https://testserver")
    c.cookies.set(COOKIE, login_cookie(make_user("viewer"))[0])
    return c


@pytest.fixture
def conn(mgr_app):
    c = db.connect(mgr_app.state.settings.db_path)
    yield c
    c.close()


@pytest.fixture
def server_id(conn):
    cur = conn.execute("INSERT INTO servers (name, url, created_at) VALUES ('pi', 'https://pi', 1)")
    conn.commit()
    return cur.lastrowid


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hook(status: int):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b"INTERN")

        def log_message(self, *a):
            pass
    httpd = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}/hook"


def test_viewer_sees_alerts_and_rules_but_not_channels(viewer_client):
    assert viewer_client.get(f"{P}/alerts").status_code == 200
    assert viewer_client.get(f"{P}/alerts/history").status_code == 200
    assert viewer_client.get(f"{P}/alert-rules").status_code == 200
    assert viewer_client.get(f"{P}/alert-channels").status_code == 403
    assert viewer_client.get(f"{P}/alert-settings").status_code == 403


def test_channel_secret_never_returned_or_audited(admin_client):
    r = admin_client.post(f"{P}/alert-channels", json={"name": "slack", "kind": "webhook",
                          "url": "https://hooks.slack.com/services/T/B/GEHEIM", "hmac_secret": "s3cret"})
    assert r.status_code == 201 and "GEHEIM" not in r.text and "s3cret" not in r.text
    assert r.json()["url_hint"] == "https://hooks.slack.com/…" and r.json()["secret_set"] is True
    cid = r.json()["id"]
    r = admin_client.patch(f"{P}/alert-channels/{cid}", json={"url": "https://hooks.slack.com/services/NEU/GEHEIM2",
                                                               "hmac_secret": "s3cret2"})
    assert r.status_code == 200 and "GEHEIM" not in r.text
    assert "GEHEIM" not in admin_client.get(f"{P}/alert-channels").text
    audit = admin_client.get(f"{P}/audit")
    assert "GEHEIM" not in audit.text and "s3cret" not in audit.text and "hooks.slack" not in audit.text
    acts = {e["action"]: e["details"] for e in audit.json()["entries"]}
    assert acts["channel.created"] == {"kind": "webhook"}
    assert acts["channel.updated"] == ["hmac_secret", "url"]
    assert admin_client.delete(f"{P}/alert-channels/{cid}").status_code == 204
    assert admin_client.delete(f"{P}/alert-channels/{cid}").status_code == 404


def test_channel_validation_and_conflict(admin_client):
    body = {"name": "a", "kind": "webhook", "url": "https://example.org/h"}
    assert admin_client.post(f"{P}/alert-channels", json=body).status_code == 201
    assert admin_client.post(f"{P}/alert-channels", json=body).status_code == 409
    assert admin_client.post(f"{P}/alert-channels", json={**body, "name": "b", "url": "ftp://x"}).status_code == 422
    assert admin_client.post(f"{P}/alert-channels", json={**body, "name": "b", "extra": 1}).status_code == 422
    assert admin_client.patch(f"{P}/alert-channels/999", json={"enabled": False}).status_code == 404


def test_test_send_reports_fixed_error(admin_client):
    httpd, url = _hook(500)
    try:
        cid = admin_client.post(f"{P}/alert-channels", json={"name": "w", "kind": "webhook", "url": url}).json()["id"]
        r = admin_client.post(f"{P}/alert-channels/{cid}/test")
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert r.status_code == 200
    assert r.json() == {"ok": False, "error": "Webhook antwortet mit HTTP 500"} and "INTERN" not in r.text
    ok = admin_client.get(f"{P}/audit").json()["entries"][0]
    assert (ok["action"], ok["details"]) == ("channel.tested", {"ok": False})


def test_test_send_success_and_unexpected_error(admin_client, monkeypatch, caplog):
    httpd, url = _hook(200)
    try:
        cid = admin_client.post(f"{P}/alert-channels", json={"name": "w", "kind": "webhook", "url": url,
                                                               "enabled": False}).json()["id"]
        assert admin_client.post(f"{P}/alert-channels/{cid}/test").json() == {"ok": True, "error": None}
    finally:
        httpd.shutdown()
        httpd.server_close()

    def boom(*a):
        raise RuntimeError("geheimer Text")
    monkeypatch.setattr(alert_api.ch, "deliver", boom)
    with caplog.at_level(logging.ERROR):
        r = admin_client.post(f"{P}/alert-channels/{cid}/test")
    assert r.json() == {"ok": False, "error": "Versand fehlgeschlagen"}
    assert "RuntimeError" in caplog.text and "geheimer" not in caplog.text
    assert admin_client.post(f"{P}/alert-channels/999/test").status_code == 404


def test_test_send_rate_limited(admin_client, monkeypatch):
    monkeypatch.setattr(alert_api.ch, "deliver", lambda *a: None)
    cid = admin_client.post(f"{P}/alert-channels", json={"name": "w", "kind": "webhook", "url": "https://x.example/h"}).json()["id"]
    other = admin_client.post(f"{P}/alert-channels", json={"name": "v", "kind": "webhook", "url": "https://x.example/h"}).json()["id"]
    assert [admin_client.post(f"{P}/alert-channels/{cid}/test").status_code for _ in range(7)] == [200] * 6 + [429]
    assert admin_client.post(f"{P}/alert-channels/{other}/test").status_code == 200


def test_mute_validation_and_audit(admin_client, server_id):
    url = f"{P}/servers/{server_id}/mute"
    for bad in (_iso(-timedelta(minutes=1)), _iso(timedelta(days=31)), "2999-01-01T00:00:00", "morgen"):
        assert admin_client.put(url, json={"until": bad}).status_code == 422, bad
    assert admin_client.put(f"{P}/servers/999999/mute", json={"until": _iso(timedelta(hours=1))}).status_code == 404
    until = _iso(timedelta(hours=2))
    r = admin_client.put(url, json={"until": until})
    assert r.status_code == 200 and r.json()["muted_until"] == until and r.json()["id"] == server_id
    assert admin_client.get(f"{P}/servers/{server_id}").json()["muted_until"] == until
    assert admin_client.delete(url).status_code == 204
    assert admin_client.get(f"{P}/servers/{server_id}").json()["muted_until"] is None
    assert admin_client.delete(f"{P}/servers/999999/mute").status_code == 404
    acts = {e["action"]: e["details"] for e in admin_client.get(f"{P}/audit").json()["entries"]}
    assert acts["server.muted"] == {"until": until} and "server.unmuted" in acts


RULE = {"name": "Eigene Regel", "metric": "moni_host_cpu_usage_percent", "op": ">", "warning": 70, "critical": 90}


def test_rule_crud_audited_and_validation(admin_client, conn, server_id):
    r = admin_client.post(f"{P}/alert-rules", json=RULE)
    assert r.status_code == 201 and r.json()["name"] == "Eigene Regel"
    rid = r.json()["id"]
    assert admin_client.post(f"{P}/alert-rules", json=RULE).status_code == 409
    assert admin_client.post(f"{P}/alert-rules", json={**RULE, "name": "x", "metric": "nope"}).status_code == 422
    # Schwellenreihenfolge wird gegen die zusammengeführten Werte geprüft
    assert admin_client.patch(f"{P}/alert-rules/{rid}", json={"warning": 95}).status_code == 422
    assert admin_client.patch(f"{P}/alert-rules/{rid}", json={}).status_code == 422
    assert admin_client.patch(f"{P}/alert-rules/999", json={"enabled": False}).status_code == 404
    r = admin_client.patch(f"{P}/alert-rules/{rid}", json={"warning": 80, "critical": 90, "enabled": False})
    assert r.status_code == 200 and r.json()["warning"] == 80 and r.json()["enabled"] is False
    assert r.json()["updated_at"].endswith("Z")
    assert any(x["id"] == rid for x in admin_client.get(f"{P}/alert-rules").json())
    # offene Instanz: Löschen schreibt resolved in den Verlauf
    conn.execute("INSERT INTO alert_instances (rule_id, server_id, key, state, severity, value, fired_at, last_notified) "
                 "VALUES (?,?,?,?,?,?,?,?)", (rid, server_id, "{}", "firing", "critical", 95, 100, 100))
    conn.commit()
    assert admin_client.delete(f"{P}/alert-rules/{rid}").status_code == 204
    assert admin_client.delete(f"{P}/alert-rules/{rid}").status_code == 404
    ev = conn.execute("SELECT event, reason, rule_name FROM alert_events").fetchall()
    assert [tuple(e) for e in ev] == [("resolved", "regel gelöscht", "Eigene Regel")]
    acts = {e["action"]: e["details"] for e in admin_client.get(f"{P}/audit").json()["entries"]}
    assert acts["rule.updated"] == ["enabled", "warning"] and "rule.created" in acts and "rule.deleted" in acts


def test_alerts_list(admin_client, conn, server_id):
    rid = conn.execute("SELECT id FROM alert_rules LIMIT 1").fetchone()[0]
    now = int(time.time())
    conn.execute("UPDATE servers SET muted_until=? WHERE id=?", (now + 3600, server_id))
    conn.execute("INSERT INTO alert_instances (rule_id, server_id, key, state, severity, value, fired_at, pending_since) "
                 "VALUES (?,?,?,?,?,?,?,?)", (rid, server_id, '{"mountpoint":"/"}', "firing", "critical", 91, 1000, 900))
    conn.execute("INSERT INTO alert_instances (rule_id, server_id, key, state, severity, value, pending_since) "
                 "VALUES (?,?,?,?,?,?,?)", (rid, server_id, '{"mountpoint":"/b"}', "pending", "warning", 80, 2000))
    conn.execute("INSERT INTO alert_instances (rule_id, server_id, key, state) VALUES (?,?,?,?)", (rid, server_id, "{}", "ok"))
    conn.commit()
    got = {tuple(a["labels"].values()): a for a in admin_client.get(f"{P}/alerts").json()}
    assert set(got) == {("/",), ("/b",)}
    assert got[("/",)]["since"] == "1970-01-01T00:16:40Z" and got[("/b",)]["since"] == "1970-01-01T00:33:20Z"
    assert got[("/",)]["muted"] is True and got[("/",)]["server"]["name"] == "pi" and got[("/",)]["state"] == "firing"


def test_history_paging(admin_client, conn, server_id):
    other = conn.execute("INSERT INTO servers (name, url, created_at) VALUES ('b', 'https://b', 1)").lastrowid
    for i in range(5):
        _event(conn, 1000 + i, None, "R", server_id if i != 2 else other, "pi", '{"a":"b"}', "firing", "warning", 1.0, 2.0,
               None, i == 4)
    conn.commit()
    r = admin_client.get(f"{P}/alerts/history?limit=2").json()
    ids = [e["id"] for e in r["entries"]]
    assert ids == sorted(ids, reverse=True) and len(ids) == 2 and r["next_before"] == ids[-1]
    assert r["entries"][0]["muted"] is True and r["entries"][0]["labels"] == {"a": "b"}
    assert r["entries"][0]["ts"] == "1970-01-01T00:16:44Z"
    r2 = admin_client.get(f"{P}/alerts/history?limit=2&before={r['next_before']}").json()
    assert [e["id"] for e in r2["entries"]] == [ids[-1] - 1, ids[-1] - 2]
    last = admin_client.get(f"{P}/alerts/history?limit=2&before={r2['next_before']}").json()
    assert len(last["entries"]) == 1 and last["next_before"] is None
    assert len(admin_client.get(f"{P}/alerts/history?server_id={other}").json()["entries"]) == 1
    for bad in ("limit=0", "limit=1001"):
        assert admin_client.get(f"{P}/alerts/history?{bad}").status_code == 422


def test_settings_validation(admin_client):
    assert admin_client.get(f"{P}/alert-settings").json() == {"remind_minutes": 240}
    assert admin_client.put(f"{P}/alert-settings", json={"remind_minutes": 60}).json() == {"remind_minutes": 60}
    assert admin_client.get(f"{P}/alert-settings").json() == {"remind_minutes": 60}
    for bad in (-1, 10081, True, "5"):
        assert admin_client.put(f"{P}/alert-settings", json={"remind_minutes": bad}).status_code == 422
    assert admin_client.put(f"{P}/alert-settings", json={"remind_minutes": 1, "x": 1}).status_code == 422
    e = admin_client.get(f"{P}/audit").json()["entries"][0]
    assert (e["action"], e["details"]) == ("alerting.settings_changed", {"remind_minutes": 60})


def _firing_rule(admin_client, conn, server_id):
    """Eigene Regel (ohne Dauer) feuert auf pi und war benachrichtigt; Snapshot hält den Wert über der Schwelle."""
    rid = admin_client.post(f"{P}/alert-rules", json=RULE).json()["id"]
    now = int(time.time())
    snap = {"schema_version": 1, "timestamp": _iso(timedelta(0)), "host": {"cpu": {"usage_percent": 95}},
            "containers": [], "docker": {"reachable": True}}
    conn.execute("UPDATE servers SET status='ok', last_snapshot=? WHERE id=?", (json.dumps(snap), server_id))
    conn.execute("INSERT INTO alert_channels (name, kind, config, min_severity, enabled, created_at) "
                 "VALUES ('h', 'webhook', '{}', 'warning', 1, 0)")
    conn.execute("INSERT INTO alert_instances (rule_id, server_id, key, state, severity, value, fired_at, last_notified) "
                 "VALUES (?,?,?,?,?,?,?,?)", (rid, server_id, "{}", "firing", "critical", 95, now - 60, now - 60))
    conn.commit()
    return rid, now


def _rule_events(conn, rid):
    return [tuple(e) for e in conn.execute("SELECT event, reason FROM alert_events WHERE rule_id=? ORDER BY id", (rid,))]


def _rule_outbox(conn, rid):
    return conn.execute("SELECT COUNT(*) FROM alert_outbox o JOIN alert_events e ON e.id=o.event_id "
                        "WHERE e.rule_id=?", (rid,)).fetchone()[0]


def _instances(conn, rid):
    return [tuple(i) for i in conn.execute("SELECT state, severity FROM alert_instances WHERE rule_id=?", (rid,))]


def test_rule_patch_resolves_once_in_patch_and_poll_race_does_not_refire(admin_client, conn, server_id):  # P5-R17
    rid, now = _firing_rule(admin_client, conn, server_id)
    admin_client.post(f"{P}/alert-rules", json={**RULE, "name": "Andere"})
    assert admin_client.patch(f"{P}/alert-rules/{rid}", json={"warning": 95}).status_code == 422
    # Namenskonflikt nach dem Auflösen: alles in einer Transaktion, also auch das Auflösen zurückgerollt
    assert admin_client.patch(f"{P}/alert-rules/{rid}", json={"name": "Andere", "critical": 92}).status_code == 409
    assert _rule_events(conn, rid) == [] and _instances(conn, rid) == [("firing", "critical")]
    assert admin_client.patch(f"{P}/alert-rules/{rid}", json={"critical": 92}).status_code == 200
    assert _rule_events(conn, rid) == [("resolved", "regel geändert")]
    assert _rule_outbox(conn, rid) == 0 and _instances(conn, rid) == []
    for t in (now - 30, now - 15, now):  # now des Poll-Zyklus liegt vor updated_at
        alerting.run(conn, t)
        conn.commit()
    assert [e[0] for e in _rule_events(conn, rid)] == ["resolved", "firing"]
    assert _rule_outbox(conn, rid) == 1
    acts = [e for e in admin_client.get(f"{P}/audit").json()["entries"] if e["action"] == "rule.updated"]
    assert [e["details"] for e in acts] == [["critical"]]


def test_noop_and_name_only_patch_keep_firing_instance(admin_client, conn, server_id):  # P5-R17
    rid, now = _firing_rule(admin_client, conn, server_id)
    conn.execute("UPDATE alert_rules SET updated_at=1 WHERE id=?", (rid,))
    conn.commit()
    r = admin_client.patch(f"{P}/alert-rules/{rid}", json={"warning": 70, "critical": 90, "name": " Eigene Regel "})
    assert r.status_code == 200 and r.json()["updated_at"] == "1970-01-01T00:00:01Z"
    r = admin_client.patch(f"{P}/alert-rules/{rid}", json={"name": "Umbenannt", "enabled": True})
    assert r.status_code == 200 and r.json()["name"] == "Umbenannt"
    alerting.run(conn, now)
    conn.commit()
    assert _rule_events(conn, rid) == [] and _instances(conn, rid) == [("firing", "critical")]
    acts = [e for e in admin_client.get(f"{P}/audit").json()["entries"] if e["action"] == "rule.updated"]
    assert [e["details"] for e in acts] == [["name"]]


def test_rule_patch_server_id_drops_instances(admin_client, conn, server_id):  # P5-R17
    rid, now = _firing_rule(admin_client, conn, server_id)
    assert admin_client.patch(f"{P}/alert-rules/{rid}", json={"server_id": server_id}).status_code == 200
    assert _rule_events(conn, rid) == [("resolved", "regel geändert")]
    assert _rule_outbox(conn, rid) == 0 and _instances(conn, rid) == []


@pytest.mark.parametrize("damage", ["key", "json"])
def test_test_send_with_broken_secret_returns_fixed_text(admin_client, conn, mgr_app, caplog, damage):
    cid = admin_client.post(f"{P}/alert-channels", json={"name": "w", "kind": "webhook",
                                                           "url": "https://example.org/h"}).json()["id"]
    if damage == "key":
        enc = "kein-gueltiger-umschlag"
    else:  # entschlüsselbar, aber kein JSON
        enc = mgr_app.state.box.encrypt("kein json")
    conn.execute("UPDATE alert_channels SET secret_enc=? WHERE id=?", (enc, cid))
    conn.commit()
    with caplog.at_level(logging.ERROR):
        r = admin_client.post(f"{P}/alert-channels/{cid}/test")
    assert (r.status_code, r.json()) == (200, {"ok": False, "error": "Kanal-Geheimnis nicht entschlüsselbar"})
    assert "SecretError" in caplog.text or "JSONDecodeError" in caplog.text
    last = admin_client.get(f"{P}/audit").json()["entries"][0]
    assert (last["action"], last["details"]) == ("channel.tested", {"ok": False})
