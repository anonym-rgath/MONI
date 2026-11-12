import hashlib
import hmac
import json
import smtplib
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest
from moni.auth.users import Conflict
from moni.manager import alert_channels as ac
from moni.manager import db
from moni.manager.alert_channels import ChannelStore, SendError, deliver, payload, send_email, send_webhook, sign
from moni.secretbox import SecretBox, generate_key


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "moni.db"
    db.migrate(p)
    c = db.connect(p)
    yield c
    c.close()


@pytest.fixture
def box():
    return SecretBox(generate_key())


class _Server:
    def __init__(self):
        self.requests = []
        self.behaviour = "ok"
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append(SimpleNamespace(headers=dict(self.headers), body=body))
                b = outer.behaviour
                if b == "slow":
                    threading.Event().wait(2)
                    return
                if b == "drip":  # Kopf sofort, Rumpf 1 Byte pro Sekunde
                    self.send_response(200)
                    self.send_header("Content-Length", "100")
                    self.end_headers()
                    try:
                        for _ in range(8):
                            self.wfile.write(b"x")
                            self.wfile.flush()
                            threading.Event().wait(1)
                    except OSError:
                        pass
                    return
                if b == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/x")
                    self.end_headers()
                    self.wfile.write(b"GEHEIM")
                    return
                self.send_response(500 if b == "500" else 200)
                self.end_headers()
                self.wfile.write(b"GEHEIM")

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/hook?token=abc"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def webhook_server():
    s = _Server()
    yield s
    s.httpd.shutdown()
    s.httpd.server_close()


def test_webhook_total_timeout(webhook_server):
    webhook_server.behaviour = "drip"
    t0 = time.monotonic()
    with pytest.raises(SendError) as ei:
        send_webhook(webhook_server.url, {"a": 1}, None, timeout=2)
    assert str(ei.value) == "Webhook nicht erreichbar" and ac.error_text(ei.value) == str(ei.value)
    assert 1 <= time.monotonic() - t0 <= 3.2


