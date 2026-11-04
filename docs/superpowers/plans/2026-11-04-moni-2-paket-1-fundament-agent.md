# Moni 2.0 – Paket 1: Fundament + Agent – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Den heutigen Ein-Datei-Monitor zum eigenständigen, tokengeschützten **Moni-Agent** umbauen: zentraler Sammler mit Ringpuffer, korrigierte und erweiterte Messung, versionierte API v1 und `/metrics`, startbar per `compose.agent.yaml`.

**Architecture:** `backend/server.py` wird durch das Paket `backend/moni/` ersetzt. Ein Hintergrund-Thread (`Collector`) misst alle 15 s Host und Container. Er berechnet CPU-Last und Raten aus der Differenz zweier Messungen und legt jeden Snapshot in einen Ringpuffer. Die FastAPI-App (`create_app`) liest nur noch aus dem Puffer, misst also nie pro Anfrage. Tokens liegen gehasht in `/data/tokens.json`.

**Tech Stack:** Python 3.14, FastAPI, uvicorn, nur Standardbibliothek für Tokens, Logs und Prometheus-Text. Docker Compose mit `tecnativa/docker-socket-proxy`.

**Spec:** [`docs/superpowers/specs/2026-11-04-moni-agent-manager-design.md`](../specs/2026-11-04-moni-agent-manager-design.md) · Übersicht: [`2026-11-04-moni-2-uebersicht.md`](2026-11-04-moni-2-uebersicht.md)

## Global Constraints

- Branch `feature/moni-2`, abgezweigt von `main`. Kein Push und kein Merge ohne Freigabe des Auftraggebers.
- Python **3.14** (Laufzeit-Image und CI). Lokale Tests: `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests -v`
- Keine neuen Laufzeit-Abhängigkeiten in diesem Paket (`requirements-docker.txt` bleibt bei `fastapi`, `uvicorn`).
- Unbekannte Werte sind `null` + `available: false`, **nie** Ersatzwerte (kein `1500` MHz, kein `0 %` beim ersten Messen).
- Feldnamen tragen Einheiten (`_bytes`, `_percent`, `_seconds`, `_celsius`, `_per_second`). Es werden Rohwerte geliefert, nicht auf MB/GB vorgerundet.
- Zeitstempel: UTC, ISO 8601 mit `Z`, z. B. `2026-11-04T12:00:00Z`. Die Funktion dafür ist `moni.timeutil.iso(dt)` aus Task 1.
- API-Präfix `/api/v1`. Fehlerantworten sind `application/problem+json` nach RFC 9457.
- Agent strikt lesend; Container non-root, `read_only`, `cap_drop: ALL`, `no-new-privileges`. Docker nur über Socket-Proxy mit `POST=0`.
- Geheimnisse (Tokens, `Authorization`-Header) erscheinen nie in Logs.
- Standardwerte: Sammel-Takt **15 s**, Ringpuffer **7200 s**, Agent-Port **9810**, Rate-Limit **120 Anfragen/Minute** pro Client-IP.
- `MONI_TLS` kennt in diesem Paket nur `off`. TLS folgt in Paket 4, und dort wird der Standard auf `on` gestellt.
- Texte für Menschen (Warnungen, Fehlermeldungen): Deutsch mit korrekten Umlauten.

## Review Focus

1. **Docker nicht erreichbar oder hängt.** Der Snapshot liefert trotzdem die Host-Daten mit `docker.reachable=false` und der Warnung `"Docker API nicht erreichbar"`, und der Sammler läuft weiter. Test in Task 3 und Task 4.
2. **Zähler springen zurück** (Container-Neustart, Interface-Reset). Die Rate ist dann `null` und nie negativ. Test in Task 4.
3. **`since` fehlt, ist ungültig, liegt in der Zukunft oder ist älter als der Puffer.** Fehlt `since` oder ist es ungültig, antwortet die API mit 422 Problem-JSON. Liegt es in der Zukunft, kommt eine leere Liste. Ist es älter als der Puffer, kommen alle vorhandenen Punkte plus `oldest`, damit der Manager die Lücke erkennt. Test in Task 4 und Task 6.
4. **`tokens.json` ist kaputt, fehlt oder ist nicht beschreibbar.** Der Agent startet trotzdem. Jede authentisierte Anfrage bekommt 401 (fail-closed). `create` überschreibt eine kaputte Datei **nicht**. Test in Task 5.
5. **Eine Messung wirft mitten im Zyklus eine Ausnahme.** Der Thread läuft weiter, der Fehler wird geloggt. Vor der ersten erfolgreichen Messung antwortet `/snapshot` mit 503. Test in Task 4 und Task 6.

---

### Task 1: Paket-Gerüst, Einstellungen, Zeit- und Log-Helfer

**Files:**
- Create: `backend/moni/__init__.py`, `backend/moni/config.py`, `backend/moni/timeutil.py`, `backend/moni/logs.py`
- Create: `backend/tests/test_config.py`
- Modify: `.github/workflows/ci.yml` (Job `backend-tests`: `python-version: "3.14"`)

**Interfaces:**
- Produces:
  - `moni.__version__: str = "2.0.0.dev0"`
  - `moni.config.Settings` (frozen dataclass) mit den Feldern `role: str`, `host_proc: str`, `host_sys: str`, `host_etc: str`, `host_root: str`, `docker_api_base: str`, `docker_stats_timeout: float`, `docker_inspect_timeout: float`, `max_docker_workers: int`, `collect_interval_seconds: int`, `buffer_seconds: int`, `data_dir: str`, `tls: str`, `port: int`, `rate_limit_per_minute: int`, `cors_origins: tuple[str, ...]`
  - `Settings.from_env(env: Mapping[str, str] = os.environ) -> Settings`
  - `moni.timeutil.utcnow() -> datetime` (tz-aware UTC), `iso(dt: datetime) -> str`, `parse_iso(s: str) -> datetime` (wirft `ValueError`)
  - `moni.logs.setup_logging(level: str = "INFO") -> None`, `moni.logs.JsonFormatter`

Zuordnung Env → Feld und Standardwert:

| Env | Feld | Standard |
|---|---|---|
| `MONI_ROLE` | role | `agent` (erlaubt: `agent`, `manager`) |
| `HOST_PROC` / `HOST_SYS` / `HOST_ETC` / `HOST_ROOT` | host_* | `/proc` / `/sys` / `/etc` / `/` |
| `DOCKER_API_BASE` | docker_api_base | `unix:///var/run/docker.sock` |
| `DOCKER_STATS_TIMEOUT` / `DOCKER_INSPECT_TIMEOUT` / `MAX_DOCKER_WORKERS` | … | `2.5` / `1.0` / `8` |
| `MONI_COLLECT_INTERVAL` | collect_interval_seconds | `15` |
| `MONI_BUFFER_SECONDS` | buffer_seconds | `7200` |
| `MONI_DATA_DIR` | data_dir | `/data` |
| `MONI_TLS` | tls | `off` (erlaubt in diesem Paket: nur `off`) |
| `MONI_PORT` | port | `9810` |
| `MONI_RATE_LIMIT_PER_MINUTE` | rate_limit_per_minute | `120` |
| `API_CORS_ORIGINS` | cors_origins | leer, kommagetrennt |

