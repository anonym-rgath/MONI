"""Gemeinsame Helfer der CI-Smoketests (nur Standardbibliothek).

Das Cookie ist "Secure" und wird deshalb per Header von Hand mitgeschickt. Nichts hier gibt Passwort,
Secret, Codes, Cookies oder CSRF aus. NUR FÜR DIE CI: Das Zertifikat des Managers (selbstsigniert, CA
erst nach dem Start bekannt) wird nicht geprüft.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import ssl
import struct
import sys
import time
import urllib.error
import urllib.request

PASSWORD = "ci-smoke-Passwort-2026"  # fester CI-Wert, die CI prüft, dass er nicht im Log steht
EMAIL = "ci-admin@example.org"

_CTX = ssl._create_unverified_context()  # nur CI, siehe Modul-Docstring


def fail(label: str, step: str, detail: str = "") -> None:
    print(f"FAIL {label}: {step}{' (' + detail + ')' if detail else ''}")
    sys.exit(1)


def expect(label: str, step: str, got: int, want: int) -> None:
    if got != want:
        fail(label, step, f"HTTP {got}, erwartet {want}")


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
            r = urllib.request.urlopen(req, timeout=15, context=_CTX)
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


def setup_admin(c: Client, label: str, token: str) -> list[str]:
    """Legt den CI-Admin samt TOTP an und gibt die Wiederherstellungscodes zurück (nie ausgeben)."""
    account = {"email": EMAIL, "name": "CI", "password": PASSWORD}
    st, _ = c.call("POST", "/api/v1/setup", {"setup_token": token, **account})
    expect(label, "setup mit echtem Token", st, 201)
    st, body = c.call("GET", "/api/v1/auth/session")
    expect(label, "session nach setup", st, 200)
    st, body = c.call("POST", "/api/v1/auth/totp/setup", {}, csrf=body.get("csrf_token"))
    expect(label, "totp einrichten", st, 200)
    code = totp(body["secret"], int(time.time()) // 30)
    st, sess = c.call("GET", "/api/v1/auth/session")
    st, body = c.call("POST", "/api/v1/auth/totp/confirm", {"code": code}, csrf=sess.get("csrf_token"))
    expect(label, "totp bestätigen", st, 200)
    return body["recovery_codes"]


def login_with_recovery(c: Client, label: str, recovery_code: str) -> str:
    """Anmelden mit festem Passwort und einem Wiederherstellungscode (kein Warten auf den TOTP-Zeitschritt nötig).

    Gibt das CSRF-Token der vollen Sitzung zurück."""
    st, _ = c.call("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PASSWORD})
    expect(label, "login", st, 200)
    st, sess = c.call("GET", "/api/v1/auth/session")
    expect(label, "session vor mfa", st, 200)
    st, _ = c.call("POST", "/api/v1/auth/login/mfa", {"code": recovery_code}, csrf=sess["csrf_token"])
    expect(label, "login mfa", st, 200)
    st, sess = c.call("GET", "/api/v1/auth/session")
    expect(label, "session nach login", st, 200)
    return sess["csrf_token"]


def write_secret_file(path: str, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
