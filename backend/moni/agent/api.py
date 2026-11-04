from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from moni import __version__
from moni.agent import prometheus
from moni.auth.deps import require_scope
from moni.collect.host import read_hostname
from moni.timeutil import parse_iso

router = APIRouter(prefix="/api/v1")
metrics_router = APIRouter()  # bewusst ohne /api/v1-Präfix
read = Depends(require_scope("read"))


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/info", dependencies=[read])
def info(request: Request):
    return {"version": __version__, "role": "agent", "api_version": "v1",
            "hostname": read_hostname(request.app.state.settings.host_etc),
            "capabilities": ["snapshot", "history", "metrics"], "paired": False, "tls": request.app.state.settings.tls}


@router.get("/snapshot", dependencies=[read])
def snapshot(request: Request):
    snap = request.app.state.collector.latest()
    if snap is None:
        raise HTTPException(503, "Noch keine Messung vorhanden")
    return snap


@router.get("/history", dependencies=[read])
def history(request: Request, since: str = Query(...)):
    try:
        t = parse_iso(since)
    except ValueError:
        raise HTTPException(422, "Parameter since muss ein ISO-8601-Zeitstempel mit Zeitzone sein") from None
    points, oldest = request.app.state.collector.history(t)
    return {"points": points, "oldest": oldest}


@metrics_router.get("/metrics", dependencies=[read])
def metrics(request: Request):
    snap = request.app.state.collector.latest()
    if snap is None:
        raise HTTPException(503, "Noch keine Messung vorhanden")
    return Response(prometheus.render(snap), media_type=prometheus.CONTENT_TYPE)
