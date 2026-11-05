# Moni 2.0 – Paket 4: mTLS & Kopplung – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agent und Manager sprechen standardmäßig TLS. Der Manager betreibt eine eigene CA, koppelt Agents per Kopplungscode und CSR und ruft gekoppelte Agents nur noch per mTLS ab, mit automatischer Zertifikatserneuerung. Der Manager liefert seine Oberfläche und API standardmäßig per HTTPS aus. Hinter einem Reverse-Proxy (`MONI_TLS=off`) wertet er Weiterleitungs-Header nur von vertrauenswürdigen Proxys aus.

**Architecture:**
- **PKI:** Neues Modul `moni/pki.py` mit reinen Funktionen über `cryptography.x509`: CA, CSR, Ausstellen, Fingerabdruck, SSL-Kontext aus Speicher.
- **Agent:**
  - `moni/agent/tls.py` verwaltet Schlüssel, Zertifikat, Kopplungszustand und **einen** `ssl.SSLContext`, den uvicorn benutzt und der nach der Kopplung im laufenden Betrieb neu geladen wird.
  - Damit die App sieht, ob der Manager ein Client-Zertifikat vorgelegt hat, reicht eine kleine Unterklasse des uvicorn-h11-Protokolls das Peer-Zertifikat als ASGI-TLS-Erweiterung (`scope["extensions"]["tls"]`) durch.
- **Manager:**
  - `moni/manager/ca.py` hält CA und Client-Zertifikat. Die Schlüssel liegen mit `SecretBox` verschlüsselt im Daten-Volume.
  - `moni/manager/pairing.py` führt die Kopplung durch.
  - `AgentClient` bekommt je Server einen passenden SSL-Kontext.
- **nginx:** Das Manager-Image erzeugt seine Konfiguration beim Start (`moni.cli tls init`) für HTTPS (Standard) oder HTTP hinter Proxy.

**Tech Stack:** Python 3.14, `cryptography` (vorhanden: x509, EC P-256), `ssl` aus der Standardbibliothek, uvicorn 0.48.0 (h11), FastAPI, nginx `ngx_http_realip_module`. Keine neue Abhängigkeit.

**Spec:** [`docs/superpowers/specs/2026-11-04-moni-agent-manager-design.md`](../specs/2026-11-04-moni-agent-manager-design.md) (§6.1, §6.2, §6.3, §6.10 HTTPS und Master-Key) · Übersicht: [`2026-11-04-moni-2-uebersicht.md`](2026-11-04-moni-2-uebersicht.md) · Vorgänger: [Paket 3](2026-11-05-moni-2-paket-3-benutzer-anmeldung.md) (Nachlauf-Abschnitt „Paket 4“)

## Ist-Stand nach Paket 3 (Schnittstellen, auf die dieser Plan baut)

- **Settings:** `Settings.tls` (`MONI_TLS`, heute nur `"off"` erlaubt, `config.py:105`), `bind_host`, `trust_local_proxy`, `data_dir`, `secret_key`.
  - `__main__.server_args(s)` liefert die uvicorn-Argumente. Weiterleitungs-Header werden nur von `127.0.0.1` vertraut (P2-R7).
  - `access_log` ist im Manager aus (P3-R6).
- **Agent:**
  - `agent/api.py`: `health`, `info` (liefert fest `"paired": False`), `snapshot`, `history`, `/metrics`.
  - Anmeldung über `auth/deps.require_scope` (Bearer gegen `TokenStore`; Sessions nur in der Rolle Manager).
- **Manager:**
  - Schema v2. `servers` hat die Spalten `url`, `token_enc`, `headers_enc`, `header_names`, `status`, `last_error` und weitere. `ServerStore.add(name, url, token, headers)` prüft URL, Token und Header streng.
  - `manager/poller.py`: `AgentClient(url, token, headers, timeout)` über `_OPENER`, ohne Proxy und ohne Weiterleitungen. `AgentError(message, fatal)`.
  - `Poller._maintain(now)` läuft im SAVEPOINT `maintenance`.
  - `manager/api.py`: `POST /servers` (admin, auditiert `server.added`).
- **CLI:**
  - `token`, `secret generate|reencrypt`. `reencrypt` dreht Server, Benutzer und OIDC-Anbieter in **einer** `BEGIN IMMEDIATE`-Transaktion.
  - `server add|list|remove`, `user …`.
- **Manager-Image:**
  - nginx lauscht auf Port 80, proxyt `/api` auf `127.0.0.1:8001` und loggt mit `log_format moni` (`$uri`).
  - supervisord startet nginx und das Backend.
  - Das Dateisystem ist schreibgeschützt (`read_only`), beschreibbar sind nur `/tmp` (tmpfs) und `/data`.
- **Compose:**
  - `compose.agent.yaml` mit `MONI_TLS=off` und Port `${MONI_AGENT_BIND:-127.0.0.1}:9810`.
  - `compose.manager.yaml` mit Port `${MONI_MANAGER_BIND:-127.0.0.1}:8080:80`.
- **CI:**
  - Agent-Smoke über HTTP.
  - Manager-Smoke inklusive `scripts/ci/manager_auth_smoke.py` über HTTP.

## Entscheidungen dieses Plans (vom Planer gesetzt)

Diese Entscheidungen trifft der Plan. Im Ledger werden sie als Rulings P4-D1 bis P4-D11 geführt.

- **D1 Kryptografie:**
  - Alle Schlüssel sind EC P-256, signiert wird mit ECDSA-SHA256.
  - Laufzeiten:
    - Manager-CA 10 Jahre, CN `Moni Manager CA`.
    - Agent-Serverzertifikat **90 Tage**, Erneuerung ab **30** Tagen Restlaufzeit.
    - Manager-Client-Zertifikat **365 Tage**, Erneuerung ab **60** Tagen.
    - HTTPS-Zertifikat des Managers **397 Tage**, Erneuerung ab **30** Tagen.
  - Zertifikats-Merkmale:
    - Agent-Zertifikate tragen nur EKU `serverAuth`, SAN aus dem Host der Agent-URL.
    - Das Manager-Client-Zertifikat trägt nur EKU `clientAuth` und den CN `moni-manager`.
  - Eine CA-Rotation gibt es in dieser Version nicht; die Doku dazu folgt in Paket 7.
- **D2 Kopplungscode:**
  - Format: `moni-pair-v1:<fp>:<token>` oder `moni-pair-v1:<fp>:<token>:<url>`.
    - `fp` ist SHA-256 über das DER des vorläufigen Agent-Zertifikats, Base64url ohne `=` (43 Zeichen).
    - `token` ist `secrets.token_urlsafe(32)`.
    - `url` ist optional und kommt aus `MONI_PUBLIC_URL` des Agents. Fehlt sie, gibt der Admin die URL beim Hinzufügen an.
  - Der Agent schreibt den Code beim Start ins Log, solange er ungekoppelt ist (Ausnahme vom Geheimnis-Verbot wie beim Setup-Token, spec §6.3). Die CLI `pair code` gibt ihn erneut aus.
  - Das Token gilt, bis es benutzt oder per `pair reset` erneuert wird.
- **D3 Agent-TLS und Erkennung des Managers:**
  - `MONI_TLS` kennt `on` (Standard für beide Rollen) und `off`.
  - Ungekoppelt: Server-TLS mit selbstsigniertem Zertifikat, keine Client-Zertifikate.
  - Gekoppelt: `CERT_OPTIONAL` mit der Manager-CA als Vertrauensanker, damit Fremdsysteme weiter per Bearer-Token zugreifen können.
  - Der Manager gilt nur dann als angemeldet, wenn ein Client-Zertifikat vorliegt, das OpenSSL gegen die CA geprüft hat, **und** das EKU `clientAuth` sowie den CN `moni-manager` trägt.
  - Das Peer-Zertifikat erreicht die App über die ASGI-TLS-Erweiterung. Dafür gibt es die Unterklasse `MoniH11Protocol` und `http="h11"`. Sie hängt von uvicorn `0.48.0` ab; ein Integrationstest mit echtem uvicorn-Server sichert das ab.
