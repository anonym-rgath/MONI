"""SQLite-Zugriff des Managers: Verbindung, versionierte Migrationen, Sicherung vor Upgrades."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(path: Path) -> sqlite3.Connection:
    """Neue Verbindung. check_same_thread bleibt aktiv: jeder Thread/jede Anfrage öffnet eine eigene."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _statements(sql: str):
    """Zerlegt SQL in einzelne Statements (kennt Strings, Kommentare und Trigger-Blöcke)."""
    buf = ""
    for piece in sql.split(";"):
        buf += piece + ";"
        if sqlite3.complete_statement(buf):
            yield buf.strip()
            buf = ""
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
        pending = _pending(current)
        if pending and existed and current > 0:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            bak = sqlite3.connect(path.with_name(f"{path.name}.bak-v{current}-{stamp}"))
            try:
                conn.backup(bak)
            finally:
                bak.close()
        for n, f in pending:
            try:
                conn.execute("BEGIN")
                for stmt in _statements(f.read_text(encoding="utf-8")):
                    conn.execute(stmt)
                conn.execute(f"PRAGMA user_version = {n}")
                conn.execute("COMMIT")
            except Exception as e:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise RuntimeError(f"Migration {n} fehlgeschlagen: {e}") from e
            current = n
        return current
    finally:
        conn.close()
