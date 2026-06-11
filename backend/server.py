from fastapi import FastAPI, APIRouter
from starlette.middleware.cors import CORSMiddleware
import logging
import os
import json
import socket
import ctypes
import ctypes.util
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib import request as urlrequest
from typing import List, Dict, Any, Tuple
from datetime import datetime, timezone

app = FastAPI(title="Linux Monitor API")
api_router = APIRouter(prefix="/api")

DOCKER_SOCKET = "/var/run/docker.sock"
DOCKER_API_BASE = os.environ.get("DOCKER_API_BASE", f"unix://{DOCKER_SOCKET}")
DOCKER_STATS_TIMEOUT = float(os.environ.get("DOCKER_STATS_TIMEOUT", "2.5"))
DOCKER_INSPECT_TIMEOUT = float(os.environ.get("DOCKER_INSPECT_TIMEOUT", "1.0"))
MAX_DOCKER_WORKERS = int(os.environ.get("MAX_DOCKER_WORKERS", "8"))
HOST_PROC = os.environ.get('HOST_PROC', '/proc')
HOST_SYS = os.environ.get('HOST_SYS', '/sys')
HOST_ETC = os.environ.get('HOST_ETC', '/etc')
API_CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("API_CORS_ORIGINS", "").split(",")
    if origin.strip()
]

# CPU usage tracking
_prev_cpu = None

def read_file(path: str) -> str:
    try:
        with open(path, 'r') as f:
            return f.read().strip()
    except Exception as e:
        logging.warning(f"Cannot read {path}: {e}")
        return ""

def get_cpu_usage() -> float:
    global _prev_cpu
    try:
        with open(f'{HOST_PROC}/stat', 'r') as f:
            line = f.readline()
        parts = line.split()
        idle = int(parts[4])
        total = sum(int(p) for p in parts[1:8])
        
        if _prev_cpu is None:
            _prev_cpu = (idle, total)
            return 0.0
        
        prev_idle, prev_total = _prev_cpu
        _prev_cpu = (idle, total)
        
        idle_delta = idle - prev_idle
        total_delta = total - prev_total
        
        if total_delta == 0:
            return 0.0
        
        return round((1 - idle_delta / total_delta) * 100, 1)
    except Exception as e:
        logging.warning(f"CPU error: {e}")
        return 0.0

def get_memory_info() -> Dict[str, Any]:
    try:
        mem = {}
        with open(f'{HOST_PROC}/meminfo', 'r') as f:
            for line in f:
                parts = line.split()
                key = parts[0].rstrip(':')
                mem[key] = int(parts[1])
        
        total = mem.get('MemTotal', 0) // 1024
        available = mem.get('MemAvailable', 0) // 1024
        used = total - available
        percent = round((used / total) * 100, 1) if total > 0 else 0
        
        # Swap info
        swap_total = mem.get('SwapTotal', 0) // 1024
        swap_free = mem.get('SwapFree', 0) // 1024
        swap_used = swap_total - swap_free
        swap_percent = round((swap_used / swap_total) * 100, 1) if swap_total > 0 else 0
        
        return {
            "total_mb": total,
            "used_mb": used,
            "available_mb": available,
            "usage_percent": percent,
            "swap_total_mb": swap_total,
            "swap_used_mb": swap_used,
            "swap_percent": swap_percent,
            "available": True
        }
    except Exception as e:
        logging.warning(f"Memory error: {e}")
        return {"total_mb": 0, "used_mb": 0, "available_mb": 0, "usage_percent": 0, "swap_total_mb": 0, "swap_used_mb": 0, "swap_percent": 0, "available": False}

def get_disk_info() -> Dict[str, Any]:
    """Get disk usage for root filesystem"""
    try:
        # /host/proc/1/root resolves to the host root when /proc is mounted in.
        disk_path = f"{HOST_PROC}/1/root" if os.path.exists(f"{HOST_PROC}/1/root") else "/"
        
        stat = os.statvfs(disk_path)
        total = (stat.f_blocks * stat.f_frsize) // (1024 * 1024 * 1024)  # GB
        free = (stat.f_bavail * stat.f_frsize) // (1024 * 1024 * 1024)   # GB
        used = total - free
        percent = round((used / total) * 100, 1) if total > 0 else 0
        
        return {
            "total_gb": total,
            "used_gb": used,
            "free_gb": free,
            "usage_percent": percent,
            "available": True
        }
    except Exception as e:
        logging.warning(f"Disk error: {e}")
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "usage_percent": 0, "available": False}