- **D4 Schlüssel auf der Platte:**
  - Den CA-Schlüssel und den Client-Schlüssel des Managers verschlüsselt `SecretBox` (§6.10) in `/data/tls/`. `secret reencrypt` schreibt beide atomar neu.
  - Den HTTPS-Schlüssel des Managers braucht nginx im Klartext; er liegt mit 0600 in `/data/tls/`.
  - Die Agent-Schlüssel und das Kopplungs-Token liegen im Klartext mit 0600 im Agent-Volume, so wie bei jedem TLS-Server.
  - An `ssl` gehen Schlüssel aus dem Speicher nur über eine kurzlebige, mit einem Zufallspasswort verschlüsselte PEM-Datei in `/tmp`, die sofort gelöscht wird. Unverschlüsselt landet nie ein Schlüssel in `/tmp`.
- **D5 Token-Modus bleibt:**
  - Gemeint sind Agents mit `MONI_TLS=off` hinter einem Proxy, z. B. SAU-INDEX über den Cloudflare Tunnel.
  - Die API zeigt je Server `"security": "mtls" | "token"`.
  - Beim Anlegen eines Token-Servers loggt Moni die Warnung `Server <name> ohne mTLS angelegt (schwächere Variante, nur hinter vertrauenswürdigem Proxy)`.
  - Token-Server mit `https://` prüfen das Zertifikat gegen den System-Vertrauensspeicher.
- **D6 Port des Agents:**
  - `compose.agent.yaml` bindet weiter standardmäßig nur an `127.0.0.1` (sicherere Voreinstellung).
  - Für die direkte mTLS-Anbindung setzt der Betreiber `MONI_AGENT_BIND=0.0.0.0`. Das wird dokumentiert.
- **D7 Manager-HTTPS:**
  - Mit `MONI_TLS=on` (Standard) lauscht nginx auf 443 mit dem HTTPS-Zertifikat aus der Manager-CA. Port 80 leitet mit 301 auf https um.
  - Die SANs kommen aus `MONI_MANAGER_HOSTNAMES` (kommagetrennt), Standard ist `<hostname>,localhost,127.0.0.1`.
  - Compose veröffentlicht `${MONI_MANAGER_BIND:-127.0.0.1}:8443:443` und `:8080:80`.
  - Läuft der Container länger, prüft die Poller-Wartung täglich und erneuert bei Bedarf, danach folgt `nginx -s reload`.
- **D8 Vertrauenswürdige Proxys:**
  - `MONI_TRUSTED_PROXIES` ist eine kommagetrennte Liste von IPs und CIDRs, Standard leer.
  - Sie gilt nur bei `MONI_TLS=off`.
  - Manager: nginx setzt `set_real_ip_from` je Eintrag, `real_ip_header X-Forwarded-For` und `real_ip_recursive on`. Das Backend vertraut weiterhin nur nginx auf 127.0.0.1.
  - Agent: uvicorn bekommt `proxy_headers=True` und `forwarded_allow_ips=<Liste>`.
  - Damit sind die Drosselung und das Rate-Limit pro echtem Client gelöst (offener Punkt P3-R2).
- **D8a TLS-Abschluss am Proxy:**
  - `X-Forwarded-Proto` wertet das Backend nicht aus. Es braucht ihn nicht, denn Cookies sind immer `Secure` und OIDC nutzt `public_url`.
  - Bei `MONI_TLS=off` gilt `MONI_MANAGER_BIND` weiter mit Standard `127.0.0.1`; nur so ist der Proxy davor die einzige Stelle, die den Manager erreicht.
- **D9 Token-Verwaltung am Agent über den Manager (§6.3):**
  - Der Agent bietet `GET/POST/DELETE /api/v1/tokens`, aber **nur** für den per Zertifikat angemeldeten Manager.
  - Der Manager reicht diese Aufrufe für mTLS-Server unter `/api/v1/servers/{id}/agent-tokens` durch (admin, auditiert).
- **D10 Neu koppeln:**
  - `pair reset` (CLI am Agent) löscht CA und Zertifikat, erzeugt einen neuen Schlüssel, ein vorläufiges Zertifikat und ein neues Token und gibt den Code aus. Danach muss der Agent neu starten (`docker compose restart moni-agent`); die CLI sagt das.
  - Im Manager wird der Server anschließend entfernt und neu gekoppelt.
- **D11:** `GET /api/v1/ca.crt` am Manager ist ohne Anmeldung erreichbar (PEM, kein Geheimnis). Fremdsysteme können damit Agent-Zertifikate prüfen.
- **Offene Punkte aus Paket 3, hier erledigt:**
  - P3-R2 (Drosselung hinter Proxy): über D8.
  - Ein `Authorization`-Header, der kein Bearer ist (z. B. Basic vom Proxy), blockiert den Cookie-Login nicht mehr; nur noch `Bearer …` schaltet auf Token-Anmeldung.
  - nginx-`error_log` für `location /api/v1/auth/oidc/callback/` auf `crit`, damit der `code` bei einem Backend-Ausfall nicht im Log landet.

## Global Constraints

- Branch `feature/moni-2`, Draft-PR #41 (nicht mergen). Commits mit Autor-E-Mail `261156405+anonym-rgath@users.noreply.github.com` (repo-lokal gesetzt). Commit-Trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Python **3.14**. Tests: `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests -v`
- **Keine neue Abhängigkeit.** Erlaubt sind `cryptography` (x509, ec, hashes, serialization), `ssl`, `secrets`, `hashlib` und das vorhandene uvicorn 0.48.0.
- Private Schlüssel, Kopplungs-Token (außer im Kopplungscode, D2), API-Tokens, `MONI_SECRET_KEY` und entschlüsselte Schlüssel stehen nie in API-Antworten, Audit-Details, `repr()` oder Fehlermeldungen.
- Dateien in `/data/tls` werden mit 0600 angelegt, schreibend immer atomar (temporäre Datei, `fsync`, `os.replace`).
- TLS mindestens **1.2** (`ssl.TLSVersion.TLSv1_2`), sonst die Standard-Ciphers von `ssl.create_default_context`. `check_hostname` ist überall an, wo gegen eine CA geprüft wird. Die einzige Ausnahme ist das Fingerabdruck-Pinning bei der Kopplung: Dort ist `CERT_NONE` gesetzt, und Moni vergleicht den Fingerabdruck **vor** dem ersten Byte Nutzdaten.
- Der Agent bleibt strikt lesend (§4). Die neuen Agent-Endpunkte ändern nur den eigenen TLS-Zustand und die eigenen Tokens, nie den Host.
- Feste deutsche Fehlertexte:
  - `401 "Kopplungs-Token ungültig"`
  - `409 "Agent ist bereits gekoppelt"`
  - `422 "Kopplungscode ist ungültig"`
  - `502 "Fingerabdruck des Agents passt nicht zum Kopplungscode"`
  - `502 "Agent nicht erreichbar"`
  - `403 "Nur für den Manager"`
  - Poller bei abgelaufenem oder ungültigem Agent-Zertifikat: `"TLS-Prüfung des Agents fehlgeschlagen"` (fatal).
- Bestehende Tests bleiben grün. Die Fixture `settings` in `tests/conftest.py` setzt künftig `MONI_TLS=off`, weil der Standard auf `on` wechselt. Tests, die TLS brauchen, setzen `on` selbst.
- Texte für Menschen auf Deutsch mit korrekten Umlauten.

