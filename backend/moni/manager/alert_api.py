"""Alarm-API: offene Alarme, Verlauf, Regeln, Kanäle, Test-Versand, Stummschalten, Einstellungen.

Nie URL, Geheimnisse oder Antwortinhalte von Empfängern in Antworten, Audit oder Log.
"""
import dataclasses
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool, StrictFloat, StrictInt, StrictStr

from moni.auth import audit
from moni.auth.deps import Principal, client_ip, require_scope
from moni.auth.users import Conflict
from moni.manager import alert_channels as ch
from moni.manager import alert_rules, alerting, db
from moni.manager.api import ServerId, _get, _out, _store
from moni.secretbox import SecretError
from moni.timeutil import iso, parse_iso

log = logging.getLogger("moni.manager.alert_api")
router = APIRouter(prefix="/api/v1")
read = Depends(require_scope("read"))
admin = Depends(require_scope("admin"))
Id = Annotated[int, Path(ge=1, le=2**63 - 1)]
MUTE_MAX = 30 * 86400
TEST_MAX, TEST_WINDOW = 6, 60
_sends: dict[int, list[float]] = {}  # Kanal-Id -> Zeitpunkte der Testversuche (gleitendes Fenster, nur im Speicher)
_sends_lock = threading.Lock()


def _ts(t: int | None) -> str | None:
    return None if t is None else iso(datetime.fromtimestamp(t, timezone.utc))


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


Num = StrictInt | StrictFloat


class RulePatch(_Body):
    name: StrictStr = None  # type: ignore[assignment]  # fehlt = unverändert (PATCH) bzw. Standard (POST)
    metric: StrictStr = None  # type: ignore[assignment]
    func: StrictStr = None  # type: ignore[assignment]
    window_seconds: StrictInt | None = None
    op: StrictStr = None  # type: ignore[assignment]
    warning: Num | None = None
    critical: Num | None = None
    for_seconds: StrictInt = None  # type: ignore[assignment]
    key_labels: list[StrictStr] = None  # type: ignore[assignment]
    label_filter: dict[StrictStr, StrictStr] = None  # type: ignore[assignment]
    require_prior_ok: StrictBool = None  # type: ignore[assignment]
    server_id: StrictInt | None = None
    enabled: StrictBool = None  # type: ignore[assignment]


class ChannelPatch(_Body):
    name: StrictStr = None  # type: ignore[assignment]
    kind: StrictStr = None  # type: ignore[assignment]
    url: StrictStr | None = None
    hmac_secret: StrictStr | None = None
    password: StrictStr | None = None
    min_severity: StrictStr = None  # type: ignore[assignment]
    enabled: StrictBool = None  # type: ignore[assignment]
    host: StrictStr = None  # type: ignore[assignment]
    port: StrictInt = None  # type: ignore[assignment]
    security: StrictStr = None  # type: ignore[assignment]
    username: StrictStr | None = None
    from_addr: StrictStr = None  # type: ignore[assignment]
    to: list[StrictStr] = None  # type: ignore[assignment]


class NewChannel(ChannelPatch):
    name: StrictStr
    kind: StrictStr


class MuteBody(_Body):
    until: StrictStr


class SettingsBody(_Body):
    remind_minutes: StrictInt


def _given(body: BaseModel) -> dict:
    return body.model_dump(exclude_unset=True)


def _rule_out(r: alert_rules.Rule) -> dict:
    d = dataclasses.asdict(r)
    return {**d, "key_labels": list(r.key_labels), "created_at": _ts(r.created_at), "updated_at": _ts(r.updated_at)}


def _channel_out(c: ch.Channel) -> dict:
    return {**dataclasses.asdict(c), "created_at": _ts(c.created_at)}


def _fail(e: Exception):
    raise HTTPException(409 if isinstance(e, Conflict) else 422, str(e)) from None


# --- offene Alarme und Verlauf ----------------------------------------------------------------------------------------

@router.get("/alerts", dependencies=[read])
def list_alerts(request: Request):
    now = int(time.time())
    with db.request_conn(request) as conn:
        rows = conn.execute(
            "SELECT i.*, r.name AS rule_name, s.name AS server_name, s.muted_until FROM alert_instances i "
            "JOIN alert_rules r ON r.id=i.rule_id JOIN servers s ON s.id=i.server_id "
            "WHERE i.state IN ('pending','firing') ORDER BY i.fired_at DESC, i.pending_since DESC, i.rule_id").fetchall()
    return [{"rule": {"id": r["rule_id"], "name": r["rule_name"]}, "server": {"id": r["server_id"], "name": r["server_name"]},
             "labels": json.loads(r["key"]), "state": r["state"], "severity": r["severity"], "value": r["value"],
             "since": _ts(r["fired_at"] if r["state"] == "firing" else r["pending_since"]),
             "muted": r["muted_until"] is not None and r["muted_until"] > now} for r in rows]


