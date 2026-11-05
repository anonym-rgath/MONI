"""Kopplung mit einem Agent per Kopplungscode (D2): Fingerabdruck pinnen, CSR holen, signieren, Zertifikat übergeben.

Nie Kopplungs-Token, Code oder Antwortinhalte in Fehlermeldungen oder Logs.
"""
import base64
import hashlib
import hmac
import http.client
import json
import logging
import re
import ssl
from datetime import datetime
from urllib.parse import urlsplit

from moni import pki
from moni.secretbox import SecretError
from .ca import ManagerPki
from .poller import AgentClient, AgentError
from .servers import Server, ServerStore

log = logging.getLogger("moni.manager.pairing")

_PART = re.compile(r"[A-Za-z0-9_-]{43}")  # Fingerabdruck (SHA-256) und token_urlsafe(32), beide Base64url ohne "="
_INVALID = "Kopplungscode ist ungültig"
_FP_MISMATCH = "Fingerabdruck des Agents passt nicht zum Kopplungscode"
_UNREACHABLE = "Agent nicht erreichbar"
_BAD_ANSWER = "Ungültige Antwort vom Agent"
_LOCAL = "Manager-Zertifikate nicht lesbar"
_PAIRED_BUT = ("Agent wurde gekoppelt, antwortet aber nicht wie erwartet – am Agent 'python -m moni.cli pair reset' "
               "ausführen und neu koppeln")
_MAX_BODY = 64 * 1024
_AGENT_STATUS = {401: (401, "Kopplungs-Token ungültig"), 409: (409, "Agent ist bereits gekoppelt"),
                 422: (502, "Zertifikat passt nicht")}