## Review Focus

1. **Angriffe auf die Kopplung:**
   - Jemand sitzt zwischen Manager und Agent (falscher Fingerabdruck): Moni bricht ab, bevor das Token gesendet wird.
   - Ein falsches oder schon benutztes Token ergibt 401.
   - Ein zweites `finish` nach erfolgreicher Kopplung ergibt 409.
   - Der Manager übernimmt SANs und Extensions **nicht** aus dem CSR; SAN und EKU legt er selbst fest.
   - Ein CSR mit ungültiger Signatur oder einem anderen Schlüsseltyp wird abgelehnt.
   - Ein Zertifikat, das nicht zum Schlüssel des Agents passt, wird abgelehnt.
   - Tests in Task 3 und Task 4.
2. **Verwechselte Client-Zertifikate:**
   - Legt ein Client ein Agent-Serverzertifikat (EKU `serverAuth`), ein Zertifikat einer fremden CA, ein abgelaufenes Zertifikat oder ein CA-Zertifikat ohne CN `moni-manager` vor, ist er **kein** Manager.
   - Ein Bearer-Token eines Fremdsystems funktioniert gleichzeitig weiter.
   - Ohne Zertifikat und ohne Token gibt es 401.
   - Tests in Task 2 (Integration mit echtem TLS) und Task 3.
3. **Ablauf und Erneuerung:**
   - Ein Agent-Zertifikat unter 30 Tagen Restlaufzeit wird automatisch erneuert, ohne Neustart.
   - Scheitert die Erneuerung, bleibt das alte Zertifikat; Moni versucht es höchstens stündlich erneut und loggt den Fehler.
   - Das Client-Zertifikat des Managers wird ohne Neustart erneuert.
   - Ein abgelaufenes Agent-Zertifikat setzt den Server-Status auf `error` mit klarem Text.
   - Tests in Task 5.
4. **Schlüssel und Geheimnisse:**
   - Kein Schlüssel und kein Token im Log oder in einer API-Antwort (Ausnahme: der Kopplungscode im Agent-Log).
   - Dateirechte 0600.
   - `secret reencrypt` dreht CA- und Client-Schlüssel mit. Scheitert die Transaktion, bleiben die alten Dateien unverändert.
   - Tests in Task 1 und Task 4.
5. **Proxy-Modus:**
   - Bei `MONI_TLS=off` wird `X-Forwarded-For` nur von Adressen aus `MONI_TRUSTED_PROXIES` übernommen; von anderen Absendern zählt die echte Absender-IP.
   - Ein Cookie-Login mit zusätzlichem Header `Authorization: Basic …` funktioniert.
   - Tests in Task 6.

---

### Task 1: PKI-Bausteine (`moni/pki.py`)

**Files:**
- Create: `backend/moni/pki.py`, `backend/tests/test_pki.py`

**Interfaces:**
- Produces:
  - `new_key() -> ec.EllipticCurvePrivateKey` (P-256)
  - `key_pem(key) -> bytes` (PKCS8, unverschlüsselt) und `load_key(pem: bytes)`
  - `cert_pem(cert) -> bytes`, `load_cert(pem: bytes) -> x509.Certificate`
  - `fingerprint(cert) -> str` (SHA-256 über DER, Base64url ohne `=`)
  - `create_ca(now: datetime) -> tuple[key, cert]`: CN `Moni Manager CA`, `BasicConstraints(ca=True, path_length=0)` kritisch, `KeyUsage(key_cert_sign, crl_sign)`, Laufzeit 10 Jahre (D1).
  - `self_signed(key, cn: str, sans: list[str], now, days: int) -> cert`: für das vorläufige Agent-Zertifikat und die Tests.
  - `make_csr(key, cn: str) -> bytes` (PEM)
  - `issue(ca_key, ca_cert, csr_pem: bytes, *, cn: str, sans: list[str], usage: Literal["server", "client"], now, days: int) -> cert`:
    - Prüft die CSR-Signatur und verlangt einen EC-P-256-Schlüssel, sonst `ValueError("CSR ist ungültig")`.
    - **Ignoriert** alle Extensions und Namen aus dem CSR.
    - Setzt `BasicConstraints(ca=False)` (kritisch) und `KeyUsage(digital_signature)` (kritisch).
    - Setzt EKU `serverAuth` bzw. `clientAuth`.
    - SANs: IP-Adressen als `IPAddress`, alles andere als `DNSName`.
    - Seriennummer `x509.random_serial_number()`, `not_before = now - 5 min`.
  - `san_for_host(host: str) -> list[str]` (gibt `[host]` zurück und prüft, dass es eine IP oder ein DNS-Name ist)
  - `matches_key(cert, key) -> bool`
  - `days_left(cert, now) -> float`
  - `has_eku(cert, usage) -> bool` und `common_name(cert) -> str | None`
  - `server_context(cert_pem, key_pem, ca_pem: bytes | None) -> ssl.SSLContext` (TLS-Server; mit `ca_pem` gilt `CERT_OPTIONAL` + `load_verify_locations(cadata=…)`)
  - `client_context(ca_pem: bytes | None, cert_pem: bytes | None = None, key_pem: bytes | None = None) -> ssl.SSLContext`:
    - Mit `ca_pem`: nur diese CA vertrauen, `check_hostname=True`.
    - Ohne `ca_pem`: System-Vertrauensspeicher.
    - Mit `cert_pem`/`key_pem`: Client-Zertifikat laden.
  - `load_chain_into(ctx, cert_pem, key_pem) -> None`: schreibt den Schlüssel mit einem Zufallspasswort verschlüsselt (`BestAvailableEncryption`) nach `tempfile.mkstemp(dir="/tmp" bzw. tempfile.gettempdir())`, ruft `ctx.load_cert_chain(certfile, keyfile, password=…)` auf und löscht beide Dateien im `finally` (D4).
  - `write_private(path: Path, data: bytes) -> None`: atomar mit 0600 (Global Constraints).

- [ ] **Step 1: Failing tests**

