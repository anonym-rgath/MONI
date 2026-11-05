import copy
import json
import socket
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import raw
from moni.config import Settings
from moni.manager import db, poller as pmod, tsdb
from moni.manager.poller import AgentClient, AgentError, Poller
from moni.manager.servers import ServerStore
from moni.secretbox import SecretBox, generate_key
from moni.timeutil import iso

T = 1793793600  # 2026-11-04T12:00:00Z
M = "moni_host_cpu_usage_percent"


def snap(ts, cpu=10.0, schema=1):
    return {"schema_version": schema, "timestamp": iso(datetime.fromtimestamp(ts, timezone.utc)),
            "host": {"cpu": {"usage_percent": cpu}}, "containers": [], "docker": {"reachable": True}}


class FakeAgents:
    """Pro URL und Pfad ein Skript aus Antworten, AgentErrors oder Aufrufbaren."""

    def __init__(self):
        self.scripts: dict[str, dict[str, list]] = {}
        self.calls: list[tuple] = []
        self.made: list[tuple] = []

    def factory(self, url, token, headers, timeout):
        self.made.append((url, token, headers, timeout))
        return FakeClient(self, url)


class FakeClient:
    def __init__(self, agents, url):
        self.agents, self.url = agents, url

    def get(self, path, params=None):
        self.agents.calls.append((self.url, path, params))
        script = self.agents.scripts[self.url].get(path)
        if script is None and path == "/api/v1/info":
            return {"version": "2.0.0", "role": "agent"}
        item = script.pop(0)
        if callable(item):
            item = item()
        if isinstance(item, Exception):
            raise item
        return item


class StubCollector:
    def __init__(self, s=None):
        self.snap = s

    def latest(self):
        return self.snap

    def history(self, since):
        return [self.snap] if self.snap and pmod._ts(self.snap) > since.timestamp() else [], None


@pytest.fixture
def settings(tmp_path):
    return Settings.from_env({"MONI_ROLE": "manager", "MONI_SECRET_KEY": generate_key(), "MONI_DATA_DIR": str(tmp_path)})


@pytest.fixture
def box(settings):
    return SecretBox(settings.secret_key)


@pytest.fixture
def conn(settings):
    db.migrate(settings.db_path)
    c = db.connect(settings.db_path)
    yield c
    c.close()


@pytest.fixture
def servers(conn, box):
    return ServerStore(conn, box)


@pytest.fixture
def agents():
    return FakeAgents()


@pytest.fixture
def local_collector():
    return StubCollector()


@pytest.fixture
def poller(settings, box, servers, agents, local_collector):
    p = Poller(settings, box, local_collector, client_factory=agents.factory)
    yield p
    p.stop()
    p.close()


def count_raw_points(conn, sid, metric):
    return conn.execute("SELECT COUNT(*) FROM samples_raw r JOIN series s ON s.id = r.series_id "
                        "WHERE s.server_id=? AND s.name=?", (sid, metric)).fetchone()[0]


def add(servers, agents, name, script, **kw):
    s = servers.add(name, f"https://{name}.example.org", f"tok-{name}", kw.get("headers"))
    agents.scripts[s.url] = script
    return s.id


def test_slow_agent_does_not_block_others(poller, servers, agents):  # Review Focus 1
    both = threading.Barrier(2, timeout=3)  # nur bei paralleler Abfrage erreichen beide die Schranke

    def slow_fail():
        both.wait()
        return AgentError("Agent nicht erreichbar")

    def ok():
        both.wait()
        return snap(T)

    a = add(servers, agents, "a", {"/api/v1/snapshot": [slow_fail]})
    b = add(servers, agents, "b", {"/api/v1/snapshot": [ok]}, headers={"X-A": "v"})
    poller.poll_once(now=T)
    assert servers.get(b).status == "ok" and servers.get(a).failures == 1
    assert servers.get(a).last_error == "Agent nicht erreichbar" and servers.get(a).status == "unknown"
    assert ("https://b.example.org", "tok-b", {"X-A": "v"}, 10.0) in agents.made