def get_cpu_info() -> Dict[str, Any]:
    cores = os.cpu_count() or 4
    freq = 1500
    try:
        freq_str = read_file(f'{HOST_SYS}/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq')
        if freq_str:
            freq = int(freq_str) // 1000
    except:
        pass
    return {"cores": cores, "frequency_mhz": freq}

def get_temperature() -> Dict[str, Any]:
    try:
        temp_str = read_file(f'{HOST_SYS}/class/thermal/thermal_zone0/temp')
        if temp_str:
            return {"celsius": round(int(temp_str) / 1000, 1), "available": True}
    except Exception as e:
        logging.warning(f"Temp error: {e}")
    return {"celsius": 0.0, "available": False}

def get_load_average() -> Dict[str, Any]:
    try:
        with open(f'{HOST_PROC}/loadavg', 'r') as f:
            load = f.read().strip().split()
        return {"1min": float(load[0]), "5min": float(load[1]), "15min": float(load[2]), "available": True}
    except Exception as e:
        logging.warning(f"Load error: {e}")
        return {"1min": 0.0, "5min": 0.0, "15min": 0.0, "available": False}

def get_uptime() -> float:
    try:
        with open(f'{HOST_PROC}/uptime', 'r') as f:
            uptime_str = f.read().strip().split()[0]
        return round(float(uptime_str) / 3600, 1)
    except:
        return 0.0

def get_hostname() -> str:
    """Get actual host hostname from /etc/hostname"""
    try:
        hostname = read_file(f'{HOST_ETC}/hostname')
        if hostname:
            return hostname
    except:
        pass
    return socket.gethostname()

def get_process_count() -> int:
    """Count running processes"""
    try:
        count = 0
        proc_dir = HOST_PROC
        for entry in os.listdir(proc_dir):
            if entry.isdigit():
                count += 1
        return count
    except:
        return 0

def docker_request(endpoint: str, timeout: float = 2.0) -> Any:
    """Query Docker API with timeout"""
    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"

    try:
        if DOCKER_API_BASE.startswith("http://") or DOCKER_API_BASE.startswith("https://"):
            url = f"{DOCKER_API_BASE.rstrip('/')}{endpoint}"
            with urlrequest.urlopen(url, timeout=timeout) as response:
                body = response.read()
            return json.loads(body.decode('utf-8', errors='ignore'))

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        socket_path = DOCKER_API_BASE.removeprefix("unix://")
        sock.connect(socket_path)
        
        request = f"GET {endpoint} HTTP/1.0\r\nHost: localhost\r\n\r\n"
        sock.send(request.encode())
        
        response = b""
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            except socket.timeout:
                break
        sock.close()
        
        if b"\r\n\r\n" in response:
            body = response.split(b"\r\n\r\n", 1)[1]
            return json.loads(body.decode('utf-8', errors='ignore'))
    except Exception as e:
        logging.warning(f"Docker API error: {e}")
    return None

