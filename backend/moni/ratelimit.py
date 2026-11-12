"""Festes 60-s-Fenster pro Client-IP, im Speicher."""
import threading
import time

from starlette.types import ASGIApp, Receive, Scope, Send

from moni.errors import problem

WINDOW = 60


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, per_minute: int, exempt: tuple[str, ...] = ("/api/v1/health",)):
        self.app, self.per_minute, self.exempt = app, per_minute, exempt
        self._lock = threading.Lock()
        self._win = -1
        self._hits: dict[str, tuple[int, int]] = {}  # ip -> (fenster, anzahl)

    def _check(self, ip: str) -> int | None:
        """None = erlaubt, sonst Sekunden bis Fensterende."""
        now = time.time()
        win = int(now // WINDOW)
        with self._lock:
            if win != self._win:  # Fensterwechsel: alles verwerfen, Speicher bleibt begrenzt
                self._win, self._hits = win, {}
            w, n = self._hits.get(ip, (win, 0))
            if n >= self.per_minute:
                return max(1, int((win + 1) * WINDOW - now) + 1)
            self._hits[ip] = (win, n + 1)
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["path"] in self.exempt:
            return await self.app(scope, receive, send)
        client = scope.get("client")
        retry = self._check(client[0] if client else "unknown")
        if retry is None:
            return await self.app(scope, receive, send)
        resp = problem(429, "Zu viele Anfragen", headers={"Retry-After": str(retry)})
        await resp(scope, receive, send)
