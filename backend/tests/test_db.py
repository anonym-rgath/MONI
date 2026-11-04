import pytest

from moni.manager import db


def test_fresh_migrate(tmp_path):
    p = tmp_path / "moni.db"
    assert db.migrate(p) == 1
    c = db.connect(p)
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {
        "servers", "series", "samples_raw", "samples_1m", "samples_1h", "meta"}
    assert not list(tmp_path.glob("moni.db.bak-*"))       # frische DB: keine Sicherung nötig


def test_migrate_is_idempotent(tmp_path):
    p = tmp_path / "moni.db"; db.migrate(p)
    assert db.migrate(p) == 1 and not list(tmp_path.glob("moni.db.bak-*"))


def test_backup_before_upgrade_and_failed_migration_keeps_state(tmp_path, monkeypatch):
    p = tmp_path / "moni.db"; db.migrate(p)
    bad = tmp_path / "mig"; bad.mkdir()
    for f in db.MIGRATIONS_DIR.glob("*.sql"):
        (bad / f.name).write_text(f.read_text())
    (bad / "0002_kaputt.sql").write_text("CREATE TABLE x (a INT); SELEC kaputt;")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", bad)
    with pytest.raises(RuntimeError, match="Migration 2"):
        db.migrate(p)
    assert db.connect(p).execute("PRAGMA user_version").fetchone()[0] == 1
    assert "x" not in {r[0] for r in db.connect(p).execute("SELECT name FROM sqlite_master")}
    assert len(list(tmp_path.glob("moni.db.bak-v1-*"))) == 1


def test_missing_data_dir_raises_german_error(tmp_path):
    with pytest.raises(RuntimeError, match="Datenverzeichnis"):
        db.migrate(tmp_path / "fehlt" / "moni.db")