- [ ] **Step 1: Branch anlegen**

```bash
git switch -c feature/moni-2
```

- [ ] **Step 2: Failing tests schreiben** – `backend/tests/test_config.py`

```python
import pytest
from datetime import datetime, timezone
from moni.config import Settings
from moni import timeutil, logs
import json, logging

def test_defaults():
    s = Settings.from_env({})
    assert s.role == "agent" and s.port == 9810
    assert s.collect_interval_seconds == 15 and s.buffer_seconds == 7200
    assert s.tls == "off" and s.rate_limit_per_minute == 120
    assert s.docker_api_base == "unix:///var/run/docker.sock"
    assert s.cors_origins == ()

def test_env_overrides():
    s = Settings.from_env({"MONI_COLLECT_INTERVAL": "5", "MONI_BUFFER_SECONDS": "60",
                           "API_CORS_ORIGINS": "https://a, https://b"})
    assert s.collect_interval_seconds == 5 and s.buffer_seconds == 60
    assert s.cors_origins == ("https://a", "https://b")

@pytest.mark.parametrize("env", [
    {"MONI_ROLE": "x"}, {"MONI_TLS": "on"}, {"MONI_COLLECT_INTERVAL": "0"},
    {"MONI_COLLECT_INTERVAL": "30", "MONI_BUFFER_SECONDS": "10"},
])
def test_invalid_env_raises(env):
    with pytest.raises(ValueError):
        Settings.from_env(env)

def test_iso_roundtrip():
    dt = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
    assert timeutil.iso(dt) == "2026-11-04T12:00:00Z"
    assert timeutil.parse_iso("2026-11-04T12:00:00Z") == dt
    assert timeutil.parse_iso("2026-11-04T13:00:00+01:00") == dt
    with pytest.raises(ValueError):
        timeutil.parse_iso("gestern")
    with pytest.raises(ValueError):
        timeutil.parse_iso("2026-11-04T12:00:00")  # ohne Zeitzone

def test_json_formatter():
    rec = logging.LogRecord("moni", logging.WARNING, __file__, 1, "Hallo %s", ("Welt",), None)
    out = json.loads(logs.JsonFormatter().format(rec))
    assert out["level"] == "WARNING" and out["msg"] == "Hallo Welt" and out["logger"] == "moni"
    assert out["ts"].endswith("Z")
```

- [ ] **Step 3: Test ausführen, er muss fehlschlagen**

Run: `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests/test_config.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'moni'`

- [ ] **Step 4: Implementieren** – `config.py`, `timeutil.py`, `logs.py`, `__init__.py` mit den Signaturen oben

`from_env` prüft auf: `role` in {agent, manager}, `tls == "off"`, `collect_interval_seconds >= 1`, `buffer_seconds >= collect_interval_seconds`. `iso` arbeitet sekundengenau, ohne Mikrosekunden. `JsonFormatter` gibt die Schlüssel `ts`, `level`, `logger`, `msg` aus und bei Exceptions zusätzlich `exc`.

- [ ] **Step 5: Tests ausführen, sie müssen bestehen**

Gleicher Befehl. Expected: PASS (auch die bestehenden `test_server.py`-Tests bleiben grün).

- [ ] **Step 6: CI auf Python 3.14 umstellen und committen**

```bash
git add backend/moni backend/tests/test_config.py .github/workflows/ci.yml
git commit -m "feat(agent): Paketgeruest, Settings, Zeit- und JSON-Log-Helfer"
```

---

### Task 2: Host-Messung (`moni/collect/host.py`)

Portiert und korrigiert die Host-Funktionen aus `server.py`. Jede Funktion ist rein lesend und wirft nie. Fehler zeigen sich als `None` bzw. `available: False`.

**Files:**
- Create: `backend/moni/collect/__init__.py`, `backend/moni/collect/host.py`
- Create: `backend/tests/test_host.py`

**Interfaces:**
- Produces (alle in `moni.collect.host`):
  - `read_cpu_times(proc: str) -> tuple[int, int] | None`: `(idle, total)` aus der ersten Zeile von `{proc}/stat`. `idle` = Spalte idle + iowait, `total` = Summe user..steal (8 Werte).
  - `cpu_percent(prev: tuple[int, int] | None, cur: tuple[int, int] | None) -> float | None`: auf 1 Nachkommastelle, `None` bei fehlendem Wert oder `total_delta <= 0`
  - `read_cpu_info(sys: str) -> dict`: `{"cores": int, "frequency_mhz": int | None}`
  - `read_memory(proc: str) -> dict`: `total_bytes, used_bytes, available_bytes, usage_percent, swap_total_bytes, swap_used_bytes, swap_percent, available`
  - `read_load(proc: str) -> dict`: `load1, load5, load15, available`
  - `read_uptime_seconds(proc: str) -> float | None`
  - `read_process_count(proc: str) -> int | None`
  - `read_hostname(etc: str) -> str`: Fallback `socket.gethostname()`
  - `read_temperatures(sys: str) -> dict`: `{"max_celsius": float | None, "zones": [{"name": str, "celsius": float}], "available": bool}`. Liest alle `thermal_zone*`, der Name kommt aus `type`.
  - `read_net_counters(proc: str) -> dict[str, tuple[int, int]] | None`: aus `{proc}/1/net/dev`, `iface -> (rx_bytes, tx_bytes)`. Ausgelassen werden `lo`, `veth*`, `docker0`, `br-*`.
  - `read_filesystems(proc: str, root: str) -> list[dict] | None`: aus `{proc}/1/mounts`, `statvfs` auf `os.path.join(root, mountpoint.lstrip("/"))`. Einträge: `mountpoint, device, fstype, total_bytes, used_bytes, free_bytes, usage_percent`.
  - `PSEUDO_FS: frozenset[str]` (u. a. `proc, sysfs, tmpfs, devtmpfs, devpts, overlay, cgroup, cgroup2, mqueue, nsfs, squashfs, tracefs, debugfs, securityfs, pstore, bpf, autofs, fusectl, configfs, binfmt_misc, hugetlbfs, rpc_pipefs, ramfs`) und `SKIP_MOUNT_PREFIXES = ("/proc", "/sys", "/dev", "/run", "/var/lib/docker", "/snap")`

- [ ] **Step 1: Failing tests schreiben** – `backend/tests/test_host.py`. Helfer `write(path, content)` aus dem bestehenden `test_server.py` übernehmen.

