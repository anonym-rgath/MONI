"""RFC 9457 Problem Details fuer alle Fehlerantworten."""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("moni.errors")
PROBLEM = "application/problem+json"


def problem(status: int, detail: str, title: str | None = None, headers: dict | None = None) -> JSONResponse:
    if title is None:
        from http import HTTPStatus
        try:
            title = HTTPStatus(status).phrase
        except ValueError:
            title = "Fehler"
    return JSONResponse({"type": "about:blank", "title": title, "status": status, "detail": detail},
                        status_code=status, media_type=PROBLEM, headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return problem(exc.status_code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        fields = sorted({str(e["loc"][-1]) for e in exc.errors() if e.get("loc")})
        return problem(422, "Ungültige oder fehlende Parameter: " + ", ".join(fields))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.error("Unbehandelter Fehler (%s) bei %s", type(exc).__name__, request.url.path)
        return problem(500, "Interner Fehler")
