"""SQLite-Zugriff des Managers: Verbindung, versionierte Migrationen, Sicherung vor Upgrades."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, Request

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _create_private(path: Path) -> None:
    """Legt die Datei mit 0600 an, falls sie fehlt; SQLite übernimmt den Modus für -wal und -shm."""
    os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))


def connect(path: Path) -> sqlite3.Connection:
    """Neue Verbindung. check_same_thread bleibt aktiv: jeder Thread/jede Anfrage öffnet eine eigene."""
    _create_private(path)
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


@contextmanager
def request_conn(request: Request):
    """Verbindung für eine Anfrage; im selben Thread öffnen, nutzen und schließen (check_same_thread bleibt aktiv).

    Eine Sync-Dependency kann in einem anderen Thread laufen als der Endpoint, deshalb nie über Funktionen hinweg teilen.
    """
    try:
        conn = connect(request.app.state.settings.db_path)
        try:
            yield conn
        finally:
            conn.close()
    except sqlite3.OperationalError as e:  # Poller-Zyklus oder CLI hält die Schreibsperre länger als busy_timeout
        if "locked" not in str(e) and "busy" not in str(e):
            raise
        raise HTTPException(503, "Datenbank ist gerade beschäftigt, bitte erneut versuchen",
                            headers={"Retry-After": "5"}) from None


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


def _backup(path: Path, version: int) -> None:
    """Sicherung vor dem Upgrade. Eigene Leseverbindung: die migrierende hält die Schreibsperre (WAL erlaubt Lesen)."""
    # Kein zweites Backup bei Crash-Loop: nach Rollback bleibt die DB auf derselben Version.
    if any(not b.name.endswith(".tmp") for b in path.parent.glob(f"{path.name}.bak-v{version}-*")):
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    final = path.with_name(f"{path.name}.bak-v{version}-{stamp}")
    tmp = final.with_name(final.name + ".tmp")
    _create_private(tmp)
    src, bak = sqlite3.connect(path), sqlite3.connect(tmp)
    try:
        src.backup(bak)
        bak.close()
        os.replace(tmp, final)
    except BaseException:
        bak.close()
        tmp.unlink(missing_ok=True)
        raise
    finally:
        src.close()


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
        # Tabellen-Umbauten (DROP + RENAME) dürfen keine ON-DELETE-CASCADE auslösen;
        # das PRAGMA wirkt nur außerhalb einer Transaktion.
        conn.execute("PRAGMA foreign_keys=OFF")
        backed_up = False
        for n, f in _pending(current):
            try:
                # Schreibsperre vor dem Lesen der Version: App-Start und CLI können gleichzeitig migrieren.
                conn.execute("BEGIN IMMEDIATE")
                current = conn.execute("PRAGMA user_version").fetchone()[0]
                if current >= n:  # schon von der anderen Instanz angewendet
                    conn.execute("COMMIT")
                    continue
                if not backed_up and existed and current > 0:
                    _backup(path, current)
                backed_up = True
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
