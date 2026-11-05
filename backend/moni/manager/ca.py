"""Manager-CA und Client-Zertifikat in <data_dir>/tls/ (D1, D4). Private Schlüssel liegen nur mit SecretBox verschlüsselt vor."""
import os
import ssl
from datetime import datetime
from pathlib import Path
from typing import Callable

from moni import pki
from moni.fsutil import write_private, write_temp
from moni.secretbox import SecretBox

CLIENT_CN = "moni-manager"
_AGENT_CN = "moni-agent"
_CLIENT_DAYS, _CLIENT_RENEW_DAYS = 365, 60
_AGENT_DAYS = 90
_KEY_FILES = ("ca.key.enc", "client.key.enc")


class ManagerPki:
    def __init__(self, data_dir: Path, box: SecretBox):
        self.dir = Path(data_dir) / "tls"
        self._box = box
        self._cached: tuple[bytes, ssl.SSLContext] | None = None  # (client.crt, Kontext); Tausch als Ganzes, threadsicher

    def _p(self, name: str) -> Path:
        return self.dir / name

    def _key_pem(self, name: str) -> bytes:
        return self._box.decrypt(self._p(name).read_text()).encode()

    def _write_key(self, name: str, key) -> None:
        write_private(self._p(name), self._box.encrypt(pki.key_pem(key).decode()).encode())

    @property
    def ca_pem(self) -> bytes:
        return self._p("ca.crt").read_bytes()

    def ensure(self, now: datetime) -> None:
        """Legt CA und Client-Zertifikat an; erneuert das Client-Zertifikat ab 60 Tagen Restlaufzeit (D1)."""
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self._p("ca.crt").exists():
            # Schlüssel vor Zertifikat: ohne ca.crt kann noch kein Agent gekoppelt sein, ein Neuanlegen ist gefahrlos
            key, cert = pki.create_ca(now)
            self._write_key("ca.key.enc", key)
            write_private(self._p("ca.crt"), pki.cert_pem(cert))
        elif not self._p("ca.key.enc").exists():  # nie still eine neue CA: alle Kopplungen wären ungültig (D1)
            raise RuntimeError("CA-Schlüssel fehlt (tls/ca.key.enc); die CA wird nicht neu angelegt")
        ca_key, ca_cert = pki.load_key(self._key_pem("ca.key.enc")), pki.load_cert(self.ca_pem)
        if not pki.matches_key(ca_cert, ca_key):
            raise RuntimeError("CA-Schlüssel passt nicht zum CA-Zertifikat")
        if self._client_ok(ca_cert, now):
            return
        key = pki.new_key()
        cert = pki.issue(ca_key, ca_cert, pki.make_csr(key, CLIENT_CN), cn=CLIENT_CN, sans=[CLIENT_CN], usage="client",
                         now=now, days=_CLIENT_DAYS)
        self._write_key("client.key.enc", key)  # passt es danach nicht zum Zertifikat, erneuert der nächste Aufruf
        write_private(self._p("client.crt"), pki.cert_pem(cert))

    def _client_ok(self, ca_cert, now: datetime) -> bool:
        try:
            cert = pki.load_cert(self._p("client.crt").read_bytes())
            cert.verify_directly_issued_by(ca_cert)
            return (pki.matches_key(cert, pki.load_key(self._key_pem("client.key.enc")))
                    and pki.days_left(cert, now) >= _CLIENT_RENEW_DAYS)
        except Exception:  # fehlt, kaputt oder von alter CA: das Client-Zertifikat ist jederzeit ersetzbar
            return False

    def sign_agent(self, csr_pem: bytes, host: str, now: datetime) -> bytes:
        """SAN kommt aus dem Host der Agent-URL, nie aus der CSR (D1)."""
        sans = pki.san_for_host(host)
        cert = pki.issue(pki.load_key(self._key_pem("ca.key.enc")), pki.load_cert(self.ca_pem), csr_pem, cn=_AGENT_CN,
                         sans=sans, usage="server", now=now, days=_AGENT_DAYS)
        return pki.cert_pem(cert)

    def client_context(self) -> ssl.SSLContext:
        """Strikter Client-Kontext mit Moni-CA und Client-Zertifikat; neu gebaut, sobald sich client.crt ändert.

        Nach dem Bau nur noch lesend genutzt, darf also von den Poller-Threads geteilt werden.
        """
        crt = self._p("client.crt").read_bytes()
        cached = self._cached
        if cached is not None and cached[0] == crt:
            return cached[1]
        ctx = pki.client_context(self.ca_pem, crt, self._key_pem("client.key.enc"))
        self._cached = (crt, ctx)
        return ctx

    def prepare_reencrypt(self) -> tuple[int, Callable[[], None], Callable[[], None]]:
        """Schreibt die neu verschlüsselten Schlüssel als temporäre Dateien; `commit` setzt sie ein, `abort` verwirft sie."""
        staged: list[tuple[Path, Path]] = []

        def abort() -> None:
            for tmp, _ in staged:
                tmp.unlink(missing_ok=True)

        def commit() -> None:
            try:
                for tmp, path in staged:
                    os.replace(tmp, path)
            finally:
                abort()  # nur Reste nach einem Fehler; eingesetzte Dateien gibt es unter dem tmp-Namen nicht mehr

        try:
            for name in _KEY_FILES:
                path = self._p(name)
                if path.exists():  # noch nie gestartet: nichts zu rotieren
                    staged.append((write_temp(path, self._box.rotate(path.read_text()).encode()), path))
        except BaseException:
            abort()
            raise
        return len(staged), commit, abort