```python
def test_issue_ignores_csr_extensions_and_sets_own_san_and_eku():
    ca_key, ca = pki.create_ca(NOW)
    key = pki.new_key()
    csr = x509.CertificateSigningRequestBuilder().subject_name(name("evil")).add_extension(
        x509.SubjectAlternativeName([x509.DNSName("bank.example")]), False).add_extension(
        x509.BasicConstraints(ca=True, path_length=None), True).sign(key, hashes.SHA256())
    cert = pki.issue(ca_key, ca, csr.public_bytes(PEM), cn="agent", sans=["10.0.0.5"], usage="server", now=NOW, days=90)
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert [str(ip) for ip in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)] == ["10.0.0.5"]
    assert pki.has_eku(cert, "server") and not pki.has_eku(cert, "client")
    assert pki.matches_key(cert, key)

@pytest.mark.parametrize("bad", ["rsa_csr", "tampered_signature", "garbage", "p384_csr"])
def test_issue_rejects_bad_csr(bad): ...                      # ValueError("CSR ist ungültig")

def test_fingerprint_format():
    key = pki.new_key(); c = pki.self_signed(key, "a", ["localhost"], NOW, 1)
    fp = pki.fingerprint(c)
    assert len(fp) == 43 and "=" not in fp and fp == pki.fingerprint(pki.load_cert(pki.cert_pem(c)))

def test_tls_roundtrip_mtls_in_memory():
    # echter Handshake über socketpair/MemoryBIO: server_context(agent, ca) + client_context(ca, manager_client)
    # → Server sieht getpeercert() mit CN moni-manager; ohne Client-Zertifikat klappt der Handshake ebenfalls (CERT_OPTIONAL)
    # und getpeercert() ist leer; ein Client-Zertifikat einer fremden CA → Handshake-Fehler

def test_client_context_checks_hostname(): ...              # Zertifikat mit SAN 10.0.0.5, Verbindung als "10.0.0.6" → ssl.SSLCertVerificationError
def test_load_chain_into_leaves_no_plain_key_in_tmp(tmp_path, monkeypatch): ...   # tempdir=tmp_path → nach Aufruf leer; keine Datei enthielt "BEGIN PRIVATE KEY"
def test_write_private_mode_and_atomic(tmp_path): ...       # 0o600, kein *.tmp übrig
def test_days_left_and_create_ca_constraints(): ...
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün.
- [ ] **Step 5: Commit** `feat(pki): CA, CSR, Ausstellen und TLS-Kontexte`

---

### Task 2: Agent-TLS: Zustand, Server-Start, Peer-Zertifikat in der App, CLI `pair`

**Files:**
- Create: `backend/moni/agent/tls.py`, `backend/moni/agent/h11tls.py`, `backend/tests/test_agent_tls.py`, `backend/tests/test_agent_tls_server.py`
- Modify: `backend/moni/config.py` (`MONI_TLS` on/off, Standard `on`; `public_url` aus `MONI_PUBLIC_URL`), `backend/moni/__main__.py`, `backend/moni/app.py` (Agent: `app.state.tls`), `backend/moni/cli.py` (`pair code|reset`), `backend/moni/agent/api.py` (`info.paired`), `backend/tests/conftest.py` (`MONI_TLS=off` in `settings`), `backend/tests/test_config.py`

**Interfaces:**
- Consumes: `pki.*` (Task 1).
- Produces:
  - `Settings.tls` ∈ `{"on", "off"}`, Standard `"on"`. Sonst `ValueError("MONI_TLS muss on oder off sein")`.
  - `Settings.public_url: str` aus `MONI_PUBLIC_URL`, Standard `""`. Nur der Agent nutzt sie, für den Kopplungscode. Wenn gesetzt: `https://host[:port]` ohne Pfad.
  - `agent.tls.TlsState(data_dir: Path)`. Dateien in `<data_dir>/tls/`: `key.pem`, `cert.pem`, `ca.pem` (nur gekoppelt), `pairing.json` (`{"token": …}`, nur ungekoppelt), `pending_key.pem` (nur während eines CSR).
    - `ensure(now) -> None`: legt beim ersten Mal Schlüssel, ein vorläufiges selbstsigniertes Zertifikat (CN `moni-agent`, SAN `localhost`, 3650 Tage) und ein Token an.
    - `paired: bool`
    - `pairing_code(public_url: str) -> str | None` (D2; `None`, wenn gekoppelt)
    - `context: ssl.SSLContext` (immer dasselbe Objekt)
    - `check_token(token: str) -> bool` (`compare_digest`)
    - `new_csr() -> bytes`: neuer Schlüssel in `pending_key.pem`, liefert das CSR.
    - `install(cert_pem: bytes, ca_pem: bytes, now) -> None`:
      - Das Zertifikat muss zum ausstehenden Schlüssel passen, von `ca_pem` signiert sein, EKU `serverAuth` tragen und gültig sein; sonst `ValueError("Zertifikat passt nicht")`.
      - Ist das erfüllt, ersetzt die Methode atomar Schlüssel, Zertifikat und CA, löscht `pairing.json` und `pending_key.pem` und lädt `context` neu:
        - `load_cert_chain` mit dem neuen Zertifikat,
        - `load_verify_locations(cadata=ca)`,
        - `verify_mode = CERT_OPTIONAL`.
    - `reset(now) -> str`: D10, liefert den neuen Code.
    - `cert_not_after: datetime`
  - `agent.h11tls.MoniH11Protocol(uvicorn.protocols.http.h11_impl.H11Protocol)`: Nach `super().connection_made(transport)` holt sie `transport.get_extra_info("ssl_object")`.
    - Gibt es ein Peer-Zertifikat (`getpeercert(binary_form=True)`), umhüllt sie `self.app` für diese Verbindung, sodass jeder Scope `scope.setdefault("extensions", {})["tls"] = {"client_cert_chain": [<PEM des Peer-Zertifikats>]}` erhält. Format nach der ASGI-TLS-Erweiterung.
    - Ohne TLS oder ohne Peer-Zertifikat bleibt der Scope unverändert.
  - `__main__`:
    - `MONI_TLS=on` und Rolle `agent`:
      1. `TlsState` **einmal** erzeugen und `ensure` aufrufen.
      2. Die App-Instanz mit `create_app(settings, tls_state=state)` bauen (neuer optionaler Parameter). Server und App nutzen damit denselben Kontext.
      3. `uvicorn.Config(app, http=MoniH11Protocol, log_config=None, **server_args(s))` erzeugen, `config.load()` aufrufen, dann `config.ssl = state.context` setzen.
      4. `uvicorn.Server(config).run()` starten.
    - Ohne TLS bleibt der bisherige Start über die Factory.
    - Rolle `manager`: Das Backend bleibt ohne TLS auf 127.0.0.1; TLS macht nginx (Task 6).
  - Beim Start des ungekoppelten Agents loggt `create_app` einmal mit WARNING: `Kopplungscode (einmalig verwenden): <code>`.
  - CLI:
    - `pair code`: gibt den Code aus und endet mit Exit 0; ist der Agent gekoppelt, `Agent ist bereits gekoppelt.` mit Exit 1.
    - `pair reset`: gibt den neuen Code und `Bitte den Agent neu starten: docker compose restart moni-agent` aus.
    - Beide nur in der Rolle `agent` mit `MONI_TLS=on`, sonst Exit 2 mit klarer Meldung.
  - `info` liefert `"paired": state.paired` (bei `off` immer `False`).

- [ ] **Step 1: Failing tests**

