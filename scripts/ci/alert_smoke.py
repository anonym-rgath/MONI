#!/usr/bin/env python3
"""Alarmkanal-Smoketest: alert_smoke.py MANAGER_URL STATE_FILE LISTEN_PORT.

Legt einen Webhook-Kanal an, löst den Test-Versand aus und prüft, dass der Manager-Container einen echten
Webhook mit gültiger HMAC-Signatur schickt (unabhängig nachgerechnet, ohne Moni-Code).

Umgebung:
  HOOK_HOST  Adresse des Runners aus Sicht des Manager-Containers (Pflicht). In der CI das Gateway des
             Docker-Netzes des Managers.
  HOOK_BIND  Bind-Adresse des Empfängers, Standard: HOOK_HOST. Der Empfänger bindet nie 0.0.0.0.
             Lokal mit Docker Desktop: HOOK_HOST=host.docker.internal HOOK_BIND=127.0.0.1

Angemeldet wird wie in pair_smoke.py mit dem Wiederherstellungscode aus STATE_FILE. Gibt nie Secret,
Passwort, Codes oder Cookies aus.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from _moni import Client, expect as _expect, fail as _fail, login_with_recovery

SECRET = "ci-hmac-geheim-5f2c"  # fester CI-Wert, die CI prüft, dass er nicht im Log steht
received: list[tuple[dict, bytes]] = []
got = threading.Event()


def fail(step: str, detail: str = "") -> None:
    _fail("Alarmkanal", step, detail)


def expect(step: str, got_: int, want: int) -> None:
    _expect("Alarmkanal", step, got_, want)


class Hook(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        received.append((dict(self.headers.items()), body))
        self.send_response(204)
        self.end_headers()
        got.set()

    def log_message(self, *args) -> None:  # kein Zugriffslog
        pass


def verify(headers: dict, body: bytes, now: float | None = None) -> str | None:
    """Gibt einen Fehlertext zurück, None bei gültiger Anfrage."""
    h = {k.lower(): v for k, v in headers.items()}
    try:
        ts = int(h["x-moni-timestamp"])
        sig = h["x-moni-signature"]
    except (KeyError, ValueError):
        return "Signatur- oder Zeitstempel-Header fehlt"
    want = "sha256=" + hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, want):
        return "HMAC-Signatur ungültig"
    if abs((time.time() if now is None else now) - ts) > 300:
        return "Zeitstempel außerhalb von 300 s"
    try:
        if json.loads(body).get("event") != "test":
            return "event != test"
    except ValueError:
        return "Body kein JSON"
    return None


def main(base: str, state_file: str, port: int) -> None:
    host = os.environ.get("HOOK_HOST") or fail("Umgebung", "HOOK_HOST fehlt")
    bind = os.environ.get("HOOK_BIND") or host
    if bind in ("0.0.0.0", "::", ""):
        fail("Umgebung", "HOOK_BIND darf nicht alle Schnittstellen sein")
    with open(state_file) as f:
        recovery = f.read().strip()
    srv = HTTPServer((bind, port), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    c = Client(base)
    csrf = login_with_recovery(c, "Alarmkanal", recovery)
    st, ch = c.call("POST", "/api/v1/alert-channels", {"name": "ci", "kind": "webhook", "url": f"http://{host}:{port}/hook",
                                                       "hmac_secret": SECRET}, csrf=csrf)
    expect("kanal anlegen", st, 201)
    st, res = c.call("POST", f"/api/v1/alert-channels/{ch['id']}/test", csrf=csrf)
    expect("test-versand", st, 200)
    if res.get("ok") is not True:
        fail("test-versand", f"ok={res.get('ok')}, error={res.get('error')}")
    if not got.wait(10):
        fail("warten auf Webhook", "keine Anfrage in 10 s")
    err = verify(*received[0])
    if err:
        fail("webhook prüfen", err)
    srv.shutdown()
    print("OK Alarmkanal")


def selftest() -> None:
    body = json.dumps({"event": "test"}).encode()
    ts = int(time.time())
    sig = "sha256=" + hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    h = {"X-Moni-Timestamp": str(ts), "X-Moni-Signature": sig}
    assert verify(h, body) is None
    assert verify(h, body + b" ") == "HMAC-Signatur ungültig"
    assert verify(h, body, now=ts + 301) == "Zeitstempel außerhalb von 300 s"
    assert verify({}, body) is not None


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--selftest":
        selftest()
        print("OK Selbsttest")
    elif len(sys.argv) != 4:
        fail("Aufruf", "alert_smoke.py MANAGER_URL STATE_FILE LISTEN_PORT")
    else:
        main(sys.argv[1], sys.argv[2], int(sys.argv[3]))