def build_container_metrics(c: Dict[str, Any], host_total_mem_bytes: int = 0) -> Dict[str, Any]:
    name = c.get('Names', ['/unknown'])[0].lstrip('/')
    state = c.get('State', 'unknown')
    full_container_id = c.get('Id', '')
    container_id = full_container_id[:12]
    created = c.get('Created', 0)

    uptime_seconds = 0
    if state == "running" and created:
        uptime_seconds = int(datetime.now(timezone.utc).timestamp() - created)

    list_health = c.get('Health') if isinstance(c.get('Health'), dict) else {}
    container = {
        "id": container_id,
        "name": name,
        "status": state,
        "health": {"status": list_health.get('Status', 'none')},
        "stats": {"available": False},
        "cpu": {"usage_percent": None, "available": False},
        "memory": {"usage_mb": None, "limit_mb": None, "usage_percent": None, "available": False},
        "network": {"rx_bytes": 0, "tx_bytes": 0, "rx_rate_kbps": 0.0, "tx_rate_kbps": 0.0},
        "uptime_seconds": uptime_seconds,
        "restart_count": 0
    }

    if state != "running":
        return container

    stats = docker_request(
        f"/containers/{full_container_id}/stats?stream=false",
        timeout=DOCKER_STATS_TIMEOUT
    )
    if not stats:
        stats = docker_request(
            f"/containers/{full_container_id}/stats?stream=false&one-shot=true",
            timeout=DOCKER_STATS_TIMEOUT
        )
    if stats:
        container['stats']['available'] = True
        try:
            cpu_stats = stats.get('cpu_stats', {})
            precpu_stats = stats.get('precpu_stats', {})
            cpu_usage = cpu_stats.get('cpu_usage', {})
            precpu_usage = precpu_stats.get('cpu_usage', {})
            cpu_delta = cpu_usage.get('total_usage', 0) - precpu_usage.get('total_usage', 0)
            system_delta = cpu_stats.get('system_cpu_usage', 0) - precpu_stats.get('system_cpu_usage', 0)
            num_cpus = cpu_stats.get('online_cpus') or len(cpu_usage.get('percpu_usage') or []) or 1
            if cpu_delta >= 0 and system_delta > 0:
                container['cpu']['usage_percent'] = round((cpu_delta / system_delta) * num_cpus * 100, 1)
                container['cpu']['available'] = True
        except Exception:
            pass

        try:
            mem_stats = stats.get('memory_stats', {})
            stats_detail = mem_stats.get('stats', {})
            mem_usage = mem_stats.get('usage')
            if mem_usage is None:
                mem_usage = mem_stats.get('rss') or stats_detail.get('anon') or stats_detail.get('total_rss')
            else:
                inactive_file = stats_detail.get('inactive_file') or stats_detail.get('total_inactive_file') or 0
                adjusted_usage = mem_usage - inactive_file
                if adjusted_usage > 0:
                    mem_usage = adjusted_usage

            mem_limit = mem_stats.get('limit', 0)
            if mem_limit == 0 or mem_limit > 10**14:
                # Container ohne eigenes Memory-Limit darf den ganzen Host nutzen.
                # host_total_mem_bytes wird einmal pro Request ermittelt und
                # durchgereicht, statt /proc/meminfo je Container neu zu parsen.
                mem_limit = host_total_mem_bytes

            if mem_usage is not None:
                container['memory']['usage_mb'] = mem_usage // (1024 * 1024)
                container['memory']['limit_mb'] = mem_limit // (1024 * 1024) if mem_limit else None
                if mem_limit > 0:
                    container['memory']['usage_percent'] = round((mem_usage / mem_limit) * 100, 1)
                container['memory']['available'] = True
        except Exception as e:
            logging.warning(f"Memory parse error for {name}: {e}")

        try:
            for net in stats.get('networks', {}).values():
                container['network']['rx_bytes'] += net.get('rx_bytes', 0)
                container['network']['tx_bytes'] += net.get('tx_bytes', 0)
        except Exception:
            pass

    inspect = docker_request(f"/containers/{full_container_id}/json", timeout=DOCKER_INSPECT_TIMEOUT)
    if inspect:
        container['restart_count'] = inspect.get('RestartCount', 0)
        state_info = inspect.get('State', {})
        health_info = state_info.get('Health')
        if health_info:
            container['health']['status'] = health_info.get('Status', 'unknown')

    return container

def get_containers() -> List[Dict[str, Any]]:
    containers, _ = get_containers_with_status()
    return containers

