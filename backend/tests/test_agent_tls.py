import ssl
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from moni import cli, pki
from moni.agent.tls import TlsState
from moni.config import Settings

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def _files(tmp_path):
    return {p.name: p.read_bytes() for p in (tmp_path / "tls").iterdir()}


def _issue(ca, csr, usage="server"):
    return pki.cert_pem(pki.issue(*ca, csr, cn="moni-agent", sans=["agent.example"], usage=usage, now=NOW, days=90))


def test_ensure_creates_files_0600_and_code(tmp_path):
    st = TlsState(tmp_path); st.ensure(NOW)
    code = st.pairing_code("https://agent.example:9810")
    prefix, fp, token, url = code.split(":", 3)
    assert prefix == "moni-pair-v1" and len(fp) == 43 and url == "https://agent.example:9810"
    assert all(oct(p.stat().st_mode & 0o777) == "0o600" for p in (tmp_path / "tls").iterdir())
    assert sorted(_files(tmp_path)) == ["cert.pem", "key.pem", "pairing.json"]
    cert = pki.load_cert((tmp_path / "tls" / "cert.pem").read_bytes())
    assert fp == pki.fingerprint(cert) and len(token) == 43
    assert pki.common_name(cert) == "moni-agent" and pki.has_eku(cert, "server")
    assert st.cert_not_after == cert.not_valid_after_utc and 3649 < pki.days_left(cert, NOW) <= 3650
    assert st.pairing_code("") == f"moni-pair-v1:{fp}:{token}"  # ohne MONI_PUBLIC_URL kein URL-Teil
    assert not st.paired and st.context.verify_mode == ssl.CERT_NONE
    assert token not in repr(st)


def test_ensure_is_idempotent_and_code_stable(tmp_path):
    st = TlsState(tmp_path); ctx = st.context
    st.ensure(NOW)
    before, code = _files(tmp_path), st.pairing_code("")
    st.ensure(NOW + timedelta(days=1))
    assert _files(tmp_path) == before and st.pairing_code("") == code and st.context is ctx
    other = TlsState(tmp_path); other.ensure(NOW)  # neuer Prozess
    assert other.pairing_code("") == code


def test_check_token(tmp_path):
    st = TlsState(tmp_path); st.ensure(NOW)
    token = st.pairing_code("").split(":")[2]
    assert st.check_token(token) and not st.check_token(token[:-1]) and not st.check_token("")
    assert not st.check_token("ä" * 43)  # nicht-ASCII darf nicht werfen


def test_install_rejects_foreign_key_wrong_ca_wrong_eku(tmp_path):
    st = TlsState(tmp_path); st.ensure(NOW)
    ca, other_ca = pki.create_ca(NOW), pki.create_ca(NOW)
    ca_pem = pki.cert_pem(ca[1])
    csr = st.new_csr()
    before, code = _files(tmp_path), st.pairing_code("")
    bad = [
        (_issue(ca, pki.make_csr(pki.new_key(), "x")), ca_pem),  # fremder Schlüssel
        (_issue(other_ca, csr), ca_pem),                         # andere CA
        (_issue(ca, csr), pki.cert_pem(other_ca[1])),            # andere CA mitgeliefert
        (_issue(ca, csr, usage="client"), ca_pem),               # falsches EKU
        (b"kein pem", ca_pem),
        (_issue(ca, csr), b"kein pem"),
    ]
    for cert, ca_bytes in bad:
        with pytest.raises(ValueError, match="^Zertifikat passt nicht$"):
            st.install(cert, ca_bytes, NOW)
    with pytest.raises(ValueError, match="^Zertifikat passt nicht$"):
        st.install(_issue(ca, csr), ca_pem, NOW + timedelta(days=91))  # abgelaufen
    assert _files(tmp_path) == before and not st.paired and st.pairing_code("") == code
    assert st.context.verify_mode == ssl.CERT_NONE
    st.install(_issue(ca, csr), ca_pem, NOW)  # danach klappt es mit dem richtigen Zertifikat
    assert st.paired


