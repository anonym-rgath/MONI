"""Obergrenze für Anfrage-Bodys am Agent, bevor FastAPI sie liest (FastAPI parst den Body vor der Anmeldung).

Der Manager braucht das nicht: nginx davor begrenzt mit client_max_body_size.
"""
from fastapi import HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from moni.errors import problem

MAX_BODY = 65536
_DETAIL = "Anfrage zu groß"


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY):
        self.app, self.max = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD"):
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.max):
            return await problem(413, _DETAIL)(scope, receive, send)  # Body wird gar nicht gelesen
        seen, started = 0, False

        async def limited() -> Message:
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max:  # gestreamt/chunked: abbrechen, nie mehr als die Grenze puffern
                    raise HTTPException(413, _DETAIL)  # fastapi-Variante, damit FastAPI sie nicht in 400 umwandelt
            return msg

        async def tracked(msg: Message) -> None:
            nonlocal started
            started = started or msg["type"] == "http.response.start"
            await send(msg)

        try:
            await self.app(scope, limited, tracked)
        except StarletteHTTPException as e:  # Rückfall, falls der Body außerhalb von FastAPI gelesen wurde
            if e.status_code != 413 or started:
                raise
            await problem(413, _DETAIL)(scope, receive, send)