def test_three_failures_unreachable_then_recovers(poller, servers, agents, conn):
    a = add(servers, agents, "a", {"/api/v1/snapshot": [AgentError("Agent nicht erreichbar")] * 3 + [snap(T + 45)]})
    for i in range(3):
        poller.poll_once(now=T + 15 * i)
    assert servers.get(a).status == "unreachable" and servers.get(a).failures == 3
    poller.poll_once(now=T + 45)
    g = servers.get(a)
    assert g.status == "ok" and g.failures == 0 and g.last_error is None and g.last_ts == T + 45 and g.last_seen == T + 45
    assert count_raw_points(conn, a, M) == 1


def test_401_is_fatal_error(poller, servers, agents):
    a = add(servers, agents, "a", {"/api/v1/snapshot": [AgentError("Agent lehnt Token ab", fatal=True)]})
    poller.poll_once(now=T)
    assert servers.get(a).status == "error" and servers.get(a).last_error == "Agent lehnt Token ab"


def test_schema_mismatch_is_error(poller, servers, agents, conn):
    a = add(servers, agents, "a", {"/api/v1/snapshot": [snap(T, schema=2)]})
    poller.poll_once(now=T)
    g = servers.get(a)
    assert g.status == "error" and g.last_error == "Snapshot-Schema 2 wird nicht unterstützt"
    assert conn.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 0


def test_gap_fill_uses_history_and_skips_known(poller, servers, agents, conn):  # Review Focus 2
    hist = [snap(T)] + [snap(T + 15 * i) for i in range(1, 20)]  # T (bekannt), T+15 .. T+285
    a = add(servers, agents, "a", {"/api/v1/history": [{"points": hist, "oldest": hist[0]["timestamp"]}],
                                   "/api/v1/snapshot": [snap(T + 300)]})
    tsdb.write_snapshots(conn, a, [snap(T)])
    conn.commit()
    servers.record_success(a, T, T, json.dumps(snap(T)), None)
    poller.poll_once(now=T + 300)
    paths = [(p, q) for _, p, q in agents.calls]
    assert paths[:2] == [("/api/v1/history", {"since": "2026-11-04T12:00:00Z"}), ("/api/v1/snapshot", None)]
    assert count_raw_points(conn, a, M) == 21
    g = servers.get(a)
    assert g.status == "ok" and g.last_ts == T + 300


def test_short_gap_fetches_only_snapshot(poller, servers, agents):
    a = add(servers, agents, "a", {"/api/v1/snapshot": [snap(T + 15), snap(T + 15)], "/api/v1/history": [{"points": []}]})
    servers.record_success(a, T, T, "{}", None)
    poller.poll_once(now=T + 15)  # 15 <= 1 * 15 (P2-R8)
    assert [p for _, p, _ in agents.calls if p != "/api/v1/info"] == ["/api/v1/snapshot"]
    poller.poll_once(now=T + 30)  # gleicher Snapshot, aber erst 15 s seit last_seen: kein Fehler
    g = servers.get(a)
    assert g.status == "ok" and g.failures == 0 and g.last_ts == T + 15 and g.last_seen == T + 15


def test_stuck_agent_becomes_unreachable_then_recovers(poller, servers, agents):  # P2-R5
    a = add(servers, agents, "a", {"/api/v1/snapshot": [snap(T)] * 4 + [snap(T + 75)],
                                   "/api/v1/history": [{"points": []}] * 4})
    poller.poll_once(now=T)
    for now in (T + 31, T + 46, T + 61):  # > 2 * 15 s seit last_seen ohne neue Daten
        poller.poll_once(now=now)
    g = servers.get(a)
    assert g.status == "unreachable" and g.failures == 3 and g.last_error == "Agent liefert keine neuen Daten"
    poller.poll_once(now=T + 76)
    assert servers.get(a).status == "ok" and servers.get(a).failures == 0


def test_never_answering_new_server_uses_created_at(poller, servers, agents, conn):  # P2-R5
    a = add(servers, agents, "a", {"/api/v1/snapshot": [snap(T)]})
    conn.execute("UPDATE servers SET last_ts=?, created_at=? WHERE id=?", (T, T - 100, a))
    conn.commit()
    poller.poll_once(now=T + 10)  # last_seen fehlt -> created_at zählt
    assert servers.get(a).last_error == "Agent liefert keine neuen Daten"


