import pytest

from moni.manager.alert_eval import Instance, evaluate, increase, instance_key, breach, values_for
from moni.manager.alert_rules import Rule
from moni.metrics_model import Sample

K = "{}"


def rule(**over) -> Rule:
    base = dict(id=1, name="r", metric="m", func="value", window_seconds=None, op=">", warning=None, critical=None,
                for_seconds=0, key_labels=(), label_filter={}, require_prior_ok=False, server_id=None,
                enabled=True, created_at=0, updated_at=0)
    base.update(over)
    return Rule(**base)


def inst(state="ok", severity=None, value=None, pending_since=None, fired_at=None, last_notified=None, seen_ok=False):
    return Instance(K, state, severity, value, pending_since, fired_at, last_notified, seen_ok)


def test_pending_then_firing_after_duration():
    r = rule(op=">", warning=75, critical=85, for_seconds=300)
    t = evaluate(r, {K: 80}, {}, 1000, 0)
    assert t[0].event is None and t[0].instance.state == "pending" and t[0].instance.pending_since == 1000
    cur = {K: t[0].instance}
    assert evaluate(r, {K: 80}, cur, 1299, 0)[0].instance.state == "pending"
    t = evaluate(r, {K: 80}, cur, 1300, 0)
    assert t[0].event == "firing" and t[0].severity == "warning" and t[0].threshold == 75
    assert t[0].instance.state == "firing" and t[0].instance.fired_at == 1300


def test_for_zero_fires_immediately():
    t = evaluate(rule(critical=1, for_seconds=0), {K: 5}, {}, 10, 0)
    assert t[0].event == "firing" and t[0].severity == "critical"


def test_exactly_at_threshold_with_strict_op_does_not_breach():
    r = rule(op=">", warning=75)
    assert breach(r, 75) == (None, None)
    assert evaluate(r, {K: 75}, {}, 0, 0)[0].instance is None
    assert breach(rule(op=">=", warning=75), 75) == ("warning", 75)
    assert breach(rule(op=">", warning=75, critical=85), 90) == ("critical", 85)


def test_dip_below_resets_pending():
    r = rule(warning=75, for_seconds=300)
    p = evaluate(r, {K: 80}, {}, 1000, 0)[0].instance
    t = evaluate(r, {K: 70}, {K: p}, 1100, 0)[0]
    assert t.event is None and t.instance is None
    again = evaluate(r, {K: 80}, {}, 1200, 0)[0].instance
    assert again.pending_since == 1200


def test_escalation_emits_changed_and_resolution_resolved():
    r = rule(warning=75, critical=85)
    f = evaluate(r, {K: 80}, {}, 0, 0)[0].instance
    assert f.severity == "warning"
    t = evaluate(r, {K: 90}, {K: f}, 10, 0)[0]
    assert t.event == "changed" and t.severity == "critical" and t.threshold == 85
    assert t.instance.severity == "critical" and t.instance.fired_at == 0
    t2 = evaluate(r, {K: 10}, {K: t.instance}, 20, 0)[0]
    assert t2.event == "resolved" and t2.severity == "critical" and t2.value == 10 and t2.instance is None
    down = evaluate(r, {K: 80}, {K: t.instance}, 20, 0)[0]
    assert down.event == "changed" and down.severity == "warning" and down.threshold == 75


def test_reminder_respects_interval_and_zero_disables():
    r = rule(warning=75)
    f = inst("firing", "warning", 80, fired_at=0, last_notified=1000)
    assert evaluate(r, {K: 80}, {K: f}, 1000 + 599, 600)[0].event is None
    t = evaluate(r, {K: 80}, {K: f}, 1600, 600)[0]
    assert t.event == "reminder" and t.instance.last_notified == 1000
    assert evaluate(r, {K: 80}, {K: f}, 10**6, 0)[0].event is None
    never = inst("firing", "warning", 80, fired_at=0, last_notified=None)
    assert evaluate(r, {K: 80}, {K: never}, 10**6, 600)[0].event is None


