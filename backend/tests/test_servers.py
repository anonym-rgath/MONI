import pytest
from moni.manager import db
from moni.manager.servers import Server, ServerStore
from moni.secretbox import SecretBox, SecretError, generate_key


@pytest.fixture
def store(tmp_path):
    p = tmp_path / "moni.db"
    db.migrate(p)
    s = ServerStore(db.connect(p), SecretBox(generate_key()))
    yield s
    s._conn.close()


def test_add_hides_secrets(store):
    s = store.add("sau", "https://moni.example.org", "tok-123", {"CF-Access-Client-Id": "id-9"})
    assert s.header_names == ["CF-Access-Client-Id"] and "tok-123" not in repr(s) and "id-9" not in repr(s)
    raw = store._conn.execute("SELECT token_enc, headers_enc, created_at FROM servers WHERE id=?", (s.id,)).fetchone()
    assert "tok-123" not in raw[0] and "id-9" not in raw[1] and isinstance(raw[2], int)
    assert store.credentials(s.id) == ("tok-123", {"CF-Access-Client-Id": "id-9"})
    assert not hasattr(s, "token") and "token" not in Server.__dataclass_fields__


@pytest.mark.parametrize("name,url,token,headers", [
    ("", "https://a", "t", None), ("x" * 65, "https://a", "t", None), ("x", "ftp://a", "t", None),
    ("x", "https://", "t", None), ("x", "https://u:p@a", "t", None),
    ("x", "https://a", "", None), ("x", "https://a", "t", {"Authorization": "y"}),
    ("x", "https://a", "t", {"authorization": "y"}), ("x", "https://a", "t", {"Bad Name": "y"}),
    ("a\x00b", "https://a", "t", None), ("a\nb", "https://a", "t", None), (None, "https://a", "t", None),
])
def test_add_validation(store, name, url, token, headers):
    with pytest.raises(ValueError):
        store.add(name, url, token, headers)


@pytest.mark.parametrize("token,headers", [
    ("a\r\nX-Evil: 1", None), ("a\x00b", None), ("\ud800", None), (5, None),
    ("t", {"X-A": "a\r\nX-Evil: 1"}), ("t", {"X-A": "\ud800"}), ("t", {"X-A": ""}),
    ("t", {"X-A": "ä"}), ("t", {"X-A": "a\x7f"}),
    ("tök", None), ("a\u200bb", None), ("a b", None),
])
def test_add_rejects_bad_secret_values_without_leaking(store, token, headers):
    with pytest.raises(ValueError) as e:
        store.add("x", "https://a", token, headers)
    msg = repr(e.value) + str(e.value.__context__) + str(e.value.__cause__)
    assert "X-Evil" not in msg and "ud800" not in msg.lower() and "\ud800" not in msg
    assert store.list() == []


def test_duplicate_name(store):
    store.add("a", "https://a", "t")
    with pytest.raises(ValueError):
        store.add("a", "https://b", "t")


def test_failure_counting(store):
    s = store.add("a", "https://a", "t")
    assert [store.record_failure(s.id, 100 + i, "Timeout") for i in range(3)] == ["unknown", "unknown", "unreachable"]
    store.record_success(s.id, 200, 195, "{}", None)
    g = store.get(s.id)
    assert g.status == "ok" and g.failures == 0 and g.last_error is None and g.last_seen == 200 and g.last_ts == 195
    assert store.record_failure(s.id, 210, "401", fatal=True) == "error"


def test_record_failure_in_outer_rolled_back_transaction(store):
    s = store.add("a", "https://a", "t")
    store._conn.execute("BEGIN")
    store.record_failure(s.id, 1, "x", fatal=True)
    assert store._conn.in_transaction  # nicht selbst committed
    store._conn.rollback()
    g = store.get(s.id)
    assert g.failures == 0 and g.status == "unknown"


def test_local_server(store):
    loc = store.ensure_local("pi")
    assert loc.is_local and loc.url is None
    assert store.ensure_local("pi").id == loc.id
    assert store.ensure_local("neu").name == "neu" and store.ensure_local("neu").id == loc.id
    assert len(store.list()) == 1
    with pytest.raises(ValueError):
        store.delete(loc.id)


def test_delete_and_enabled(store):
    s = store.add("a", "https://a", "t")
    assert store.set_enabled(s.id, False) and store.get(s.id).enabled is False
    assert store.delete(s.id) and not store.delete(s.id) and store.get(s.id) is None
    assert not store.set_enabled(s.id, True)


def test_wrong_key_credentials(store):
    s = store.add("a", "https://a", "t")
    other = ServerStore(store._conn, SecretBox(generate_key()))
    with pytest.raises(SecretError):
        other.credentials(s.id)


def test_reencrypt_all(store):
    old = generate_key()
    st = ServerStore(store._conn, SecretBox(old))
    s = st.add("a", "https://a", "tok", {"X-A": "v"})
    st.ensure_local("pi")
    new = generate_key()
    st2 = ServerStore(store._conn, SecretBox(f"{new},{old}"))
    assert st2.reencrypt_all() == 1
    assert ServerStore(store._conn, SecretBox(new)).credentials(s.id) == ("tok", {"X-A": "v"})


@pytest.mark.parametrize("url", ["https://a\r\n/x", "https://a/\x1b[31m", "https://a\x00", "https://a b", " https://a",
                                 "https://a\t", "https://a:99999", "https://a:abc", None, 5, "https://ä"])
def test_add_rejects_bad_urls(store, url):
    with pytest.raises(ValueError):
        store.add("x", url, "t")
    assert store.list() == []


def test_delete_missing_is_false_and_local_is_atomic_refusal(store):
    assert store.delete(999) is False
    loc = store.ensure_local("pi")
    with pytest.raises(ValueError):
        store.delete(loc.id)
    assert store.get(loc.id) is not None


def test_reencrypt_all_rolls_back_on_foreign_key(store):
    a = store.add("a", "https://a", "t1")
    b = ServerStore(store._conn, SecretBox(generate_key())).add("b", "https://b", "t2")
    before = [tuple(r) for r in store._conn.execute("SELECT id, token_enc, headers_enc FROM servers ORDER BY id")]
    with pytest.raises(SecretError):
        store.reencrypt_all()
    assert not store._conn.in_transaction
    assert before == [tuple(r) for r in store._conn.execute("SELECT id, token_enc, headers_enc FROM servers ORDER BY id")]
    assert a.id != b.id


@pytest.mark.parametrize("url", ["https://a/?x=1", "https://a#frag", "https://a/pfad?", "https://a/#"])
def test_add_rejects_query_and_fragment(store, url):  # Final 6
    with pytest.raises(ValueError, match="Query oder Fragment"):
        store.add("x", url, "t")


@pytest.mark.parametrize("name", ["Content-Length", "transfer-encoding", "CONNECTION", "Upgrade", "Proxy-Authorization",
                                  "TE", "Trailer", "Keep-Alive"])
def test_add_rejects_framing_headers(store, name):  # Final 7
    with pytest.raises(ValueError, match="nicht erlaubt"):
        store.add("x", "https://a", "t", {name: "1"})
    assert store.list() == []