```python
# test_agent_tls.py (ohne Netzwerk)
def test_ensure_creates_files_0600_and_code(tmp_path):
    st = TlsState(tmp_path); st.ensure(NOW)
    code = st.pairing_code("https://agent.example:9810")
    prefix, fp, token, url = code.split(":", 3)
    assert prefix == "moni-pair-v1" and len(fp) == 43 and url == "https://agent.example:9810"
    assert all(oct(p.stat().st_mode & 0o777) == "0o600" for p in (tmp_path / "tls").iterdir())

def test_ensure_is_idempotent_and_code_stable(tmp_path): ...
def test_install_rejects_foreign_key_wrong_ca_wrong_eku(tmp_path): ...     # je ValueError, Zustand unverändert (weiter ungekoppelt)
def test_install_switches_context_to_cert_optional(tmp_path):
    ...; st.install(cert, ca, NOW)
    assert st.paired and st.pairing_code("") is None and st.context.verify_mode == ssl.CERT_OPTIONAL
    assert not (tmp_path / "tls" / "pairing.json").exists()
def test_reset_creates_new_token_and_unpairs(tmp_path): ...
def test_settings_tls_values(): ...                      # Standard "on"; "off"; "yes" → ValueError; MONI_PUBLIC_URL mit Pfad → ValueError
def test_cli_pair_code_and_reset(tmp_path, monkeypatch, capsys): ...

# test_agent_tls_server.py (echter uvicorn-Server in einem Thread auf 127.0.0.1:<freier Port>, wie __main__ ihn baut)
def test_peer_cert_reaches_app_only_with_manager_cert(agent_tls_server, manager_ctx, foreign_ctx, plain_ctx):
    # Agent gekoppelt mit Test-CA. Hilfsroute /_test/peer gibt scope["extensions"].get("tls") zurück (nur im Test eingebunden)
    assert get(agent_tls_server, "/_test/peer", manager_ctx)["client_cert_chain"]
    assert get(agent_tls_server, "/_test/peer", plain_ctx) is None             # ohne Zertifikat: Handshake ok, keine Erweiterung
    with pytest.raises(ssl.SSLError):
        get(agent_tls_server, "/_test/peer", foreign_ctx)                      # fremde CA: Handshake scheitert
def test_context_reload_after_install_affects_new_connections(...): ...      # ungekoppelt → install → neuer Client mit CA-Prüfung verbindet sich
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün. Die Hilfsroute `/_test/peer` existiert nur im Test (Fixture fügt sie der App hinzu).
- [ ] **Step 5: Commit** `feat(agent): TLS standardmäßig an, Kopplungszustand, Peer-Zertifikat für die App`

---

### Task 3: Agent: Manager-Anmeldung per Zertifikat, Kopplungs- und Token-Endpunkte

**Files:**
- Create: `backend/moni/agent/pairing_api.py`, `backend/tests/test_agent_pairing_api.py`
- Modify: `backend/moni/auth/deps.py`, `backend/moni/app.py` (Router nur bei Agent und `tls=on`), `backend/tests/test_deps.py`

**Interfaces:**
- Consumes: `TlsState` (Task 2), `pki.load_cert/has_eku/common_name`, `TokenStore`.
- Produces:
  - `deps.manager_cert(request) -> bool`. Nur in der Rolle `agent` mit `tls=on` und `state.paired` wahr, wenn alle drei Bedingungen gelten:
    - `scope["extensions"]["tls"]["client_cert_chain"][0]` ist ein Zertifikat mit EKU `clientAuth` und CN `moni-manager` (D3).
    - Das Zertifikat ist von der gespeicherten CA signiert.
    - Es ist zeitlich gültig.
    Das Signieren hat OpenSSL schon geprüft; die Prüfung hier ist zusätzliche Absicherung.
  - `require_scope("read")` akzeptiert zusätzlich `manager_cert`, dann `Principal(scope="read", actor="manager", user_id=None, session=None)`. Bearer bleibt unverändert. Ist beides vorhanden, gewinnt das Zertifikat.
  - `require_manager() -> Callable[[Request], Principal]`: Ist `manager_cert` falsch, folgt `403 "Nur für den Manager"`. Ohne Zertifikat und ohne Token: 401.
  - Endpunkte (Prefix `/api/v1`, nur Agent mit `tls=on`):

| Route | Zugang | Body | Ergebnis |
|---|---|---|---|
| `POST /tls/csr` | ungekoppelt: Header `X-Moni-Pairing-Token` gültig (sonst `401 "Kopplungs-Token ungültig"`). Gekoppelt: `require_manager` | – | `{"csr": "<PEM>"}` |
| `POST /tls/cert` | wie oben | `{"certificate": PEM, "ca": PEM}` (je max. 16 KB) | 204. `ValueError` → `422 "Zertifikat passt nicht"`. Ungekoppelt + Token → Kopplung. Gekoppelt + Manager → Erneuerung; dabei muss `ca` gleich der gespeicherten CA sein, sonst 422. Log INFO `Agent gekoppelt` bzw. `Agent-Zertifikat erneuert` (ohne Inhalte). |
| `GET /tokens` | `require_manager` | – | `[{id, name, scope, created_at, expires_at}]` |
| `POST /tokens` | `require_manager` | `{name, expires_days?}` | `201 {…, "token"}` (Scope immer `read`, §6.3) |
| `DELETE /tokens/{id}` | `require_manager` | – | 204/404 |

  - Ist der Agent schon gekoppelt, liefert der Pairing-Token-Weg `409 "Agent ist bereits gekoppelt"`.
  - Das Token wird erst durch ein erfolgreiches `install` verbraucht.
  - `POST /tls/csr` darf mehrfach kommen; der jeweils letzte ausstehende Schlüssel gilt.
  - Fehlversuche mit dem Kopplungs-Token: höchstens 10 pro Minute pro IP. Darüber folgt `429`; dafür wird das bestehende `LoginThrottle` mit eigener Instanz wiederverwendet.

- [ ] **Step 1: Failing tests** (ASGI-Scope mit injizierter `extensions.tls` über eine kleine Test-Middleware; Zertifikate aus `pki`)

```python
def test_pairing_flow_via_token(agent_client, state, ca):
    assert agent_client.post("/api/v1/tls/csr").status_code == 401
    csr = agent_client.post("/api/v1/tls/csr", headers=TOKEN_HDR).json()["csr"]
    cert = pki.issue(*ca, csr.encode(), cn="agent", sans=["127.0.0.1"], usage="server", now=NOW, days=90)
    r = agent_client.post("/api/v1/tls/cert", headers=TOKEN_HDR, json={"certificate": pem(cert), "ca": pem(ca[1])})
    assert r.status_code == 204 and state.paired
    assert agent_client.post("/api/v1/tls/csr", headers=TOKEN_HDR).status_code == 409

@pytest.mark.parametrize("cert_kind,expected", [("manager", 200), ("agent_server_cert", 401), ("other_cn", 401),
                                                ("foreign_ca", 401), ("expired", 401), ("none", 401)])
def test_snapshot_with_client_cert(paired_client, cert_kind, expected): ...

def test_bearer_still_works_when_paired(paired_client, token): ...
def test_manager_only_routes_reject_bearer_admin(paired_client, admin_token): ...   # 403 "Nur für den Manager"
def test_renewal_requires_same_ca(...): ...
def test_token_endpoints_create_read_scope_only(...): ...
def test_pairing_token_bruteforce_429(...): ...
def test_routes_absent_when_tls_off(...): ...          # 404
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün.
- [ ] **Step 5: Commit** `feat(agent): Manager-Anmeldung per Client-Zertifikat, Kopplung und Token-Verwaltung`

---

### Task 4: Manager: CA, Client-Zertifikat, Kopplung, mTLS-Abruf

**Files:**
- Create: `backend/moni/manager/ca.py`, `backend/moni/manager/pairing.py`, `backend/moni/manager/migrations/0003_tls.sql`, `backend/tests/test_manager_ca.py`, `backend/tests/test_pairing.py`
- Modify: `backend/moni/manager/servers.py`, `backend/moni/manager/poller.py` (`AgentClient`), `backend/moni/manager/api.py`, `backend/moni/app.py`, `backend/moni/cli.py` (`server add --pairing-code-file`, `tls init`, `secret reencrypt`), `backend/tests/test_servers.py`, `backend/tests/test_poller.py`, `backend/tests/test_manager_api.py`, `backend/tests/test_cli.py`