```python
from moni.collect import host

def test_cpu_times_and_percent(tmp_path):
    write(tmp_path / "stat", "cpu  100 0 100 700 0 0 0 0 0 0\n")
    assert host.read_cpu_times(str(tmp_path)) == (700, 900)
    assert host.cpu_percent((700, 900), (1400, 1800)) == 22.2
    assert host.cpu_percent(None, (1400, 1800)) is None
    assert host.cpu_percent((700, 900), (700, 900)) is None

def test_cpu_info_without_cpufreq_is_none(tmp_path):
    info = host.read_cpu_info(str(tmp_path))
    assert info["frequency_mhz"] is None and info["cores"] >= 1

def test_memory_bytes(tmp_path):
    write(tmp_path / "meminfo", "MemTotal: 8000000 kB\nMemAvailable: 6000000 kB\n"
                                "SwapTotal: 1000000 kB\nSwapFree: 800000 kB\n")
    m = host.read_memory(str(tmp_path))
    assert m["total_bytes"] == 8_192_000_000 and m["available_bytes"] == 6_144_000_000
    assert m["used_bytes"] == 2_048_000_000 and m["usage_percent"] == 25.0
    assert m["swap_total_bytes"] == 1_024_000_000 and m["swap_used_bytes"] == 204_800_000
    assert m["swap_percent"] == 20.0 and m["available"] is True

def test_memory_missing_is_none(tmp_path):
    m = host.read_memory(str(tmp_path))
    assert m["available"] is False and m["total_bytes"] is None and m["usage_percent"] is None

def test_load_uptime_processes(tmp_path):
    write(tmp_path / "loadavg", "0.50 0.75 1.00 1/234 5678\n")
    write(tmp_path / "uptime", "7200.55 100.0\n")
    (tmp_path / "1").mkdir(); (tmp_path / "42").mkdir(); (tmp_path / "self").mkdir()
    assert host.read_load(str(tmp_path)) == {"load1": 0.5, "load5": 0.75, "load15": 1.0, "available": True}
    assert host.read_uptime_seconds(str(tmp_path)) == 7200.55
    assert host.read_process_count(str(tmp_path)) == 2

def test_temperatures_all_zones(tmp_path):
    for i, (typ, milli) in enumerate([("cpu-thermal", "52500"), ("gpu", "61000")]):
        write(tmp_path / f"class/thermal/thermal_zone{i}/type", typ)
        write(tmp_path / f"class/thermal/thermal_zone{i}/temp", milli)
    t = host.read_temperatures(str(tmp_path))
    assert t["max_celsius"] == 61.0 and t["available"] is True
    assert {"name": "cpu-thermal", "celsius": 52.5} in t["zones"]

def test_temperatures_none(tmp_path):
    assert host.read_temperatures(str(tmp_path)) == {"max_celsius": None, "zones": [], "available": False}

def test_net_counters_filters_virtual(tmp_path):
    write(tmp_path / "1/net/dev",
          "Inter-|   Receive  |  Transmit\n face |bytes packets|bytes\n"
          "    lo: 100 1 0 0 0 0 0 0 100 1 0 0 0 0 0 0\n"
          "  eth0: 5000 10 0 0 0 0 0 0 3000 8 0 0 0 0 0 0\n"
          "vethab: 1 1 0 0 0 0 0 0 1 1 0 0 0 0 0 0\n"
          "docker0: 1 1 0 0 0 0 0 0 1 1 0 0 0 0 0 0\n")
    assert host.read_net_counters(str(tmp_path)) == {"eth0": (5000, 3000)}

def test_filesystems_filters_and_dedupes(tmp_path, monkeypatch):
    write(tmp_path / "1/mounts",
          "/dev/root / ext4 rw 0 0\nproc /proc proc rw 0 0\ntmpfs /run tmpfs rw 0 0\n"
          "/dev/sda1 /mnt/data ext4 rw 0 0\n"
          "overlay /var/lib/docker/overlay2/x/merged overlay rw 0 0\n"
          "/dev/root /home ext4 rw 0 0\n")
    class St: f_blocks, f_bfree, f_bavail, f_frsize = 1000, 400, 300, 4096
    seen = []
    monkeypatch.setattr(host.os, "statvfs", lambda p: seen.append(p) or St)
    fs = host.read_filesystems(str(tmp_path), "/host/root")
    assert [f["mountpoint"] for f in fs] == ["/", "/mnt/data"]
    assert seen == ["/host/root/", "/host/root/mnt/data"]
    assert fs[0] == {"mountpoint": "/", "device": "/dev/root", "fstype": "ext4",
                          "total_bytes": 4_096_000, "used_bytes": 2_457_600,
                          "free_bytes": 1_228_800, "usage_percent": 66.7}

def test_filesystems_unreadable_mounts_is_none(tmp_path):
    assert host.read_filesystems(str(tmp_path), "/") is None
```

- [ ] **Step 2: Tests ausführen, sie müssen fehlschlagen**

Run: `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests/test_host.py -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: `moni/collect/host.py` implementieren** mit den Signaturen oben

`usage_percent` der Dateisysteme wird wie bei `df` berechnet: `used / (used + f_bavail*f_frsize) * 100`. Schlägt `statvfs` für einen Mountpunkt fehl, wird dieser Eintrag übersprungen und auf DEBUG geloggt. Bei Dubletten auf demselben `device` gewinnt der erste Eintrag.

- [ ] **Step 4: Tests ausführen, sie müssen bestehen**

Gleicher Befehl. Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/moni/collect backend/tests/test_host.py
git commit -m "feat(agent): Host-Messung mit allen Dateisystemen, Netz und Temperaturzonen"
```

---

### Task 3: Docker-Messung (`moni/collect/docker.py`)

Portiert `docker_request`, `build_container_metrics` und `get_containers_with_status` und stellt dabei auf Rohwerte in Bytes um.

**Files:**
- Create: `backend/moni/collect/docker.py`
- Create: `backend/tests/test_docker.py`

**Interfaces:**
- Consumes: `Settings` (Task 1)
- Produces:
  - `class DockerClient(base: str)` mit `get(endpoint: str, timeout: float) -> Any | None` (HTTP oder Unix-Socket, wie bisher)
  - `container_metrics(client: DockerClient, c: dict, host_total_mem_bytes: int | None, stats_timeout: float, inspect_timeout: float) -> dict`, liefert die Schlüssel `id` (12 Zeichen), `full_id`, `name`, `image`, `status`, `health` (`healthy|unhealthy|starting|none|unknown`), `stats_available`, `cpu_percent`, `memory_usage_bytes`, `memory_limit_bytes`, `memory_percent`, `rx_bytes`, `tx_bytes`, `restart_count`, `uptime_seconds`. Nicht messbare Werte sind `None`.
  - `collect_containers(client: DockerClient, settings: Settings, host_total_mem_bytes: int | None) -> tuple[list[dict], dict, list[str]]`: `(containers, docker_status, warnings)`. `docker_status` = `{"reachable", "container_count", "running_count", "stats_available_count", "health": {"healthy", "unhealthy", "starting", "none"}}`.
  - Warnungstexte genau so: `"Docker API nicht erreichbar"`, `"Docker-Stats teilweise nicht verfügbar"`, `"Mindestens ein Container ist unhealthy"`

- [ ] **Step 1: Failing tests schreiben** – `backend/tests/test_docker.py`. Ein Fake-Client mit einer Map `endpoint-präfix -> antwort` ersetzt `DockerClient`.

