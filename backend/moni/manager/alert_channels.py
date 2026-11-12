"""Benachrichtigungskanäle (Webhook, E-Mail): Speicher und Versand. Geheimnisse liegen nur verschlüsselt in der DB."""
import hashlib
import hmac
import json
import logging
import smtplib
import sqlite3
import ssl
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from urllib.parse import urlsplit

from ..auth.users import Conflict, normalize_email
from ..secretbox import SecretBox
from ..timeutil import iso
from .poller import _NoRedirect
from .servers import _check_name, _visible_ascii

log = logging.getLogger("moni.manager.alert_channels")

# Kein Proxy aus der Umgebung, keine Weiterleitungen (P2-R6): die URL kann ein Token enthalten
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
_SEVERITIES = ("warning", "critical")
_SEV_TAG = {"warning": "[WARNUNG]", "critical": "[KRITISCH]"}
_EVENT_WORD = {"firing": "ausgelöst", "resolved": "behoben", "changed": "Schwere geändert",
               "reminder": "weiterhin aktiv", "test": "Testnachricht"}


class SendError(Exception):
    """Fester deutscher Text ohne Antwortinhalte, URLs oder Zugangsdaten."""


# Die einzigen Versand-Fehlertexte, die nach außen gehen (API, last_error). Der Text kommt aus dieser Tabelle,
# nie aus der Exception selbst; so kann auch ein künftiger Fehler ohne festen Text nichts preisgeben.
_SEND_TEXTS = {t: t for t in ("Webhook nicht erreichbar", "SMTP-Anmeldung fehlgeschlagen", "SMTP-Server nicht erreichbar",
                              "SMTP-Server lehnt Nachricht ab", "TLS-Prüfung fehlgeschlagen",
                              "SMTP-Server unterstützt SMTPUTF8 nicht",
                              "SMTP-Server bietet keine passende Anmeldemethode an",
                              *(f"Webhook antwortet mit HTTP {n}" for n in range(100, 600)))}


def error_text(e: SendError) -> str:
    return _SEND_TEXTS.get(str(e), "Versand fehlgeschlagen")


@dataclass(frozen=True)
class Channel:
    """Bewusst ohne Geheimnisse (URL, HMAC, Passwort), damit repr() und API-Ausgaben sicher sind."""
    id: int
    name: str
    kind: str
    config: dict
    min_severity: str
    enabled: bool
    created_at: int
    secret_set: bool  # HMAC-Secret bzw. SMTP-Passwort gespeichert
    url_hint: str | None  # nur Webhook: Schema und Host


# ---------- Validierung ----------

def _check_webhook_url(url) -> None:
    if not isinstance(url, str) or not url or len(url) > 2048 or not _visible_ascii(url):
        raise ValueError("URL ist ungültig (nur sichtbares ASCII ohne Leerzeichen, höchstens 2048 Zeichen)")
    try:
        u = urlsplit(url)
        host = u.hostname
        u.port  # noqa: B018 - prüft den Port
    except ValueError:
        raise ValueError("URL ist ungültig") from None
    if u.scheme not in ("http", "https") or not host:
        raise ValueError("URL muss mit http:// oder https:// beginnen und einen Host enthalten")
    if "@" in u.netloc:
        raise ValueError("URL darf keine Zugangsdaten enthalten")
    if "#" in url:
        raise ValueError("URL darf kein Fragment (#) enthalten")