def test_webhook_signature_verifiable_independently(webhook_server):
    send_webhook(webhook_server.url, {"a": 1}, "geheim")
    req = webhook_server.requests[0]
    ts, body = req.headers["X-Moni-Timestamp"], req.body
    expected = "sha256=" + hmac.new(b"geheim", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert req.headers["X-Moni-Signature"] == expected and json.loads(body) == {"a": 1}
    assert req.headers["Content-Type"] == "application/json"
    assert sign("geheim", int(ts), body) == expected


def test_webhook_without_secret_has_no_signature(webhook_server):
    send_webhook(webhook_server.url, {"a": 1}, None)
    assert "X-Moni-Signature" not in webhook_server.requests[0].headers


@pytest.mark.parametrize("behaviour,msg", [("500", "Webhook antwortet mit HTTP 500"), ("redirect", "Webhook antwortet mit HTTP 302"),
                                           ("slow", "Webhook nicht erreichbar"), ("refused", "Webhook nicht erreichbar")])
def test_webhook_errors_are_fixed_texts_without_body(webhook_server, behaviour, msg):
    url = webhook_server.url
    if behaviour == "refused":
        webhook_server.httpd.shutdown()
        webhook_server.httpd.server_close()
    webhook_server.behaviour = behaviour
    with pytest.raises(SendError) as ei:
        send_webhook(url, {"a": 1}, "s", timeout=0.5)
    e = ei.value
    assert str(e) == msg and e.__context__ is None and e.__cause__ is None
    assert "GEHEIM" not in str(e) and "token" not in str(e)
    if behaviour == "redirect":
        assert len(webhook_server.requests) == 1  # nicht gefolgt


class FakeSMTP:
    calls: list = []
    fail: dict = {}

    def __init__(self, *a, **kw):
        FakeSMTP.calls.append(("init", a, kw))

    def _do(self, name, *a, **kw):
        FakeSMTP.calls.append((name, a, kw))
        if name in FakeSMTP.fail:
            raise FakeSMTP.fail[name]

    def ehlo(self, *a, **kw): self._do("ehlo")
    def starttls(self, **kw): self._do("starttls", **kw)
    def login(self, u, p): self._do("login", u, p)
    def send_message(self, m): self._do("send", m)
    def quit(self): self._do("quit")
    def close(self): self._do("close")


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.calls, FakeSMTP.fail = [], {}
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", type("FakeSSL", (FakeSMTP,), {}))
    return FakeSMTP


CFG = {"host": "mail.example.org", "port": 587, "security": "starttls", "username": "u",
       "from_addr": "moni@example.org", "to": ["a@example.org", "b@example.org"]}


def _names():
    return [c[0] for c in FakeSMTP.calls]


def test_email_starttls_before_login_and_cert_checked(fake_smtp):
    send_email(CFG, "pw", "Betreff\r\nBcc: x@evil", "Text")
    assert _names()[:6] == ["init", "ehlo", "starttls", "ehlo", "login", "send"]
    assert FakeSMTP.calls[0][1] == ("mail.example.org", 587) and FakeSMTP.calls[0][2]["timeout"] == 15
    ctx = FakeSMTP.calls[2][2]["context"]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert FakeSMTP.calls[4][1] == ("u", "pw")
    msg = next(c for c in FakeSMTP.calls if c[0] == "send")[1][0]
    assert "\n" not in msg["Subject"] and msg["Bcc"] is None
    assert msg["To"] == "a@example.org, b@example.org" and msg.get_content().strip() == "Text"


def test_email_tls_uses_smtp_ssl_with_context_and_no_starttls(fake_smtp):
    send_email({**CFG, "security": "tls", "port": 465, "username": None}, None, "S", "T")
    assert _names() == ["init", "send", "quit", "close"] and FakeSMTP.calls[0][2]["context"].check_hostname


@pytest.mark.parametrize("stage,exc,msg", [
    ("login", smtplib.SMTPAuthenticationError(535, b"GEHEIM"), "SMTP-Anmeldung fehlgeschlagen"),
    ("login", smtplib.SMTPNotSupportedError("GEHEIM"), "SMTP-Server bietet keine passende Anmeldemethode an"),
    ("send", smtplib.SMTPRecipientsRefused({"a": (550, b"GEHEIM")}), "SMTP-Server lehnt Nachricht ab"),
    ("send", smtplib.SMTPSenderRefused(550, b"GEHEIM", "x"), "SMTP-Server lehnt Nachricht ab"),
    ("send", smtplib.SMTPDataError(554, b"GEHEIM"), "SMTP-Server lehnt Nachricht ab"),
    ("login", smtplib.SMTPException("GEHEIM"), "SMTP-Server bietet keine passende Anmeldemethode an"),
    ("login", smtplib.SMTPServerDisconnected("GEHEIM"), "SMTP-Server nicht erreichbar"),
    ("send", smtplib.SMTPNotSupportedError("GEHEIM"), "SMTP-Server unterstützt SMTPUTF8 nicht"),
    ("starttls", smtplib.SMTPNotSupportedError("GEHEIM"), "TLS-Prüfung fehlgeschlagen"),
    ("starttls", ssl.SSLCertVerificationError("GEHEIM"), "TLS-Prüfung fehlgeschlagen"),
    ("starttls", ssl.SSLError("GEHEIM"), "TLS-Prüfung fehlgeschlagen"),
    ("init", ssl.SSLCertVerificationError("GEHEIM"), "TLS-Prüfung fehlgeschlagen"),
    ("send", smtplib.SMTPServerDisconnected("GEHEIM"), "SMTP-Server nicht erreichbar"),
    ("send", TimeoutError("GEHEIM"), "SMTP-Server nicht erreichbar"),
    ("init", ConnectionRefusedError("GEHEIM"), "SMTP-Server nicht erreichbar"),
])
def test_email_errors_mapped(fake_smtp, monkeypatch, stage, exc, msg):
    if stage == "init":
        def boom(*a, **kw):
            raise exc
        monkeypatch.setattr(smtplib, "SMTP", boom)
    else:
        FakeSMTP.fail[stage] = exc
    with pytest.raises(SendError) as ei:
        send_email(CFG, "pw", "S", "T")
    assert str(ei.value) == msg and ei.value.__context__ is None
    assert "login" not in _names() or stage in ("login", "send")  # nie Anmeldung ohne TLS


def _wh(**over):
    return {"name": "Slack", "kind": "webhook", "url": "https://hooks.slack.com/services/T0/B0/SECRETTOKEN",
            "hmac_secret": "HMACSECRET", **over}


def _em(**over):
    return {"name": "Mail", "kind": "email", "host": "mail.example.org", "port": 587, "security": "starttls",
            "username": "u", "password": "PWSECRET", "from_addr": "Moni@Example.org", "to": ["A@example.org"], **over}


def test_channel_secrets_encrypted_and_never_in_channel(conn, box):
    st = ChannelStore(conn, box)
    w, m = st.create(_wh(), 1), st.create(_em(), 1)
    dump = json.dumps([tuple(r) for r in conn.execute("SELECT * FROM alert_channels")])
    for s in ("SECRETTOKEN", "HMACSECRET", "PWSECRET"):
        assert s not in dump
        assert s not in repr(st.list()) and s not in str(st.get(w.id).__dict__)
    assert w.secret_set and m.secret_set and w.config == {} and "password" not in m.config
    assert w.url_hint == "https://hooks.slack.com/…" and m.url_hint is None
    assert m.config["from_addr"] == "moni@example.org" and m.config["to"] == ["a@example.org"]
    assert st.secrets(w.id) == {"url": "https://hooks.slack.com/services/T0/B0/SECRETTOKEN", "hmac": "HMACSECRET"}
    assert st.secrets(m.id) == {"password": "PWSECRET"}
    assert not st.create(_wh(name="n2", hmac_secret=None), 1).secret_set


def test_update_keeps_secrets_kind_fixed_and_duplicate(conn, box):
    st = ChannelStore(conn, box)
    w = st.create(_wh(), 1)
    u = st.update(w.id, {"name": "Slack2", "min_severity": "critical", "enabled": False}, )
    assert (u.name, u.min_severity, u.enabled) == ("Slack2", "critical", False) and u.secret_set
    assert st.secrets(w.id)["hmac"] == "HMACSECRET" and st.secrets(w.id)["url"].endswith("SECRETTOKEN")
    st.update(w.id, {"url": "https://x.example/y"})
    assert st.secrets(w.id)["url"] == "https://x.example/y" and st.secrets(w.id)["hmac"] == "HMACSECRET"
    with pytest.raises(ValueError, match="Art des Kanals lässt sich nicht ändern"):
        st.update(w.id, {"kind": "email"})
    with pytest.raises(Conflict, match="Kanalname ist bereits vergeben"):
        st.create(_em(name="Slack2"), 1)
    assert st.update(999, {"name": "x"}) is None
    assert st.delete(w.id) and not st.delete(w.id) and st.get(w.id) is None


def test_host_change_requires_new_password(conn, box):
    st = ChannelStore(conn, box)
    m = st.create(_em(), 1)
    st.update(m.id, {"port": 25, "security": "tls"})  # gleicher Host: Passwort bleibt
    assert st.secrets(m.id)["password"] == "PWSECRET"
    with pytest.raises(ValueError, match="Passwort fehlt"):
        st.update(m.id, {"host": "other.example.org"})
    assert st.get(m.id).config["host"] == "mail.example.org"
    st.update(m.id, {"host": "other.example.org", "password": "NEU"})
    assert st.secrets(m.id)["password"] == "NEU"
    nopw = st.create(_em(name="M2", password=None, username=None), 1)
    st.update(nopw.id, {"host": "o2.example.org"})  # ohne Passwort kein Zwang


@pytest.mark.parametrize("patch", [
    {"url": "ftp://x.example/a"}, {"url": "https://u:p@x.example/a"}, {"url": "https://x.example/ä"},
    {"url": "https://x.example/" + "a" * 2048}, {"url": "https://x.example/a b"}, {"url": "https://x.example/a#f"},
    {"url": ""}, {"hmac_secret": ""}, {"name": ""}, {"min_severity": "info"}])
def test_webhook_validation(conn, box, patch):
    with pytest.raises(ValueError):
        ChannelStore(conn, box).create(_wh(**patch), 1)


@pytest.mark.parametrize("patch", [
    {"host": "a b"}, {"host": ""}, {"host": "x" * 254}, {"port": 0}, {"port": 70000}, {"port": True},
    {"security": "none"}, {"to": []}, {"to": ["a@example.org"] * 11}, {"to": ["kaputt"]}, {"from_addr": "kaputt"}])
def test_email_validation(conn, box, patch):
    with pytest.raises(ValueError):
        ChannelStore(conn, box).create(_em(**patch), 1)


def test_http_webhook_warning_has_no_url(conn, box, caplog):
    with caplog.at_level("WARNING"):
        ChannelStore(conn, box).create(_wh(name="Offen", url="http://x.example/SECRETTOKEN"), 1)
    assert "Offen" in caplog.text and "SECRETTOKEN" not in caplog.text and "x.example" not in caplog.text


EVENT = {"event": "firing", "severity": "critical", "rule_name": "CPU-Auslastung", "server_name": "pi",
         "key": "{}", "value": 92.5, "threshold": 85.0, "reason": None, "ts": 1_700_000_000}


def test_payload_text_german():
    p = payload(EVENT, "Moni")
    assert p == {"version": 1, "event": "firing", "severity": "critical", "rule": "CPU-Auslastung", "server": "pi",
                 "labels": {}, "value": 92.5, "threshold": 85.0, "reason": None, "at": "2023-11-14T22:13:20Z",
                 "manager": "Moni", "text": "[KRITISCH] CPU-Auslastung auf pi: 92.5 (Schwelle 85) – ausgelöst"}
    e = {**EVENT, "event": "resolved", "severity": "warning", "key": '{"mountpoint":"/"}', "value": None, "threshold": None}
    p = payload(e, "Moni")
    assert p["labels"] == {"mountpoint": "/"}
    assert p["text"] == "[WARNUNG] CPU-Auslastung auf pi (mountpoint=/) – behoben"
    words = {"changed": "Schwere geändert", "reminder": "weiterhin aktiv", "test": "Testnachricht"}
    for ev, w in words.items():
        assert payload({**EVENT, "event": ev}, "M")["text"].endswith(f" – {w}")


def test_deliver_dispatches(conn, box, webhook_server, fake_smtp):
    st = ChannelStore(conn, box)
    w = st.create(_wh(url=webhook_server.url, hmac_secret="k"), 1)
    deliver(w, st.secrets(w.id), EVENT, "Moni")
    sent = json.loads(webhook_server.requests[0].body)
    assert sent["text"].startswith("[KRITISCH]") and "X-Moni-Signature" in webhook_server.requests[0].headers
    m = st.create(_em(), 1)
    deliver(m, st.secrets(m.id), EVENT, "Moni")
    msg = next(c for c in FakeSMTP.calls if c[0] == "send")[1][0]
    assert msg["Subject"] == "[KRITISCH] CPU-Auslastung auf pi: 92.5 (Schwelle 85)"
    assert "ausgelöst" in msg.get_content()


def test_reencrypt_includes_channels(conn):
    key = generate_key()
    st = ChannelStore(conn, SecretBox(key))
    w = st.create(_wh(), 1)
    st.create(_em(), 1)
    st.create(_em(name="M2", password=None), 1)
    before = conn.execute("SELECT secret_enc FROM alert_channels WHERE id=?", (w.id,)).fetchone()[0]
    box2 = SecretBox(generate_key() + "," + key)
    conn.execute("BEGIN IMMEDIATE")
    assert ChannelStore(conn, box2).reencrypt_all() == 2
    conn.commit()
    assert conn.execute("SELECT secret_enc FROM alert_channels WHERE id=?", (w.id,)).fetchone()[0] != before
    assert ChannelStore(conn, box2).secrets(w.id)["hmac"] == "HMACSECRET"


def test_cli_reencrypt_counts_channels(tmp_path, monkeypatch, capsys):
    import os
    from moni import cli
    monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MONI_SECRET_KEY", generate_key())
    monkeypatch.setenv("MONI_ROLE", "manager")
    monkeypatch.setattr("moni.manager.nginx.CONF_PATH", tmp_path / "nginx" / "moni.conf")
    db.migrate(tmp_path / "moni.db")
    c = db.connect(tmp_path / "moni.db")
    ChannelStore(c, SecretBox(os.environ["MONI_SECRET_KEY"])).create(_wh(), 1)
    assert cli.main(["secret", "reencrypt"]) == 0
    assert capsys.readouterr().out == "Neu verschlüsselt: 1\n"


@pytest.mark.parametrize("text,expected", [
    ("Webhook antwortet mit HTTP 500", "Webhook antwortet mit HTTP 500"),
    ("TLS-Prüfung fehlgeschlagen", "TLS-Prüfung fehlgeschlagen"),
    ("Webhook antwortet: GEHEIM https://hooks.example/T/B/x", "Versand fehlgeschlagen"),
    ("Webhook antwortet mit HTTP 9999", "Versand fehlgeschlagen"),
])
def test_error_text_only_from_fixed_list(text, expected):
    assert ac.error_text(SendError(text)) == expected


def test_store_drops_secrets_of_other_kind(conn, box):
    st = ChannelStore(conn, box)
    w = st.create(_wh(hmac_secret="H", password="PW"), 1)
    assert st.secrets(w.id) == {"url": _wh()["url"], "hmac": "H"}
    e = st.create({**_em(), "name": "mail", "password": "PW", "url": "https://x.example.org/", "hmac_secret": "H"}, 1)
    assert st.secrets(e.id) == {"password": "PW"}
    st.update(w.id, {"password": "PW2"})
    assert st.secrets(w.id) == {"url": _wh()["url"], "hmac": "H"}