```python
from moni.collect import docker
from moni.config import Settings

RUNNING = {"Id": "a" * 64, "Names": ["/web"], "Image": "nginx:1", "State": "running", "Created": 0}

class Fake:
    def __init__(self, routes): self.routes = routes
    def get(self, endpoint, timeout):
        for prefix, val in self.routes.items():
            if endpoint.startswith(prefix): return val
        return None

def stats(mem_usage=500 * 2**20, inactive=100 * 2**20, limit=2**30):
    return {"cpu_stats": {"cpu_usage": {"total_usage": 400}, "system_cpu_usage": 2000, "online_cpus": 2},
            "precpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 1000},
            "memory_stats": {"usage": mem_usage, "limit": limit, "stats": {"inactive_file": inactive}},
            "networks": {"eth0": {"rx_bytes": 10, "tx_bytes": 20}, "eth1": {"rx_bytes": 1, "tx_bytes": 2}}}

def test_running_container_metrics():
    c = docker.container_metrics(Fake({"/containers/" + "a" * 64 + "/stats": stats(),
                                       "/containers/" + "a" * 64 + "/json": {"RestartCount": 3, "State": {"Health": {"Status": "healthy"}}}}),
                                 RUNNING, 8 * 2**30, 2.5, 1.0)
    assert c["id"] == "a" * 12 and c["name"] == "web" and c["image"] == "nginx:1"
    assert c["cpu_percent"] == 40.0
    assert c["memory_usage_bytes"] == 400 * 2**20 and c["memory_limit_bytes"] == 2**30
    assert c["memory_percent"] == 39.1
    assert (c["rx_bytes"], c["tx_bytes"]) == (11, 22)
    assert c["restart_count"] == 3 and c["health"] == "healthy" and c["stats_available"] is True

def test_unlimited_container_uses_host_total():
    c = docker.container_metrics(Fake({"/containers/" + "a" * 64 + "/stats": stats(limit=0)}), RUNNING, 4 * 2**30, 2.5, 1.0)
    assert c["memory_limit_bytes"] == 4 * 2**30 and c["memory_percent"] == 9.8

def test_stopped_container_has_no_stats():
    c = docker.container_metrics(Fake({}), RUNNING | {"State": "exited"}, None, 2.5, 1.0)
    assert c["status"] == "exited" and c["stats_available"] is False
    assert c["cpu_percent"] is None and c["memory_usage_bytes"] is None and c["uptime_seconds"] is None

def test_collect_unreachable():
    cs, st, warn = docker.collect_containers(Fake({}), Settings.from_env({}), None)
    assert cs == [] and st["reachable"] is False and warn == ["Docker API nicht erreichbar"]

def test_collect_aggregates():
    routes = {"/containers/json": [RUNNING, RUNNING | {"Id": "b" * 64, "Names": ["/db"], "State": "exited"}],
              "/containers/" + "a" * 64 + "/json": {"State": {"Health": {"Status": "unhealthy"}}}}
    cs, st, warn = docker.collect_containers(Fake(routes), Settings.from_env({}), None)
    assert st["container_count"] == 2 and st["running_count"] == 1
    assert st["stats_available_count"] == 0 and st["health"]["unhealthy"] == 1
    assert "Docker-Stats teilweise nicht verfügbar" in warn
    assert "Mindestens ein Container ist unhealthy" in warn
```

- [ ] **Step 2: Tests ausführen, sie müssen fehlschlagen**

Run: `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests/test_docker.py -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: `moni/collect/docker.py` implementieren**

Die Rechenlogik für CPU und Speicher wird aus `server.py:261-309` übernommen, inklusive Abzug von `inactive_file` und Fallback auf den Host-Speicher bei `limit == 0` oder `limit > 10**14`. Ausgabe in Bytes. Die Container werden parallel per `ThreadPoolExecutor(max_workers=min(settings.max_docker_workers, n))` abgefragt. Wirft ein Worker, wird nur dieser Container verworfen und geloggt. `uptime_seconds` wird nur bei `running` gesetzt, sonst `None`.

- [ ] **Step 4: Tests ausführen, sie müssen bestehen**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/moni/collect/docker.py backend/tests/test_docker.py
git commit -m "feat(agent): Docker-Messung in Bytes mit Image und Health-Aufschluesselung"
```

---

### Task 4: Sammler mit Ringpuffer (`moni/collector.py`)

**Files:**
- Create: `backend/moni/collector.py`
- Create: `backend/tests/test_collector.py`

**Interfaces:**
- Consumes: `host.*` (Task 2), `docker.DockerClient`, `docker.collect_containers` (Task 3), `Settings`, `timeutil`
- Produces:
  - `SCHEMA_VERSION = 1`
  - `read_raw(settings: Settings, client: DockerClient) -> dict`: eine Rohmessung mit `taken_at` (datetime), `cpu_times`, `net` (Dict der Zähler), `host` (fertige Host-Blöcke ohne CPU-%/Raten), `containers`, `docker`, `warnings`
  - `derive(prev: dict | None, cur: dict) -> dict`: **reine Funktion**. Sie baut aus zwei Rohmessungen den Snapshot und ist ab hier der Vertrag für alle späteren Pakete:

```text
{ "schema_version": 1, "timestamp": "<iso Z>",
  "host": { "hostname", "uptime_seconds", "process_count",
            "cpu": {"usage_percent", "cores", "frequency_mhz", "available"},
            "load": {...read_load}, "memory": {...read_memory},
            "filesystems": [...] | null,
            "network": [{"interface", "rx_bytes", "tx_bytes", "rx_bytes_per_second", "tx_bytes_per_second"}] | null,
            "temperature": {...read_temperatures} },
  "containers": [ {...container_metrics ohne full_id, plus "rx_bytes_per_second", "tx_bytes_per_second"} ],
  "docker": {...docker_status}, "warnings": [str] }
```

  - `class Collector(settings: Settings, client: DockerClient | None = None)` mit:
    - `collect_once() -> dict`: misst, leitet ab, hängt an den Puffer an und gibt den Snapshot zurück
    - `latest() -> dict | None`
    - `history(since: datetime) -> tuple[list[dict], str | None]`: Punkte mit `timestamp > since` und der älteste Zeitstempel im Puffer
    - `start() -> None` / `stop() -> None`: Daemon-Thread mit `threading.Event.wait(interval)`
    - Puffergröße: `deque(maxlen=settings.buffer_seconds // settings.collect_interval_seconds)`

Regeln in `derive`: Raten = `(cur - prev) / elapsed_seconds`, gerundet auf 1 Nachkommastelle. `None`, wenn `prev` fehlt, `elapsed <= 0`, die Differenz negativ ist oder der Schlüssel (Interface bzw. `full_id`) vorher nicht existierte. CPU-% kommt aus `host.cpu_percent(prev.cpu_times, cur.cpu_times)`. Host-Warnungen genau so: `"Host-Speicher nicht lesbar"`, `"Dateisysteme nicht lesbar"`, `"Netzwerk nicht lesbar"`, `"Load Average nicht lesbar"`, `"Temperatur nicht lesbar"`, `"CPU-Last noch nicht berechenbar"` (nur bei der ersten Messung).

