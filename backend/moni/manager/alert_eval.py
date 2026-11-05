"""Reiner Zustandsautomat für Alarmregeln (kein DB-, kein Netzwerkzugriff).

evaluate liefert genau eine Transition je Schlüssel aus values und current, auch unveränderte.
Transition.instance None heißt: Instanz löschen. Ein Schlüssel im Zustand ok ohne Gedächtnis wird als None
zurückgegeben (nichts zu speichern); nur bei require_prior_ok bleibt die ok-Instanz mit seen_ok erhalten.
last_notified setzt evaluate nie selbst, es wird unverändert durchgereicht.
"""
import json
import operator
from dataclasses import dataclass, replace

from ..metrics_model import Sample
from .alert_rules import Rule

_CMP = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


@dataclass
class Instance:
    key: str
    state: str  # ok | pending | firing
    severity: str | None
    value: float | None
    pending_since: int | None
    fired_at: int | None
    last_notified: int | None
    seen_ok: bool


@dataclass(frozen=True)
class Transition:
    key: str
    event: str | None  # firing | resolved | changed | reminder | None
    instance: Instance | None
    severity: str | None
    value: float | None
    threshold: float | None
    reason: str | None = None


def instance_key(labels: dict[str, str], key_labels: tuple[str, ...]) -> str:
    # Fehlendes Label zählt als "" im Schlüssel.
    return json.dumps({k: labels.get(k, "") for k in key_labels}, sort_keys=True, separators=(",", ":"))


def breach(rule: Rule, value: float) -> tuple[str | None, float | None]:
    cmp = _CMP[rule.op]
    for sev, thr in (("critical", rule.critical), ("warning", rule.warning)):
        if thr is not None and cmp(value, thr):
            return sev, thr
    return None, None


def increase(points: list[tuple[int, float]]) -> float:
    """Summe der positiven Differenzen; ein Rücksprung (Zähler-Reset) zählt nicht als Abnahme (D2)."""
    return sum(max(b[1] - a[1], 0) for a, b in zip(points, points[1:]))


def values_for(rule: Rule, samples: list[Sample]) -> dict[str, float]:
    pick = max if rule.op in (">", ">=") else min
    out: dict[str, float] = {}
    for s in samples:
        labels = dict(s.labels)
        if s.name != rule.metric or any(labels.get(k) != v for k, v in rule.label_filter.items()):
            continue
        k = instance_key(labels, rule.key_labels)
        out[k] = pick(out[k], s.value) if k in out else s.value
    return out


def _thr(rule: Rule, sev: str | None) -> float | None:
    return {"critical": rule.critical, "warning": rule.warning}.get(sev)


def _reminder(rule: Rule, cur: Instance, now: int, remind_seconds: int) -> Transition:
    due = (cur.state == "firing" and remind_seconds > 0 and cur.last_notified is not None
           and now - cur.last_notified >= remind_seconds)
    return Transition(cur.key, "reminder" if due else None, cur, cur.severity, cur.value, _thr(rule, cur.severity))


def _one(rule: Rule, key: str, v: float, cur: Instance | None, now: int, remind_seconds: int) -> Transition:
    sev, thr = breach(rule, v)
    base = cur or Instance(key, "ok", None, None, None, None, None, False)
    if sev is None:
        if base.state == "firing":
            ok = replace(base, state="ok", severity=None, value=v, pending_since=None, fired_at=None, seen_ok=True)
            ok = ok if rule.require_prior_ok else None
            return Transition(key, "resolved", ok, base.severity, v, None)
        if rule.require_prior_ok:
            return Transition(key, None, replace(base, state="ok", severity=None, value=v, pending_since=None,
                                                 seen_ok=True), None, v, None)
        return Transition(key, None, None, None, v, None)
    if base.state == "firing":
        new = replace(base, severity=sev, value=v)
        if sev != base.severity:
            return Transition(key, "changed", new, sev, v, thr)
        return replace(_reminder(rule, new, now, remind_seconds), value=v)
    if base.state == "ok":
        if rule.require_prior_ok and not base.seen_ok:
            return Transition(key, None, replace(base, value=v), None, v, None)
        base = replace(base, state="pending", pending_since=now)
    new = replace(base, severity=sev, value=v)
    if now - new.pending_since >= rule.for_seconds:
        new = replace(new, state="firing", fired_at=now)
        return Transition(key, "firing", new, sev, v, thr)
    return Transition(key, None, new, sev, v, thr)


def evaluate(rule: Rule, values: dict[str, float] | None, current: dict[str, Instance], now: int,
             remind_seconds: int) -> list[Transition]:
    if values is None:  # eingefroren (D2): nur Erinnerungen
        return [_reminder(rule, c, now, remind_seconds) for c in current.values()]
    out = [_one(rule, k, v, current.get(k), now, remind_seconds) for k, v in values.items()]
    for k, c in current.items():
        if k in values:
            continue
        if c.state == "firing":  # D10
            out.append(Transition(k, "resolved", None, c.severity, c.value, None, "entfallen"))
        else:
            out.append(Transition(k, None, None, None, None, None))
    return out
