"""Sammler: Rohmessung, reine Ableitung (derive) und Ringpuffer."""
import logging
import threading
from collections import deque
from datetime import datetime

from moni.collect import host as hostmod
from moni.collect.docker import DockerClient, collect_containers
from moni.config import Settings
from moni.timeutil import iso, utcnow

SCHEMA_VERSION = 1
log = logging.getLogger("moni.collector")


def read_raw(settings: Settings, client: DockerClient) -> dict:
    proc = settings.host_proc
    memory = hostmod.read_memory(proc)
    containers, docker_status, warnings = collect_containers(client, settings, memory["total_bytes"])
    return {
        "taken_at": utcnow(),
        "cpu_times": hostmod.read_cpu_times(proc),
        "net": hostmod.read_net_counters(proc),
        "host": {
            "hostname": hostmod.read_hostname(settings.host_etc),
            "uptime_seconds": hostmod.read_uptime_seconds(proc),
            "process_count": hostmod.read_process_count(proc),
            "cpu_info": hostmod.read_cpu_info(settings.host_sys),
            "memory": memory,
            "load": hostmod.read_load(proc),
            "filesystems": hostmod.read_filesystems(proc, settings.host_root),
            "temperature": hostmod.read_temperatures(settings.host_sys),
        },
        "containers": containers, "docker": docker_status, "warnings": warnings,
    }


def _rate(prev: int | None, cur: int | None, elapsed: float) -> float | None:
    if prev is None or cur is None or elapsed <= 0 or cur < prev:
        return None
    return round((cur - prev) / elapsed, 1)


def derive(prev: dict | None, cur: dict) -> dict:
    """Reine Funktion: baut aus zwei Rohmessungen den Snapshot (Vertrag für alle Pakete)."""
    elapsed = (cur["taken_at"] - prev["taken_at"]).total_seconds() if prev else 0
    h = cur["host"]
    warnings = list(cur["warnings"])

    usage = hostmod.cpu_percent(prev["cpu_times"], cur["cpu_times"]) if prev else None
    if prev is None:
        warnings.append("CPU-Last noch nicht berechenbar")

    prev_net = (prev or {}).get("net") or {}
    network = None
    if cur["net"] is not None:
        network = []
        for iface, (rx, tx) in cur["net"].items():
            before = prev_net.get(iface)
            network.append({
                "interface": iface, "rx_bytes": rx, "tx_bytes": tx,
                "rx_bytes_per_second": _rate(before[0], rx, elapsed) if before else None,
                "tx_bytes_per_second": _rate(before[1], tx, elapsed) if before else None,
            })

    prev_c = {c["full_id"]: c for c in (prev or {}).get("containers", [])}
    containers = []
    for c in cur["containers"]:
        before = prev_c.get(c["full_id"])
        out = {k: v for k, v in c.items() if k != "full_id"}
        out["rx_bytes_per_second"] = _rate(before["rx_bytes"], c["rx_bytes"], elapsed) if before else None
        out["tx_bytes_per_second"] = _rate(before["tx_bytes"], c["tx_bytes"], elapsed) if before else None
        containers.append(out)

    for ok, text in ((h["memory"]["available"], "Host-Speicher nicht lesbar"),
                     (h["filesystems"] is not None, "Dateisysteme nicht lesbar"),
                     (cur["net"] is not None, "Netzwerk nicht lesbar"),
                     (h["load"]["available"], "Load Average nicht lesbar"),
                     (h["temperature"]["available"], "Temperatur nicht lesbar")):
        if not ok:
            warnings.append(text)

    return {
        "schema_version": SCHEMA_VERSION, "timestamp": iso(cur["taken_at"]),
        "host": {
            "hostname": h["hostname"], "uptime_seconds": h["uptime_seconds"],
            "process_count": h["process_count"],
            "cpu": {"usage_percent": usage, "cores": h["cpu_info"]["cores"],
                    "frequency_mhz": h["cpu_info"]["frequency_mhz"], "available": usage is not None},
            "load": h["load"], "memory": h["memory"], "filesystems": h["filesystems"],
            "network": network, "temperature": h["temperature"],
        },
        "containers": containers, "docker": cur["docker"], "warnings": warnings,
    }


class Collector:
    def __init__(self, settings: Settings, client: DockerClient | None = None):
        self._settings = settings
        self._client = client if client is not None else DockerClient(settings.docker_api_base)
        self._buffer: deque[tuple[datetime, dict]] = deque(
            maxlen=max(1, settings.buffer_seconds // settings.collect_interval_seconds))
        self._lock = threading.Lock()
        self._prev_raw: dict | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def collect_once(self) -> dict:
        cur = read_raw(self._settings, self._client)
        snap = derive(self._prev_raw, cur)
        self._prev_raw = cur
        with self._lock:
            self._buffer.append((cur["taken_at"].replace(microsecond=0), snap))  # wie der emittierte Zeitstempel
        return snap

    def latest(self) -> dict | None:
        with self._lock:
            return self._buffer[-1][1] if self._buffer else None

    def history(self, since: datetime) -> tuple[list[dict], str | None]:
        with self._lock:
            items = list(self._buffer)
        oldest = items[0][1]["timestamp"] if items else None
        return [s for t, s in items if t > since], oldest

    def _run_one_cycle(self) -> None:
        try:
            self.collect_once()
        except Exception:  # Sicherheitsnetz: der Sammel-Thread darf nie sterben
            log.exception("Messzyklus fehlgeschlagen")

    def _loop(self) -> None:
        while True:
            self._run_one_cycle()
            if self._stop.wait(self._settings.collect_interval_seconds):
                break

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="moni-collector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