- [ ] **Step 1: Failing tests schreiben** – `backend/tests/test_collector.py`. Rohmessungen werden als Dicts von Hand gebaut. Für `collect_once` wird `collector.read_raw` per `monkeypatch` ersetzt.

```python
from datetime import datetime, timedelta, timezone
from moni import collector
from moni.config import Settings

T0 = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)

def raw(t, idle_total=(700, 900), net={"eth0": (1000, 500)}, containers=None, **host_over):
    host = {"hostname": "pi", "uptime_seconds": 1.0, "process_count": 1,
            "cpu_info": {"cores": 4, "frequency_mhz": None},
            "memory": {"available": True}, "load": {"available": True},
            "filesystems": [], "temperature": {"available": True, "max_celsius": 50.0, "zones": []}}
    host.update(host_over)
    return {"taken_at": t, "cpu_times": idle_total, "net": net,
            "host": host, "containers": containers or [],
            "docker": {"reachable": True}, "warnings": []}

def test_first_snapshot_has_no_rates_and_warns():
    s = collector.derive(None, raw(T0))
    assert s["schema_version"] == 1 and s["timestamp"] == "2026-11-04T12:00:00Z"
    assert s["host"]["cpu"]["usage_percent"] is None and s["host"]["cpu"]["available"] is False
    assert s["host"]["network"][0]["rx_bytes_per_second"] is None
    assert "CPU-Last noch nicht berechenbar" in s["warnings"]

def test_rates_and_cpu_from_delta():
    s = collector.derive(raw(T0), raw(T0 + timedelta(seconds=10), (1400, 1800), {"eth0": (2000, 1500)}))
    assert s["host"]["cpu"]["usage_percent"] == 22.2
    assert s["host"]["network"][0] == {"interface": "eth0", "rx_bytes": 2000, "tx_bytes": 1500,
                                       "rx_bytes_per_second": 100.0, "tx_bytes_per_second": 100.0}

def test_counter_reset_gives_none_not_negative():
    c0 = [{"full_id": "x", "rx_bytes": 5000, "tx_bytes": 5000}]
    c1 = [{"full_id": "x", "rx_bytes": 10, "tx_bytes": 10}]
    s = collector.derive(raw(T0, containers=c0), raw(T0 + timedelta(seconds=15), containers=c1))
    assert s["containers"][0]["rx_bytes_per_second"] is None
    assert "full_id" not in s["containers"][0]

def test_unreadable_blocks_produce_warnings():
    s = collector.derive(None, raw(T0, net=None, filesystems=None, memory={"available": False}))
    assert s["host"]["network"] is None and s["host"]["filesystems"] is None
    assert {"Netzwerk nicht lesbar", "Dateisysteme nicht lesbar", "Host-Speicher nicht lesbar"} <= set(s["warnings"])

def test_ring_buffer_and_history(monkeypatch):
    c = collector.Collector(Settings.from_env({"MONI_COLLECT_INTERVAL": "15", "MONI_BUFFER_SECONDS": "60"}), client=object())
    times = iter([T0 + timedelta(seconds=15 * i) for i in range(6)])
    monkeypatch.setattr(collector, "read_raw", lambda s, cl: raw(next(times)))
    for _ in range(6): c.collect_once()
    pts, oldest = c.history(T0)
    assert len(pts) == 4 and oldest == "2026-11-04T12:00:30Z"
    assert [p["timestamp"] for p in c.history(T0 + timedelta(seconds=60))[0]] == ["2026-11-04T12:01:15Z"]
    assert c.history(T0 + timedelta(days=1))[0] == []
    assert c.latest()["timestamp"] == "2026-11-04T12:01:15Z"

def test_loop_survives_exception(monkeypatch):
    c = collector.Collector(Settings.from_env({"MONI_COLLECT_INTERVAL": "1", "MONI_BUFFER_SECONDS": "10"}), client=object())
    calls = {"n": 0}
    def flaky(s, cl):
        calls["n"] += 1
        if calls["n"] == 1: raise OSError("kaputt")
        return raw(T0 + timedelta(seconds=calls["n"]))
    monkeypatch.setattr(collector, "read_raw", flaky)
    c._run_one_cycle(); c._run_one_cycle()   # erster wirft, zweiter misst
    assert c.latest() is not None
```

`raw()["host"]` enthält `cpu_info` (aus `read_cpu_info`). `derive` macht daraus `host.cpu`, also `cores` und `frequency_mhz` plus das berechnete `usage_percent` und `available`.

`Collector._run_one_cycle() -> None` ist der Schleifenkörper. Er ruft `collect_once` auf und fängt jede `Exception` mit `logging.exception` ab. `start()` ruft ihn in der Schleife auf.

- [ ] **Step 2: Tests ausführen, sie müssen fehlschlagen**

Run: `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests/test_collector.py -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: `moni/collector.py` implementieren** mit den Signaturen und Regeln oben

Puffer und `latest` werden mit einem `threading.Lock` geschützt. `read_raw` ruft die Host-Funktionen mit den Pfaden aus `settings` auf und ermittelt `collect_containers` mit `host_total_mem_bytes = memory["total_bytes"]`.

- [ ] **Step 4: Tests ausführen, sie müssen bestehen**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/moni/collector.py backend/tests/test_collector.py
git commit -m "feat(agent): Sammler mit Delta-CPU, Raten und Ringpuffer"
```

---

### Task 5: API-Tokens und CLI (`moni/auth/tokens.py`, `moni/cli.py`)

**Files:**
- Create: `backend/moni/auth/__init__.py`, `backend/moni/auth/tokens.py`, `backend/moni/cli.py`
- Create: `backend/tests/test_tokens.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Token`: `id: str` (8 Hex-Zeichen), `name: str`, `scope: str`, `created_at: str` (ISO), `expires_at: str | None`, `hash: str` (SHA-256 hex)
  - `SCOPES = frozenset({"read"})`
  - `class TokenStore(path: Path)` mit:
    - `create(name: str, scope: str = "read", expires_at: datetime | None = None) -> tuple[Token, str]`: gibt den Klartext `moni_<id>_<secret>` zurück, `secret = secrets.token_urlsafe(32)`. Ein unbekannter Scope wirft `ValueError`, eine kaputte Datei `RuntimeError`.
    - `verify(plaintext: str, now: datetime | None = None) -> Token | None`: Vergleich mit `hmac.compare_digest`. Abgelaufen, unbekannt, falsches Format oder kaputte Datei ergibt `None`.
    - `list() -> list[Token]`, `revoke(token_id: str) -> bool`
  - Persistenz: JSON `{"version": 1, "tokens": [...]}`, atomar geschrieben (Temp-Datei + `os.replace`), Dateimodus `0o600`
  - `moni.cli.main(argv: list[str] | None = None) -> int` mit den Befehlen `token create --name N [--expires-days D]`, `token list`, `token revoke ID`. Die Datei ist `Path(settings.data_dir) / "tokens.json"`.

- [ ] **Step 1: Failing tests schreiben**

