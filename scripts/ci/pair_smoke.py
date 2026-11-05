#!/usr/bin/env python3
"""Kopplungs-Smoketest: pair_smoke.py MANAGER_URL PAIRING_CODE AGENT_URL STATE_FILE.

Koppelt einen echten Agent per mTLS an den Manager und wartet auf Status ok, Sicherheit mtls und Verlaufspunkte.
Angemeldet wird mit den festen CI-Zugangsdaten aus _moni.py und dem Wiederherstellungscode, den
manager_auth_smoke.py in STATE_FILE ablegt (robust gegen den TOTP-Zeitschritt, der nach der Anmeldung dort
bereits verbraucht ist). Gibt nie Passwort, Codes, Cookies oder CSRF aus.
"""
from __future__ import annotations

import sys
import time

from _moni import Client, expect as _expect, fail as _fail, login_with_recovery


def fail(step: str, detail: str = "") -> None:
    _fail("Kopplung", step, detail)


def expect(step: str, got: int, want: int) -> None:
    _expect("Kopplung", step, got, want)


def main(base: str, pairing_code: str, agent_url: str, state_file: str) -> None:
    with open(state_file) as f:
        recovery = f.read().strip()
    c = Client(base)
    csrf = login_with_recovery(c, "Kopplung", recovery)
    st, server = c.call("POST", "/api/v1/servers", {"name": "ci-agent", "pairing_code": pairing_code, "url": agent_url}, csrf=csrf)
    expect("server koppeln", st, 201)
    sid = server["id"]
    deadline = time.time() + 60
    while True:
        st, servers = c.call("GET", "/api/v1/servers")
        expect("servers lesen", st, 200)
        cur = next((s for s in servers if s["id"] == sid), None)
        if cur and cur.get("status") == "ok" and cur.get("security") == "mtls":
            break
        if time.time() > deadline:
            fail("warten auf ok/mtls", f"status={cur and cur.get('status')}, security={cur and cur.get('security')}")
        time.sleep(2)
    while True:
        st, hist = c.call("GET", f"/api/v1/servers/{sid}/history?metric=moni_host_memory_usage_percent")
        expect("verlauf lesen", st, 200)
        n = sum(len(x["points"]) for x in hist["series"])
        if n >= 2:
            break
        if time.time() > deadline:
            fail("warten auf Verlauf", f"{n} Punkte")
        time.sleep(3)
    print("OK Kopplung")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        fail("Aufruf", "pair_smoke.py MANAGER_URL PAIRING_CODE AGENT_URL STATE_FILE")
    main(*sys.argv[1:])
