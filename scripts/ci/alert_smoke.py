#!/usr/bin/env python3
"""Alarmkanal-Smoketest: alert_smoke.py MANAGER_URL STATE_FILE LISTEN_PORT.

Legt einen Webhook-Kanal an, löst den Test-Versand aus und prüft, dass der Manager-Container einen echten
Webhook mit gültiger HMAC-Signatur schickt (unabhängig nachgerechnet, ohne Moni-Code).

Umgebung:
  HOOK_HOST  Adresse des Runners aus Sicht des Manager-Containers (Pflicht). In der CI das Gateway des
             Docker-Netzes des Managers.
  HOOK_BIND  Bind-Adresse des Empfängers, Standard: HOOK_HOST. Der Empfänger bindet nie 0.0.0.0.
             Lokal mit Docker Desktop: HOOK_HOST=host.docker.internal HOOK_BIND=127.0.0.1

Angemeldet wird wie in pair_smoke.py mit dem nächsten Wiederherstellungscode aus STATE_FILE (wird entnommen). Gibt nie Secret,
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

from _moni import HMAC_SECRET as SECRET, Client, expect as _expect, fail as _fail, login_with_recovery, take_recovery, verify

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


def main(base: str, state_file: str, port: int) -> None:
    host = os.environ.get("HOOK_HOST") or fail("Umgebung", "HOOK_HOST fehlt")
    bind = os.environ.get("HOOK_BIND") or host
    if bind in ("0.0.0.0", "::", ""):
        fail("Umgebung", "HOOK_BIND darf nicht alle Schnittstellen sein")
    recovery = take_recovery(state_file)
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
    if json.loads(received[0][1]).get("event") != "test":
        fail("webhook prüfen", "event != test")
    srv.shutdown()
    # Kanal wieder entfernen: sein Endpunkt ist nach dem Test tot und soll später keine Fehlzustellungen erzeugen
    st, _ = c.call("DELETE", f"/api/v1/alert-channels/{ch['id']}", csrf=csrf)
    expect("kanal löschen", st, 204)
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
