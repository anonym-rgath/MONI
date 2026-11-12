import ipaddress
import logging
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from moni.collect.host import read_hostname

log = logging.getLogger(__name__)


def _num(conv, what, env, name, default):
    try:
        return conv(env.get(name, default))
    except ValueError:
        raise ValueError(f"{name} muss {what} sein") from None


def _int(env, name, default):
    return _num(int, "eine ganze Zahl", env, name, default)


def _float(env, name, default):
    return _num(float, "eine Zahl", env, name, default)


def _flag(raw: str) -> bool:
    v = raw.strip().lower()
    if v not in ("", "0", "1", "true", "false"):
        raise ValueError("MONI_TRUST_LOCAL_PROXY muss 0, 1, true oder false sein")
    return v in ("1", "true")


_HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?")


def _check_host(host: str) -> None:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _HOST_RE.fullmatch(host):
            raise ValueError("MONI_BIND_HOST muss eine IP-Adresse oder ein Hostname sein") from None


def _list(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def _proxies(raw: str) -> tuple[str, ...]:
    """IPs und CIDRs (D8); als Netz normalisiert, weil uvicorn Netze strikt parst."""
    out = []
    for x in _list(raw):
        try:
            out.append(str(ipaddress.ip_network(x, strict=False)))
        except ValueError:
            raise ValueError(f"MONI_TRUSTED_PROXIES enthält eine ungültige Adresse: {x}") from None
    return tuple(out)


def _host_name(x: str) -> str | None:
    """IP-Adresse oder DNS-Name in der Form, die auch im Zertifikat steht; sonst None."""
    try:
        return str(ipaddress.ip_address(x))
    except ValueError:
        ok = len(x) <= 253 and _HOST_RE.fullmatch(x)
        return x.lower() if ok else None


def _manager_hostnames(raw: str, host_etc: str) -> tuple[str, ...]:
    """SANs des HTTPS-Zertifikats (D7); Standard <hostname>, localhost, 127.0.0.1."""
    names = _list(raw)
    if not names:  # ein unbrauchbarer Hostname des Systems fällt aus dem Standard heraus, statt den Start zu blockieren
        return tuple(dict.fromkeys(n for n in (_host_name(read_hostname(host_etc)), "localhost", "127.0.0.1") if n))
    out = []
    for x in names:
        if (n := _host_name(x)) is None:
            raise ValueError(f"MONI_MANAGER_HOSTNAMES enthält einen ungültigen Namen: {x}")
        out.append(n)
    return tuple(dict.fromkeys(out))


def _public_url(raw: str) -> str:
    url = raw.removesuffix("/")
    if not url:
        return ""
    err = "MONI_PUBLIC_URL muss https://host[:port] sein"
    try:
        p = urlsplit(url)
        port = p.port  # ValueError bei kaputtem oder zu großem Port
        _check_host(p.hostname or "")
    except ValueError:
        raise ValueError(err) from None
    # nur Schema und host[:port]: kein Pfad, keine Query, kein Fragment, keine Zugangsdaten
    if url != f"https://{p.netloc}" or "@" in p.netloc or p.netloc.endswith(":") or port == 0:
        raise ValueError(err)
    return url


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
    bind_host: str = "0.0.0.0"
    trust_local_proxy: bool = False
    session_idle_minutes: int = 60
    session_max_hours: int = 12
    public_url: str = ""
    trusted_proxies: tuple[str, ...] = ()
    manager_hostnames: tuple[str, ...] = ("localhost", "127.0.0.1")
    https_port: int = 8443  # von außen erreichbarer HTTPS-Port des Managers, Ziel der Umleitung von Port 80

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
            tls=g("MONI_TLS", "on"),
            port=_int(env, "MONI_PORT", "9810"),
            rate_limit_per_minute=_int(env, "MONI_RATE_LIMIT_PER_MINUTE", "120"),
            cors_origins=tuple(o.strip() for o in g("API_CORS_ORIGINS", "").split(",") if o.strip()),
            secret_key=g("MONI_SECRET_KEY", ""),
            poll_timeout_seconds=_float(env, "MONI_POLL_TIMEOUT", "10"),
            retention_raw_hours=_int(env, "MONI_RETENTION_RAW_HOURS", "48"),
            retention_minute_days=_int(env, "MONI_RETENTION_MINUTE_DAYS", "30"),
            retention_hour_days=_int(env, "MONI_RETENTION_HOUR_DAYS", "365"),
            bind_host=g("MONI_BIND_HOST", "0.0.0.0"),
            trust_local_proxy=_flag(g("MONI_TRUST_LOCAL_PROXY", "")),
            session_idle_minutes=_int(env, "MONI_SESSION_IDLE_MINUTES", "60"),
            session_max_hours=_int(env, "MONI_SESSION_MAX_HOURS", "12"),
            public_url=_public_url(g("MONI_PUBLIC_URL", "")),
            trusted_proxies=_proxies(g("MONI_TRUSTED_PROXIES", "")),
            https_port=_int(env, "MONI_HTTPS_PORT", "8443"),
            manager_hostnames=_manager_hostnames(g("MONI_MANAGER_HOSTNAMES", ""), g("HOST_ETC", "/etc")),
        )
        if s.role not in ("agent", "manager"):
            raise ValueError(f"MONI_ROLE muss agent oder manager sein, nicht {s.role!r}")
        _check_host(s.bind_host)
        if s.tls not in ("on", "off"):
            raise ValueError("MONI_TLS muss on oder off sein")
        if s.tls == "on" and s.trusted_proxies:
            log.warning("MONI_TRUSTED_PROXIES wird bei MONI_TLS=on ignoriert")
        if s.collect_interval_seconds < 1:
            raise ValueError("MONI_COLLECT_INTERVAL muss >= 1 sein")
        if s.buffer_seconds < s.collect_interval_seconds:
            raise ValueError("MONI_BUFFER_SECONDS muss >= MONI_COLLECT_INTERVAL sein")
        if not 1 <= s.port <= 65535:
            raise ValueError("MONI_PORT muss zwischen 1 und 65535 liegen")
        if not 1 <= s.https_port <= 65535:
            raise ValueError("MONI_HTTPS_PORT muss zwischen 1 und 65535 liegen")
        for name, v in [("MONI_RATE_LIMIT_PER_MINUTE", s.rate_limit_per_minute), ("MAX_DOCKER_WORKERS", s.max_docker_workers),
                        ("MONI_RETENTION_RAW_HOURS", s.retention_raw_hours),
                        ("MONI_RETENTION_MINUTE_DAYS", s.retention_minute_days),
                        ("MONI_RETENTION_HOUR_DAYS", s.retention_hour_days),
                        ("MONI_SESSION_IDLE_MINUTES", s.session_idle_minutes), ("MONI_SESSION_MAX_HOURS", s.session_max_hours)]:
            if v < 1:
                raise ValueError(f"{name} muss >= 1 sein")
        for name, v in [("DOCKER_STATS_TIMEOUT", s.docker_stats_timeout), ("DOCKER_INSPECT_TIMEOUT", s.docker_inspect_timeout),
                        ("MONI_POLL_TIMEOUT", s.poll_timeout_seconds)]:
            if not (math.isfinite(v) and v > 0):  # nan/inf
                raise ValueError(f"{name} muss > 0 sein")
        if s.role == "manager" and not s.secret_key:
            raise ValueError("MONI_SECRET_KEY fehlt – erzeugen mit: python -m moni.cli secret generate")
        return s
