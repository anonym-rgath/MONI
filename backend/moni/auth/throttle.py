"""Drosselung fehlgeschlagener Anmeldungen pro IP (D8), nur im Speicher."""
import threading

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