class PairingError(Exception):
    """Fester deutscher Text; `status` ist der HTTP-Status für die Manager-API."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


def parse_code(code) -> tuple[str, str, str | None]:
    parts = code.split(":", 3) if isinstance(code, str) else []
    if not (len(parts) >= 3 and parts[0] == "moni-pair-v1" and _PART.fullmatch(parts[1]) and _PART.fullmatch(parts[2])
            and (len(parts) == 3 or parts[3])):
        raise ValueError(_INVALID)
    return parts[1], parts[2], parts[3] if len(parts) == 4 else None


def _pinned_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    # einzige Ausnahme von der CA-Prüfung: der Fingerabdruck wird vor der ersten Anfrage selbst verglichen
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    return ctx


def _fingerprint(der: bytes) -> str:  # wie pki.fingerprint, direkt über das DER
    return base64.urlsafe_b64encode(hashlib.sha256(der).digest()).rstrip(b"=").decode()


def _post(conn: http.client.HTTPSConnection, path: str, token: str, body: dict | None = None) -> tuple[int, bytes]:
    conn.request("POST", path, body=json.dumps(body).encode() if body is not None else b"",
                 headers={"X-Moni-Pairing-Token": token, "Content-Type": "application/json", "Accept": "application/json"})
    r = conn.getresponse()
    return r.status, r.read(_MAX_BODY + 1)


def _agent_error(status: int) -> PairingError:
    st, msg = _AGENT_STATUS.get(status, (502, f"Agent antwortet mit HTTP {status}"))
    return PairingError(msg, st)


def _local(mp: ManagerPki, client: bool = False):
    """Alles, was lokal scheitern kann, vor dem ersten Kontakt zum Agent: sonst hieße es fälschlich "nicht erreichbar"."""
    try:
        out = mp.agent_signer(), mp.ca_pem.decode(), mp.client_context() if client else None
    except (SecretError, OSError, ValueError):  # ssl.SSLError ist ein OSError
        out = None
    if out is None:  # außerhalb von except: kein __context__ mit Pfaden oder Schlüsselinhalten
        raise PairingError(_LOCAL, 500)
    return out


def _signed(sign, csr_answer, host: str, now: datetime) -> bytes | PairingError:
    try:
        return sign(csr_answer["csr"].encode(), host, now)
    except (ValueError, KeyError, TypeError, AttributeError):  # Antwort des Agents ist nicht vertrauenswürdig
        return PairingError(_BAD_ANSWER)


def _exchange(conn, token: str, host: str, sign, ca_pem: str, now: datetime) -> PairingError | int:
    status, body = _post(conn, "/api/v1/tls/csr", token)
    if status != 200:
        return _agent_error(status)
    try:
        answer = json.loads(body) if len(body) <= _MAX_BODY else None
    except ValueError:
        answer = None
    cert_pem = _signed(sign, answer, host, now)
    if isinstance(cert_pem, PairingError):
        return cert_pem
    status, _ = _post(conn, "/api/v1/tls/cert", token, {"certificate": cert_pem.decode(), "ca": ca_pem})
    if status != 204:
        return _agent_error(status)
    return int(pki.load_cert(cert_pem).not_valid_after_utc.timestamp())


def renew(client: AgentClient, url: str, mp: ManagerPki, now: datetime) -> int:
    """Erneuert das Zertifikat eines gekoppelten Agents per mTLS; liefert das neue Ablaufdatum (Unix-Sekunden)."""
    sign, ca_pem, _ = _local(mp)
    cert_pem = _signed(sign, client.post("/api/v1/tls/csr", {}), urlsplit(url).hostname, now)
    if isinstance(cert_pem, PairingError):
        raise cert_pem
    client.post("/api/v1/tls/cert", {"certificate": cert_pem.decode(), "ca": ca_pem})
    return int(pki.load_cert(cert_pem).not_valid_after_utc.timestamp())


def pair(url: str, code: str, mp: ManagerPki, now: datetime, timeout: float) -> int:
    """Koppelt den Agent unter `url`; liefert das Ablaufdatum seines neuen Zertifikats (Unix-Sekunden)."""
    fp, token, _ = parse_code(code)
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname:
        raise ValueError("mTLS braucht eine https://-URL")
    host, port = u.hostname, u.port or 443
    pki.san_for_host(host)  # ungültiger Host: Abbruch, bevor der Agent etwas sieht
    sign, ca_pem, ctx = _local(mp, client=True)
    result: PairingError | int
    conn = http.client.HTTPSConnection(host, port, context=_pinned_context(), timeout=timeout)
    try:
        conn.connect()
        if not hmac.compare_digest(_fingerprint(conn.sock.getpeercert(binary_form=True) or b"").encode(), fp.encode()):
            result = PairingError(_FP_MISMATCH)
        else:  # dieselbe gepinnte Verbindung für CSR und Zertifikat
            result = _exchange(conn, token, host, sign, ca_pem, now)
    except (OSError, http.client.HTTPException):  # Verbindung, Timeout, TLS-Abbruch
        result = PairingError(_UNREACHABLE)
    finally:
        conn.close()
    if isinstance(result, PairingError):  # außerhalb von except: kein __context__ mit Verbindungsdetails
        raise result
    # Kontrollabruf mit echter CA- und Hostnamen-Prüfung; der Agent ist ab hier schon gekoppelt
    try:
        info = AgentClient(url, "", {}, timeout, ssl_context=ctx).get("/api/v1/info")
        reason = None if info.get("paired") is True else "Agent bestätigt die Kopplung nicht"
    except AgentError as e:
        reason = "TLS-Prüfung des Agents fehlgeschlagen" if e.fatal else _UNREACHABLE
    if reason is not None:  # außerhalb von except; Grund nur ins Log, der Admin braucht den Hinweis auf pair reset
        log.warning("Kontrollabruf nach der Kopplung fehlgeschlagen: %s", reason)
        raise PairingError(_PAIRED_BUT)
    return result


def add_server(store: ServerStore, mp: ManagerPki, name: str, code, url: str | None, now: datetime,
               timeout: float) -> Server:
    """Gemeinsamer Ablauf für API und CLI: alles prüfen, was ohne Agent geht, dann koppeln und speichern."""
    _, _, code_url = parse_code(code)
    if url and code_url and url.rstrip("/") != code_url.rstrip("/"):
        raise ValueError("URL im Kopplungscode passt nicht")
    url = url or code_url
    if not url:
        raise ValueError("URL fehlt")
    store.check_mtls(name, url)
    return store.add_mtls(name, url, pair(url, code, mp, now, timeout))
