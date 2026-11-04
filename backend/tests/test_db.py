import sqlite3

import pytest

from moni.manager import db


def test_fresh_migrate(tmp_path):
    p = tmp_path / "moni.db"
    assert db.migrate(p) == 1
    c = db.connect(p)
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert c.execute("PRAGMA synchronous").fetchone()[0] == 1
    assert c.row_factory is sqlite3.Row
    assert {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {
        "servers", "series", "samples_raw", "samples_1m", "samples_1h", "meta"}
    c.close()
    assert not list(tmp_path.glob("moni.db.bak-*"))       # frische DB: keine Sicherung nötig


def test_migrate_is_idempotent(tmp_path):
    p = tmp_path / "moni.db"; db.migrate(p)
    assert db.migrate(p) == 1 and not list(tmp_path.glob("moni.db.bak-*"))


def _bad_dir(tmp_path, monkeypatch, sql):
    bad = tmp_path / "mig"; bad.mkdir()
    for f in db.MIGRATIONS_DIR.glob("*.sql"):
        (bad / f.name).write_text(f.read_text())
    (bad / "0002_x.sql").write_text(sql)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", bad)


def test_backup_before_upgrade_and_failed_migration_keeps_state(tmp_path, monkeypatch):
    p = tmp_path / "moni.db"; db.migrate(p)
    _bad_dir(tmp_path, monkeypatch, "CREATE TABLE x (a INT); SELEC kaputt;")
    with pytest.raises(RuntimeError, match="Migration 2"):
        db.migrate(p)
    c = db.connect(p)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 1
    assert "x" not in {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
    c.close()
    baks = list(tmp_path.glob("moni.db.bak-v1-*"))
    assert len(baks) == 1
    b = sqlite3.connect(baks[0])
    assert b.execute("PRAGMA user_version").fetchone()[0] == 1
    b.close()
    assert not list(tmp_path.glob("*.tmp"))


def test_crash_loop_creates_only_one_backup(tmp_path, monkeypatch):
    p = tmp_path / "moni.db"; db.migrate(p)
    _bad_dir(tmp_path, monkeypatch, "SELEC kaputt;")
    for _ in range(2):
        with pytest.raises(RuntimeError, match="Migration 2"):
            db.migrate(p)
    assert len(list(tmp_path.glob("moni.db.bak-v1-*"))) == 1


def test_table_rebuild_migration_keeps_child_rows(tmp_path, monkeypatch):
    p = tmp_path / "moni.db"; db.migrate(p)
    c = db.connect(p)
    c.execute("INSERT INTO servers (id, name, created_at) VALUES (1, 's', 0)")
    c.execute("INSERT INTO series (id, server_id, name, labels) VALUES (1, 1, 'm', '[]')")
    c.execute("INSERT INTO samples_raw VALUES (1, 10, 1.0)")
    c.commit(); c.close()
    _bad_dir(tmp_path, monkeypatch,
             "CREATE TABLE servers_new (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL); "
             "INSERT INTO servers_new SELECT id, name, created_at FROM servers; DROP TABLE servers; "
             "ALTER TABLE servers_new RENAME TO servers;")
    assert db.migrate(p) == 2
    c = db.connect(p)
    assert c.execute("SELECT count(*) FROM series").fetchone()[0] == 1
    assert c.execute("SELECT count(*) FROM samples_raw").fetchone()[0] == 1
    c.close()


def test_foreign_key_violation_rolls_back(tmp_path, monkeypatch):
    p = tmp_path / "moni.db"; db.migrate(p)
    _bad_dir(tmp_path, monkeypatch, "INSERT INTO series (server_id, name, labels) VALUES (99, 'a', '[]');")
    with pytest.raises(RuntimeError, match="Fremdschlüssel"):
        db.migrate(p)
    c = db.connect(p)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 1
    assert c.execute("SELECT count(*) FROM series").fetchone()[0] == 0
    c.close()


def test_newer_db_version_is_refused(tmp_path):
    p = tmp_path / "moni.db"; db.migrate(p)
    c = sqlite3.connect(p); c.execute("PRAGMA user_version = 9"); c.close()
    with pytest.raises(RuntimeError, match="neuer"):
        db.migrate(p)
    assert not list(tmp_path.glob("moni.db.bak-*"))


def test_statements_skip_stray_semicolons():
    assert list(db._statements("SELECT 1;;\n-- c\n;  ; SELECT 2; -- ende")) == ["SELECT 1;", "SELECT 2;"]


def test_missing_data_dir_raises_german_error(tmp_path):
    with pytest.raises(RuntimeError, match="Datenverzeichnis"):
        db.migrate(tmp_path / "fehlt" / "moni.db")


def test_concurrent_migrate_applies_once(tmp_path, monkeypatch):  # Final 3
    p = tmp_path / "moni.db"; db.migrate(p)
    _bad_dir(tmp_path, monkeypatch, "CREATE TABLE x (a INT);")
    real = db._pending

    def racing(current):  # die CLI migriert zwischen Versionsabfrage und Schritt
        monkeypatch.setattr(db, "_pending", real)
        assert db.migrate(p) == 2
        return real(current)

    monkeypatch.setattr(db, "_pending", racing)
    assert db.migrate(p) == 2
    assert len(list(tmp_path.glob("moni.db.bak-v1-*"))) == 1


def test_db_and_backup_files_are_private(tmp_path, monkeypatch):  # Final 11
    import os
    import stat
    old = os.umask(0o022)
    try:
        p = tmp_path / "moni.db"; db.migrate(p)
        c = db.connect(p); c.execute("CREATE TABLE IF NOT EXISTS t (a)"); c.commit()
        _bad_dir(tmp_path, monkeypatch, "CREATE TABLE x (a INT);")
        assert db.migrate(p) == 2
        modes = {f.name: oct(stat.S_IMODE(f.stat().st_mode))
                 for f in (p, *tmp_path.glob("moni.db-*"), *tmp_path.glob("moni.db.bak-*"))}
        c.close()
    finally:
        os.umask(old)
    assert any(".bak-" in n for n in modes) and any(n.endswith("-wal") for n in modes)
    assert modes == dict.fromkeys(modes, "0o600")
