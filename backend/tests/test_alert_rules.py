import sqlite3

import pytest
from moni.auth.users import Conflict
from moni.manager import db
from moni.manager.alert_rules import KNOWN_METRICS, Rule, RuleStore, validate
from moni.manager.servers import ServerStore
from moni.secretbox import SecretBox, generate_key


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "moni.db"
    db.migrate(p)
    c = db.connect(p)
    yield c
    c.close()


def _data(**over):
    d = {"name": "R", "metric": "moni_host_cpu_usage_percent", "op": ">", "warning": 75, "critical": 85}
    d.update(over)
    return d


def test_migration_seeds_default_rules(conn):
    rules = {r.name: r for r in RuleStore(conn).list()}
    assert len(rules) == 9
    disk = rules["Dateisystem voll"]
    assert (disk.metric, disk.op, disk.warning, disk.critical, disk.for_seconds, disk.key_labels) == \
        ("moni_filesystem_usage_percent", ">", 75, 85, 300, ("mountpoint",))
    assert rules["Container gestoppt"].require_prior_ok and rules["Restart-Schleife"].window_seconds == 600
    assert rules["Restart-Schleife"].func == "increase" and rules["Restart-Schleife"].op == ">="
    assert rules["Server nicht erreichbar"].warning is None and rules["Server nicht erreichbar"].metric == "moni_server_up"
    assert rules["Temperatur"].key_labels == ("zone", "id")
    assert rules["Container unhealthy"].critical is None and rules["Container unhealthy"].warning == 1
    got = {n: (r.metric, r.func, r.window_seconds, r.op, r.warning, r.critical, r.for_seconds, r.key_labels,
               r.require_prior_ok) for n, r in rules.items()}
    assert got == {
        "Server nicht erreichbar": ("moni_server_up", "value", None, "<", None, 1, 0, (), False),
        "CPU-Auslastung": ("moni_host_cpu_usage_percent", "value", None, ">", 75, 85, 300, (), False),
        "Arbeitsspeicher": ("moni_host_memory_usage_percent", "value", None, ">", 75, 85, 300, (), False),
        "Dateisystem voll": ("moni_filesystem_usage_percent", "value", None, ">", 75, 85, 300, ("mountpoint",), False),
        "Temperatur": ("moni_host_temperature_celsius", "value", None, ">", 65, 75, 120, ("zone", "id"), False),
        "Container gestoppt": ("moni_container_up", "value", None, "<", None, 1, 60, ("name",), True),
        "Container unhealthy": ("moni_container_healthy", "value", None, "<", 1, None, 120, ("name",), False),
        "Restart-Schleife": ("moni_container_restarts", "increase", 600, ">=", None, 3, 0, ("name",), False),
        "Docker-API nicht erreichbar": ("moni_docker_up", "value", None, "<", None, 1, 60, (), False)}
    assert all(r.enabled and r.server_id is None and r.label_filter == {} for r in rules.values())


@pytest.mark.parametrize("patch,msg", [
    ({"metric": "erfunden"}, "Unbekannte Metrik"),
    ({"warning": None, "critical": None}, "Mindestens eine Schwelle"),
    ({"func": "value", "window_seconds": 60}, "Fenster"),
    ({"func": "increase", "window_seconds": None}, "Fenster"),
    ({"func": "increase", "window_seconds": 59}, "Fenster"),
    ({"func": "increase", "window_seconds": 86401}, "Fenster"),
    ({"func": "median"}, "Unbekannte Funktion median"),
    ({"key_labels": ["cpu\n"]}, "Labels"), ({"label_filter": {"cpu\n": "x"}}, "Labels"),
    ({"label_filter": {k: "x" for k in "abcdef"}}, "Label-Filter"), ({"label_filter": {"a": "x" * 129}}, "Label-Filter"),
    ({"op": "!="}, "Vergleich"),
    ({"op": ">", "warning": 90, "critical": 80}, "Warnschwelle"),
    ({"op": "<", "warning": 1, "critical": 2}, "Warnschwelle"),
    ({"op": ">", "warning": 80, "critical": 80}, "Warnschwelle"),
    ({"warning": float("nan")}, "endliche"), ({"critical": float("inf")}, "endliche"), ({"warning": True}, "endliche"),
    ({"key_labels": ["Name!"]}, "Labels"), ({"key_labels": list("abcdef")}, "Labels"),
    ({"label_filter": {"Bad": "x"}}, "Labels"), ({"label_filter": {"a": 1}}, "Labels"),
    ({"for_seconds": -1}, "Dauer"), ({"for_seconds": 86401}, "Dauer"),
    ({"name": ""}, "Name muss"), ({"name": "x" * 65}, "Name muss"), ({"name": 5}, "Name muss"),
])
def test_validate_rejects(patch, msg):
    with pytest.raises(ValueError, match=msg) as e:
        validate(_data(**patch))
    assert e.value.__context__ is None