def get_containers_with_status() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    containers = []
    docker_status = {
        "reachable": False,
        "container_count": 0,
        "running_count": 0,
        "stats_available_count": 0,
        "stats_unavailable_count": 0,
        "health_healthy_count": 0,
        "health_unhealthy_count": 0,
        "health_none_count": 0,
        "warnings": []
    }

    try:
        data = docker_request("/containers/json?all=true")
        if data is None:
            docker_status["warnings"].append("Docker API nicht erreichbar")
            return containers, docker_status

        docker_status["reachable"] = True
        if not data:
            return containers, docker_status

        # Host-Gesamtspeicher einmal pro Request ermitteln und als Fallback fuer
        # Container ohne eigenes Memory-Limit an alle Worker durchreichen.
        host_total_mem_bytes = get_memory_info()['total_mb'] * 1024 * 1024

        worker_count = max(1, min(MAX_DOCKER_WORKERS, len(data)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {executor.submit(build_container_metrics, c, host_total_mem_bytes): c for c in data}
            for future in as_completed(future_map):
                try:
                    containers.append(future.result())
                except Exception as e:
                    logging.warning(f"Container metrics worker error: {e}")
    except Exception as e:
        logging.error(f"Container error: {e}")
        docker_status["warnings"].append("Container-Metriken konnten nicht gelesen werden")

    running_containers = [c for c in containers if c.get("status") == "running"]
    docker_status["container_count"] = len(containers)
    docker_status["running_count"] = len(running_containers)
    docker_status["stats_available_count"] = sum(1 for c in running_containers if c.get("stats", {}).get("available"))
    docker_status["stats_unavailable_count"] = max(
        0,
        docker_status["running_count"] - docker_status["stats_available_count"]
    )
    docker_status["health_healthy_count"] = sum(1 for c in containers if c.get("health", {}).get("status") == "healthy")
    docker_status["health_unhealthy_count"] = sum(1 for c in containers if c.get("health", {}).get("status") == "unhealthy")
    docker_status["health_none_count"] = sum(1 for c in containers if c.get("health", {}).get("status") == "none")

    if docker_status["stats_unavailable_count"] > 0:
        docker_status["warnings"].append("Docker Stats teilweise nicht verfuegbar")
    if docker_status["health_unhealthy_count"] > 0:
        docker_status["warnings"].append("Mindestens ein Container ist unhealthy")

    return containers, docker_status

def build_host_metrics() -> Dict[str, Any]:
    """Host-Metriken einsammeln und Lesefehler transparent als warnings melden.

    Jeder Getter liefert ein `available`-Flag. Schlaegt ein Read fehl, bleibt
    der numerische Wert zwar 0, wird aber ueber `available=False` und die
    aggregierte `warnings`-Liste als ungueltig markiert - so kann das Dashboard
    echte Nullen von Lesefehlern unterscheiden.
    """
    cpu_info = get_cpu_info()
    memory = get_memory_info()
    disk = get_disk_info()
    load = get_load_average()
    temperature = get_temperature()

    warnings = []
    if not memory.get("available", True):
        warnings.append("Host-Speicher nicht lesbar")
    if not disk.get("available", True):
        warnings.append("Host-Disk nicht lesbar")
    if not load.get("available", True):
        warnings.append("Load Average nicht lesbar")
    if not temperature.get("available", True):
        warnings.append("Temperatur nicht lesbar")

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cpu": {"usage_percent": get_cpu_usage(), "cores": cpu_info["cores"], "frequency_mhz": cpu_info["frequency_mhz"]},
        "memory": memory,
        "disk": disk,
        "load_average": load,
        "temperature": temperature,
        "uptime_hours": get_uptime(),
        "process_count": get_process_count(),
        "hostname": get_hostname(),
        "warnings": warnings
    }

@api_router.get("/")
async def root():
    return {"message": "Linux Monitor API", "status": "live"}

@api_router.get("/metrics/host")
def get_host_metrics():
    return build_host_metrics()

@api_router.get("/metrics/containers")
def get_container_metrics():
    return get_containers()

@api_router.get("/metrics/all")
def get_all_metrics():
    containers, docker_status = get_containers_with_status()
    return {
        "host": build_host_metrics(),
        "containers": containers,
        "docker": docker_status
    }

app.include_router(api_router)

if API_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_credentials=False,
        allow_origins=API_CORS_ORIGINS,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