**Interfaces:**
- Consumes: `pki` (Task 1), Agent-Endpunkte aus Task 3 (HTTP-Vertrag), `SecretBox`.
- Produces:
  - Migration `0003_tls.sql`:
    `ALTER TABLE servers ADD COLUMN security TEXT NOT NULL DEFAULT 'token' CHECK (security IN ('token','mtls'));`
    `ALTER TABLE servers ADD COLUMN cert_not_after INTEGER;`
    `ALTER TABLE servers ADD COLUMN cert_renew_tried INTEGER;`
  - `manager.ca.ManagerPki(data_dir: Path, box: SecretBox)`. Dateien in `/data/tls/`:
    - `ca.crt`, `ca.key.enc` (Fernet)
    - `client.crt`, `client.key.enc` (Fernet)
    - `https.crt`, `https.key` (0600, Klartext für nginx; Task 6)

    Methoden:
    - `ensure(now) -> None`: legt CA und Client-Zertifikat an bzw. erneuert das Client-Zertifikat ab 60 Tagen Restlaufzeit (D1).
    - `ca_pem: bytes`
    - `sign_agent(csr_pem, host, now) -> bytes` (90 Tage, `usage="server"`, SAN `pki.san_for_host(host)`)
    - `client_context() -> ssl.SSLContext`: gecacht; nach einer Erneuerung neu gebaut.
    - `prepare_reencrypt() -> tuple[int, Callable[[], None], Callable[[], None]]`: schreibt beide `.enc` mit `box.rotate` als temporäre Dateien (0600). Liefert `(2, commit, abort)`. `commit` setzt die Dateien per `os.replace` ein, `abort` löscht sie.
  - `manager.pairing.parse_code(code: str) -> tuple[fp, token, url | None]`: Fehler ergeben `ValueError("Kopplungscode ist ungültig")`.
  - `manager.pairing.pair(url: str, code: str, pki: ManagerPki, now, timeout) -> int` (liefert `cert_not_after`). Ablauf:
    1. Verbindung mit `CERT_NONE`. Der Fingerabdruck von `getpeercert(binary_form=True)` wird **vor** dem Senden der Anfrage verglichen; bei Abweichung `PairingError("Fingerabdruck des Agents passt nicht zum Kopplungscode")`. Umsetzung über `http.client.HTTPSConnection` mit eigenem Kontext; nach `connect()` prüfen, dann `request()`.
    2. `POST /api/v1/tls/csr` mit `X-Moni-Pairing-Token`.
    3. `sign_agent(csr, host aus url)`.
    4. `POST /api/v1/tls/cert` über dieselbe gepinnte Verbindung.
    5. Kontrollabruf `GET /api/v1/info` mit `client_context()` und echter CA- und Hostnamen-Prüfung. Verlangt wird `paired: true`.

    `PairingError(message)` trägt feste Texte (siehe Global Constraints). HTTP 401 vom Agent wird zu `"Kopplungs-Token ungültig"`, 409 zu `"Agent ist bereits gekoppelt"`.
  - `ServerStore.add_mtls(name, url, cert_not_after) -> Server`: URL muss `https://` sein, kein Token. Dazu `Server.security` und `Server.cert_not_after`.
  - `AgentClient(url, token, headers, timeout, ssl_context=None)`:
    - `mtls`-Server: `ssl_context=pki.client_context()`, kein `Authorization`-Header.
    - `token`-Server mit https: System-Vertrauensspeicher (D5).
    - `ssl.SSLCertVerificationError` und Handshake-Fehler führen zu `AgentError("TLS-Prüfung des Agents fehlgeschlagen", fatal=True)`.
  - API:
    - `POST /api/v1/servers` akzeptiert **entweder** `{name, url, token, headers?}` (Token-Modus, D5-Warnung im Log) **oder** `{name, pairing_code, url?}` (mTLS). Ist `url` sowohl im Body als auch im Code angegeben und weichen sie ab, folgt 422.
    - `PairingError` ergibt 502 bzw. 401/409 mit dem festen Text.
    - Audit `server.added` mit `details` `{"url", "security"}`.
    - Die Server-JSON-Ausgabe hat zusätzlich `security` und `cert_not_after` (ISO oder null).
  - `GET /api/v1/ca.crt` ohne Anmeldung, Typ `application/x-pem-file` (D11).
  - `app.py` (Manager): `ManagerPki(...).ensure(now)` beim Start, `app.state.pki`. Der Poller erhält `pki`.
  - CLI:
    - `tls init`: erzeugt bzw. erneuert CA und Client-Zertifikat (Task 6 erweitert um HTTPS und nginx).
    - `server add --name X --pairing-code-file PFAD|- [--url U]`.
    - `secret reencrypt` zählt CA- und Client-Schlüssel mit. Ablauf: zuerst `prepare_reencrypt()`, dann die DB-Transaktion. Nach dem Commit wird `commit()` aufgerufen, bei jedem Fehler `abort()`.

- [ ] **Step 1: Failing tests**

```python
def test_pair_end_to_end_against_real_tls_agent(agent_tls_server, manager_pki):   # Fixture aus Task 2, echter uvicorn mit TLS
    exp = pairing.pair(agent_tls_server.url, agent_tls_server.code, manager_pki, NOW, 5)
    client = AgentClient(agent_tls_server.url, "", {}, 5, ssl_context=manager_pki.client_context())
    assert client.get("/api/v1/info")["paired"] is True and exp > NOW_TS

def test_pair_aborts_on_wrong_fingerprint_before_sending_token(agent_tls_server, manager_pki, spy):
    code = agent_tls_server.code.replace(agent_tls_server.fp, "A" * 43)
    with pytest.raises(PairingError, match="Fingerabdruck"):
        pairing.pair(agent_tls_server.url, code, manager_pki, NOW, 5)
    assert spy.requests == []                                     # Agent hat keine Anfrage gesehen

def test_pair_wrong_token_401_and_second_pair_409(...): ...
def test_mtls_server_uses_no_bearer_and_checks_hostname(...): ...  # Agent-URL mit anderem Host als SAN → AgentError TLS
def test_api_add_by_pairing_code_and_by_token(...): ...            # security mtls/token; Token-Variante loggt Warnung
@pytest.mark.parametrize("code", ["", "moni-pair-v2:x:y", "moni-pair-v1:kurz:t", "moni-pair-v1:" + "A"*43 + ":"])
def test_parse_code_invalid(code): ...
def test_ca_files_encrypted_and_0600(...): ...                     # ca.key.enc enthält kein "PRIVATE KEY"; Modus 0600
def test_reencrypt_rotates_tls_keys_atomically(...): ...           # DB-Fehler simuliert → alte .enc unverändert, keine .tmp übrig
def test_ca_crt_public(...): ...
def test_migration_v3_keeps_servers(...): ...
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün.
- [ ] **Step 5: Commit** `feat(manager): eigene CA, Kopplung per Code und CSR, Abruf per mTLS`

---

### Task 5: Zertifikatserneuerung und Agent-Tokens über den Manager

**Files:**
- Modify: `backend/moni/manager/poller.py` (`_maintain`), `backend/moni/manager/ca.py`, `backend/moni/manager/pairing.py` (`renew`), `backend/moni/manager/api.py`, `backend/tests/test_poller.py`, `backend/tests/test_pairing.py`, `backend/tests/test_manager_api.py`

**Interfaces:**
- Produces:
  - `pairing.renew(client: AgentClient, url, pki, now) -> int`: `POST /tls/csr` → `sign_agent` → `POST /tls/cert` mit `ca`, beides per mTLS. Liefert das neue `cert_not_after`.
  - `Poller._maintain(now)` führt zusätzlich aus, **außerhalb** der DB-Transaktion, weil es Netzwerk braucht:
    - Sie prüft für jeden `mtls`-Server, ob `cert_not_after - now < 30 Tage` gilt und `cert_renew_tried` älter als 3600 s ist.
    - Ist das der Fall, ruft sie `renew` auf. Bei Erfolg schreibt sie `cert_not_after`, bei Fehler nur `cert_renew_tried = now`, loggt eine WARNING ohne Inhalte und lässt das alte Zertifikat in Kraft.
    - Außerdem ruft sie `pki.ensure(now)` auf; das erneuert das Client-Zertifikat und baut den Kontext neu.
    - Die DB-Schreibvorgänge laufen danach in einer eigenen kurzen Transaktion.
  - Server-Status bei abgelaufenem Agent-Zertifikat: Den Fehler liefert der Abruf bereits (`AgentError` TLS, fatal), Status `error`. Eine zusätzliche Logik ist nicht nötig, nur ein Test.
  - Manager-API (admin, nur für `mtls`-Server, sonst `422 "Nur für gekoppelte Server"`):
    - `GET /api/v1/servers/{id}/agent-tokens`
    - `POST /api/v1/servers/{id}/agent-tokens` mit `{name, expires_days?}` → 201, Token einmalig
    - `DELETE /api/v1/servers/{id}/agent-tokens/{token_id}`

    Audit `agent_token.created` bzw. `agent_token.revoked` mit `target=<server name>` und `details={"token_id"}`. Agent-Fehler ergeben `502 "Agent nicht erreichbar"`.
  - Alle neuen Routen sind admin-only. Der Routen-Inventartest aus Paket 3 deckt sie automatisch ab und muss grün bleiben.

- [ ] **Step 1: Failing tests**

```python
def test_renewal_when_30_days_left(paired_env, clock):
    clock.set(paired_env.cert_not_after - 29 * 86400)
    paired_env.poller.poll_once(clock.now)
    assert paired_env.server().cert_not_after > paired_env.cert_not_after
    assert paired_env.agent_state.cert_not_after.timestamp() == paired_env.server().cert_not_after

