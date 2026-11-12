#!/usr/bin/env python3
"""E2E-Alarmtest: e2e_alert.py MANAGER_URL STATE_FILE LISTEN_PORT AGENT_NAME --stop-cmd CMD --start-cmd CMD

Löst die Standardregel "Server nicht erreichbar" echt aus (D9): Agent stoppen -> Webhook "firing", Agent starten ->
Webhook "resolved" mit reason null; danach liefert der Agent ohne neue Kopplung wieder Daten. Der Agent muss bereits
gekoppelt sein (Name AGENT_NAME). CMD läuft ohne Shell (shlex.split). Bei jedem Fehlschlag läuft --start-cmd trotzdem.

Umgebung: HOOK_HOST / HOOK_BIND wie in alert_smoke.py. Angemeldet wird mit dem nächsten Wiederherstellungscode aus
STATE_FILE. Gibt nie Secret, Passwort, Codes oder Cookies aus.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from _moni import HMAC_SECRET, Client, expect as _expect, fail as _fail, login_with_recovery, take_recovery, verify

LABEL = "E2E-Alarm"
RULE = "Server nicht erreichbar"
METRIC = "moni_host_memory_usage_percent"
events: list[dict] = []  # {"payload": dict, "sig_err": str | None}, vom Empfänger-Thread befüllt


def fail(step: str, detail: str = "") -> None:
    _fail(LABEL, step, detail)


def expect(step: str, got: int, want: int) -> None:
    _expect(LABEL, step, got, want)


class Hook(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {}
        events.append({"payload": payload if isinstance(payload, dict) else {}, "sig_err": verify(dict(self.headers.items()), body)})
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


def find_event(evs: list[dict], event: str, rule: str, server: str) -> dict | None:
    """Erster passender Webhook (Ereignis, Regel, Server), sonst None. Die Signatur prüft der Aufrufer."""
    for e in evs:
        p = e["payload"]
        if p.get("event") == event and p.get("rule") == rule and p.get("server") == server:
            return e
    return None


def wait_for(label: str, seconds: int, check, interval: float = 2):
    """Ruft check() bis zur Frist auf, gibt das erste Ergebnis zurück, das nicht None/False ist."""
    end = time.time() + seconds
    while True:
        r = check()
        if r:
            return r
        if time.time() > end:
            fail(f"warten auf {label}", f"keine Antwort in {seconds} s")
        time.sleep(interval)


def run(cmd: str) -> None:
    subprocess.run(shlex.split(cmd), check=True)


def main(base: str, state_file: str, port: int, agent: str, stop_cmd: str, start_cmd: str) -> None:
    host = os.environ.get("HOOK_HOST") or fail("Umgebung", "HOOK_HOST fehlt")
    bind = os.environ.get("HOOK_BIND") or host
    if bind in ("0.0.0.0", "::", ""):
        fail("Umgebung", "HOOK_BIND darf nicht alle Schnittstellen sein")
    srv = HTTPServer((bind, port), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    stopped = False
    try:
        c = Client(base)
        csrf = login_with_recovery(c, LABEL, take_recovery(state_file))
        st, _ = c.call("POST", "/api/v1/alert-channels", {"name": "ci-e2e", "kind": "webhook", "url": f"http://{host}:{port}/hook",
                                                          "hmac_secret": HMAC_SECRET, "min_severity": "warning"}, csrf=csrf)
        expect("kanal anlegen", st, 201)

        def server() -> dict | None:
            st, servers = c.call("GET", "/api/v1/servers")
            expect("servers lesen", st, 200)
            return next((s for s in servers if s["name"] == agent), None)

        def points(sid: int) -> int:
            st, hist = c.call("GET", f"/api/v1/servers/{sid}/history?metric={METRIC}")
            expect("verlauf lesen", st, 200)
            return sum(len(x["points"]) for x in hist["series"])

        srv_row = wait_for("Server", 30, server)
        sid = srv_row["id"]
        wait_for("Status ok", 90, lambda: (server() or {}).get("status") == "ok")
        before = wait_for("2 Verlaufspunkte", 90, lambda: points(sid) if points(sid) >= 2 else None)

        stopped = True
        run(stop_cmd)
        fired = wait_for("Webhook firing", 120, lambda: find_event(events, "firing", RULE, agent))
        if fired["sig_err"]:
            fail("webhook firing prüfen", fired["sig_err"])

        run(start_cmd)
        stopped = False
        resolved = wait_for("Webhook resolved", 120, lambda: find_event(events, "resolved", RULE, agent))
        if resolved["sig_err"]:
            fail("webhook resolved prüfen", resolved["sig_err"])
        if resolved["payload"].get("reason") is not None:
            fail("webhook resolved prüfen", "reason nicht null")
        wait_for("Status ok nach Neustart", 120, lambda: (server() or {}).get("status") == "ok")
        after = points(sid)
        if after < before:
            fail("verlauf nach neustart", f"{after} Punkte, vorher {before}")

        st, hist = c.call("GET", f"/api/v1/alerts/history?server_id={sid}")
        expect("alarmverlauf lesen", st, 200)
        seen = {e["event"] for e in hist["entries"] if e["rule_name"] == RULE}
        if not {"firing", "resolved"} <= seen:
            fail("alarmverlauf", f"Ereignisse {sorted(seen)}")
        print("OK E2E-Alarm")
    finally:
        srv.shutdown()
        if stopped:  # Fehlschlag zwischen Stopp und Start: Agent wieder starten, damit die Aufräumschritte ihn finden
            run(start_cmd)


def selftest() -> None:
    def ev(event, server="a", rule=RULE, err=None, **extra):
        return {"payload": {"event": event, "rule": rule, "server": server, **extra}, "sig_err": err}

    evs = [ev("firing", server="b"), ev("firing", rule="CPU-Auslastung"), ev("resolved"), ev("firing", err="x")]
    assert find_event(evs, "firing", RULE, "a")["sig_err"] == "x"  # Treffer, Signaturfehler bleibt sichtbar
    assert find_event(evs, "resolved", RULE, "a") is evs[2]
    assert find_event(evs, "changed", RULE, "a") is None
    assert find_event(evs, "firing", RULE, "c") is None
    assert find_event([], "firing", RULE, "a") is None
    assert ev("resolved", reason=None)["payload"].get("reason") is None
    assert ev("resolved", reason="rule_deleted")["payload"].get("reason") is not None


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--selftest"]:
        selftest()
        print("OK Selbsttest")
    elif len(a) == 8 and a[4] == "--stop-cmd" and a[6] == "--start-cmd":
        main(a[0], a[1], int(a[2]), a[3], a[5], a[7])
    else:
        fail("Aufruf", "e2e_alert.py MANAGER_URL STATE_FILE LISTEN_PORT AGENT_NAME --stop-cmd CMD --start-cmd CMD")