@router.get("/alerts/history", dependencies=[read])
def alert_history(request: Request, limit: Annotated[int, Query(ge=1, le=1000)] = 100,
                  before: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
                  server_id: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None):
    with db.request_conn(request) as conn:
        rows = conn.execute("SELECT * FROM alert_events WHERE (? IS NULL OR id < ?) AND (? IS NULL OR server_id=?) "
                            "ORDER BY id DESC LIMIT ?", (before, before, server_id, server_id, limit + 1)).fetchall()
    more = len(rows) > limit
    entries = [{"id": r["id"], "ts": _ts(r["ts"]), "rule_id": r["rule_id"], "rule_name": r["rule_name"],
                "server_id": r["server_id"], "server_name": r["server_name"], "labels": json.loads(r["key"]),
                "event": r["event"], "severity": r["severity"], "value": r["value"], "threshold": r["threshold"],
                "reason": r["reason"], "muted": bool(r["muted"])} for r in rows[:limit]]
    return {"entries": entries, "next_before": entries[-1]["id"] if more else None}


# --- Regeln -----------------------------------------------------------------------------------------------------------

@router.get("/alert-rules", dependencies=[read])
def list_rules(request: Request):
    with db.request_conn(request) as conn:
        return [_rule_out(r) for r in alert_rules.RuleStore(conn).list()]


@router.post("/alert-rules", status_code=201)
def create_rule(request: Request, body: RulePatch, p: Principal = admin):
    with db.request_conn(request) as conn:
        try:
            r = alert_rules.RuleStore(conn).create(_given(body), int(time.time()))
        except (ValueError, Conflict) as e:
            _fail(e)
        audit.record(conn, p.actor, "rule.created", target=r.name, ip=client_ip(request))
        return JSONResponse(_rule_out(r), status_code=201)


_EVAL_FIELDS = ("metric", "func", "window_seconds", "op", "warning", "critical", "for_seconds", "key_labels",
                "label_filter", "require_prior_ok", "server_id")


@router.patch("/alert-rules/{rule_id}")
def update_rule(request: Request, rule_id: Id, body: RulePatch, p: Principal = admin):
    patch = _given(body)
    if not patch:
        raise HTTPException(422, "Mindestens ein Feld angeben")
    with db.request_conn(request) as conn:
        store = alert_rules.RuleStore(conn)
        with store._tx():  # Lesen, Auflösen, Ändern und Audit gemeinsam oder gar nicht (P5-R17)
            old = store.get(rule_id)
            if old is None:
                raise HTTPException(404, "Regel nicht gefunden")
            cur = {k: v for k, v in _rule_out(old).items() if k not in ("id", "created_at", "updated_at")}
            merged = {**cur, **patch}  # Schwellenreihenfolge wird gegen die zusammengeführten Werte geprüft
            try:
                v = alert_rules.validate(merged)
            except ValueError as e:
                _fail(e)
            if v == cur:
                return _rule_out(old)  # nichts geändert: kein updated_at, kein Audit
            now = int(time.time())
            if any(v[k] != cur[k] for k in _EVAL_FIELDS):
                alerting.drop_rule(conn, rule_id, now, reason="regel geändert")  # Verlauf mit altem Namen
            try:
                r = store.update(rule_id, merged, now)
            except (ValueError, Conflict) as e:
                _fail(e)
            new = _rule_out(r)
            audit.record(conn, p.actor, "rule.updated", target=r.name, ip=client_ip(request),
                         details=sorted(k for k in patch if new[k] != cur[k]))
        return new


@router.delete("/alert-rules/{rule_id}", status_code=204)
def delete_rule(request: Request, rule_id: Id, p: Principal = admin):
    with db.request_conn(request) as conn:
        store = alert_rules.RuleStore(conn)
        with store._tx():  # Verlauf, Löschen und Audit gemeinsam oder gar nicht (P5-R9)
            old = store.get(rule_id)
            if old is None:
                raise HTTPException(404, "Regel nicht gefunden")
            alerting.drop_rule(conn, rule_id, int(time.time()))
            store.delete(rule_id)
            audit.record(conn, p.actor, "rule.deleted", target=old.name, ip=client_ip(request))
    return Response(status_code=204)


# --- Kanäle -----------------------------------------------------------------------------------------------------------

def _channels(request: Request, conn) -> ch.ChannelStore:
    return ch.ChannelStore(conn, request.app.state.box)


@router.get("/alert-channels", dependencies=[admin])
def list_channels(request: Request):
    with db.request_conn(request) as conn:
        return [_channel_out(c) for c in _channels(request, conn).list()]


@router.post("/alert-channels", status_code=201)
def create_channel(request: Request, body: NewChannel, p: Principal = admin):
    with db.request_conn(request) as conn:
        try:
            c = _channels(request, conn).create(_given(body), int(time.time()))
        except (ValueError, Conflict) as e:
            _fail(e)
        audit.record(conn, p.actor, "channel.created", target=c.name, ip=client_ip(request), details={"kind": c.kind})
        return JSONResponse(_channel_out(c), status_code=201)


