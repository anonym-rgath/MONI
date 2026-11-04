"""Prometheus-Textformat 0.0.4, handgeschrieben. Fehlende/None-Werte werden ausgelassen."""
from moni.timeutil import parse_iso

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

HELP = {
    "moni_host_cpu_usage_percent": "CPU-Auslastung des Hosts in Prozent",
    "moni_host_load1": "Load Average 1 Minute", "moni_host_load5": "Load Average 5 Minuten",
    "moni_host_load15": "Load Average 15 Minuten",
    "moni_host_memory_total_bytes": "Gesamter Arbeitsspeicher in Bytes",
    "moni_host_memory_used_bytes": "Belegter Arbeitsspeicher in Bytes",
    "moni_host_memory_usage_percent": "Arbeitsspeicher-Auslastung in Prozent",
    "moni_host_swap_used_bytes": "Belegter Swap in Bytes",
    "moni_host_uptime_seconds": "Laufzeit des Hosts in Sekunden",
    "moni_host_processes": "Anzahl Prozesse auf dem Host",
    "moni_host_temperature_celsius": "Temperatur je Zone in Grad Celsius",
    "moni_filesystem_size_bytes": "Größe des Dateisystems in Bytes",
    "moni_filesystem_used_bytes": "Belegter Platz des Dateisystems in Bytes",
    "moni_filesystem_usage_percent": "Auslastung des Dateisystems in Prozent",
    "moni_network_receive_bytes": "Empfangene Bytes je Schnittstelle (kumulativ)",
    "moni_network_transmit_bytes": "Gesendete Bytes je Schnittstelle (kumulativ)",
    "moni_container_up": "1, wenn der Container läuft",
    "moni_container_cpu_usage_percent": "CPU-Auslastung des Containers in Prozent",
    "moni_container_memory_usage_bytes": "Speichernutzung des Containers in Bytes",
    "moni_container_restarts": "Anzahl Neustarts des Containers",
    "moni_container_healthy": "1, wenn der Healthcheck des Containers gesund meldet",
    "moni_docker_up": "1, wenn die Docker-API erreichbar ist",
    "moni_snapshot_timestamp_seconds": "Zeitpunkt des Snapshots (Unix-Sekunden)",
}


def _esc(v) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _num(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return repr(v)


def render(snapshot: dict) -> str:
    fam: dict[str, list[str]] = {}

    def add(metric, value, /, **labels):
        if not isinstance(value, (int, float)):  # None, fehlend oder unbrauchbar
            return
        lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
        fam.setdefault(metric, []).append(f"{metric}{{{lab}}} {_num(value)}" if lab else f"{metric} {_num(value)}")

    h = snapshot.get("host") or {}
    add("moni_host_cpu_usage_percent", (h.get("cpu") or {}).get("usage_percent"))
    for k in ("load1", "load5", "load15"):
        add(f"moni_host_{k}", (h.get("load") or {}).get(k))
    mem = h.get("memory") or {}
    for k, n in (("total_bytes", "memory_total_bytes"), ("used_bytes", "memory_used_bytes"),
                 ("usage_percent", "memory_usage_percent"), ("swap_used_bytes", "swap_used_bytes")):
        add(f"moni_host_{n}", mem.get(k))
    add("moni_host_uptime_seconds", h.get("uptime_seconds"))
    add("moni_host_processes", h.get("process_count"))
    for z in (h.get("temperature") or {}).get("zones") or []:
        add("moni_host_temperature_celsius", z.get("celsius"), zone=z.get("name"))
    for f in h.get("filesystems") or []:
        lab = {"mountpoint": f.get("mountpoint"), "device": f.get("device"), "fstype": f.get("fstype")}
        add("moni_filesystem_size_bytes", f.get("total_bytes"), **lab)
        add("moni_filesystem_used_bytes", f.get("used_bytes"), **lab)
        add("moni_filesystem_usage_percent", f.get("usage_percent"), **lab)
    for n in h.get("network") or []:
        add("moni_network_receive_bytes", n.get("rx_bytes"), interface=n.get("interface"))
        add("moni_network_transmit_bytes", n.get("tx_bytes"), interface=n.get("interface"))
    for c in snapshot.get("containers") or []:
        name = c.get("name")
        add("moni_container_up", c.get("status") == "running",
            name=name, image=c.get("image"), status=c.get("status"))
        add("moni_container_cpu_usage_percent", c.get("cpu_percent"), name=name)
        add("moni_container_memory_usage_bytes", c.get("memory_usage_bytes"), name=name)
        add("moni_container_restarts", c.get("restart_count"), name=name)
        if c.get("health") not in (None, "none"):
            add("moni_container_healthy", c["health"] == "healthy", name=name)
    reachable = (snapshot.get("docker") or {}).get("reachable")
    if reachable is not None:
        add("moni_docker_up", bool(reachable))
    add("moni_snapshot_timestamp_seconds", int(parse_iso(snapshot["timestamp"]).timestamp()))

    lines = []
    for name, samples in fam.items():
        lines += [f"# HELP {name} {HELP[name]}", f"# TYPE {name} gauge", *samples]
    return "\n".join(lines) + "\n"