def test_frozen_values_keep_state_but_allow_reminder():
    r = rule(warning=75, for_seconds=300)
    f = inst("firing", "warning", 80, fired_at=0, last_notified=0)
    p = Instance("p", "pending", "warning", 80, 5, None, None, False)
    t = evaluate(r, None, {K: f, "p": p}, 1000, 600)
    by = {x.key: x for x in t}
    assert by[K].event == "reminder" and by["p"].event is None
    assert by["p"].instance == p and by[K].instance.state == "firing"
    assert evaluate(r, None, {K: f}, 100, 600)[0].event is None


def test_require_prior_ok():
    r = rule(metric="moni_container_up", op="<", critical=1, require_prior_ok=True, for_seconds=0)
    t = evaluate(r, {K: 0}, {}, 0, 0)[0]
    assert t.instance.state == "ok" and t.event is None and not t.instance.seen_ok
    seen = evaluate(r, {K: 1}, {}, 0, 0)[0].instance
    assert seen.seen_ok and seen.state == "ok"
    assert evaluate(r, {K: 0}, {K: seen}, 15, 0)[0].event == "firing"
    # nach Behebung bleibt seen_ok erhalten
    f = evaluate(r, {K: 0}, {K: seen}, 15, 0)[0].instance
    back = evaluate(r, {K: 1}, {K: f}, 30, 0)[0]
    assert back.event == "resolved" and back.instance.state == "ok" and back.instance.seen_ok


def test_vanished_firing_instance_resolved_with_reason_entfallen():
    r = rule(warning=75)
    f = inst("firing", "warning", 80, fired_at=0)
    t = evaluate(r, {}, {K: f}, 10, 0)[0]
    assert t.event == "resolved" and t.reason == "entfallen" and t.instance is None
    assert t.severity == "warning" and t.value == 80 and t.threshold is None


def test_vanished_pending_instance_dropped_silently():
    p = inst("pending", "warning", 80, pending_since=0)
    t = evaluate(rule(warning=75, for_seconds=60), {}, {K: p}, 10, 0)[0]
    assert t.event is None and t.instance is None


def test_group_by_uses_max_for_greater_ops():
    samples = [Sample("m", (("name", "a"), ("x", "1")), 10), Sample("m", (("name", "a"), ("x", "2")), 90),
               Sample("m", (("name", "b"), ("x", "1")), 5), Sample("other", (("name", "a"),), 99)]
    ka, kb = instance_key({"name": "a"}, ("name",)), instance_key({"name": "b"}, ("name",))
    assert values_for(rule(key_labels=("name",), op=">"), samples) == {ka: 90, kb: 5}
    assert values_for(rule(key_labels=("name",), op="<"), samples) == {ka: 10, kb: 5}
    assert values_for(rule(key_labels=("name",), label_filter={"x": "2"}), samples) == {ka: 90}


def test_instance_key_is_canonical_and_missing_label_is_empty():
    assert instance_key({"b": "2", "a": "1", "z": "9"}, ("b", "a")) == '{"a":"1","b":"2"}'
    assert instance_key({}, ()) == "{}"
    assert instance_key({}, ("a",)) == '{"a":""}'


@pytest.mark.parametrize("pts,expected", [([(0, 1), (60, 2), (120, 4)], 3), ([(0, 5), (60, 0), (120, 2)], 2), ([], 0)])
def test_increase_handles_resets(pts, expected):
    assert increase(pts) == expected


def test_lower_than_ops_mirror():
    r = rule(op="<", warning=20, critical=10)
    assert breach(r, 15) == ("warning", 20)
    assert breach(r, 5) == ("critical", 10)
    assert breach(r, 20) == (None, None)
    assert breach(rule(op="<=", warning=20), 20) == ("warning", 20)