def test_install_without_pending_key_fails(tmp_path):
    st = TlsState(tmp_path); st.ensure(NOW)
    ca = pki.create_ca(NOW)
    with pytest.raises(ValueError, match="^Zertifikat passt nicht$"):
        st.install(_issue(ca, pki.make_csr(pki.new_key(), "x")), pki.cert_pem(ca[1]), NOW)


def test_install_switches_context_to_cert_optional(tmp_path):
    st = TlsState(tmp_path); st.ensure(NOW); ctx = st.context
    token = st.pairing_code("").split(":")[2]
    ca = pki.create_ca(NOW)
    csr = st.new_csr()
    pending = (tmp_path / "tls" / "pending_key.pem").read_bytes()
    cert, ca_pem = _issue(ca, csr), pki.cert_pem(ca[1])
    st.install(cert, ca_pem, NOW)
    assert st.paired and st.pairing_code("") is None and st.context.verify_mode == ssl.CERT_OPTIONAL
    assert not (tmp_path / "tls" / "pairing.json").exists()
    assert _files(tmp_path) == {"key.pem": pending, "cert.pem": cert, "ca.pem": ca_pem}
    assert all(oct(p.stat().st_mode & 0o777) == "0o600" for p in (tmp_path / "tls").iterdir())
    assert st.context is ctx and st.cert_not_after == pki.load_cert(cert).not_valid_after_utc
    assert not st.check_token(token)
    again = TlsState(tmp_path); again.ensure(NOW)  # Neustart: gekoppelt bleibt gekoppelt
    assert again.paired and again.context.verify_mode == ssl.CERT_OPTIONAL and again.pairing_code("") is None


def test_reset_creates_new_token_and_unpairs(tmp_path):
    st = TlsState(tmp_path); st.ensure(NOW)
    old_code = st.pairing_code("")
    ca = pki.create_ca(NOW)
    st.install(_issue(ca, st.new_csr()), pki.cert_pem(ca[1]), NOW)
    old_key = (tmp_path / "tls" / "key.pem").read_bytes()
    code = st.reset(NOW, "https://agent.example")
    assert code.startswith("moni-pair-v1:") and code.endswith(":https://agent.example")
    assert not st.paired and sorted(_files(tmp_path)) == ["cert.pem", "key.pem", "pairing.json"]
    assert (tmp_path / "tls" / "key.pem").read_bytes() != old_key
    _, fp, token, _ = code.split(":", 3)
    assert fp != old_code.split(":")[1] and token != old_code.split(":")[2] and st.check_token(token)
    fresh = TlsState(tmp_path); fresh.ensure(NOW)  # nach dem Neustart
    assert fresh.pairing_code("https://agent.example") == code and fresh.context.verify_mode == ssl.CERT_NONE


def test_settings_tls_values():
    assert Settings.from_env({}).tls == "on"
    assert Settings.from_env({"MONI_TLS": "off"}).tls == "off"
    with pytest.raises(ValueError, match="^MONI_TLS muss on oder off sein$"):
        Settings.from_env({"MONI_TLS": "yes"})
    assert Settings.from_env({}).public_url == ""


@pytest.mark.parametrize("v,exp", [("", ""), ("https://agent.example:9810", "https://agent.example:9810"),
                                   ("https://agent.example/", "https://agent.example"), ("https://10.0.0.5", "https://10.0.0.5"),
                                   ("https://[::1]:9810", "https://[::1]:9810")])
def test_public_url_valid(v, exp):
    assert Settings.from_env({"MONI_PUBLIC_URL": v}).public_url == exp


@pytest.mark.parametrize("v", ["http://agent.example", "https://agent.example/pfad", "https://agent.example?x=1",
                               "https://agent.example#f", "https://u@agent.example", "https://agent.example:99999",
                               "https://agent.example:0", "https://agent.example:", "https://bad_host", "https://",
                               "agent.example", "https://[abc]"])
def test_public_url_invalid(v):
    with pytest.raises(ValueError, match=r"^MONI_PUBLIC_URL muss https://host\[:port\] sein$"):
        Settings.from_env({"MONI_PUBLIC_URL": v})