def test_wrong_secret_key_marks_only_that_server(poller, servers, agents, conn):  # Review Focus 3
    a = ServerStore(conn, SecretBox(generate_key())).add("a", "https://a.example.org", "tok-a").id
    b = add(servers, agents, "b", {"/api/v1/snapshot": [snap(T)]})
    poller.poll_once(now=T)
    assert servers.get(a).status == "error" and servers.get(a).last_error == "Zugangsdaten nicht entschlüsselbar"
    assert servers.get(b).status == "ok"
    assert [m[0] for m in agents.made] == ["https://b.example.org"]


def test_db_error_is_logged_and_thread_survives(poller, servers, agents, monkeypatch, caplog):  # Review Focus 4
    now = int(time.time())
    a = add(servers, agents, "a", {"/api/v1/snapshot": [snap(now), snap(now)]})
    monkeypatch.setattr(tsdb, "write_snapshots",
                        lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    poller._run_one_cycle()
    poller._run_one_cycle()
    assert caplog.text.count("Abrufzyklus fehlgeschlagen") == 2
    assert servers.get(a).status == "unknown" and servers.get(a).failures == 0  # ganzer Zyklus zurückgerollt
    assert not poller._conn.in_transaction


def test_bad_snapshot_rolls_back_only_that_server(poller, servers, agents, conn, monkeypatch):  # P2-R4
    a = add(servers, agents, "a", {"/api/v1/snapshot": [snap(T, cpu=1.0)]})
    b = add(servers, agents, "b", {"/api/v1/snapshot": [snap(T, cpu=2.0)]})
    real = tsdb.write_snapshots

    def write(c, sid, snaps):
        real(c, sid, snaps)  # schreibt erst, damit der Savepoint wirklich etwas zurückrollen muss
        if sid == a:
            raise TypeError("kaputt")
        return T

    monkeypatch.setattr(tsdb, "write_snapshots", write)
    poller.poll_once(now=T)
    assert servers.get(a).status == "error" and servers.get(a).last_error == "Ungültige Antwort vom Agent"
    assert count_raw_points(conn, a, M) == 0
    assert servers.get(b).status == "ok" and count_raw_points(conn, b, M) == 1


def test_implausible_timestamps_dropped(poller, servers, agents, conn):  # P2-R4
    a = add(servers, agents, "a", {"/api/v1/snapshot": [snap(T + 301)]})
    b = add(servers, agents, "b", {"/api/v1/snapshot": [snap(T - 3 * 3600 - 1)]})
    c = add(servers, agents, "c", {"/api/v1/snapshot": [snap(T + 300)]})
    poller.poll_once(now=T)
    for sid in (a, b):
        g = servers.get(sid)
        assert g.status == "unknown" and g.failures == 1 and g.last_error == "Zeitstempel des Agents unplausibel"
        assert count_raw_points(conn, sid, M) == 0
    assert servers.get(c).status == "ok"


def test_local_server_stored(poller, servers, conn, local_collector):
    loc = servers.ensure_local("pi")
    local_collector.snap = snap(T, cpu=42.0)
    before = copy.deepcopy(local_collector.snap)
    poller.poll_once(now=T + 5)
    g = servers.get(loc.id)
    assert g.status == "ok" and g.last_ts == T and g.last_seen == T + 5
    stored = conn.execute("SELECT last_snapshot FROM servers WHERE id=?", (loc.id,)).fetchone()[0]
    assert json.loads(stored) == before and local_collector.snap == before
    poller.poll_once(now=T + 10)  # nicht neuer, erst 5 s -> nichts
    assert servers.get(loc.id).last_seen == T + 5 and servers.get(loc.id).failures == 0
    poller.poll_once(now=T + 36)  # hängender lokaler Sammler (P2-R5)
    g = servers.get(loc.id)
    assert g.failures == 1 and g.last_error == "Lokale Messung liefert keine neuen Daten"
    assert count_raw_points(conn, loc.id, M) == 1


def test_disabled_server_not_polled(poller, servers, agents):
    a = add(servers, agents, "a", {"/api/v1/snapshot": [snap(T)]})
    servers.set_enabled(a, False)
    poller.poll_once(now=T)
    assert agents.made == [] and servers.get(a).status == "unknown"


def test_info_first_success_then_every_300s(poller, servers, agents, conn):
    a = add(servers, agents, "a", {"/api/v1/snapshot": [snap(T), snap(T + 15), snap(T + 300)],
                                   "/api/v1/history": [{"points": [], "oldest": None}]})
    for now in (T, T + 15, T + 300):
        poller.poll_once(now=now)
    # T: snapshot, info | T+15: snapshot | T+300: history, snapshot, info
    assert [n for n, (_, p, _) in enumerate(agents.calls) if p == "/api/v1/info"] == [1, 5]
    info = conn.execute("SELECT agent_info FROM servers WHERE id=?", (a,)).fetchone()[0]
    assert json.loads(info) == {"version": "2.0.0", "role": "agent"}


def test_rollup_and_prune_every_300s(poller, servers, monkeypatch):
    calls = []
    monkeypatch.setattr(tsdb, "rollup", lambda c, now: calls.append(("rollup", now)))
    monkeypatch.setattr(tsdb, "prune", lambda c, now, s: calls.append(("prune", now)))
    for now in (T, T + 15, T + 299, T + 300):
        poller.poll_once(now=now)
    assert calls == [("rollup", T), ("prune", T), ("rollup", T + 300), ("prune", T + 300)]


def test_maintenance_purges_sessions_and_old_audit(poller, conn, settings):  # D4/D10
    from moni.auth import audit
    from moni.auth.sessions import SessionStore
    conn.execute("INSERT INTO users (id, email, role, kind, created_at) VALUES (1, 'a@b.de', 'admin', 'local', ?)", (T,))
    conn.commit()
    st = SessionStore(conn, settings)
    st.create(1, "full", T - 13 * 3600, None)           # älter als 12 h: abgelaufen
    fresh, plain = st.create(1, "full", T - 10, None)
    audit.record(conn, "x", "alt", now=T - 366 * 86400)
    audit.record(conn, "x", "frisch", now=T - 86400)
    poller.poll_once(now=T)
    assert [r[0] for r in conn.execute("SELECT id_hash FROM sessions")] == [fresh.id_hash]
    assert [r[0] for r in conn.execute("SELECT action FROM audit_log")] == ["frisch"]


def test_start_stop_thread(settings, box, servers):
    p = Poller(settings, box, StubCollector())
    p.start()
    first = p._thread
    p.start()  # idempotent
    assert p._thread is first and first.is_alive()
    p.stop()
    assert not first.is_alive()


# --- AgentClient gegen echte HTTP-Server ------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        routes = {"/ok": (200, b'{"a": 1}'), "/redirect": (302, b""), "/broken": (200, b"{nope"),
                  "/list": (200, b"[1]"), "/big": (200, b'{"x": "' + b"a" * 2000 + b'"}'), "/fail": (500, b"secret body"),
                  "/forbidden": (403, b""), "/slow": None}
        if self.path.startswith("/trickle/"):  # Kopf sofort, Rumpf 1 Byte je 0,2 s
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.2)
            except OSError:
                pass
            return
        if self.path.startswith("/good/"):
            r = (200, json.dumps(snap(int(time.time())) if self.path.endswith("/snapshot") else {}).encode())
        else:
            r = routes.get(self.path.split("?")[0], (404, b""))
        if r is None:
            time.sleep(1)
            r = (200, b"{}")
        self.send_response(r[0])
        if r[0] == 302:
            self.send_header("Location", "http://evil.example.org/x")
        self.send_header("Content-Length", str(len(r[1])))
        self.end_headers()
        self.wfile.write(r[1])