def test_no_renewal_with_31_days_left(...): ...
def test_renewal_failure_keeps_old_cert_and_retries_hourly(...): ...   # Agent liefert 500 → tried gesetzt; nach 30 min kein Versuch, nach 61 min erneut
def test_client_cert_renewed_without_restart(...): ...                 # 59 Tage Rest → neues client.crt, client_context() liefert neuen Kontext, Abruf klappt
def test_expired_agent_cert_sets_status_error(...): ...
def test_agent_tokens_via_manager(...): ...                            # Klartext nur einmal, Audit ohne Token
def test_agent_tokens_rejected_for_token_servers(...): ...
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün.
- [ ] **Step 5: Commit** `feat(manager): automatische Zertifikatserneuerung, Agent-Tokens über den Manager`

---

### Task 6: Manager-HTTPS, vertrauenswürdige Proxys, Härtungen aus Paket 3

**Files:**
- Create: `backend/moni/manager/nginx.py`, `backend/tests/test_nginx_conf.py`
- Modify:
  - `backend/moni/config.py` (`MONI_TRUSTED_PROXIES`, `MONI_MANAGER_HOSTNAMES`), `backend/tests/test_config.py`
  - `backend/moni/manager/ca.py` (HTTPS-Zertifikat), `backend/moni/cli.py` (`tls init` schreibt die nginx-Konfiguration), `backend/moni/__main__.py` (Agent: `forwarded_allow_ips`)
  - `backend/moni/auth/deps.py` (Header, der kein Bearer ist), `backend/tests/test_deps.py`
  - `backend/moni/manager/poller.py` (tägliche HTTPS-Prüfung und `nginx -s reload`)
  - `Dockerfile`

**Interfaces:**
- Produces:
  - Settings:
    - `trusted_proxies: tuple[str, ...]`: jeder Eintrag per `ipaddress.ip_network(x, strict=False)` geprüft, sonst `ValueError("MONI_TRUSTED_PROXIES enthält eine ungültige Adresse: <eintrag>")`.
    - `manager_hostnames: tuple[str, ...]`: Standard `(<hostname>, "localhost", "127.0.0.1")`, jeder Eintrag als IP oder DNS-Name geprüft.
  - `nginx.render(settings) -> str`:
    - Liefert die vollständige `server`-Konfiguration.
    - Übernimmt aus dem heutigen Dockerfile die Sicherheits-Header, die temporären Pfade, `log_format moni`, `location /` und `location /api`.
    - Bei `tls=on`:
      - `listen 443 ssl;` mit `ssl_certificate /data/tls/https.crt`, `ssl_certificate_key /data/tls/https.key`, `ssl_protocols TLSv1.2 TLSv1.3`
      - ein zweiter `server` auf `listen 80;` mit `return 301 https://$host$request_uri;`
    - Bei `tls=off`: `listen 80;` und für jeden Eintrag in `trusted_proxies` `set_real_ip_from <cidr>;`, dazu `real_ip_header X-Forwarded-For; real_ip_recursive on;`.
    - Immer: `location /api/v1/auth/oidc/callback/ { error_log /dev/stderr crit; proxy_pass …; }` (offener Punkt aus Paket 3).
  - `ManagerPki.ensure_https(hostnames, now) -> bool` (`True` = neu geschrieben): 397 Tage, Erneuerung ab 30 Tagen oder wenn sich die Hostnamen geändert haben.
  - CLI `tls init` schreibt `/tmp/moni-nginx/moni.conf` (Verzeichnis anlegen) aus `nginx.render` und ruft `ensure`/`ensure_https` auf.
  - Dockerfile:
    - Die statische Datei `sites-available/default` entfällt. Die Haupt-`nginx.conf` bindet `include /tmp/moni-nginx/*.conf;` statt `sites-enabled` ein.
    - `CMD` wird zu `["sh", "-c", "python -m moni.cli tls init && exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf"]`.
    - `EXPOSE 80 443`.
    - Healthcheck: `curl -fsSk https://127.0.0.1/api/v1/health || curl -fsS http://127.0.0.1/api/v1/health`.
  - Poller-Wartung: einmal pro 24 h `ensure_https`. Bei `True` folgt `subprocess.run(["nginx", "-s", "reload"], timeout=10, check=False)`; ein Fehler wird geloggt.
  - Agent mit `tls=off` und gesetzten `trusted_proxies`: `server_args` liefert `proxy_headers=True` und `forwarded_allow_ips=",".join(trusted_proxies)`. Prüfen, ob uvicorn 0.48 CIDR-Einträge annimmt; sonst eine Fehlermeldung, die auf einzelne IPs verweist.
  - `deps`: Nur ein `Authorization`-Header mit Schema `Bearer` (Groß- und Kleinschreibung egal) schaltet auf Token-Anmeldung. Jedes andere Schema wird ignoriert, dann gilt das Cookie. `Bearer` ohne Wert ergibt weiterhin 401.

- [ ] **Step 1: Failing tests**

```python
def test_render_tls_on_has_443_redirect_and_no_realip(): ...
def test_render_tls_off_with_trusted_proxies_sets_realip(): ...
def test_render_oidc_callback_error_log_crit(): ...
def test_render_keeps_security_headers_and_log_format(): ...     # Strings aus dem bisherigen Dockerfile 1:1 vorhanden
@pytest.mark.parametrize("value", ["10.0.0.0/8", "172.18.0.1", "fd00::/8", "10.0.0.0/8, 192.168.1.10"])
def test_trusted_proxies_valid(value): ...
@pytest.mark.parametrize("value", ["kein-ip", "10.0.0.0/33", "*"])
def test_trusted_proxies_invalid(value): ...
def test_https_cert_sans_and_renewal(...): ...
def test_basic_auth_header_falls_back_to_cookie(mgr_client, ...): ... # Authorization: Basic … + gültiges Cookie → 200
def test_agent_forwarded_ips_only_from_trusted(...): ...              # server_args bei tls=off + trusted
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün. Danach `docker compose -f compose.manager.yaml up -d --build` lokal mit `MONI_TLS=on`: `curl -k https://127.0.0.1:8443/api/v1/health` muss `ok` liefern, `curl -I http://127.0.0.1:8080/` muss 301 liefern.
- [ ] **Step 5: Commit** `feat(manager): HTTPS standardmäßig, Proxy-Modus mit vertrauenswürdigen Proxys`

---

### Task 7: Compose, CI-E2E (Kopplung per mTLS), Doku-Variablen

**Files:**
- Modify: `compose.agent.yaml`, `compose.manager.yaml`, `.env.example`, `.github/workflows/ci.yml`, `scripts/ci/manager_auth_smoke.py` (Basis-URL https mit `--insecure`-Option), `docs/superpowers/plans/2026-11-04-moni-2-uebersicht.md` (nur Link, kein Abhaken)
- Create: `scripts/ci/pair_smoke.py`