def test_cli_pair_code_and_reset(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MONI_TLS", "on")
    monkeypatch.setenv("MONI_PUBLIC_URL", "https://agent.example:9810")
    assert cli.main(["pair", "code"]) == 0
    code = capsys.readouterr().out.strip()
    assert code.startswith("moni-pair-v1:") and code.endswith(":https://agent.example:9810")
    assert cli.main(["pair", "code"]) == 0 and capsys.readouterr().out.strip() == code
    assert cli.main(["pair", "reset"]) == 0
    new, hint = capsys.readouterr().out.strip().splitlines()
    assert new != code and new.startswith("moni-pair-v1:")
    assert hint == "Bitte den Agent neu starten: docker compose restart moni-agent"
    assert cli.main(["pair", "code"]) == 0 and capsys.readouterr().out.strip() == new
    st = TlsState(tmp_path); st.ensure(NOW)
    ca = pki.create_ca(datetime.now(timezone.utc))
    st.install(pki.cert_pem(pki.issue(*ca, st.new_csr(), cn="moni-agent", sans=["agent.example"], usage="server",
                                      now=datetime.now(timezone.utc), days=90)), pki.cert_pem(ca[1]), datetime.now(timezone.utc))
    assert cli.main(["pair", "code"]) == 1
    assert "Agent ist bereits gekoppelt." in capsys.readouterr().err
    for env in ({"MONI_TLS": "off"}, {"MONI_ROLE": "manager", "MONI_SECRET_KEY": "x"}):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        for cmd in ("code", "reset"):
            assert cli.main(["pair", cmd]) == 2
            assert capsys.readouterr().err.strip() == "Fehler: Kopplung gibt es nur beim Agent mit MONI_TLS=on."
        monkeypatch.setenv("MONI_TLS", "on")


def test_create_app_logs_code_once_and_reports_paired(tmp_path, collector, monkeypatch, caplog):
    from fastapi.testclient import TestClient
    from moni.app import create_app
    monkeypatch.setattr("moni.app.setup_logging", lambda: None)  # sonst entfernt basicConfig(force=True) caplog
    s = Settings.from_env({"MONI_DATA_DIR": str(tmp_path), "MONI_TLS": "on", "MONI_PUBLIC_URL": "https://a.example"})
    caplog.set_level("WARNING", "moni.app")
    app = create_app(s, collector)
    st = app.state.tls
    code = st.pairing_code("https://a.example")
    logged = [r.getMessage() for r in caplog.records if r.levelname == "WARNING" and "Kopplungscode" in r.getMessage()]
    assert logged == [f"Kopplungscode (einmalig verwenden): {code}"]
    h = {"Authorization": f"Bearer {app.state.tokens.create('t', 'read')[1]}"}
    c = TestClient(app)
    assert c.get("/api/v1/info", headers=h).json()["paired"] is False
    ca = pki.create_ca(datetime.now(timezone.utc))
    st.install(pki.cert_pem(pki.issue(*ca, st.new_csr(), cn="moni-agent", sans=["a.example"], usage="server",
                                      now=datetime.now(timezone.utc), days=90)), pki.cert_pem(ca[1]), datetime.now(timezone.utc))
    assert c.get("/api/v1/info", headers=h).json()["paired"] is True
    caplog.clear()
    passed = TlsState(tmp_path); passed.ensure(NOW)
    assert create_app(s, collector, tls_state=passed).state.tls is passed  # gekoppelt: kein Code im Log
    assert not [r for r in caplog.records if "Kopplungscode" in r.getMessage()]


def test_tls_off_has_no_state_and_is_unpaired(settings, collector, auth):
    from fastapi.testclient import TestClient
    from moni.app import create_app
    app = create_app(replace(settings, tls="off"), collector)
    assert app.state.tls is None and not (settings.db_path.parent / "tls").exists()
    assert TestClient(app).get("/api/v1/info", headers=auth).json()["paired"] is False
