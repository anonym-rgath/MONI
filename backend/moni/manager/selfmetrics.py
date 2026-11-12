"""Eigenkennzahlen des Managers (D6): Werte im Speicher, Labels nur mit Servernamen."""
import threading
from pathlib import Path

from moni.agent.prometheus import build_info, family, sample

from .servers import Server


class PollStats:
    def __init__(self):
        self._lock = threading.Lock()
        self._servers: dict[str, dict] = {}
        self._cycle: float | None = None

    def _entry(self, server: str) -> dict:
        return self._servers.setdefault(server, {"seconds": None, "errors": 0})

    def record(self, server: str, seconds: float) -> None:
        with self._lock:
            self._entry(server)["seconds"] = seconds

    def fail(self, server: str) -> None:
        """Zählt je Statusfehler genau einmal; wird nur aus Poller._fail gerufen."""
        with self._lock:
            self._entry(server)["errors"] += 1

    def cycle(self, seconds: float) -> None:
        with self._lock:
            self._cycle = seconds

    def snapshot(self) -> dict:
        with self._lock:
            return {"servers": {k: dict(v) for k, v in self._servers.items()}, "cycle": self._cycle}


def _db_size(db_path: Path) -> int:
    total = 0
    for p in (db_path, db_path.with_name(db_path.name + "-wal")):
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def render(stats: dict, servers: list[Server], db_path: Path, version: str) -> str:
    def per_server(name, help_, kind, value):
        return family(name, help_, kind, [sample(name, [("server", s.name)], v)
                                          for s in servers if (v := value(s)) is not None])

    st = stats["servers"]
    lines = build_info("manager", version)
    lines += per_server("moni_manager_server_up", "1, wenn der Server zuletzt erreichbar war", "gauge",
                        lambda s: int(s.status == "ok"))
    lines += per_server("moni_manager_poll_duration_seconds", "Dauer des letzten Abrufs je Server", "gauge",
                        lambda s: st.get(s.name, {}).get("seconds"))
    lines += per_server("moni_manager_poll_errors_total", "Fehlgeschlagene Abrufe seit Prozessstart", "counter",
                        lambda s: st.get(s.name, {}).get("errors", 0))
    if stats["cycle"] is not None:
        lines += family("moni_manager_poll_cycle_duration_seconds", "Dauer des letzten Abrufzyklus", "gauge",
                        [sample("moni_manager_poll_cycle_duration_seconds", [], stats["cycle"])])
    lines += family("moni_manager_db_size_bytes", "Größe der Datenbankdatei samt WAL", "gauge",
                    [sample("moni_manager_db_size_bytes", [], _db_size(db_path))])
    return "\n".join(lines) + "\n"
