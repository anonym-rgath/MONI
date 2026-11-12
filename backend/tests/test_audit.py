import pytest

from moni.auth import audit
from moni.manager import db


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "moni.db"
    db.migrate(p)
    c = db.connect(p)
    yield c
    c.close()


def test_audit_record_and_page(conn):
    for i in range(5):
        audit.record(conn, "cli", "user.created", target=f"u{i}", now=i)
    page = audit.entries(conn, limit=2)
    assert [e["target"] for e in page] == ["u4", "u3"]
    assert [e["target"] for e in audit.entries(conn, limit=2, before=page[-1]["id"])] == ["u2", "u1"]
    assert page[0]["ts"].endswith("Z") and page[0]["ts"].startswith("1970-01-01T00:00:04")
    assert not conn.in_transaction  # ohne äußere Transaktion wurde committet


def test_audit_details_roundtrip_and_none(conn):
    audit.record(conn, "cli", "a", details={"k": [1, "ä"]}, ip="1.2.3.4", now=1)
    audit.record(conn, "cli", "b", now=2)
    b, a = audit.entries(conn)
    assert a["details"] == {"k": [1, "ä"]} and a["ip"] == "1.2.3.4" and b["details"] is None and b["target"] is None


def test_audit_rejects_nan_details(conn):
    with pytest.raises(ValueError):
        audit.record(conn, "cli", "a", details={"x": float("nan")}, now=1)
    assert audit.entries(conn) == []


def test_audit_defaults_ts_to_now(conn):
    audit.record(conn, "cli", "a")
    assert audit.entries(conn)[0]["ts"].startswith("20")


def test_audit_no_commit_inside_transaction(conn):
    conn.execute("BEGIN")
    audit.record(conn, "cli", "a", now=1)
    assert conn.in_transaction
    conn.rollback()
    assert audit.entries(conn) == []


def test_audit_truncates_and_prunes(conn):
    audit.record(conn, "a" * 1000, "b" * 1000, target="t" * 1000, ip="i" * 1000, now=10)
    audit.record(conn, "cli", "x", now=100)
    e = audit.entries(conn)[1]
    assert [len(e[k]) for k in ("actor", "action", "target", "ip")] == [256] * 4
    assert audit.prune(conn, 50) == 1 and len(audit.entries(conn)) == 1
    assert not conn.in_transaction
