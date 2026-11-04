from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from moni import __version__, errors
from moni.agent.api import router
from moni.auth.tokens import TokenStore
from moni.collector import Collector
from moni.config import Settings
from moni.logs import setup_logging
from moni.ratelimit import RateLimitMiddleware


def create_app(settings: Settings | None = None, collector: Collector | None = None) -> FastAPI:
    setup_logging()
    settings = settings or Settings.from_env()
    own = collector is None
    collector = collector or Collector(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if own:
            collector.start()
        try:
            yield
        finally:
            if own:
                collector.stop()

    app = FastAPI(title="Moni Agent", version=__version__, docs_url="/api/v1/docs", openapi_url="/api/v1/openapi.json",
                  redoc_url=None, lifespan=lifespan)
    app.state.settings, app.state.collector = settings, collector
    app.state.tokens = TokenStore(Path(settings.data_dir) / "tokens.json")
    errors.install(app)
    app.include_router(router)
    app.add_middleware(RateLimitMiddleware, per_minute=settings.rate_limit_per_minute)
    if settings.cors_origins:  # zuletzt = aussen, damit auch 429 CORS-Header bekommt
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET"],
                           allow_headers=["Authorization"])
    return app
