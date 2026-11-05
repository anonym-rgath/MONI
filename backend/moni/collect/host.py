"""Rein lesende Host-Messung aus /proc, /sys und /etc. Jede Funktion wirft nie."""
import glob
import logging
import os
import socket

log = logging.getLogger(__name__)

PSEUDO_FS = frozenset({
    "proc", "sysfs", "tmpfs", "devtmpfs", "devpts", "overlay", "cgroup", "cgroup2",
    "mqueue", "nsfs", "squashfs", "tracefs", "debugfs", "securityfs", "pstore", "bpf",
    "autofs", "fusectl", "configfs", "binfmt_misc", "hugetlbfs", "rpc_pipefs", "ramfs",
})
SKIP_MOUNT_PREFIXES = ("/proc", "/sys", "/dev", "/run", "/var/lib/docker", "/snap")
_VIRTUAL_IFACES = ("veth", "br-")


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read()
    except (OSError, ValueError) as e:
        log.debug("Lesen von %s fehlgeschlagen: %s", path, e)
        return None


def _pct(part: float, whole: float) -> float | None:
    return round(part / whole * 100, 1) if whole > 0 else None


def read_cpu_times(proc: str) -> tuple[int, int] | None:
    text = _read(f"{proc}/stat")
    try:
        v = [int(x) for x in text.splitlines()[0].split()[1:9]]
        if len(v) < 8:
            return None
        return v[3] + v[4], sum(v)
    except (AttributeError, IndexError, ValueError):
        log.warning("%s/stat nicht lesbar", proc)
        return None


def cpu_percent(prev: tuple[int, int] | None, cur: tuple[int, int] | None) -> float | None:
    if prev is None or cur is None:
        return None
    total = cur[1] - prev[1]
    if total <= 0:
        return None
    return round((1 - (cur[0] - prev[0]) / total) * 100, 1)


def read_cpu_info(sys: str) -> dict:
    mhz = None
    text = _read(f"{sys}/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    try:
        mhz = int(text) // 1000 if text is not None else None
    except ValueError:
        pass
    return {"cores": os.cpu_count() or 1, "frequency_mhz": mhz}


def _meminfo(proc: str) -> dict[str, int]:
    out = {}
    for line in (_read(f"{proc}/meminfo") or "").splitlines():
        try:
            key, val = line.split(":", 1)
            out[key] = int(val.split()[0]) * 1024
        except (ValueError, IndexError):
            continue
    return out


def read_memory(proc: str) -> dict:
    m = _meminfo(proc)
    empty = {"total_bytes": None, "used_bytes": None, "available_bytes": None,
             "usage_percent": None, "swap_total_bytes": None, "swap_used_bytes": None,
             "swap_percent": None, "available": False}
    if "MemTotal" not in m or "MemAvailable" not in m:
        return empty
    total, avail = m["MemTotal"], m["MemAvailable"]
    used = total - avail
    r = {"total_bytes": total, "used_bytes": used, "available_bytes": avail,
         "usage_percent": _pct(used, total), "swap_total_bytes": None,
         "swap_used_bytes": None, "swap_percent": None, "available": True}
    if "SwapTotal" in m and "SwapFree" in m:
        r["swap_total_bytes"] = m["SwapTotal"]
        r["swap_used_bytes"] = m["SwapTotal"] - m["SwapFree"]
        r["swap_percent"] = _pct(r["swap_used_bytes"], m["SwapTotal"])
    return r


def read_load(proc: str) -> dict:
    text = _read(f"{proc}/loadavg")
    try:
        a, b, c = (float(x) for x in text.split()[:3])
        return {"load1": a, "load5": b, "load15": c, "available": True}
    except (AttributeError, ValueError):
        return {"load1": None, "load5": None, "load15": None, "available": False}


def read_uptime_seconds(proc: str) -> float | None:
    text = _read(f"{proc}/uptime")
    try:
        return float(text.split()[0])
    except (AttributeError, IndexError, ValueError):
        return None


def read_process_count(proc: str) -> int | None:
    try:
        return sum(1 for n in os.listdir(proc) if n.isdigit())
    except OSError as e:
        log.warning("Prozesse in %s nicht lesbar: %s", proc, e)
        return None


def read_hostname(etc: str) -> str:
    text = _read(f"{etc}/hostname")
    return text.strip() if text and text.strip() else socket.gethostname()


def read_temperatures(sys: str) -> dict:
    zones = []
    for zone in sorted(glob.glob(f"{sys}/class/thermal/thermal_zone*")):
        raw = _read(f"{zone}/temp")
        try:
            celsius = int(raw) / 1000
        except (TypeError, ValueError):
            continue
        name = (_read(f"{zone}/type") or os.path.basename(zone)).strip()
        zones.append({"id": os.path.basename(zone), "name": name, "celsius": celsius})
    return {"max_celsius": max((z["celsius"] for z in zones), default=None),
            "zones": zones, "available": bool(zones)}


def read_net_counters(proc: str) -> dict[str, tuple[int, int]] | None:
    text = _read(f"{proc}/1/net/dev")
    if text is None:
        return None
    out = {}
    for line in text.splitlines()[2:]:
        name, sep, rest = line.partition(":")
        name = name.strip()
        if not sep or name in ("lo", "docker0") or name.startswith(_VIRTUAL_IFACES):
            continue
        try:
            f = rest.split()
            out[name] = (int(f[0]), int(f[8]))
        except (IndexError, ValueError):
            continue
    return out


def _skipped(mountpoint: str) -> bool:
    return any(mountpoint == p or mountpoint.startswith(p + "/") for p in SKIP_MOUNT_PREFIXES)


def read_filesystems(proc: str, root: str) -> list[dict] | None:
    text = _read(f"{proc}/1/mounts")
    if text is None:
        return None
    result, seen_devices = [], set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mountpoint, fstype = parts[0], parts[1].replace("\\040", " "), parts[2]
        if fstype in PSEUDO_FS or _skipped(mountpoint) or device in seen_devices:
            continue
        try:
            st = os.statvfs(os.path.join(root, mountpoint.lstrip("/")))
        except OSError as e:
            log.debug("statvfs für %s fehlgeschlagen: %s", mountpoint, e)
            continue
        seen_devices.add(device)
        total = st.f_blocks * st.f_frsize
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        free = st.f_bavail * st.f_frsize
        result.append({"mountpoint": mountpoint, "device": device, "fstype": fstype,
                       "total_bytes": total, "used_bytes": used, "free_bytes": free,
                       "usage_percent": _pct(used, used + free)})
    return result
