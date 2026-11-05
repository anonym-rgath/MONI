#!/usr/bin/env python3
"""Anmelde-Smoketest gegen einen laufenden Manager: manager_auth_smoke.py BASE_URL SETUP_TOKEN.

Nur Standardbibliothek. Das Cookie ist "Secure" und wird deshalb per Header von Hand mitgeschickt.
Gibt nie Passwort, Secret, Codes, Cookies oder CSRF aus, nur Schritt und HTTP-Status.
"""
import base64
import hashlib
import hmac
import json
import struct
import sys
import time
import urllib.error
import urllib.request

PASSWORD = "ci-smoke-Passwort-2026"  # fester CI-Wert, die CI prüft, dass er nicht im Log steht
EMAIL = "ci-admin@example.org"


def fail(step: str, detail: str = "") -> None:
    print(f"FAIL Anmeldung: {step}{' (' + detail + ')' if detail else ''}")
    sys.exit(1)


class Client:
    def __init__(self, base: str):
        self.base, self.cookie = base.rstrip("/"), None

    def call(self, method: str, path: str, body: dict | None = None, csrf: str | None = None) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = f"moni_session={self.cookie}"
        if csrf:
            headers["X-CSRF-Token"] = csrf
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            r = urllib.request.urlopen(req, timeout=15)
        except urllib.error.HTTPError as e:
            r = e
        for h in r.headers.get_all("Set-Cookie") or []:
            if h.startswith("moni_session="):
                v = h.split(";", 1)[0].split("=", 1)[1]
                self.cookie = v or None  # leer = gelöscht
        raw = r.read()
        try:
            return r.status, json.loads(raw) if raw else {}
        except ValueError:
            return r.status, {}


def totp(secret_b32: str, step: int) -> str:
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = mac[-1] & 15
    return f"{(struct.unpack('>I', mac[o:o + 4])[0] & 0x7FFFFFFF) % 10**6:06d}"


def expect(step: str, got: int, want: int) -> None:
    if got != want:
        fail(step, f"HTTP {got}, erwartet {want}")


def main(base: str, token: str) -> None:
    c = Client(base)
    st, body = c.call("GET", "/api/v1/setup")
    expect("setup abfragen", st, 200)
    if body.get("required") is not True:
        fail("setup abfragen", "required ist nicht true")
    account = {"email": EMAIL, "name": "CI", "password": PASSWORD}
    st, _ = c.call("POST", "/api/v1/setup", {"setup_token": "setup_falsch", **account})
    expect("setup mit falschem Token", st, 403)
    st, _ = c.call("POST", "/api/v1/setup", {"setup_token": token, **account})
    expect("setup mit echtem Token", st, 201)
    st, body = c.call("GET", "/api/v1/auth/session")
    expect("session nach setup", st, 200)
    st, body = c.call("POST", "/api/v1/auth/totp/setup", {}, csrf=body.get("csrf_token"))
    expect("totp einrichten", st, 200)
    code = totp(body["secret"], int(time.time()) // 30)
    st, sess = c.call("GET", "/api/v1/auth/session")
    st, body = c.call("POST", "/api/v1/auth/totp/confirm", {"code": code}, csrf=sess.get("csrf_token"))
    expect("totp bestätigen", st, 200)
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
    if len(sys.argv) != 3:
        fail("Aufruf", "manager_auth_smoke.py BASE_URL SETUP_TOKEN")
    main(sys.argv[1], sys.argv[2])
