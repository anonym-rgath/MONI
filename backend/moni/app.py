from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from moni import __version__, errors
from moni.agent.api import metrics_router, router
from moni.auth.tokens import TokenStore
from moni.collector import Collector
from moni.collect.host import read_hostname
from moni.config import Settings
from moni.manager import db
from moni.manager.api import router as manager_router
from moni.manager.poller import Poller
from moni.manager.servers import ServerStore
from moni.logs import setup_logging
from moni.ratelimit import RateLimitMiddleware
from moni.secretbox import SecretBox


def create_app(settings: Settings | None = None, collector: Collector | None = None, poller=None) -> FastAPI:
    setup_logging()
    settings = settings or Settings.from_env()
    manager = settings.role == "manager"
    own_col, own_pol = collector is None, poller is None
    collector = collector or Collector(settings)
    box = None
    if manager:
        try:
            db.migrate(settings.db_path)
        except Exception as e:
            raise RuntimeError(f"Datenbank konnte nicht vorbereitet werden ({type(e).__name__}). "
                               "Ist das Datenverzeichnis vorhanden und beschreibbar?") from None
        box = SecretBox(settings.secret_key)  # ValueError ohne Schlüssel in der Meldung
        conn = db.connect(settings.db_path)
        try:
            ServerStore(conn, box).ensure_local(read_hostname(settings.host_etc))
        finally:
            conn.close()
        poller = poller or Poller(settings, box, collector)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if own_col:
            collector.start()
        if manager and own_pol:
            poller.start()
        try:
            yield
        finally:
            if manager and own_pol:
                poller.stop()
            if own_col:
                collector.stop()

    app = FastAPI(title="Moni Manager" if manager else "Moni Agent", version=__version__, docs_url="/api/v1/docs",
                  openapi_url="/api/v1/openapi.json", redoc_url=None, lifespan=lifespan)
    app.state.settings, app.state.collector = settings, collector
    app.state.tokens = TokenStore(Path(settings.data_dir) / "tokens.json")
    errors.install(app)
    if manager:
        app.state.box, app.state.poller = box, poller
        app.include_router(manager_router)
    else:
        app.include_router(router)
        app.include_router(metrics_router)
    app.add_middleware(RateLimitMiddleware, per_minute=settings.rate_limit_per_minute)
    if settings.cors_origins:  # zuletzt = aussen, damit auch 429 CORS-Header bekommt
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET"],
                           allow_headers=["Authorization"])
    return app
