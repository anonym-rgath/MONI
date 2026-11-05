#!/usr/bin/env python3
"""Anmelde-Smoketest gegen einen laufenden Manager: manager_auth_smoke.py BASE_URL SETUP_TOKEN [STATE_FILE].

Gemeinsame Helfer in _moni.py (Zertifikat des Managers wird nicht geprüft, nur CI). STATE_FILE (0600) bekommt
einen Wiederherstellungscode für pair_smoke.py. Gibt nie Passwort, Secret, Codes, Cookies oder CSRF aus.
"""
from __future__ import annotations

import sys

from _moni import Client, fail as _fail, expect as _expect, setup_admin, write_secret_file


def fail(step: str, detail: str = "") -> None:
    _fail("Anmeldung", step, detail)


def expect(step: str, got: int, want: int) -> None:
    _expect("Anmeldung", step, got, want)


def main(base: str, token: str, state_file: str | None) -> None:
    c = Client(base)
    st, body = c.call("GET", "/api/v1/setup")
    expect("setup abfragen", st, 200)
    if body.get("required") is not True:
        fail("setup abfragen", "required ist nicht true")
    st, _ = c.call("POST", "/api/v1/setup", {"setup_token": "setup_falsch", "email": "x@example.org", "name": "x", "password": "x"})
    expect("setup mit falschem Token", st, 403)
    codes = setup_admin(c, "Anmeldung", token)
    if state_file:
        write_secret_file(state_file, codes[0])
    st, sess = c.call("GET", "/api/v1/auth/session")
    expect("session nach totp", st, 200)
    if sess.get("stage") != "full":
        fail("session nach totp", "stage ist nicht full")
    st, _ = c.call("GET", "/api/v1/servers")
    expect("servers lesen mit Cookie", st, 200)
    st, _ = c.call("POST", "/api/v1/servers", {"name": "x", "url": "http://127.0.0.1:1", "token": "x"})
    expect("servers anlegen ohne CSRF", st, 403)
    old = c.cookie
    st, _ = c.call("POST", "/api/v1/auth/logout", None, csrf=sess.get("csrf_token"))
    expect("logout", st, 204)
    c.cookie = old  # das alte Cookie muss serverseitig tot sein
    st, _ = c.call("GET", "/api/v1/auth/session")
    expect("session nach logout", st, 401)
    print("OK Anmeldung")


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        fail("Aufruf", "manager_auth_smoke.py BASE_URL SETUP_TOKEN [STATE_FILE]")
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else None)
