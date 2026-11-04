import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


def _num(conv, what, env, name, default):
    try:
        return conv(env.get(name, default))
    except ValueError:
        raise ValueError(f"{name} muss {what} sein") from None


def _int(env, name, default):
    return _num(int, "eine ganze Zahl", env, name, default)


def _float(env, name, default):
    return _num(float, "eine Zahl", env, name, default)


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
    secret_key: str = field(default="", repr=False)
    poll_timeout_seconds: float = 10.0
    retention_raw_hours: int = 48
    retention_minute_days: int = 30
    retention_hour_days: int = 365

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "moni.db"

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
            docker_stats_timeout=_float(env, "DOCKER_STATS_TIMEOUT", "2.5"),
            docker_inspect_timeout=_float(env, "DOCKER_INSPECT_TIMEOUT", "1.0"),
            max_docker_workers=_int(env, "MAX_DOCKER_WORKERS", "8"),
            collect_interval_seconds=_int(env, "MONI_COLLECT_INTERVAL", "15"),
            buffer_seconds=_int(env, "MONI_BUFFER_SECONDS", "7200"),
            data_dir=g("MONI_DATA_DIR", "/data"),
            tls=g("MONI_TLS", "off"),
            port=_int(env, "MONI_PORT", "9810"),
            rate_limit_per_minute=_int(env, "MONI_RATE_LIMIT_PER_MINUTE", "120"),
            cors_origins=tuple(o.strip() for o in g("API_CORS_ORIGINS", "").split(",") if o.strip()),
            secret_key=g("MONI_SECRET_KEY", ""),
            poll_timeout_seconds=_float(env, "MONI_POLL_TIMEOUT", "10"),
            retention_raw_hours=_int(env, "MONI_RETENTION_RAW_HOURS", "48"),
            retention_minute_days=_int(env, "MONI_RETENTION_MINUTE_DAYS", "30"),
            retention_hour_days=_int(env, "MONI_RETENTION_HOUR_DAYS", "365"),
        )
        if s.role not in ("agent", "manager"):
            raise ValueError(f"MONI_ROLE muss agent oder manager sein, nicht {s.role!r}")
        if s.tls != "off":
            raise ValueError("MONI_TLS kennt in diesem Paket nur 'off'")
        if s.collect_interval_seconds < 1:
            raise ValueError("MONI_COLLECT_INTERVAL muss >= 1 sein")
        if s.buffer_seconds < s.collect_interval_seconds:
            raise ValueError("MONI_BUFFER_SECONDS muss >= MONI_COLLECT_INTERVAL sein")
        if not 1 <= s.port <= 65535:
            raise ValueError("MONI_PORT muss zwischen 1 und 65535 liegen")
        for name, v in [("MONI_RATE_LIMIT_PER_MINUTE", s.rate_limit_per_minute), ("MAX_DOCKER_WORKERS", s.max_docker_workers),
                        ("MONI_RETENTION_RAW_HOURS", s.retention_raw_hours),
                        ("MONI_RETENTION_MINUTE_DAYS", s.retention_minute_days),
                        ("MONI_RETENTION_HOUR_DAYS", s.retention_hour_days)]:
            if v < 1:
                raise ValueError(f"{name} muss >= 1 sein")
        for name, v in [("DOCKER_STATS_TIMEOUT", s.docker_stats_timeout), ("DOCKER_INSPECT_TIMEOUT", s.docker_inspect_timeout),
                        ("MONI_POLL_TIMEOUT", s.poll_timeout_seconds)]:
            if not v > 0:  # auch NaN
                raise ValueError(f"{name} muss > 0 sein")
        if s.role == "manager" and not s.secret_key:
            raise ValueError("MONI_SECRET_KEY fehlt – erzeugen mit: python -m moni.cli secret generate")
        return s
