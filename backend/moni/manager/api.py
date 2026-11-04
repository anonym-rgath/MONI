"""Manager-API v1: Serverliste, Verwaltung (admin) und Verlaufsabfrage. Nie Token- oder Header-Werte ausgeben."""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from moni import __version__
from moni.auth.deps import require_scope
from moni.collect.host import read_hostname
from moni.manager import db, tsdb
from moni.manager.servers import Server, ServerStore
from moni.timeutil import iso, parse_iso, utcnow

router = APIRouter(prefix="/api/v1")
read = Depends(require_scope("read"))
admin = Depends(require_scope("admin"))


class NewServer(BaseModel):
    name: str
    url: str
    token: str
    headers: dict[str, str] | None = None


class Patch(BaseModel):
    enabled: bool


@contextmanager
def _store(request: Request):
    # Verbindung im selben Worker-Thread öffnen und nutzen (check_same_thread bleibt aktiv); eine Sync-Dependency
    # könnte in einem anderen Thread laufen als der Endpoint.
    conn = db.connect(request.app.state.settings.db_path)
    try:
        yield conn, ServerStore(conn, request.app.state.box)
    finally:
        conn.close()


def _json(s: str | None):
    return json.loads(s) if s else None


def _out(conn, s: Server, detail: bool = False) -> dict:
    r = conn.execute("SELECT last_snapshot, agent_info FROM servers WHERE id=?", (s.id,)).fetchone()
    snap, info = _json(r["last_snapshot"]), _json(r["agent_info"])
    out = {"id": s.id, "name": s.name, "url": s.url, "is_local": s.is_local, "enabled": s.enabled, "status": s.status,
           "failures": s.failures, "last_error": s.last_error,
           "last_seen": iso(datetime.fromtimestamp(s.last_seen, timezone.utc)) if s.last_seen is not None else None,
           "header_names": s.header_names, "warnings": len((snap or {}).get("warnings") or []),
           "agent_version": (info or {}).get("version")}
    if detail:
        out["agent_info"] = info
    return out


def _get(store: ServerStore, server_id: int) -> Server:
    s = store.get(server_id)
    if s is None:
        raise HTTPException(404, "Server nicht gefunden")
    return s


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/info", dependencies=[read])
def info(request: Request):
    cfg = request.app.state.settings
    with _store(request) as (_, store):
        n = len(store.list())
    return {"version": __version__, "role": "manager", "api_version": "v1", "hostname": read_hostname(cfg.host_etc),
            "tls": cfg.tls, "server_count": n}


@router.get("/servers", dependencies=[read])
def list_servers(request: Request):
    with _store(request) as (conn, store):
        return [_out(conn, s) for s in store.list()]


@router.post("/servers", dependencies=[admin], status_code=201)
def create_server(request: Request, body: NewServer):
    with _store(request) as (conn, store):
        try:
            return _out(conn, store.add(body.name, body.url, body.token, body.headers))
        except ValueError as e:
            raise HTTPException(422, str(e)) from None


@router.get("/servers/{server_id}", dependencies=[read])
def get_server(request: Request, server_id: int):
    with _store(request) as (conn, store):
        return _out(conn, _get(store, server_id), detail=True)


@router.patch("/servers/{server_id}", dependencies=[admin])
def patch_server(request: Request, server_id: int, body: Patch):
    with _store(request) as (conn, store):
        if not store.set_enabled(server_id, body.enabled):
            raise HTTPException(404, "Server nicht gefunden")
        return _out(conn, _get(store, server_id))


@router.delete("/servers/{server_id}", dependencies=[admin], status_code=204)
def delete_server(request: Request, server_id: int):
    with _store(request) as (_, store):
        try:
            found = store.delete(server_id)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
    if not found:
        raise HTTPException(404, "Server nicht gefunden")
    return Response(status_code=204)


@router.get("/servers/{server_id}/snapshot", dependencies=[read])
def snapshot(request: Request, server_id: int):
    with _store(request) as (conn, store):
        _get(store, server_id)
        snap = conn.execute("SELECT last_snapshot FROM servers WHERE id=?", (server_id,)).fetchone()[0]
    if snap is None:
        raise HTTPException(404, "Noch kein Snapshot")
    return Response(snap, media_type="application/json")


@router.get("/servers/{server_id}/series", dependencies=[read])
def series(request: Request, server_id: int):
    with _store(request) as (conn, store):
        _get(store, server_id)
        return tsdb.list_series(conn, server_id)


@router.get("/servers/{server_id}/history", dependencies=[read])
def history(request: Request, server_id: int, metric: str, from_: str | None = Query(None, alias="from"),
            to: str | None = None, resolution: str = "auto", label: list[str] = Query(default=[])):
    try:
        end = parse_iso(to) if to else utcnow()
        start = parse_iso(from_) if from_ else end - timedelta(hours=1)
    except ValueError:
        raise HTTPException(422, "Parameter from/to müssen ISO-8601-Zeitstempel mit Zeitzone sein") from None
    labels = {}
    for item in label:
        k, sep, v = item.partition(":")
        if not sep:
            raise HTTPException(422, "Parameter label muss die Form key:value haben")
        labels[k] = v
    with _store(request) as (conn, store):
        _get(store, server_id)
        try:
            return tsdb.query(conn, server_id, metric, int(start.timestamp()), int(end.timestamp()), resolution, labels or None)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
