import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    role: str
    host_proc: str
    host_sys: str
    host_etc: str
    host_root: str
    docker_api_base: str
    docker_stats_timeout: float
    docker_inspect_timeout: float
    max_docker_workers: int
    collect_interval_seconds: int
    buffer_seconds: int
    data_dir: str
    tls: str
    port: int
    rate_limit_per_minute: int
    cors_origins: tuple[str, ...]

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        g = env.get
        s = cls(
            role=g("MONI_ROLE", "agent"),
            host_proc=g("HOST_PROC", "/proc"),
            host_sys=g("HOST_SYS", "/sys"),
            host_etc=g("HOST_ETC", "/etc"),
            host_root=g("HOST_ROOT", "/"),
            docker_api_base=g("DOCKER_API_BASE", "unix:///var/run/docker.sock"),
            docker_stats_timeout=float(g("DOCKER_STATS_TIMEOUT", "2.5")),
            docker_inspect_timeout=float(g("DOCKER_INSPECT_TIMEOUT", "1.0")),
            max_docker_workers=int(g("MAX_DOCKER_WORKERS", "8")),
            collect_interval_seconds=int(g("MONI_COLLECT_INTERVAL", "15")),
            buffer_seconds=int(g("MONI_BUFFER_SECONDS", "7200")),
            data_dir=g("MONI_DATA_DIR", "/data"),
            tls=g("MONI_TLS", "off"),
            port=int(g("MONI_PORT", "9810")),
            rate_limit_per_minute=int(g("MONI_RATE_LIMIT_PER_MINUTE", "120")),
            cors_origins=tuple(o.strip() for o in g("API_CORS_ORIGINS", "").split(",") if o.strip()),
        )
        if s.role not in ("agent", "manager"):
            raise ValueError(f"MONI_ROLE muss agent oder manager sein, nicht {s.role!r}")
        if s.tls != "off":
            raise ValueError("MONI_TLS kennt in diesem Paket nur 'off'")
        if s.collect_interval_seconds < 1:
            raise ValueError("MONI_COLLECT_INTERVAL muss >= 1 sein")
        if s.buffer_seconds < s.collect_interval_seconds:
            raise ValueError("MONI_BUFFER_SECONDS muss >= MONI_COLLECT_INTERVAL sein")
        return s