@pytest.fixture
def http_url():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()
    srv.server_close()
    t.join(5)


@pytest.mark.parametrize("path,message,fatal", [
    ("/redirect", "Agent antwortet mit HTTP 302", False),
    ("/broken", "Ungültige Antwort vom Agent", False),
    ("/list", "Ungültige Antwort vom Agent", False),
    ("/big", "Ungültige Antwort vom Agent", False),
    ("/fail", "Agent antwortet mit HTTP 500", False),
    ("/forbidden", "Agent lehnt Token ab", True),
    ("/slow", "Agent nicht erreichbar", False),
])
def test_agent_client_errors(http_url, monkeypatch, path, message, fatal):
    monkeypatch.setattr(pmod, "_MAX_BYTES", 1000)
    with pytest.raises(AgentError) as e:
        AgentClient(http_url, "geheim-token", {"X-A": "geheim-header"}, 0.3).get(path)
    assert e.value.message == message and e.value.fatal is fatal
    assert e.value.__context__ is None and "geheim" not in repr(e.value) + str(e.value)


def test_agent_client_ok_and_unreachable(http_url):
    assert AgentClient(http_url, "t", {}, 2).get("/ok", {"since": "x"}) == {"a": 1}
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # gebunden, aber kein listen -> abgelehnt oder Timeout
        with pytest.raises(AgentError) as e:
            AgentClient(f"http://127.0.0.1:{port}", "t", {}, 0.3).get("/ok")
    assert e.value.message == "Agent nicht erreichbar"


