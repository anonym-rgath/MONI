"""Drosselung fehlgeschlagener Anmeldungen pro IP (D8), nur im Speicher."""
import ipaddress
import threading

from fastapi import Request

from .deps import client_ip

MAX_IPS = 10_000


class LoginThrottle:
    def __init__(self, max_failures: int = 20, window: int = 900):
        self._max = max_failures
        self._window = window
        self._hits: dict[str, list[int]] = {}  # IP -> Zeitpunkte der Fehlversuche; Reihenfolge = letzter Fehlversuch
        self._lock = threading.Lock()

    def _recent(self, hits: list[int], now: int) -> list[int]:
        return [t for t in hits if now - t < self._window]

    def retry_after(self, ip: str, now: int) -> int | None:
        with self._lock:
            hits = self._recent(self._hits.get(ip, []), now)
        if len(hits) < self._max:
            return None
        return max(1, hits[-self._max] + self._window - now)  # bis der älteste zählende Fehlversuch abläuft

    def fail(self, ip: str, now: int) -> None:
        with self._lock:
            hits = [*self._recent(self._hits.pop(ip, []), now), now][-self._max:]  # mehr zählt nie
            # zuerst abgelaufene Einträge (stehen vorn), dann die ältesten verwerfen
            while self._hits and now - self._hits[next(iter(self._hits))][-1] >= self._window:
                del self._hits[next(iter(self._hits))]
            while len(self._hits) >= MAX_IPS:
                del self._hits[next(iter(self._hits))]
            self._hits[ip] = hits


def throttle_key(request: Request) -> str:
    """IP für die Drosselung; IPv6 auf das /64-Präfix gekürzt (P3-R2), sonst wechselt ein Angreifer einfach die Adresse."""
    ip = client_ip(request) or ""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if a.version == 4:
        return ip
    if a.ipv4_mapped:
        return str(a.ipv4_mapped)
    return f"{ipaddress.IPv6Address(int(a) >> 64 << 64)}/64"
