import pytest

from moni.auth.sessions import SessionStore
from moni.auth.users import UserStore
from moni.config import Settings
from moni.manager import db
from moni.secretbox import SecretBox, generate_key


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "moni.db"
    db.migrate(p)
    c = db.connect(p)
    yield c
    c.close()


@pytest.fixture
def sessions(conn):
    return SessionStore(conn, Settings.from_env({}))  # idle 60 min, max 12 h


@pytest.fixture
def user_id(conn):
    return UserStore(conn, SecretBox(generate_key())).create("a@b.de", "", "admin", "local", password="x" * 12).id


def _last_seen(sessions):
    return sessions._conn.execute("SELECT last_seen FROM sessions").fetchone()[0]


def _count(sessions):
    return sessions._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]


def test_session_roundtrip_and_hash_only(sessions, user_id):
    s, plain = sessions.create(user_id, "full", 1000, "1.2.3.4")
    assert sessions.get(plain, 1001).user_id == user_id
    assert plain not in repr(s) and s.csrf not in repr(s) and s.id_hash not in repr(s)
    assert sessions._conn.execute("SELECT COUNT(*) FROM sessions WHERE id_hash=?", (plain,)).fetchone()[0] == 0
    assert sessions._conn.execute("SELECT COUNT(*) FROM sessions WHERE id_hash=?", (s.id_hash,)).fetchone()[0] == 1


@pytest.mark.parametrize("bad", ["", "unbekannt", None, 5, b"x", "\ud800"])
def test_get_unknown_or_malformed_is_none(sessions, bad):
    assert sessions.get(bad, 1) is None


def test_idle_and_absolute_expiry(sessions, user_id):      # idle 60 min, max 12 h
    s, p = sessions.create(user_id, "full", 0, None)
    assert sessions.get(p, 3599) and sessions.get(p, 3599 + 3600) is None
    assert _count(sessions) == 0
    s, p = sessions.create(user_id, "full", 0, None)
    for t in range(0, 12 * 3600, 3000):
        assert sessions.get(p, t)
    assert sessions.get(p, 12 * 3600) is None


def test_pre_auth_stage_expires_after_600s(sessions, user_id):
    for stage in ("mfa", "enroll"):
        _, p = sessions.create(user_id, stage, 0, None)
        assert sessions.get(p, 599) and sessions.get(p, 600) is None


def test_touch_throttled(sessions, user_id):
    _, p = sessions.create(user_id, "full", 0, None)
    sessions.get(p, 30)
    assert _last_seen(sessions) == 0
    sessions.get(p, 61)
    assert _last_seen(sessions) == 61


def test_rotate_invalidates_old(sessions, user_id):
    s, old = sessions.create(user_id, "mfa", 0, None)
    n, new = sessions.rotate(s, "full", 10, None)
    assert sessions.get(old, 11) is None and sessions.get(new, 11).stage == "full"
    assert n.csrf != s.csrf and _count(sessions) == 1


def test_delete_for_user_keeps_current(sessions, user_id):
    a, pa = sessions.create(user_id, "full", 0, None)
    sessions.create(user_id, "full", 0, None)
    assert sessions.delete_for_user(user_id, keep=a.id_hash) == 1
    assert sessions.get(pa, 1) and _count(sessions) == 1
    assert sessions.delete_for_user(user_id) == 1 and _count(sessions) == 0


def test_delete_and_purge_expired(sessions, user_id):
    a, pa = sessions.create(user_id, "full", 0, None)
    sessions.delete(a.id_hash)
    assert sessions.get(pa, 1) is None
    sessions.create(user_id, "full", 0, None)      # abgelaufen bei 4000
    sessions.create(user_id, "mfa", 3900, None)    # noch gültig bei 4000
    sessions.create(user_id, "full", 3900, None)
    assert sessions.purge_expired(4000) == 1 and _count(sessions) == 2


def test_user_delete_cascades_sessions(conn, sessions, user_id):
    sessions.create(user_id, "full", 0, None)
    other = UserStore(conn, SecretBox(generate_key()))
    other.create("c@d.de", "", "admin", "local", password="y" * 12)
    other.delete(user_id)
    assert _count(sessions) == 0


def test_store_does_not_commit_inside_outer_transaction(conn, sessions, user_id):
    conn.execute("BEGIN")
    sessions.create(user_id, "full", 0, None)
    conn.rollback()
    assert _count(sessions) == 0
