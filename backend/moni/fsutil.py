import os
import secrets
from pathlib import Path


def write_temp(path: Path, data: bytes) -> Path:
    """Temporäre Datei neben `path` mit 0600 schreiben und fsyncen; der Aufrufer setzt sie per os.replace ein."""
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o600)  # unabhängig von umask und Dateisystem
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def write_private(path: Path, data: bytes) -> None:
    """Atomar (tmp, fsync, replace) mit 0600 schreiben."""
    tmp = write_temp(path, data)
    try:
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