```python
import json, os
from datetime import datetime, timedelta, timezone
import pytest
from moni.auth.tokens import TokenStore
from moni import cli

def test_create_verify_revoke(tmp_path):
    st = TokenStore(tmp_path / "tokens.json")
    tok, plain = st.create("grafana")
    assert plain.startswith(f"moni_{tok.id}_") and plain not in (tmp_path / "tokens.json").read_text()
    assert oct(os.stat(tmp_path / "tokens.json").st_mode & 0o777) == "0o600"
    assert st.verify(plain).name == "grafana"
    assert st.verify(plain[:-1] + "x") is None and st.verify("Bearer quatsch") is None
    assert st.revoke(tok.id) is True and st.verify(plain) is None

def test_expired_token(tmp_path):
    st = TokenStore(tmp_path / "t.json")
    _, plain = st.create("x", expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert st.verify(plain, now=datetime(2026, 1, 2, tzinfo=timezone.utc)) is None

def test_unknown_scope(tmp_path):
    with pytest.raises(ValueError):
        TokenStore(tmp_path / "t.json").create("x", scope="admin")

def test_corrupt_file_fails_closed_and_is_not_overwritten(tmp_path):
    p = tmp_path / "t.json"; p.write_text("{kaputt")
    st = TokenStore(p)
    assert st.verify("moni_deadbeef_abc") is None
    with pytest.raises(RuntimeError):
        st.create("x")
    assert p.read_text() == "{kaputt"

def test_missing_file_is_empty(tmp_path):
    assert TokenStore(tmp_path / "nope.json").list() == []

def test_cli_roundtrip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
    assert cli.main(["token", "create", "--name", "manager", "--expires-days", "30"]) == 0
    plain = capsys.readouterr().out.strip().splitlines()[-1]
    assert plain.startswith("moni_")
    assert cli.main(["token", "list"]) == 0
    out = capsys.readouterr().out
    assert "manager" in out and plain not in out
    tid = plain.split("_")[1]
    assert cli.main(["token", "revoke", tid]) == 0
    assert cli.main(["token", "revoke", tid]) == 1
```

- [ ] **Step 2: Tests ausführen, sie müssen fehlschlagen**

Run: `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests/test_tokens.py -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: `tokens.py` und `cli.py` implementieren**

Das Hashing ist ein einfaches SHA-256 über den gesamten Klartext. Für 256-Bit-Zufallstokens reicht das, ein langsamer Hash ist nur bei Passwörtern nötig. `cli token create` gibt in der letzten Zeile nur den Klartext aus, davor den Hinweis „Token wird nur jetzt angezeigt.“ Ist die Datei nicht schreibbar, meldet die CLI einen verständlichen Fehler und endet mit Exit-Code 2.

- [ ] **Step 4: Tests ausführen, sie müssen bestehen**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/moni/auth backend/moni/cli.py backend/tests/test_tokens.py
git commit -m "feat(agent): gehashte API-Tokens mit Ablauf, Widerruf und CLI"
```

---

### Task 6: Agent-API v1, Problem Details, Rate-Limit, App-Fabrik

**Files:**
- Create: `backend/moni/errors.py`, `backend/moni/ratelimit.py`, `backend/moni/auth/deps.py`, `backend/moni/agent/__init__.py`, `backend/moni/agent/api.py`, `backend/moni/app.py`, `backend/moni/__main__.py`
- Create: `backend/tests/test_agent_api.py`

**Interfaces:**
- Consumes: `Collector` (Task 4), `TokenStore` (Task 5), `Settings`, `setup_logging`
- Produces:
  - `moni.errors.install(app: FastAPI) -> None`: registriert Handler für `HTTPException`, `RequestValidationError` und unbehandelte `Exception`. Antwort: `application/problem+json` mit `type` (`"about:blank"`), `title`, `status`, `detail`. Bei 500 enthält die Antwort keine internen Details.
  - `moni.auth.deps.require_scope(scope: str) -> Callable` (FastAPI-Dependency). Der `TokenStore` kommt aus `request.app.state.tokens`. Fehlender oder ungültiger Header ergibt 401 mit `WWW-Authenticate: Bearer`, ein fehlender Scope 403.
  - `moni.ratelimit.RateLimitMiddleware(app, per_minute: int, exempt: tuple[str, ...] = ("/api/v1/health",))`: festes 60-s-Fenster pro Client-IP. Bei Überschreitung 429 Problem-JSON mit `Retry-After`.
  - `moni.agent.api.router: APIRouter` (Präfix `/api/v1`) und `moni.agent.api.metrics_route` (Pfad `/metrics`, ergänzt in Task 7)
  - `moni.app.create_app(settings: Settings | None = None, collector: Collector | None = None) -> FastAPI`. Liegt kein `collector` vor, wird einer erzeugt und im `lifespan` gestartet und gestoppt. `app.state.settings`, `app.state.collector` und `app.state.tokens` werden gesetzt. Swagger-UI unter `/api/v1/docs`, OpenAPI unter `/api/v1/openapi.json`.
  - `python -m moni` startet uvicorn mit `create_app` (factory) auf `0.0.0.0:{settings.port}`. `access_log` bleibt an, Header werden dabei nicht geloggt.

Endpunkte:

| Pfad | Auth | Antwort |
|---|---|---|
| `GET /api/v1/health` | keine | `{"status": "ok"}` |
| `GET /api/v1/info` | `read` | `{"version", "role": "agent", "api_version": "v1", "hostname", "capabilities": ["snapshot", "history", "metrics"], "paired": false, "tls": "off"}` |
| `GET /api/v1/snapshot` | `read` | letzter Snapshot; vor der ersten Messung 503 mit `detail` `"Noch keine Messung vorhanden"` |
| `GET /api/v1/history?since=<iso>` | `read` | `{"points": [...], "oldest": <iso> \| null}`; fehlendes oder ungültiges `since` ergibt 422 |

- [ ] **Step 1: Failing tests schreiben** – Fixtures in `backend/tests/conftest.py`, damit Task 7 sie mitnutzt: `store` (`TokenStore`), `token` + `auth` (`{"Authorization": "Bearer <klartext>"}`), `measured` (ruft einmal `collect_once` mit gepatchtem `read_raw` auf) und `client` mit `Settings.from_env({"MONI_DATA_DIR": str(tmp_path), "MONI_RATE_LIMIT_PER_MINUTE": "5"})`, einem `Collector`, der **nicht** gestartet wird (Snapshots kommen per `collector._buffer`/`collect_once` mit gepatchtem `read_raw`, wie in Task 4), und einem Token aus `TokenStore(tmp_path/"tokens.json").create("t")`.

