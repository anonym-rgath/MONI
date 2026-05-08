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
from typing import List, Dict, Any
from datetime import datetime, timezone

app = FastAPI(title="Pi Monitor API")
api_router = APIRouter(prefix="/api")

DOCKER_SOCKET = "/var/run/docker.sock"
DOCKER_API_BASE = os.environ.get("DOCKER_API_BASE", f"unix://{DOCKER_SOCKET}")
DOCKER_STATS_TIMEOUT = float(os.environ.get("DOCKER_STATS_TIMEOUT", "1.5"))
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
            "swap_percent": swap_percent
        }
    except Exception as e:
        logging.warning(f"Memory error: {e}")
        return {"total_mb": 0, "used_mb": 0, "available_mb": 0, "usage_percent": 0, "swap_total_mb": 0, "swap_used_mb": 0, "swap_percent": 0}

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
            "usage_percent": percent
        }
    except Exception as e:
        logging.warning(f"Disk error: {e}")
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "usage_percent": 0}

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

def get_temperature() -> float:
    try:
        temp_str = read_file(f'{HOST_SYS}/class/thermal/thermal_zone0/temp')
        if temp_str:
            return round(int(temp_str) / 1000, 1)
    except:
        pass
    return 0.0

def get_load_average() -> Dict[str, float]:
    try:
        with open(f'{HOST_PROC}/loadavg', 'r') as f:
            load = f.read().strip().split()
        return {"1min": float(load[0]), "5min": float(load[1]), "15min": float(load[2])}
    except Exception as e:
        logging.warning(f"Load error: {e}")
        return {"1min": 0.0, "5min": 0.0, "15min": 0.0}

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

def build_container_metrics(c: Dict[str, Any]) -> Dict[str, Any]:
    name = c.get('Names', ['/unknown'])[0].lstrip('/')
    state = c.get('State', 'unknown')
    container_id = c.get('Id', '')[:12]
    created = c.get('Created', 0)

    uptime_seconds = 0
    if state == "running" and created:
        uptime_seconds = int(datetime.now(timezone.utc).timestamp() - created)

    container = {
        "id": container_id,
        "name": name,
        "status": state,
        "cpu": {"usage_percent": 0.0},
        "memory": {"usage_mb": 0, "limit_mb": 0, "usage_percent": 0.0},
        "network": {"rx_bytes": 0, "tx_bytes": 0, "rx_rate_kbps": 0.0, "tx_rate_kbps": 0.0},
        "uptime_seconds": uptime_seconds,
        "restart_count": 0
    }

    if state != "running":
        return container

    stats = docker_request(
        f"/containers/{container_id}/stats?stream=false&one-shot=true",
        timeout=DOCKER_STATS_TIMEOUT
    )
    if stats:
        try:
            cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - stats['precpu_stats']['cpu_usage']['total_usage']
            system_delta = stats['cpu_stats']['system_cpu_usage'] - stats['precpu_stats']['system_cpu_usage']
            num_cpus = stats['cpu_stats'].get('online_cpus', 1)
            if system_delta > 0:
                container['cpu']['usage_percent'] = round((cpu_delta / system_delta) * num_cpus * 100, 1)
        except Exception:
            pass

        try:
            mem_stats = stats.get('memory_stats', {})
            mem_usage = mem_stats.get('usage', 0)
            stats_detail = mem_stats.get('stats', {})
            if stats_detail:
                inactive_file = stats_detail.get('inactive_file', 0)
                cache = stats_detail.get('cache', 0)
                mem_usage = max(0, mem_usage - inactive_file - cache)

            if mem_usage == 0:
                mem_usage = mem_stats.get('rss', 0)
            if mem_usage == 0:
                mem_usage = stats_detail.get('anon', 0)

            mem_limit = mem_stats.get('limit', 0)
            if mem_limit == 0 or mem_limit > 10**14:
                mem_limit = get_memory_info()['total_mb'] * 1024 * 1024

            container['memory']['usage_mb'] = mem_usage // (1024 * 1024)
            container['memory']['limit_mb'] = mem_limit // (1024 * 1024)
            if mem_limit > 0:
                container['memory']['usage_percent'] = round((mem_usage / mem_limit) * 100, 1)
        except Exception as e:
            logging.warning(f"Memory parse error for {name}: {e}")

        try:
            for net in stats.get('networks', {}).values():
                container['network']['rx_bytes'] += net.get('rx_bytes', 0)
                container['network']['tx_bytes'] += net.get('tx_bytes', 0)
        except Exception:
            pass

    inspect = docker_request(f"/containers/{container_id}/json", timeout=DOCKER_INSPECT_TIMEOUT)
    if inspect:
        container['restart_count'] = inspect.get('RestartCount', 0)

    return container

def get_containers() -> List[Dict[str, Any]]:
    containers = []
    try:
        data = docker_request("/containers/json?all=true")
        if not data:
            return containers

        worker_count = max(1, min(MAX_DOCKER_WORKERS, len(data)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {executor.submit(build_container_metrics, c): c for c in data}
            for future in as_completed(future_map):
                try:
                    containers.append(future.result())
                except Exception as e:
                    logging.warning(f"Container metrics worker error: {e}")
    except Exception as e:
        logging.error(f"Container error: {e}")
    
    return containers

@api_router.get("/")
async def root():
    return {"message": "Pi Monitor API", "status": "live"}

@api_router.get("/metrics/host")
async def get_host_metrics():
    cpu_info = get_cpu_info()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cpu": {"usage_percent": get_cpu_usage(), "cores": cpu_info["cores"], "frequency_mhz": cpu_info["frequency_mhz"]},
        "memory": get_memory_info(),
        "disk": get_disk_info(),
        "load_average": get_load_average(),
        "temperature": {"celsius": get_temperature()},
        "uptime_hours": get_uptime(),
        "process_count": get_process_count(),
        "hostname": get_hostname()
    }

@api_router.get("/metrics/containers")
async def get_container_metrics():
    return get_containers()

@api_router.get("/metrics/all")
async def get_all_metrics():
    cpu_info = get_cpu_info()
    return {
        "host": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cpu": {"usage_percent": get_cpu_usage(), "cores": cpu_info["cores"], "frequency_mhz": cpu_info["frequency_mhz"]},
            "memory": get_memory_info(),
            "disk": get_disk_info(),
            "load_average": get_load_average(),
            "temperature": {"celsius": get_temperature()},
            "uptime_hours": get_uptime(),
            "process_count": get_process_count(),
            "hostname": get_hostname()
        },
        "containers": get_containers()
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