def _check_secret(value, what: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 256 or not _visible_ascii(value, allow_space=True):
        raise ValueError(f"{what} ist ungültig (1 bis 256 Zeichen, sichtbares ASCII)")


def _email_config(v: dict) -> dict:
    host, port = v.get("host"), v.get("port")
    if not isinstance(host, str) or not host or len(host) > 253 or not _visible_ascii(host):
        raise ValueError("SMTP-Host ist ungültig")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("SMTP-Port muss zwischen 1 und 65535 liegen")
    if v.get("security") not in ("starttls", "tls"):
        raise ValueError("Verschlüsselung muss starttls oder tls sein")
    to = v.get("to")
    if not isinstance(to, list) or not 1 <= len(to) <= 10:
        raise ValueError("Es sind 1 bis 10 Empfänger nötig")
    username = v.get("username")
    if username is not None:
        _check_secret(username, "Benutzername")
    return {"host": host, "port": port, "security": v["security"], "username": username,
            "from_addr": normalize_email(v.get("from_addr")), "to": [normalize_email(a) for a in to]}


# ---------- Speicher ----------

class ChannelStore:
    def __init__(self, conn: sqlite3.Connection, box: SecretBox):
        self._conn = conn
        self._box = box

    @contextmanager
    def _tx(self):
        """Committet nur, wenn die Transaktion hier begonnen wurde; sonst entscheidet der Aufrufer (P2-R1)."""
        if self._conn.in_transaction:
            yield
            return
        self._conn.execute("BEGIN IMMEDIATE")
        with self._conn:
            yield

    def secrets(self, channel_id: int) -> dict:
        r = self._conn.execute("SELECT secret_enc FROM alert_channels WHERE id=?", (channel_id,)).fetchone()
        return json.loads(self._box.decrypt(r["secret_enc"])) if r and r["secret_enc"] else {}

    def _channel(self, r: sqlite3.Row) -> Channel:
        sec = json.loads(self._box.decrypt(r["secret_enc"])) if r["secret_enc"] else {}
        hint = None
        if r["kind"] == "webhook" and sec.get("url"):
            u = urlsplit(sec["url"])
            hint = f"{u.scheme}://{u.netloc}" + ("/…" if u.path.strip("/") or u.query else "")
        return Channel(r["id"], r["name"], r["kind"], json.loads(r["config"]), r["min_severity"], bool(r["enabled"]),
                       r["created_at"], "hmac" in sec or "password" in sec, hint)

    def list(self) -> list[Channel]:
        return [self._channel(r) for r in self._conn.execute("SELECT * FROM alert_channels ORDER BY id")]

    def get(self, channel_id: int) -> Channel | None:
        r = self._conn.execute("SELECT * FROM alert_channels WHERE id=?", (channel_id,)).fetchone()
        return self._channel(r) if r else None

    def _build(self, v: dict, sec: dict, old_host: str | None = None) -> tuple:
        """Prüft die zusammengeführten Werte und liefert (name, config, secret_enc, min_severity, enabled)."""
        _check_name(v.get("name"))
        keep = ("url", "hmac") if v["kind"] == "webhook" else ("password",)
        sec = {k: x for k, x in sec.items() if k in keep}  # nie Geheimnisse der anderen Kanal-Art speichern
        if v.get("min_severity") not in _SEVERITIES:
            raise ValueError("Mindest-Schwere muss warning oder critical sein")
        if not isinstance(v.get("enabled"), bool):
            raise ValueError("enabled muss true oder false sein")
        if v["kind"] == "webhook":
            _check_webhook_url(sec.get("url"))
            if "hmac" in sec:
                _check_secret(sec["hmac"], "HMAC-Secret")
            config = {}
        else:
            config = _email_config(v)
            if "password" in sec:
                _check_secret(sec["password"], "Passwort")
        return v["name"], json.dumps(config), self._box.encrypt(json.dumps(sec)) if sec else None, \
            v["min_severity"], int(v["enabled"])

    def create(self, data: dict, now: int) -> Channel:
        kind = data.get("kind")
        if kind not in ("webhook", "email"):
            raise ValueError("Art des Kanals muss webhook oder email sein")
        v = {"min_severity": "warning", "enabled": True, "username": None, **data}
        if kind == "email" and "port" not in v:
            v["port"] = 465 if v.get("security") == "tls" else 587
        if kind == "email" and "security" not in v:
            v["security"] = "starttls"
        sec = {k: data[s] for k, s in (("url", "url"), ("hmac", "hmac_secret"), ("password", "password"))
               if data.get(s) is not None}
        row = self._build(v, sec)
        if sec.get("url", "").startswith("http://"):
            log.warning("Webhook-Kanal %s verwendet unverschlüsseltes http", row[0])  # nie die URL loggen
        dup = False
        try:
            with self._tx():
                cur = self._conn.execute(
                    "INSERT INTO alert_channels (name, kind, config, secret_enc, min_severity, enabled, created_at) "
                    "VALUES (?,?,?,?,?,?,?)", (row[0], kind, row[1], row[2], row[3], row[4], now))
        except sqlite3.IntegrityError as e:
            if "UNIQUE" not in str(e):
                raise
            dup = True
        if dup:
            raise Conflict("Kanalname ist bereits vergeben")
        return self.get(cur.lastrowid)

    def update(self, channel_id: int, data: dict) -> Channel | None:
        dup = False
        try:
            with self._tx():
                r = self._conn.execute("SELECT * FROM alert_channels WHERE id=?", (channel_id,)).fetchone()
                if r is None:
                    return None
                if data.get("kind", r["kind"]) != r["kind"]:
                    raise ValueError("Art des Kanals lässt sich nicht ändern")
                old_cfg = json.loads(r["config"])
                v = {"name": r["name"], "kind": r["kind"], "min_severity": r["min_severity"],
                     "enabled": bool(r["enabled"]), **old_cfg, **data}
                sec = json.loads(self._box.decrypt(r["secret_enc"])) if r["secret_enc"] else {}
                for key, s in (("url", "url"), ("hmac", "hmac_secret"), ("password", "password")):
                    if s in data:  # None entfernt das Geheimnis, fehlender Schlüssel behält es
                        sec.pop(key, None)
                        if data[s] is not None:
                            sec[key] = data[s]
                if r["kind"] == "email" and "password" in sec and v["host"] != old_cfg["host"] and data.get("password") is None:
                    raise ValueError("Passwort fehlt")
                row = self._build(v, sec)
                if "url" in data and sec.get("url", "").startswith("http://"):
                    log.warning("Webhook-Kanal %s verwendet unverschlüsseltes http", row[0])
                self._conn.execute("UPDATE alert_channels SET name=?, config=?, secret_enc=?, min_severity=?, enabled=? "
                                   "WHERE id=?", (*row, channel_id))
        except sqlite3.IntegrityError as e:
            if "UNIQUE" not in str(e):
                raise
            dup = True
        if dup:
            raise Conflict("Kanalname ist bereits vergeben")
        return self.get(channel_id)

    def delete(self, channel_id: int) -> bool:
        with self._tx():
            return self._conn.execute("DELETE FROM alert_channels WHERE id=?", (channel_id,)).rowcount > 0

    def reencrypt_all(self) -> int:
        n = 0
        with self._tx():
            for r in self._conn.execute("SELECT id, secret_enc FROM alert_channels WHERE secret_enc IS NOT NULL").fetchall():
                self._conn.execute("UPDATE alert_channels SET secret_enc=? WHERE id=?",
                                   (self._box.rotate(r["secret_enc"]), r["id"]))
                n += 1
        return n


# ---------- Nachricht ----------

def _num(x) -> str:
    return f"{x:.10g}"


def payload(event: dict, manager_name: str) -> dict:
    """Schema v1. `event` hat die Spaltennamen von alert_events; Labels stammen aus `key` (P5-R5)."""
    labels = json.loads(event["key"]) if event.get("key") else {}
    value, thr = event.get("value"), event.get("threshold")
    text = f"{_SEV_TAG.get(event['severity'], '[' + str(event['severity']).upper() + ']')} {event['rule_name']} auf {event['server_name']}"
    if labels:
        text += " (" + ", ".join(f"{k}={v}" for k, v in labels.items()) + ")"
    if value is not None:
        text += f": {_num(value)}"
    if thr is not None:
        text += f" (Schwelle {_num(thr)})"
    text += f" – {_EVENT_WORD[event['event']]}"
    if event.get("reason"):
        text += f" ({event['reason']})"
    return {"version": 1, "event": event["event"], "severity": event["severity"], "rule": event["rule_name"],
            "server": event["server_name"], "labels": labels, "value": value, "threshold": thr,
            "reason": event.get("reason"), "at": iso(datetime.fromtimestamp(event["ts"], timezone.utc)),
            "manager": manager_name, "text": text}


# ---------- Versand ----------

def sign(secret: str, ts: int, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def send_webhook(url: str, body: dict, hmac_secret: str | None, timeout: float = 10) -> None:
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "moni"}
    if hmac_secret:
        ts = int(time.time())
        headers.update({"X-Moni-Timestamp": str(ts), "X-Moni-Signature": sign(hmac_secret, ts, raw)})
    err = None
    deadline = time.monotonic() + timeout  # Gesamtfrist; timeout allein gilt je Socket-Operation
    try:
        with _OPENER.open(urllib.request.Request(url, raw, headers, method="POST"), timeout=timeout) as resp:
            left = 65536  # Antwort wird nur gelesen und verworfen
            while left > 0:
                if time.monotonic() > deadline:
                    raise TimeoutError
                chunk = resp.read1(min(left, 4096))
                if not chunk:
                    break
                left -= len(chunk)
    except urllib.error.HTTPError as e:
        err = f"Webhook antwortet mit HTTP {e.code}"
        e.close()
    except Exception:  # URLError, Timeout, Verbindungsabbruch, ungültige Antwort
        err = "Webhook nicht erreichbar"
    if err:
        raise SendError(err)  # außerhalb von except: kein __context__ mit URL oder Antwort


def send_email(cfg: dict, password: str | None, subject: str, text: str, timeout: float = 15) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"] = cfg["from_addr"], ", ".join(cfg["to"])
    msg["Subject"] = " ".join(subject.split())  # keine Zeilenumbrüche im Betreff
    msg["Date"], msg["Message-ID"] = formatdate(localtime=False), make_msgid(domain=cfg["from_addr"].rpartition("@")[2])  # getfqdn() würde bei langsamem DNS blockieren
    msg.set_content(text)
    ctx = ssl.create_default_context()
    err, stage, smtp = None, "connect", None
    try:
        if cfg["security"] == "tls":
            smtp = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=timeout, context=ctx)
        else:
            smtp = smtplib.SMTP(cfg["host"], cfg["port"], timeout=timeout)
            stage = "starttls"  # nie Klartext: fehlt STARTTLS, bricht der Versand ab
            smtp.ehlo()
            smtp.starttls(context=ctx)
            smtp.ehlo()
        if cfg.get("username"):
            stage = "login"
            smtp.login(cfg["username"], password or "")
        stage = "send"
        smtp.send_message(msg)
        try:
            smtp.quit()
        except Exception:
            pass  # Nachricht ist angenommen
    except ssl.SSLError:
        err = "TLS-Prüfung fehlgeschlagen"
    except smtplib.SMTPNotSupportedError:
        err = {"login": "SMTP-Server bietet keine passende Anmeldemethode an",
               "send": "SMTP-Server unterstützt SMTPUTF8 nicht"}.get(stage, "TLS-Prüfung fehlgeschlagen")
    except smtplib.SMTPAuthenticationError:
        err = "SMTP-Anmeldung fehlgeschlagen"
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        err = "SMTP-Server lehnt Nachricht ab"
    except smtplib.SMTPServerDisconnected:
        err = "SMTP-Server nicht erreichbar"
    except smtplib.SMTPException:  # beim Login: smtplib findet keine passende Methode
        err = "SMTP-Server bietet keine passende Anmeldemethode an" if stage == "login" else "SMTP-Server nicht erreichbar"
    except Exception:  # OSError, Timeout
        err = "SMTP-Server nicht erreichbar"
    finally:
        if smtp is not None:
            try:
                smtp.close()
            except Exception:
                pass
    if err:
        raise SendError(err)


def deliver(channel: Channel, secrets: dict, event: dict, manager_name: str) -> None:
    body = payload(event, manager_name)
    if channel.kind == "webhook":
        send_webhook(secrets["url"], body, secrets.get("hmac"))
    else:
        send_email(channel.config, secrets.get("password"), body["text"].rsplit(" – ", 1)[0], body["text"])