```python
def test_health_needs_no_token(client):
    r = client.get("/api/v1/health"); assert r.status_code == 200 and r.json() == {"status": "ok"}

def test_snapshot_requires_bearer(client):
    r = client.get("/api/v1/snapshot")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["status"] == 401

def test_snapshot_before_first_measurement_is_503(client, auth):
    r = client.get("/api/v1/snapshot", headers=auth)
    assert r.status_code == 503 and r.json()["detail"] == "Noch keine Messung vorhanden"

def test_snapshot_and_info(client, auth, measured):
    assert client.get("/api/v1/snapshot", headers=auth).json()["schema_version"] == 1
    info = client.get("/api/v1/info", headers=auth).json()
    assert info["role"] == "agent" and info["api_version"] == "v1" and info["paired"] is False

def test_history_validation(client, auth, measured):
    assert client.get("/api/v1/history", headers=auth).status_code == 422
    assert client.get("/api/v1/history?since=gestern", headers=auth).status_code == 422
    r = client.get("/api/v1/history?since=2000-01-01T00:00:00Z", headers=auth).json()
    assert len(r["points"]) >= 1 and r["oldest"] is not None

def test_revoked_token_401(client, auth, store, token):
    store.revoke(token.id)
    assert client.get("/api/v1/info", headers=auth).status_code == 401

def test_rate_limit(client, auth):
    codes = [client.get("/api/v1/info", headers=auth).status_code for _ in range(6)]
    assert codes[-1] == 429
    assert client.get("/api/v1/health").status_code == 200   # ausgenommen

def test_authorization_header_not_logged(client, auth, caplog):
    client.get("/api/v1/info", headers=auth)
    assert auth["Authorization"].split()[1] not in caplog.text
```

- [ ] **Step 2: Tests ausführen, sie müssen fehlschlagen**

Run: `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests/test_agent_api.py -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: Module implementieren** mit den Signaturen oben

CORS-Middleware wie bisher nur bei gesetzten `cors_origins` und nur für `GET`. In `create_app` wird `setup_logging()` aufgerufen.

- [ ] **Step 4: Tests ausführen, sie müssen bestehen**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/moni backend/tests/test_agent_api.py
git commit -m "feat(agent): API v1 mit Token-Auth, RFC 9457 und Rate-Limit"
```

---

### Task 7: Prometheus-Endpunkt `/metrics`

**Files:**
- Create: `backend/moni/agent/prometheus.py`
- Modify: `backend/moni/agent/api.py` (Route `/metrics`, Auth `read`)
- Create: `backend/tests/test_prometheus.py`

**Interfaces:**
- Consumes: Snapshot-Format (Task 4)
- Produces: `render(snapshot: dict) -> str` im Prometheus-Textformat 0.0.4, jede Metrik mit `# HELP` und `# TYPE gauge`. `None`-Werte werden ausgelassen. Content-Type: `text/plain; version=0.0.4; charset=utf-8`.

Metriknamen (Labels in Klammern):
`moni_host_cpu_usage_percent`, `moni_host_load1|load5|load15`, `moni_host_memory_total_bytes`, `moni_host_memory_used_bytes`, `moni_host_memory_usage_percent`, `moni_host_swap_used_bytes`, `moni_host_uptime_seconds`, `moni_host_processes`, `moni_host_temperature_celsius{zone}`, `moni_filesystem_size_bytes{mountpoint,device,fstype}`, `moni_filesystem_used_bytes{…}`, `moni_filesystem_usage_percent{…}`, `moni_network_receive_bytes{interface}`, `moni_network_transmit_bytes{interface}`, `moni_container_up{name,image,status}` (1 bei `running`, sonst 0), `moni_container_cpu_usage_percent{name}`, `moni_container_memory_usage_bytes{name}`, `moni_container_restarts{name}`, `moni_container_healthy{name}` (1/0, nur wenn Health ≠ `none`), `moni_docker_up` (1/0), `moni_snapshot_timestamp_seconds`

- [ ] **Step 1: Failing tests schreiben**

```python
from moni.agent import prometheus

SNAP = {"timestamp": "2026-11-04T12:00:00Z",
        "host": {"cpu": {"usage_percent": 12.5}, "load": {"load1": 0.5, "load5": None, "load15": 1.0},
                 "memory": {"total_bytes": 100, "used_bytes": 50, "usage_percent": 50.0, "swap_used_bytes": 0},
                 "uptime_seconds": 10.0, "process_count": 3,
                 "temperature": {"zones": [{"name": "cpu", "celsius": 50.0}]},
                 "filesystems": [{"mountpoint": "/", "device": "/dev/root", "fstype": "ext4",
                                  "total_bytes": 4096000, "used_bytes": 1, "usage_percent": 66.7}],
                 "network": None},
        "containers": [{"name": 'we"b', "image": "nginx", "status": "running", "health": "none",
                        "cpu_percent": None, "memory_usage_bytes": 5, "restart_count": 0}],
        "docker": {"reachable": True}}

def test_render():
    out = prometheus.render(SNAP)
    assert "# TYPE moni_host_cpu_usage_percent gauge" in out
    assert "moni_host_cpu_usage_percent 12.5\n" in out
    assert "moni_host_load5" not in out.replace("# HELP moni_host_load5", "").replace("# TYPE moni_host_load5", "")
    assert 'moni_filesystem_size_bytes{mountpoint="/",device="/dev/root",fstype="ext4"} 4096000' in out
    assert 'moni_container_up{name="we\\"b",image="nginx",status="running"} 1' in out
    assert 'moni_container_healthy{' not in out
    assert "moni_docker_up 1" in out
    assert "moni_snapshot_timestamp_seconds 1793793600" in out

def test_metrics_endpoint(client, auth, measured):
    r = client.get("/metrics", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert client.get("/metrics").status_code == 401
```

Die Fixtures `client`, `auth` und `measured` stammen aus `conftest.py` (Task 6).

- [ ] **Step 2: Tests ausführen, sie müssen fehlschlagen**

Run: `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests/test_prometheus.py -v`
Expected: FAIL

- [ ] **Step 3: `render` und Route implementieren**

Label-Werte werden escaped: `\` → `\\`, `"` → `\"`, Zeilenumbruch → `\n`. Vor der ersten Messung liefert `/metrics` 503 Problem-JSON wie `/snapshot`.