@router.patch("/alert-channels/{channel_id}")
def update_channel(request: Request, channel_id: Id, body: ChannelPatch, p: Principal = admin):
    patch = _given(body)
    if not patch:
        raise HTTPException(422, "Mindestens ein Feld angeben")
    with db.request_conn(request) as conn:
        try:
            c = _channels(request, conn).update(channel_id, patch)
        except (ValueError, Conflict) as e:
            _fail(e)
        if c is None:
            raise HTTPException(404, "Kanal nicht gefunden")
        audit.record(conn, p.actor, "channel.updated", target=c.name, ip=client_ip(request), details=sorted(patch))
        return _channel_out(c)


@router.delete("/alert-channels/{channel_id}", status_code=204)
def delete_channel(request: Request, channel_id: Id, p: Principal = admin):
    with db.request_conn(request) as conn:
        store = _channels(request, conn)
        old = store.get(channel_id)
        if old is None or not store.delete(channel_id):
            raise HTTPException(404, "Kanal nicht gefunden")
        audit.record(conn, p.actor, "channel.deleted", target=old.name, ip=client_ip(request))
    return Response(status_code=204)


def _throttled(channel_id: int) -> bool:
    now = time.monotonic()
    with _sends_lock:
        hits = [t for t in _sends.get(channel_id, []) if now - t < TEST_WINDOW]
        if len(hits) >= TEST_MAX:
            _sends[channel_id] = hits
            return True
        _sends[channel_id] = [*hits, now]  # der Versuch zählt vor dem Versand
        return False


@router.post("/alert-channels/{channel_id}/test")
def test_channel(request: Request, channel_id: Id, p: Principal = admin):
    with db.request_conn(request) as conn:
        store = _channels(request, conn)
        c = store.get(channel_id)
        if c is None:
            raise HTTPException(404, "Kanal nicht gefunden")
        if _throttled(channel_id):
            raise HTTPException(429, "Zu viele Testversuche, bitte eine Minute warten",
                                headers={"Retry-After": str(TEST_WINDOW)})
        name = (conn.execute("SELECT name FROM servers WHERE is_local=1").fetchone() or ["moni"])[0]
        try:
            secrets = store.secrets(channel_id)
        except SecretError:
            secrets = None
        if conn.in_transaction:
            conn.commit()  # kein Netzwerk unter offener Transaktion
        event = {"event": "test", "severity": "warning", "rule_name": "Test", "server_name": name, "key": "{}",
                 "value": None, "threshold": None, "reason": None, "ts": int(time.time())}
        error = None
        if secrets is None:
            error = "Kanal-Geheimnis nicht entschlüsselbar"
        else:
            try:
                ch.deliver(c, secrets, event, name)
            except ch.SendError as e:
                error = str(e)
            except Exception as e:
                log.error("Test-Versand fehlgeschlagen (%s)", type(e).__name__)
                error = "Versand fehlgeschlagen"
        audit.record(conn, p.actor, "channel.tested", target=c.name, ip=client_ip(request), details={"ok": error is None})
    return {"ok": error is None, "error": error}


# --- Stummschalten und Einstellungen ----------------------------------------------------------------------------------

@router.put("/servers/{server_id}/mute")
def mute(request: Request, server_id: ServerId, body: MuteBody, p: Principal = admin):
    now = int(time.time())
    try:
        until = parse_iso(body.until)
    except (ValueError, OverflowError):
        raise HTTPException(422, "Zeitpunkt muss ein ISO-8601-Zeitstempel mit Zeitzone sein") from None
    if not now < until.timestamp() <= now + MUTE_MAX:
        raise HTTPException(422, "Stummschaltung muss in der Zukunft liegen und darf höchstens 30 Tage dauern")
    with _store(request) as (conn, store):
        s = _get(store, server_id)
        store.set_muted(server_id, int(until.timestamp()))
        audit.record(conn, p.actor, "server.muted", target=s.name, ip=client_ip(request), details={"until": iso(until)})
        return _out(conn, _get(store, server_id))


@router.delete("/servers/{server_id}/mute", status_code=204)
def unmute(request: Request, server_id: ServerId, p: Principal = admin):
    with _store(request) as (conn, store):
        s = _get(store, server_id)
        store.set_muted(server_id, None)
        audit.record(conn, p.actor, "server.unmuted", target=s.name, ip=client_ip(request))
    return Response(status_code=204)


@router.get("/alert-settings", dependencies=[admin])
def get_settings(request: Request):
    with db.request_conn(request) as conn:
        return alerting.settings(conn)


@router.put("/alert-settings")
def put_settings(request: Request, body: SettingsBody, p: Principal = admin):
    with db.request_conn(request) as conn:
        try:
            out = alerting.save_settings(conn, body.remind_minutes)
        except ValueError as e:
            _fail(e)
        audit.record(conn, p.actor, "alerting.settings_changed", target="alerting", ip=client_ip(request), details=out)
        return out
