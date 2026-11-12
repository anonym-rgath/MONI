"""TLS- und Kopplungszustand des Agents in <data_dir>/tls/ (D2, D3, D4, D10)."""
import hmac
import json
import os
import secrets
import ssl
from datetime import datetime
from pathlib import Path

from moni import pki

_CN = "moni-agent"


class TlsError(Exception):
    """Fester deutscher Text für den Admin, nie ein ssl-Traceback."""


class TlsState:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "tls"
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)  # immer dasselbe Objekt; der Server hält es fest
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._cert = None
        self._token: str | None = None

    def _p(self, name: str) -> Path:
        return self.dir / name

    @property
    def paired(self) -> bool:
        return self._p("ca.pem").exists()

    @property
    def cert_not_after(self) -> datetime:
        return self._cert.not_valid_after_utc

    def ensure(self, now: datetime) -> None:
        if not self._p("cert.pem").exists():
            self._provisional(now)
        else:
            self._recover()
            if not self.paired and not self._p("pairing.json").exists():
                self._new_token()
        self._load()

    def _recover(self) -> None:
        """Absturz in install(): Zertifikat ist neu, der Schlüssel liegt noch als pending_key.pem; Paar vervollständigen."""
        try:
            cert = pki.load_cert(self._p("cert.pem").read_bytes())
            if pki.matches_key(cert, pki.load_key(self._p("key.pem").read_bytes())):
                return
            pending = self._p("pending_key.pem")
            if pending.exists() and pki.matches_key(cert, pki.load_key(pending.read_bytes())):
                os.replace(pending, self._p("key.pem"))
                return
        except ValueError:  # Datei kaputt: wie ein nicht passendes Paar behandeln
            pass
        raise TlsError("Zertifikat und Schlüssel des Agents passen nicht zusammen – "
                       "'python -m moni.cli pair reset' ausführen und den Agent neu koppeln")

    def _provisional(self, now: datetime) -> None:
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Erst Zertifikat und CA weg: Bricht es mittendrin ab, legt ensure() beim nächsten Start alles neu an.
        for name in ("ca.pem", "cert.pem", "pending_key.pem"):
            self._p(name).unlink(missing_ok=True)
        key = pki.new_key()
        pki.write_private(self._p("key.pem"), pki.key_pem(key))
        cert = pki.self_signed(key, _CN, ["localhost"], now, 3650)
        pki.write_private(self._p("cert.pem"), pki.cert_pem(cert))
        self._cert = cert
        self._new_token()

    def _new_token(self) -> None:
        token = secrets.token_urlsafe(32)
        pki.write_private(self._p("pairing.json"), json.dumps({"token": token}).encode())
        self._token = token

    def _load(self) -> None:
        cert_pem, key_pem = self._p("cert.pem").read_bytes(), self._p("key.pem").read_bytes()
        ca_pem = self._p("ca.pem").read_bytes() if self.paired else None
        pj = self._p("pairing.json")
        self._token = json.loads(pj.read_bytes())["token"] if pj.exists() else None
        pki.server_context(cert_pem, key_pem, ca_pem)  # erst frisch prüfen, damit der laufende Kontext nie halb geladen ist
        pki.load_chain_into(self.context, cert_pem, key_pem)
        if ca_pem is not None:
            self.context.load_verify_locations(cadata=ca_pem.decode())
            self.context.verify_mode = ssl.CERT_OPTIONAL
        self._cert = pki.load_cert(cert_pem)

    def pairing_code(self, public_url: str) -> str | None:
        if self.paired or self._token is None:
            return None
        code = f"moni-pair-v1:{pki.fingerprint(self._cert)}:{self._token}"
        return f"{code}:{public_url}" if public_url else code

    def check_token(self, token: str) -> bool:
        if self.paired or self._token is None:
            return False
        # isascii zuerst: einsame Surrogate oder Umlaute dürfen nicht werfen
        return isinstance(token, str) and token.isascii() and hmac.compare_digest(token.encode(), self._token.encode())

    def new_csr(self) -> bytes:
        """Nur im Event-Loop-Thread aufrufen, wie install(): Kopplungszustand und SSLContext nie parallel ändern."""
        key = pki.new_key()
        pki.write_private(self._p("pending_key.pem"), pki.key_pem(key))
        return pki.make_csr(key, _CN)

    def install(self, cert_pem: bytes, ca_pem: bytes, now: datetime) -> None:
        """Nur im Event-Loop-Thread aufrufen (lädt den laufenden SSLContext neu, der nicht threadsicher ist)."""
        ok = False
        try:
            key_pem = self._p("pending_key.pem").read_bytes()
            cert, ca = pki.load_cert(cert_pem), pki.load_cert(ca_pem)
            cert.verify_directly_issued_by(ca)
            ok = (pki.matches_key(cert, pki.load_key(key_pem)) and pki.has_eku(cert, "server")
                  and cert.not_valid_before_utc <= now <= cert.not_valid_after_utc)
            if ok:
                pki.server_context(cert_pem, key_pem, ca_pem)  # alles, was ssl prüft, vor dem ersten Schreiben
        except Exception:  # Vertrauensgrenze: jeder Fehler ergibt dieselbe feste Meldung
            ok = False
        if not ok:  # außerhalb von except, damit kein __context__ mitläuft
            raise ValueError("Zertifikat passt nicht")
        # Reihenfolge für ensure(): pending_key.pem bleibt bis zum letzten Schritt, dann ersetzt er key.pem atomar
        # cert.pem vor ca.pem: bricht es dazwischen ab, bleibt der Agent ungekoppelt und ensure() kann neu koppeln lassen
        pki.write_private(self._p("cert.pem"), cert_pem)
        pki.write_private(self._p("ca.pem"), ca_pem)
        self._p("pairing.json").unlink(missing_ok=True)
        os.replace(self._p("pending_key.pem"), self._p("key.pem"))
        self._load()

    def reset(self, now: datetime, public_url: str = "") -> str:
        """D10: neuer Schlüssel, vorläufiges Zertifikat, neues Token. Wirksam erst nach Neustart des Agents."""
        self._provisional(now)
        return self.pairing_code(public_url)
