import hashlib
import logging
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from moni import __version__, errors
from moni.agent import pairing_api
from moni.agent.api import metrics_router, router
from moni.agent.tls import TlsState
from moni.auth import admin_api, api as auth_api, oidc
from moni.auth.throttle import LoginThrottle
from moni.auth.tokens import TokenStore
from moni.auth.users import UserStore
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
from moni.timeutil import utcnow

log = logging.getLogger("moni.app")


def create_app(settings: Settings | None = None, collector: Collector | None = None, poller: Poller | None = None,
               tls_state: TlsState | None = None) -> FastAPI:
    setup_logging()
    settings = settings or Settings.from_env()
    manager = settings.role == "manager"
    own_col, own_pol = collector is None, poller is None
    collector = collector or Collector(settings)
    box = setup_hash = None
    if manager:
        try:
            db.migrate(settings.db_path)
        except RuntimeError:
            raise  # db.migrate liefert bereits klare deutsche Meldungen
        except (OSError, sqlite3.Error) as e:
            raise RuntimeError(f"Datenbank konnte nicht vorbereitet werden: {e}") from None
        box = SecretBox(settings.secret_key)  # ValueError ohne Schlüssel in der Meldung
        conn = db.connect(settings.db_path)
        name = read_hostname(settings.host_etc)
        try:
            if UserStore(conn, box).count() == 0:
                tok = f"setup_{secrets.token_urlsafe(24)}"
                # einzige erlaubte Ausnahme von „keine Geheimnisse im Log“ (D9); gespeichert wird nur der Hash
                log.warning("Ersteinrichtung offen: Setup-Token %s – gilt, bis der erste Admin angelegt ist", tok)
                setup_hash = hashlib.sha256(tok.encode()).digest()
            store = ServerStore(conn, box)
            store.ensure_local(name)
        except ValueError:
            if not any(s.is_local for s in store.list()):  # erster Start: kein bisheriger Name, auf den man ausweichen kann
                raise RuntimeError(f"Lokaler Servername '{name}' ist bereits durch einen Remote-Server belegt") from None
            log.warning("Lokaler Servername '%s' ist bereits durch einen Remote-Server belegt; "
                        "der lokale Server behält seinen bisherigen Namen", name)
        finally:
            conn.close()
        poller = poller or Poller(settings, box, collector)
    elif settings.tls == "on":
        if tls_state is None:
            tls_state = TlsState(Path(settings.data_dir))
            tls_state.ensure(utcnow())
        if not tls_state.paired:  # wie beim Setup-Token die einzige erlaubte Ausnahme vom Geheimnis-Verbot im Log (D2)
            log.warning("Kopplungscode (einmalig verwenden): %s", tls_state.pairing_code(settings.public_url))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if own_col:
            collector.start()
        try:
            if manager and own_pol:
                poller.start()
            try:
                yield
            finally:
                if manager and own_pol:
                    poller.stop()
        finally:
            if own_col:
                collector.stop()

    app = FastAPI(title="Moni Manager" if manager else "Moni Agent", version=__version__, docs_url="/api/v1/docs",
                  openapi_url="/api/v1/openapi.json", redoc_url=None, lifespan=lifespan)
    app.state.settings, app.state.collector = settings, collector
    app.state.tokens = TokenStore(Path(settings.data_dir) / "tokens.json")
    app.state.tls = tls_state if not manager and settings.tls == "on" else None
    errors.install(app)
    if manager:
        app.state.box, app.state.poller = box, poller
        app.state.setup_hash, app.state.throttle = setup_hash, LoginThrottle()
        app.state.oidc_flows = oidc.Flows()
        app.include_router(manager_router)
        app.include_router(auth_api.router)
        app.include_router(admin_api.router)
        app.include_router(oidc.router)
    else:
        app.include_router(router)
        app.include_router(metrics_router)
        if settings.tls == "on":
            app.state.pairing_throttle = LoginThrottle(max_failures=10, window=60)
            app.include_router(pairing_api.router)
    app.add_middleware(RateLimitMiddleware, per_minute=settings.rate_limit_per_minute)
    if settings.cors_origins:  # zuletzt = aussen, damit auch 429 CORS-Header bekommt
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET"],
                           allow_headers=["Authorization"])
    return app
