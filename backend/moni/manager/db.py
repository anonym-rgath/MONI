"""SQLite-Zugriff des Managers: Verbindung, versionierte Migrationen, Sicherung vor Upgrades."""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(path: Path) -> sqlite3.Connection:
    """Neue Verbindung. check_same_thread bleibt aktiv: jeder Thread/jede Anfrage öffnet eine eigene."""
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        if conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
            raise RuntimeError(f"WAL-Modus für {path} nicht verfügbar")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except BaseException:
        conn.close()
        raise
    return conn


def _statements(sql: str):
    """Zerlegt SQL in einzelne Statements (kennt Strings, Kommentare und Trigger-Blöcke)."""
    buf = ""
    for piece in sql.split(";"):
        buf += piece + ";"
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            buf = ""
            body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--"))
            if body.strip(" \t\r\n;"):
                yield stmt
    rest = "\n".join(l for l in buf[:-1].splitlines() if not l.strip().startswith("--")).strip()
    if rest:
        yield rest


def _pending(current: int) -> list[tuple[int, Path]]:
    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    return [(n, f) for f in files if (n := int(f.name[:4])) > current]


def migrate(path: Path) -> int:
    path = Path(path)
    if not path.parent.is_dir():
        raise RuntimeError(f"Datenverzeichnis {path.parent} existiert nicht")
    existed = path.exists()
    conn = connect(path)
    try:
        conn.isolation_level = None  # Autocommit: BEGIN/COMMIT/ROLLBACK explizit
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        newest = max((int(f.name[:4]) for f in MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")), default=0)
        if current > newest:
            raise RuntimeError(f"Datenbank-Version {current} ist neuer als diese Moni-Version")
        pending = _pending(current)
        # Kein zweites Backup bei Crash-Loop: nach Rollback bleibt die DB auf derselben Version.
        has_bak = any(not b.name.endswith(".tmp") for b in path.parent.glob(f"{path.name}.bak-v{current}-*"))
        if pending and existed and current > 0 and not has_bak:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            final = path.with_name(f"{path.name}.bak-v{current}-{stamp}")
            tmp = final.with_name(final.name + ".tmp")
            bak = sqlite3.connect(tmp)
            try:
                conn.backup(bak)
                bak.close()
                os.replace(tmp, final)
            except BaseException:
                bak.close()
                tmp.unlink(missing_ok=True)
                raise
        # Tabellen-Umbauten (DROP + RENAME) dürfen keine ON-DELETE-CASCADE auslösen;
        # das PRAGMA wirkt nur außerhalb einer Transaktion.
        conn.execute("PRAGMA foreign_keys=OFF")
        for n, f in pending:
            try:
                conn.execute("BEGIN")
                for stmt in _statements(f.read_text(encoding="utf-8")):
                    conn.execute(stmt)
                conn.execute(f"PRAGMA user_version = {n}")
                if conn.execute("PRAGMA foreign_key_check").fetchone():
                    raise sqlite3.IntegrityError("Fremdschlüssel verletzt")
                conn.execute("COMMIT")
            except Exception as e:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise RuntimeError(f"Migration {n} fehlgeschlagen: {e}") from e
            current = n
        return current
    finally:
        conn.close()