def test_validate_normalizes():
    v = validate(_data(name=" R ", metric="moni_server_up", key_labels=["a", "a"], label_filter={"mountpoint": "/"}))
    assert v["name"] == "R" and v["func"] == "value" and v["for_seconds"] == 0 and v["key_labels"] == ["a"]
    assert v["require_prior_ok"] is False and v["enabled"] is True and v["server_id"] is None
    assert "moni_server_up" in KNOWN_METRICS
    w = validate(_data(op="<", warning=90, critical=80, func="increase", window_seconds=60))
    assert w["window_seconds"] == 60


def test_crud_and_unique_name(conn):
    st = RuleStore(conn)
    r = st.create(_data(key_labels=["mountpoint"], label_filter={"mountpoint": "/"}), 100)
    assert isinstance(r, Rule) and r.created_at == r.updated_at == 100 and r.label_filter == {"mountpoint": "/"}
    assert st.get(r.id) == r and not conn.in_transaction
    with pytest.raises(Conflict, match="Regelname ist bereits vergeben") as e:
        st.create(_data(), 101)
    assert e.value.__context__ is None
    u = st.update(r.id, _data(name="R2", enabled=False, critical=95), 200)
    assert (u.name, u.enabled, u.critical, u.created_at, u.updated_at) == ("R2", False, 95, 100, 200)
    with pytest.raises(Conflict) as e:
        st.update(r.id, _data(name="CPU-Auslastung"), 300)
    assert e.value.__context__ is None
    assert st.update(9999, _data(name="Q"), 1) is None
    assert st.delete(r.id) is True and st.delete(r.id) is False and st.get(r.id) is None


def test_server_must_exist_and_cascades(conn):
    st = RuleStore(conn)
    with pytest.raises(ValueError, match="Server"):
        st.create(_data(server_id=42), 1)
    s = ServerStore(conn, SecretBox(generate_key())).add("sau", "https://a.example.org", "tok")
    r = st.create(_data(server_id=s.id), 1)
    conn.execute("DELETE FROM servers WHERE id=?", (s.id,))
    conn.commit()
    assert st.get(r.id) is None


def test_no_commit_inside_foreign_transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    RuleStore(conn).create(_data(), 1)
    assert conn.in_transaction
    conn.rollback()
    assert len(RuleStore(conn).list()) == 9


def test_transaction_is_public_and_commits(conn):
    with RuleStore(conn).transaction():
        conn.execute("UPDATE alert_rules SET enabled=0")
    assert not conn.in_transaction and not any(r.enabled for r in RuleStore(conn).list())


def test_non_unique_integrity_error_propagates(conn):
    st = RuleStore(conn)
    r = st.create(_data(), 1)
    conn.execute("CREATE TRIGGER no_ins BEFORE INSERT ON alert_rules BEGIN SELECT RAISE(ABORT, 'kaputt'); END")
    conn.execute("CREATE TRIGGER no_upd BEFORE UPDATE ON alert_rules BEGIN SELECT RAISE(ABORT, 'kaputt'); END")
    for call in (lambda: st.create(_data(name="N"), 2), lambda: st.update(r.id, _data(name="M"), 2)):
        with pytest.raises(sqlite3.IntegrityError, match="kaputt"):  # kein Conflict, wird weitergereicht
            call()
    assert not conn.in_transaction