def test_agent_client_rejects_non_http_scheme():
    with pytest.raises(AgentError):
        AgentClient("file:///etc", "t", {}, 1).get("/passwd")


def test_real_agent_roundtrip(tmp_path, monkeypatch):  # Integration über HTTP, Header kommt an
    import uvicorn
    from moni import collector as cmod
    from moni.app import create_app
    from moni.auth.tokens import TokenStore
    from moni.collector import Collector

    s = Settings.from_env({"MONI_DATA_DIR": str(tmp_path), "MONI_TLS": "off"})
    col = Collector(s, client=object())
    monkeypatch.setattr(cmod, "read_raw", lambda st, cl: raw(datetime.now(timezone.utc)))
    col.collect_once()
    _, plain = TokenStore(tmp_path / "tokens.json").create("manager")
    app = create_app(s, col)
    seen = {}

    @app.middleware("http")
    async def grab(request, call_next):
        seen.update(request.headers)
        return await call_next(request)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=sock.getsockname()[1], log_level="warning"))
    t = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    t.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        url = f"http://127.0.0.1:{sock.getsockname()[1]}/"
        got = AgentClient(url, plain, {"X-Moni-Test": "hallo"}, 5).get("/api/v1/snapshot")
        assert got == col.latest() and got["schema_version"] == 1
        assert seen["x-moni-test"] == "hallo" and seen["authorization"] == f"Bearer {plain}"
        with pytest.raises(AgentError) as e:
            AgentClient(url, "moni_00000000_falsch", {}, 5).get("/api/v1/snapshot")
        assert e.value.fatal and e.value.message == "Agent lehnt Token ab"
    finally:
        server.should_exit = True
        t.join(5)
        sock.close()
    assert not t.is_alive()


def test_fetch_new_drops_known_and_duplicates(agents):
    agents.scripts["u"] = {"/api/v1/history": [{"points": [snap(T - 15), snap(T), snap(T + 15), snap(T + 300)]}],
                           "/api/v1/snapshot": [snap(T + 300)]}
    got = pmod.fetch_new(agents.factory("u", "t", {}, 1), T, T + 300, 15)
    assert [s["timestamp"] for s in got] == [snap(T + 15)["timestamp"], snap(T + 300)["timestamp"]]


def test_server_deleted_during_fetch_does_not_break_cycle(poller, servers, agents, settings, conn):
    def deleted():
        c = db.connect(settings.db_path)  # Worker-Thread: eigene Verbindung, wie die spätere API
        try:
            with c:
                c.execute("DELETE FROM servers WHERE name IN ('a', 'c')")
        finally:
            c.close()
        return snap(T)

    a = add(servers, agents, "a", {"/api/v1/snapshot": [deleted]})
    b = add(servers, agents, "b", {"/api/v1/snapshot": [snap(T)]})
    add(servers, agents, "c", {"/api/v1/snapshot": [AgentError("Agent nicht erreichbar")]})
    poller.poll_once(now=T)
    assert servers.get(a) is None and servers.get(b).status == "ok" and count_raw_points(conn, b, M) == 1


def test_trickling_agent_hits_total_deadline_and_others_are_written(http_url, tmp_path, conn, servers):  # Fix 1
    t0 = time.monotonic()
    with pytest.raises(AgentError) as e:
        AgentClient(http_url + "trickle", "t", {}, 1).get("/x")
    assert e.value.message == "Agent nicht erreichbar" and time.monotonic() - t0 < 1.6
    a = servers.add("a", http_url + "trickle", "t").id
    b = servers.add("b", http_url + "good", "t").id
    s = Settings.from_env({"MONI_ROLE": "manager", "MONI_SECRET_KEY": "x", "MONI_DATA_DIR": str(tmp_path),
                           "MONI_POLL_TIMEOUT": "1"})
    p = Poller(s, servers._box, StubCollector())
    try:
        p.poll_once(int(time.time()))
    finally:
        p.close()
    assert servers.get(a).last_error == "Agent nicht erreichbar" and servers.get(b).status == "ok"