- [ ] **Step 4: Tests ausführen, sie müssen bestehen**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/moni/agent backend/tests
git commit -m "feat(agent): Prometheus/OpenMetrics-Endpunkt"
```

---

### Task 8: Container, Compose, CI und Altcode entfernen

**Files:**
- Modify: `Dockerfile`: neue Stufe `agent` vor der bisherigen Endstufe; die Endstufe heißt nun `manager` und bleibt die letzte Stufe, damit `compose.yaml` sie weiter baut
- Create: `compose.agent.yaml`
- Modify: `.github/workflows/ci.yml`
- Modify: `.dockerignore` (falls nötig, damit `backend/tests` nicht im Image landet)
- Delete: `backend/server.py`, `backend/tests/test_server.py`

**Interfaces:**
- Consumes: `python -m moni` (Task 6), `python -m moni.cli` (Task 5)
- Produces: Image-Target `agent`, Compose-Dienste `moni-agent` und `docker-socket-proxy`, Volume `moni-agent-data`

Vorgaben:
- **Stufe `agent`**: `python:3.14-slim` mit demselben Digest wie die bestehende Endstufe, `pip install -r backend/requirements-docker.txt`, `COPY backend/moni /app/moni`, Benutzer `app` (non-root), `/data` gehört `app`, `ENV MONI_DATA_DIR=/data PYTHONUNBUFFERED=1`, `EXPOSE 9810`, `HEALTHCHECK` per `python -c` mit `urllib` auf `http://127.0.0.1:9810/api/v1/health` (kein curl im Image), `CMD ["python", "-m", "moni"]`.
- **Stufe `manager`** (Übergang bis Paket 2): kopiert `backend/moni` statt `server.py`. supervisor startet `python -m moni` mit `MONI_ROLE=agent MONI_PORT=8001`. Kommentar im Dockerfile: `# shortcut: Manager-Stufe startet bis Paket 2 den Agent-Code hinter nginx; alte UI ist auf diesem Branch außer Betrieb.`
- **`compose.agent.yaml`**:
  - `moni-agent`: `build: {context: ., target: agent}`, `read_only`, `tmpfs /tmp`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`
  - Volumes: `/proc:/host/proc:ro`, `/sys:/host/sys:ro`, `/:/host/root:ro,rslave`, `/etc/hostname:/host/etc/hostname:ro`, `moni-agent-data:/data`
  - Env: `HOST_PROC=/host/proc`, `HOST_SYS=/host/sys`, `HOST_ETC=/host/etc`, `HOST_ROOT=/host/root`, `DOCKER_API_BASE=http://docker-socket-proxy:2375`, `MONI_TLS=off`
  - Port: `"${MONI_AGENT_BIND:-127.0.0.1}:9810:9810"`. Standardmäßig nur localhost, weil es in diesem Paket noch kein TLS gibt.
  - Netze: `default` und `socket-proxy`
  - `docker-socket-proxy`: `tecnativa/docker-socket-proxy` per Digest gepinnt (beim Umsetzen mit `docker buildx imagetools inspect` ermitteln; Stand 2026-11-04 `latest@sha256:1f5038b54f06c3e18422902cf00ba21803d1c97805aae032e5e6673d532d3459`), Env `CONTAINERS=1`, `POST=0`, Socket `:ro`, `security_opt: [no-new-privileges:true]`, nur im Netz `socket-proxy`
  - `networks.socket-proxy.internal: true`
- **CI**: Der Job `build` baut beide Targets (`agent` als `moni-agent:ci`, `manager` als `moni-manager:ci`). Der Boot-Smoke läuft auf dem Linux-Runner per `docker compose -f compose.agent.yaml up -d --build` (siehe Step 2).

- [ ] **Step 1: Dockerfile, `compose.agent.yaml` und CI wie oben ändern; `server.py` und `test_server.py` löschen**

- [ ] **Step 2: CI-Smoke-Skript im Job `build`**

```bash
docker compose -f compose.agent.yaml up -d --build
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:9810/api/v1/health | grep -q '"ok"' && break; sleep 1; done
test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9810/api/v1/snapshot)" = 401
TOKEN=$(docker compose -f compose.agent.yaml exec -T moni-agent python -m moni.cli token create --name ci | tail -n1)
sleep 20   # mindestens eine Messung abwarten (Takt 15 s)
curl -fsS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9810/api/v1/snapshot > snap.json
python3 -c "import json; s=json.load(open('snap.json')); assert s['docker']['reachable'], s['warnings']; assert s['host']['filesystems'], 'keine Dateisysteme'; print('OK', len(s['containers']), 'Container')"
curl -fsS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9810/metrics | grep -q '^moni_docker_up 1'
test "$(docker inspect --format '{{.Config.User}} {{.HostConfig.ReadonlyRootfs}} {{.HostConfig.CapDrop}}' $(docker compose -f compose.agent.yaml ps -q moni-agent))" = "app true [ALL]"
```

Danach läuft immer: `docker compose -f compose.agent.yaml logs` und `down -v`.

- [ ] **Step 3: Gesamte Testsuite lokal ausführen**

Run: `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests -v`
Expected: alle Tests PASS, keine Referenz mehr auf `server`

- [ ] **Step 4: Beide Images lokal bauen**

Run: `docker build --target agent -t moni-agent:dev . && docker build --target manager -t moni-manager:dev .`
Expected: beide Builds erfolgreich

- [ ] **Step 5: Compose-Datei prüfen**

Run: `docker compose -f compose.agent.yaml config >/dev/null && echo OK`
Expected: `OK`

(Der volle Start mit `/:/host/root` wird auf dem CI-Runner bzw. dem Pi geprüft. Docker Desktop auf macOS teilt `/` nicht.)

- [ ] **Step 6: Commit**

```bash
git add -A Dockerfile compose.agent.yaml .github/workflows/ci.yml .dockerignore backend
git commit -m "feat(agent): Agent-Image, compose.agent.yaml mit Socket-Proxy, CI-Smoke; server.py entfernt"
```

---

### Task 9: Prüfung auf echtem Host (Schritt für den Betreiber)

Diese Prüfung braucht Zugriff auf den Pi. Sie ist **Aufgabe des Betreibers**, Claude hat keinen Zugang. Damit wird die Spec-Vorgabe „am echten Host verifiziert“ erfüllt.

- [ ] **Step 1: Auf dem Pi** im Repo-Checkout des Branches starten (nur localhost):

```bash
docker compose -f compose.agent.yaml up -d --build
docker compose -f compose.agent.yaml exec -T moni-agent python -m moni.cli token create --name test | tail -n1
```

- [ ] **Step 2: Nach 20 s mit dem Token abfragen und vergleichen**

```bash
curl -s -H "Authorization: Bearer <TOKEN>" http://127.0.0.1:9810/api/v1/snapshot | python3 -m json.tool
df -h; ip -s link; cat /sys/class/thermal/thermal_zone*/temp
```

Erwartet:
- `host.filesystems` entspricht den echten Platten aus `df -h`, also auch `/` mit der realen Größe der SD-Karte bzw. SSD.
- `host.network` enthält das echte Interface (`eth0`/`wlan0`), und die Raten sind nach der zweiten Messung gesetzt.
- Die Temperatur stimmt mit `/sys` überein.
- `warnings` ist leer.

Abweichungen gehen als Befund zurück in die Planung von Paket 2.

- [ ] **Step 3: Aufräumen**

`docker compose -f compose.agent.yaml down -v`

---

## Selbst-Review (erledigt)

- **Spec-Abdeckung Paket 1:** §6.1 (Agent-Image), §6.2 (Pull-Seite des Agents, Ringpuffer, `info`, Versionierung), §6.3 (Tokens; mTLS bewusst Paket 4), §6.4 (alle fünf Endpunkte und Konventionen; OpenAPI-Abgleich in der CI folgt in Paket 7), §6.5 (Messumfang, Host-Mount, Altfehler), §6.11 (Modulstruktur) sind abgedeckt. Die übrigen Abschnitte sind laut Übersicht anderen Paketen zugeordnet.
- **Typen:** Snapshot-Schlüssel, `Collector`-Methoden und `TokenStore`-Signaturen werden in Task 4–8 einheitlich verwendet.
- **Review Focus:** Alle fünf Punkte haben Tests in Task 3, 4, 5 und 6.