**Interfaces:**
- Compose:
  - Agent: `MONI_TLS=${MONI_TLS:-on}`, `MONI_PUBLIC_URL=${MONI_PUBLIC_URL:-}`, `MONI_TRUSTED_PROXIES=${MONI_TRUSTED_PROXIES:-}`. Port bleibt `${MONI_AGENT_BIND:-127.0.0.1}:9810:9810` (D6). Healthcheck im Dockerfile für `on` per https ohne Zertifikatsprüfung (nur Lebenszeichen).
  - Manager: `MONI_TLS=${MONI_TLS:-on}`, `MONI_TRUSTED_PROXIES`, `MONI_MANAGER_HOSTNAMES`. Ports `${MONI_MANAGER_BIND:-127.0.0.1}:8443:443` und `${MONI_MANAGER_BIND:-127.0.0.1}:8080:80`.
- `.env.example` dokumentiert jede neue Variable mit einem Satz, dazu D6 und D8 (wann `MONI_AGENT_BIND=0.0.0.0`, wann `MONI_TLS=off` und `MONI_TRUSTED_PROXIES`).
- `scripts/ci/pair_smoke.py MANAGER_URL CODE AGENT_URL` (nur Standardbibliothek, Manager per https ohne Zertifikatsprüfung). Ablauf:
  1. Admin anlegen und anmelden, wie `manager_auth_smoke` es tut. Die gemeinsamen Funktionen kommen in ein kleines Modul `scripts/ci/_moni.py`, keine Kopie.
  2. `POST /api/v1/servers` mit `pairing_code` und `url`.
  3. Bis zu 60 s warten, bis der Server `status == "ok"` und `security == "mtls"` hat.
  4. `GET /servers/{id}/history?metric=moni_host_memory_usage_percent` muss mindestens 2 Punkte liefern.
  5. Ausgabe `OK Kopplung`.
- CI:
  - Agent-Smoke auf TLS umstellen:
    - `curl -fsSk https://127.0.0.1:9810/api/v1/health`
    - Snapshot mit Token über `curl -k`
    - `paired` ist `false`
    - Der Kopplungscode steht im Log; das ist erlaubt und wird geprüft.
  - Manager-Smoke auf `https://127.0.0.1:8443` mit `-k` umstellen; `http://127.0.0.1:8080/` liefert 301.
  - Neuer Schritt „Kopplung (mTLS)“:
    - `docker network connect <agent-netz> <manager-container>`, damit der Manager den Agent unter `https://moni-agent:9810` erreicht.
    - Code per `grep -oE 'moni-pair-v1:[A-Za-z0-9_:-]+' agent.log | head -n1` aus der Logdatei holen (R7: erst in eine Datei schreiben).
    - `pair_smoke.py` aufrufen.
    - Danach im Agent-Log prüfen, dass `Agent gekoppelt` erscheint und das Kopplungs-Token **nach** der Kopplung nicht erneut geloggt wurde.
    - Ein Bearer-Token für Fremdsysteme funktioniert weiter: Snapshot per `curl --cacert ca.crt --resolve moni-agent:9810:127.0.0.1` mit `ca.crt` aus `GET /api/v1/ca.crt`.

- [ ] **Step 1:** Skripte und CI anpassen. Lokal ausführen: beide Compose-Stacks, Netz verbinden, `pair_smoke.py`. Expected: `OK Kopplung`.
- [ ] **Step 2:** Volle Suite grün.
- [ ] **Step 3: Commit** `ci: Kopplung per mTLS Ende-zu-Ende, HTTPS-Smoke für Manager und Agent`

---

### Task 8 (Betreiber, optional): Umstellung beim Autor

Nicht durch Subagents.

1. **Pi (Manager):** Er läuft hinter Traefik mit Domain. In `.env` `MONI_TLS=off` und `MONI_TRUSTED_PROXIES=<IP bzw. Netz des Traefik-Containers>` setzen. Traefik leitet auf Port 8080 des Managers weiter. Prüfen: Fehlversuche beim Login werden pro echter Client-IP gezählt (Audit-Log zeigt verschiedene IPs).
2. **SAU-INDEX (Agent):** bleibt im Token-Modus hinter dem Cloudflare Tunnel (`MONI_TLS=off`, Spec §6.3). Server-Eintrag wie in Paket 2, Task 10. In der Serverliste steht `security: "token"`.
3. **Optional mTLS testen:** einen Agent im selben Netz wie den Pi mit `MONI_TLS=on` und `MONI_AGENT_BIND=0.0.0.0` starten, den Kopplungscode aus `docker compose logs moni-agent` holen und den Agent per `POST /api/v1/servers` mit `pairing_code` koppeln.

---

## Nachlauf aus dem Abschlussreview (2026-11-05)

Paket 4 ist umgesetzt (Commits `92fd657..c09b8f5`, 853 Tests grün). Die CI-Schritte „HTTPS-Smoke“ und „Kopplung (mTLS)“ liefen lokal; auf GitHub Actions laufen sie mit diesem Push zum ersten Mal.

Diese Punkte hat der Abschlussreview bewusst auf spätere Pakete verschoben:

- **Paket 6 (Oberfläche):**
  - Für Fehler nach erfolgter Kopplung einen Hinweis auf `pair reset` zeigen. Das betrifft drei Fälle: Die Antwort ging verloren, die Datenbank war gesperrt, oder ein Agent wurde erneut hinzugefügt.
  - Für unbekannte Agent-Token-IDs 404 statt 502 zurückgeben.
  - Der 401-Handler der Oberfläche darf `"Kopplungs-Token ungültig"` nicht als „Sitzung abgelaufen“ deuten.
  - `GET agent-tokens` in den Test aufnehmen, der prüft, was Betrachter lesen dürfen.
- **Paket 7 (Release, Doku, Härtung):**
  - Agent-`install()` (Absturzfenster zwischen `key.pem` und `cert.pem`): `cert.pem` zuerst schreiben, `pending_key.pem` bis zum Schluss behalten und die Wiederherstellung in `ensure()` einbauen, dazu ein deutscher Hinweis auf `pair reset` statt eines SSL-Tracebacks.
  - Die Kopplung braucht eine Gesamtfrist.
  - `_OPENER` ist toter Code.
  - Warnung ausgeben, wenn `MONI_TRUSTED_PROXIES` bei `MONI_TLS=on` gesetzt ist; dann wird es ignoriert.
  - Doku:
    - CA-Rotation.
    - Die CA nur gezielt einsetzen (`--cacert` statt System-Vertrauensspeicher).
    - Ein Agent, dessen Zertifikat ganz abgelaufen ist, muss neu gekoppelt werden.
    - Upgrade-Hinweis: Der Agent hat jetzt standardmäßig TLS an.
    - Die nginx-Zertifikatspfade sind fest auf `/data/tls` gesetzt.
  - Kleinere Punkte:
    - `san_for_host` strenger machen.
    - `sans=[]` abweisen.
    - Ein einzeln übergebenes Zertifikat oder ein einzelner Schlüssel in `client_context` soll einen Fehler auslösen.
    - Den eingelesenen CA-Schlüssel im Agent zwischenspeichern.
    - Die doppelte `NewToken`-Validierung zusammenführen.
    - `throttle_key` nach `throttle.py` verschieben.
    - Die Erneuerung soll `_stop` beachten.
  - CI und Testlücken:
    - Agent-Neustart vor der Prüfung, dass der Code nicht erneut geloggt wird.
    - Getrennte Fristen in `pair_smoke`.
    - `fchmod` in `write_secret_file`.
    - Die Ziffern-Regex soll auch URLs erfassen.
  - Die `StarletteDeprecationWarning` aus der Testausgabe entfernen.