def test_hanging_fetch_does_not_block_cycle(settings, box, servers, agents, monkeypatch):  # Fix 1
    release = threading.Event()
    monkeypatch.setattr(pmod, "_GRACE", 0)
    s = Settings.from_env({"MONI_ROLE": "manager", "MONI_SECRET_KEY": settings.secret_key,
                           "MONI_DATA_DIR": settings.data_dir, "MONI_POLL_TIMEOUT": "0.1"})
    a = add(servers, agents, "a", {"/api/v1/snapshot": [lambda: release.wait(5) and snap(T)]})
    b = add(servers, agents, "b", {"/api/v1/snapshot": [snap(T)]})
    p = Poller(s, box, StubCollector(), client_factory=agents.factory)
    try:
        t0 = time.monotonic()
        p.poll_once(now=T)
        assert time.monotonic() - t0 < 2
    finally:
        release.set()
        p.close()
    assert servers.get(a).last_error == "Agent nicht erreichbar" and servers.get(b).status == "ok"


def test_failed_history_still_stores_snapshot(poller, servers, agents, conn, caplog):  # Fix 3
    a = add(servers, agents, "a", {"/api/v1/history": [AgentError("Ungültige Antwort vom Agent")],
                                   "/api/v1/snapshot": [snap(T + 300)]})
    servers.record_success(a, T, T, "{}", None)
    with caplog.at_level("INFO", logger="moni.poller"):
        poller.poll_once(now=T + 300)
    g = servers.get(a)
    assert g.status == "ok" and g.last_ts == T + 300 and count_raw_points(conn, a, M) == 1
    assert "Lückenfüllung übersprungen" in caplog.text and "tok-a" not in caplog.text


def test_fatal_history_error_still_fatal(poller, servers, agents):
    a = add(servers, agents, "a", {"/api/v1/history": [AgentError("Agent lehnt Token ab", fatal=True)]})
    servers.record_success(a, T, T, "{}", None)
    poller.poll_once(now=T + 300)
    assert servers.get(a).status == "error"


def test_corrupt_credentials_row_marks_only_that_server(poller, servers, agents, conn, box):
    a = add(servers, agents, "a", {})
    c = add(servers, agents, "c", {})
    b = add(servers, agents, "b", {"/api/v1/snapshot": [snap(T)]})
    conn.execute("UPDATE servers SET headers_enc=? WHERE id=?", (box.encrypt("{kaputt"), a))
    conn.execute("UPDATE servers SET headers_enc=? WHERE id=?", (box.encrypt("[1]"), c))
    conn.commit()
    poller.poll_once(now=T)
    for sid in (a, c):
        assert servers.get(sid).status == "error" and servers.get(sid).last_error == "Zugangsdaten nicht entschlüsselbar"
    assert servers.get(b).status == "ok"


def test_proxy_env_is_ignored(http_url):  # P2-R6
    # Eigener Prozess: der Opener entsteht beim Import, die Umgebung muss also vorher gesetzt sein.
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k.lower() != "no_proxy"}
    env.update({k: "http://127.0.0.1:9" for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy")})
    code = ("import sys; from moni.manager.poller import AgentClient; "
            "assert AgentClient(sys.argv[1], 't', {}, 2).get('/ok') == {'a': 1}")
    r = subprocess.run([sys.executable, "-c", code, http_url], env=env, cwd=os.path.dirname(os.path.dirname(__file__)),
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr[-500:]


def test_stop_between_fetch_and_write_skips_writing(poller, servers, agents):
    def stop_then_snap():
        poller._stop.set()
        return snap(T)

    a = add(servers, agents, "a", {"/api/v1/snapshot": [stop_then_snap]})
    poller.poll_once(now=T)
    assert servers.get(a).status == "unknown" and servers.get(a).last_ts is None


def test_info_failure_not_retried_every_cycle(poller, servers, agents):
    a = add(servers, agents, "a", {"/api/v1/snapshot": [snap(T), snap(T + 15), snap(T + 300)],
                                   "/api/v1/history": [{"points": []}],
                                   "/api/v1/info": [AgentError("Agent antwortet mit HTTP 404"), {"version": "x"}]})
    for now in (T, T + 15, T + 300):
        poller.poll_once(now=now)
    assert [p for _, p, _ in agents.calls].count("/api/v1/info") == 2
    assert servers.get(a).status == "ok"


def test_warning_logged_only_on_status_change(poller, servers, agents, caplog):
    add(servers, agents, "a", {"/api/v1/snapshot": [AgentError("Agent nicht erreichbar")] * 5})
    with caplog.at_level("DEBUG", logger="moni.poller"):
        for i in range(5):
            poller.poll_once(now=T + 15 * i)
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING" and r.name == "moni.poller"]  # D5-Warnung beim Anlegen zählt nicht
    assert warnings == ["Abruf von a fehlgeschlagen: Agent nicht erreichbar (Status unreachable)"]


# --- Final-Review-Fixwelle --------------------------------------------------

def test_extreme_values_do_not_stop_storage(poller, servers, agents, conn):  # Final 1a
    big = 1.7e308
    pts = [snap(T, big), snap(T + 15, big), snap(T + 60, -big), snap(T + 75, -big)]
    a = add(servers, agents, "a", {"/api/v1/history": [{"points": pts}], "/api/v1/snapshot": [snap(T + 3600)]})
    b = add(servers, agents, "b", {"/api/v1/snapshot": [snap(T + 3600, cpu=2.0)]})
    servers.record_success(a, T - 15, T - 15, "{}", None)
    poller.poll_once(now=T + 3600)
    assert servers.get(a).status == "ok" and servers.get(b).status == "ok"
    assert count_raw_points(conn, a, M) == 1 and count_raw_points(conn, b, M) == 1
    assert conn.execute("SELECT value FROM meta WHERE key='rollup_1h_until'").fetchone()[0] == str(T + 3600)


def test_failed_maintenance_keeps_server_writes_and_retries(poller, servers, agents, conn, caplog):  # Final 1b
    b = add(servers, agents, "b", {"/api/v1/snapshot": [snap(T + 3600), snap(T + 3615)]})
    sid = conn.execute("INSERT INTO series (server_id, name, labels) VALUES (?, 'x', '[]')", (b,)).lastrowid
    conn.executemany("INSERT INTO samples_raw VALUES (?, ?, ?)",  # an write_snapshots vorbei: AVG ±inf, Stunde NaN
                     [(sid, T, 1.7e308), (sid, T + 1, 1.7e308), (sid, T + 60, -1.7e308), (sid, T + 61, -1.7e308)])
    conn.commit()
    with caplog.at_level("ERROR", logger="moni.poller"):
        poller.poll_once(now=T + 3600)
    assert servers.get(b).status == "ok" and count_raw_points(conn, b, M) == 1
    assert "Verdichtung fehlgeschlagen" in caplog.text and poller._last_maintenance is None
    conn.execute("DELETE FROM samples_raw WHERE series_id=?", (sid,))
    conn.commit()
    poller.poll_once(now=T + 3615)  # nächster Zyklus versucht es erneut
    assert poller._last_maintenance == T + 3615 and not conn.in_transaction


class ClockAgent:
    """Agent mit 15-s-Takt: Punkte bei T, T+15, … bis zu `now`."""

    def __init__(self):
        self.now = T

    def get(self, path, params=None):
        pts = [snap(t) for t in range(T, self.now + 1, 15)]
        if path == "/api/v1/history":
            since = int(datetime.fromisoformat(params["since"]).timestamp())
            return {"points": [p for p in pts if pmod._ts(p) > since]}
        return pts[-1] if path == "/api/v1/snapshot" else {}


def test_slow_poll_cycle_loses_no_remote_points(settings, box, servers, conn):  # Final 2
    agent = ClockAgent()
    a = servers.add("a", "https://a.example.org", "t").id
    p = Poller(settings, box, StubCollector(), client_factory=lambda *x: agent)
    try:
        for now in range(T, T + 301, 20):  # Abruf alle 20 s gegen einen 15-s-Agent
            agent.now = now
            p.poll_once(now=now)
    finally:
        p.close()
    assert count_raw_points(conn, a, M) == len(range(T, T + 301, 15))


def test_slow_poll_cycle_loses_no_local_points(settings, box, servers, conn, monkeypatch):  # Final 2
    from moni import collector as cmod
    from moni.collector import Collector
    clock = [T]
    monkeypatch.setattr(cmod, "read_raw", lambda s, c: raw(datetime.fromtimestamp(clock[0], timezone.utc)))
    col = Collector(settings, client=object())
    loc = servers.ensure_local("pi").id
    p = Poller(settings, box, col)
    try:
        for now in range(T, T + 301, 5):
            clock[0] = now
            if now % 15 == 0:
                col.collect_once()
            if now % 20 == 0:
                p.poll_once(now=now)
    finally:
        p.close()
    n = conn.execute("SELECT COUNT(DISTINCT r.ts) FROM samples_raw r JOIN series s ON s.id = r.series_id "
                     "WHERE s.server_id=?", (loc,)).fetchone()[0]
    assert n == len(range(T, T + 301, 15))


def test_more_servers_than_old_pool_all_contacted(poller, servers, agents):  # Final 4
    n = 12
    together = threading.Barrier(n, timeout=3)  # nur erreichbar, wenn alle gleichzeitig laufen

    def ok():
        together.wait()
        return snap(T)

    ids = [add(servers, agents, f"s{i}", {"/api/v1/snapshot": [ok]}) for i in range(n)]
    poller.poll_once(now=T)
    assert [servers.get(i).status for i in ids] == ["ok"] * n


def test_nan_snapshot_keeps_previous_json(poller, servers, agents, conn, caplog):  # Final 8
    bad = snap(T + 15, cpu=float("nan"))
    bad["host"]["uptime_seconds"] = 5.0
    a = add(servers, agents, "a", {"/api/v1/snapshot": [snap(T), bad]})
    poller.poll_once(now=T)
    with caplog.at_level("WARNING", logger="moni.poller"):
        poller.poll_once(now=T + 15)
    stored = conn.execute("SELECT last_snapshot FROM servers WHERE id=?", (a,)).fetchone()[0]
    assert json.loads(stored, parse_constant=lambda c: pytest.fail(c)) == snap(T)
    assert servers.get(a).last_ts == T + 15 and count_raw_points(conn, a, "moni_host_uptime_seconds") == 1
    assert "nicht als JSON speicherbar" in caplog.text


# --- mTLS-Server (Paket 4) ----------------------------------------------------

class _Pki:
    def __init__(self, ctx=None, error=None):
        self.ctx, self.error = ctx, error

    def client_context(self):
        if self.error:
            raise self.error
        return self.ctx


def _mtls_factory(agents, made):
    def factory(url, token, headers, timeout, ssl_context=None):
        made.append((url, token, headers, ssl_context))
        return FakeClient(agents, url)
    return factory


def test_mtls_server_polled_with_client_context_and_no_token(settings, box, servers, agents, local_collector):
    sid = servers.add_mtls("pi", "https://pi.example", 1).id
    tid = add(servers, agents, "tok", {"/api/v1/snapshot": [snap(T)]})
    agents.scripts["https://pi.example"] = {"/api/v1/snapshot": [snap(T)]}
    ctx, made = object(), []
    p = Poller(settings, box, local_collector, client_factory=_mtls_factory(agents, made), pki=_Pki(ctx))
    p.poll_once(now=T)
    p.close()
    assert ("https://pi.example", "", {}, ctx) in made
    assert ("https://tok.example.org", "tok-tok", {}, None) in made  # Token-Server: kein Moni-Kontext
    assert servers.get(sid).status == "ok" and servers.get(tid).status == "ok"


@pytest.mark.parametrize("pki", [None, _Pki(error=pmod.SecretError("Geheimnis nicht entschlüsselbar")),
                                 _Pki(error=OSError("kaputt"))])
def test_mtls_server_without_usable_client_cert_is_error(settings, box, servers, agents, local_collector, pki):
    sid = servers.add_mtls("pi", "https://pi.example", 1).id
    made = []
    p = Poller(settings, box, local_collector, client_factory=_mtls_factory(agents, made), pki=pki)
    p.poll_once(now=T)
    p.close()
    s = servers.get(sid)
    assert made == [] and (s.status, s.last_error) == ("error", "Client-Zertifikat nicht verfügbar")
