# Moni 2.0 – Paket 3: Benutzer & Anmeldung – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Manager bekommt eigene Benutzerkonten. Dazu gehören Ersteinrichtung per Setup-Token, Passwort (Argon2id) mit verpflichtender TOTP-2FA und Wiederherstellungscodes, serverseitige Sessions mit CSRF-Schutz, Sperre nach Fehlversuchen, die Rollen Admin und Betrachter, OIDC-Anmeldung, ein Audit-Log und eine API zur Verwaltung von Benutzern und Tokens. Alles funktioniert vollständig über die API und die CLI, ohne Oberfläche.

**Architecture:** Neue Module unter `backend/moni/auth/`. Die Speicherklassen (`users.py`, `sessions.py`, `audit.py`) arbeiten auf der Manager-SQLite (Migration `0002_auth.sql`) und folgen dem Transaktionsmuster von `ServerStore`: Ein Commit erfolgt nur, wenn die Methode die Transaktion selbst geöffnet hat (P2-R1). `deps.require_scope` wird zum gemeinsamen Einstieg. Er erkennt einen Bearer-Token (wie bisher) oder ein Session-Cookie und liefert einen `Principal`. Die Rollen werden so abgebildet: Admin → `admin`, Betrachter → `read`. Die Router `auth/api.py` (Anmeldung, eigenes Konto), `auth/admin_api.py` (Benutzer, Tokens, Audit) und `auth/oidc.py` werden nur in der Rolle `manager` eingebunden. Der Agent bleibt unverändert und kennt weiterhin nur Bearer-Tokens.

**Tech Stack:** Python 3.14, FastAPI, sqlite3, `cryptography` (Argon2id mit PHC-Format, TOTP, Fernet über `SecretBox`), neu `PyJWT==2.15.1` (Prüfung des OIDC-ID-Tokens), urllib für die OIDC-Aufrufe.

**Spec:** [`docs/superpowers/specs/2026-11-04-moni-agent-manager-design.md`](../specs/2026-11-04-moni-agent-manager-design.md) (§6.7, §6.10 Master-Key/Admin-Reset, §4 Anti-Ziele) · Übersicht: [`2026-11-04-moni-2-uebersicht.md`](2026-11-04-moni-2-uebersicht.md) · Vorgänger: [Paket 2](2026-11-04-moni-2-paket-2-manager-kern.md)

## Ist-Stand nach Paket 2 (Schnittstellen, auf die dieser Plan baut)

- `moni.config.Settings` (frozen dataclass, `from_env`), Hilfsfunktionen `_int`/`_float` für benannte Env-Fehler, Property `db_path`.
- `moni.manager.db.connect(path)` / `migrate(path)`: Migrationen `NNNN_*.sql` in `moni/manager/migrations/`, Sicherung vor jedem Upgrade, 0600-Dateirechte. Tabelle `meta(key, value)` ist vorhanden.
- `moni.manager.servers.ServerStore(conn, box)` mit `_tx(immediate)` (Commit nur bei eigener Transaktion) und `reencrypt_all()`.
- `moni.manager.api`: `_store(request)` öffnet pro Anfrage eine Verbindung und macht aus „locked/busy“ ein 503 mit `Retry-After: 5`. Die Routen `/api/v1/servers…` nutzen `dependencies=[read]` bzw. `[admin]`.
- `moni.auth.deps.require_scope(scope)` prüft nur Bearer-Tokens gegen `app.state.tokens` (`TokenStore`, `tokens.json`); `admin` schließt `read` ein (inline).
- `moni.auth.tokens.TokenStore(path)`: `create(name, scope, expires_at)`, `verify`, `list`, `revoke`; `SCOPES = {"read", "admin"}`.
- `moni.manager.poller.Poller._maintain(now)` führt alle 300 s `tsdb.rollup`/`tsdb.prune` in einem eigenen SAVEPOINT aus.
- `moni.app.create_app(settings, collector, poller)`: Manager-Verdrahtung (migrate → SecretBox → ensure_local → Poller), `app.state.settings/tokens/box/poller`.
- `moni.cli`: Gruppen `token`, `secret` (`generate`, `reencrypt`), `server`. `_manager(args)` öffnet DB und SecretBox.
- Logs sind JSON-Zeilen (`moni.logs.JsonFormatter`), Fehler RFC 9457 (`moni.errors.problem`).
- Manager-Image: nginx (Port 80) → uvicorn `127.0.0.1:8001`; `X-Forwarded-For` wird nur von 127.0.0.1 vertraut (P2-R7).

## Entscheidungen dieses Plans (vom Planer gesetzt)

Diese Entscheidungen trifft der Plan. Im Ledger werden sie als Rulings P3-D1 bis P3-D11 geführt. Ein Implementer weicht nicht davon ab.

- **D1 Argon2id aus `cryptography`** (`Argon2id.derive_phc_encoded`/`verify_phc_encoded`), keine eigene Argon2-Bibliothek. Parameter nach OWASP: `memory_cost=19456` KiB, `iterations=2`, `lanes=1`, Salt 16 Byte, Länge 32. Das reicht auch für den Pi.
- **D2 TOTP aus `cryptography`** (`twofactor.totp.TOTP`) mit SHA-1, 6 Stellen und 30 s, damit es zu allen gängigen Authenticator-Apps passt. Toleranz ±1 Zeitschritt, Schutz gegen Wiederverwendung über `totp_last_step`. Der QR-Code entsteht erst in der Oberfläche (Paket 6) aus der `otpauth://`-URI; serverseitig gibt es keine QR-Bibliothek.
- **D3 OIDC mit `PyJWT==2.15.1`** für die Signaturprüfung. JWKS und Discovery holt Moni selbst über eine einzige HTTP-Hilfsfunktion (`oidc._http`): nur HTTPS, keine Weiterleitungen, kein Proxy aus der Umgebung (wie P2-R6), Timeout 10 s, höchstens 1 MB.
- **D4 Sessions in SQLite** (Tabelle `sessions`, nur der SHA-256-Hash der Session-ID wird gespeichert). Das Cookie `moni_session` hat immer `HttpOnly; Secure; SameSite=Strict; Path=/`, auch bei `MONI_TLS=off`. Bis Paket 4 HTTPS bringt, funktioniert die Anmeldung im Browser deshalb nur über HTTPS (Proxy) oder über `localhost`. API-Clients sind nicht betroffen. Leerlauf **60 min** (`MONI_SESSION_IDLE_MINUTES`), absolute Laufzeit **12 h** (`MONI_SESSION_MAX_HOURS`). Die Zwischenstufen `mfa` und `enroll` gelten höchstens **600 s**. `last_seen` wird höchstens alle 60 s geschrieben, um die SD-Karte zu schonen.
- **D5 CSRF per Synchronizer-Token.** Cookie-authentisierte Anfragen mit `POST/PUT/PATCH/DELETE` brauchen den Header `X-CSRF-Token`, der zum CSRF-Wert der Session passt. Bearer-Anfragen sind ausgenommen, weil der Browser keinen Bearer-Header automatisch mitsendet. Login und Setup haben noch keine Session und werden über `SameSite=Strict`, das Setup-Token bzw. das Passwort und die IP-Drosselung geschützt.
- **D6 API-Tokens bleiben in `tokens.json`** (`TokenStore`, keine DB-Migration). Neu ist nur die Verwaltung über die API. Das erfüllt §6.7 „wie beim Agent“ ohne zweiten Speicherort.
- **D7 OIDC-Konfiguration in `meta`** unter dem Schlüssel `oidc` als JSON. Das Client-Secret liegt mit `SecretBox` verschlüsselt (§6.10). Konten werden nie automatisch angelegt. Bei der ersten OIDC-Anmeldung wird ein vorher angelegtes Konto mit `kind="oidc"` über die E-Mail verknüpft. Das gelingt nur, wenn das ID-Token `email_verified: true` enthält. Danach zählt nur noch `(iss, sub)`. Kosten, falls falsch: Anbieter ohne `email_verified` (z. B. Microsoft Entra) funktionieren erst mit einer späteren Option.
- **D8 Sperren.** Ein Konto wird nach **5** Fehlversuchen in Folge (Passwort oder zweiter Faktor) für **15 min** gesperrt (`locked_until`, in der DB). Pro IP gibt es nach **20** Fehlversuchen in **15 min** ein 429 mit `Retry-After` (im Speicher). Alle Anmeldefehler bekommen dieselbe Antwort `401 "Anmeldung fehlgeschlagen"`, auch bei unbekannter E-Mail oder gesperrtem Konto. Bei unbekannter E-Mail läuft eine Argon2-Prüfung gegen einen Dummy-Hash, damit die Antwortzeit nichts verrät.
- **D9 Setup-Token im Log** (so verlangt es §6.7). Es wird bei jedem Start neu erzeugt, solange kein Benutzer existiert. Es gilt nur, bis der erste Admin angelegt ist. Gespeichert ist nur sein Hash in `app.state`. Das ist die einzige erlaubte Ausnahme von „keine Geheimnisse im Log“.
- **D10 Audit-Aufbewahrung 365 Tage**, aufgeräumt in der Poller-Wartung. Abgelaufene Sessions werden dort ebenfalls gelöscht.
- **D11 Passwortregeln nach NIST SP 800-63B:** 12 bis 256 Zeichen, nicht gleich der E-Mail, keine Zeichenklassen-Pflicht.
- **Bewusst akzeptiert (offener Punkt aus P2 T8):** Ein POST mit **kaputtem JSON** ohne Anmeldung erhält 422 statt 401, weil FastAPI den Body vor den Dependencies parst. Dabei wird nichts preisgegeben. Gültiges JSON ohne Anmeldung ergibt weiterhin 401.

## Global Constraints

- Branch `feature/moni-2`, Draft-PR #41 (nicht mergen). Commits mit Autor-E-Mail `261156405+anonym-rgath@users.noreply.github.com` (repo-lokal gesetzt). Commit-Trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Python **3.14**. Tests: `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests -v`
- Neue Laufzeit-Abhängigkeit **nur** `PyJWT==2.15.1` (in `backend/requirements-docker.txt`). Sonst Standardbibliothek und das vorhandene `cryptography`.
- Der Agent bleibt unverändert: keine Benutzer, keine Sessions, keine neuen Routen. Alle bestehenden Tests bleiben grün, `compose.agent.yaml` funktioniert weiter.
- Geheimnisse stehen nie in Logs, API-Antworten (außer einmalig bei der Erzeugung), `repr()`, Audit-Details oder Fehlermeldungen. Das gilt für Passwörter, Passwort-Hashes, TOTP-Secrets, Wiederherstellungscodes, Session-IDs, CSRF-Werte, API-Tokens, OIDC-Client-Secret, `code`, `id_token` und `access_token`. In der DB liegen Passwörter nur als Argon2id-PHC, Wiederherstellungscodes und Session-IDs nur als SHA-256, TOTP-Secrets und das OIDC-Client-Secret nur über `SecretBox`. Einzige Ausnahme ist das Setup-Token im Log (D9).
- Rollen der Benutzer: `admin` und `viewer` (in Texten „Admin“ und „Betrachter“). Ein Betrachter darf nur lesen und Benutzer, Tokens und Audit-Log weder sehen noch ändern. Es muss immer mindestens **ein aktiver Admin** existieren (Rolle `admin`, nicht deaktiviert).
- Zeitstempel: in der API UTC ISO 8601 mit `Z` (`timeutil.iso`), in SQLite ganze Unix-Sekunden.
- Fehler als RFC 9457 (`errors.problem` bzw. `HTTPException`) mit festen deutschen Texten:
  - Anmeldung: `401 "Anmeldung fehlgeschlagen"`
  - ohne gültige Anmeldung: `401 "Nicht angemeldet"`
  - CSRF: `403 "CSRF-Token fehlt oder ist ungültig"`
  - Rolle: `403 "Keine Berechtigung"`
  - letzter Admin: `409 "Der letzte aktive Admin kann nicht entfernt, herabgestuft oder deaktiviert werden"`
  - doppelte E-Mail: `409 "E-Mail-Adresse ist bereits vergeben"`
  - IP-Drosselung: `429 "Zu viele Fehlversuche, bitte später erneut versuchen"`
- Datenbankzugriff pro Anfrage über eine eigene Verbindung im selben Thread (Muster `_store` aus Paket 2). „locked/busy“ ergibt 503.
- Tests, die Cookies brauchen, nutzen `TestClient(app, base_url="https://testserver")`, weil `Secure`-Cookies sonst nicht mitgeschickt werden.
- Texte für Menschen auf Deutsch mit korrekten Umlauten.

## Review Focus

1. **Brute Force und Kontoerkennung.** Unbekannte E-Mail, falsches Passwort, falscher Code und gesperrtes Konto liefern dieselbe Antwort. Nach 5 Fehlversuchen ist das Konto 15 min gesperrt, und selbst das richtige Passwort hilft dann nicht. Nach 20 Fehlversuchen von einer IP folgt ein 429. Ein TOTP-Code lässt sich nicht zweimal verwenden, ein Wiederherstellungscode nur einmal. Tests in Task 2 und Task 5.
2. **Halbe Anmeldung.** Eine Session in der Stufe `mfa` oder `enroll` hat keinen Zugriff auf Daten- oder Admin-Routen (401). Nach dem zweiten Faktor wechselt die Session-ID, die alte ist danach ungültig. Logout, Passwortwechsel, 2FA-Reset und Deaktivierung beenden die betroffenen Sessions sofort. Tests in Task 3, Task 4 und Task 5.
3. **Letzter Admin.** Weder über die API noch über die CLI lässt sich der letzte aktive Admin löschen, herabstufen oder deaktivieren, auch nicht durch sich selbst. Zwei gleichzeitige Ersteinrichtungen ergeben genau einen Admin. Tests in Task 2, Task 5 und Task 6.
4. **CSRF und Rollen.** Eine Cookie-Anfrage, die etwas ändert, wird ohne oder mit falschem `X-CSRF-Token` mit 403 abgelehnt. Bearer-Anfragen funktionieren ohne CSRF-Header. Ein Betrachter bekommt bei `/users`, `/tokens`, `/audit` und `POST /servers` ein 403, darf aber `/servers` lesen. Tests in Task 4 und Task 6.
5. **Manipulierte OIDC-Antworten.** Falsches oder fehlendes `state` bzw. Cookie, falsche `nonce`, falsches `aud` oder `iss`, abgelaufenes Token, `alg=none`/`HS256`, unbekannte `kid`, `email_verified=false`, unbekannte E-Mail, lokales statt OIDC-Konto und deaktiviertes Konto: Jeder dieser Fälle führt zu keiner Session und einem Audit-Eintrag `oidc.failure`, nie zu einem 500. Test in Task 7.

---

### Task 1: Krypto-Bausteine (Passwörter, TOTP, Wiederherstellungscodes)

**Files:**
- Create: `backend/moni/auth/passwords.py`, `backend/moni/auth/totp.py`
- Create: `backend/tests/test_passwords.py`, `backend/tests/test_totp.py`
- Modify: `backend/tests/conftest.py` (Hilfsfunktion `code_at(secret, t)` für alle Auth-Tests)

**Interfaces:**
- Produces (`passwords.py`):
  - `hash_password(password: str) -> str`: Argon2id-PHC-String mit den Parametern aus D1.
  - `verify_password(password: str, phc: str) -> bool`: liefert nie eine Exception, auch nicht bei kaputtem oder fremdem `phc`.
  - `dummy_verify(password: str) -> None`: prüft gegen einen einmalig erzeugten Dummy-Hash (lazy, threadsicher), damit unbekannte Konten gleich lange brauchen.
  - `check_policy(password: str, email: str) -> None`: wirft `ValueError` mit den Texten `"Passwort muss mindestens 12 Zeichen lang sein"`, `"Passwort darf höchstens 256 Zeichen lang sein"` und `"Passwort darf nicht der E-Mail-Adresse entsprechen"` (Vergleich ohne Groß- und Kleinschreibung).
- Produces (`totp.py`):
  - `new_secret() -> str`: 20 Zufallsbytes, Base32 ohne `=`.
  - `provisioning_uri(secret: str, email: str) -> str`: `otpauth://totp/Moni:<email>?secret=…&issuer=Moni`.
  - `match_step(secret: str, code: str, now: float, last_step: int | None) -> int | None`: Liefert den passenden Zeitschritt (`now // 30` ±1) oder `None`. Akzeptiert nur genau 6 Ziffern (Leerzeichen werden entfernt). Schritte `<= last_step` werden abgelehnt. Der Vergleich läuft mit `hmac.compare_digest`.
  - `new_recovery_codes() -> list[str]`: 10 Codes im Format `xxxxx-xxxxx` (Kleinbuchstaben a–z und Ziffern 2–7, je ≥ 50 Bit).
  - `normalize_recovery(code: str) -> str`: Kleinschreibung, ohne `-` und Leerzeichen.
  - `hash_code(code: str) -> str`: SHA-256 hex über den normalisierten Code.

- [ ] **Step 1: Failing tests schreiben**

```python
# test_passwords.py
from moni.auth.passwords import check_policy, dummy_verify, hash_password, verify_password

def test_hash_is_argon2id_phc_and_verifies():
    h = hash_password("korrekt-pferd-batterie")
    assert h.startswith("$argon2id$") and "m=19456" in h and "t=2" in h and "p=1" in h
    assert verify_password("korrekt-pferd-batterie", h) and not verify_password("falsch-falsch-falsch", h)

def test_same_password_different_salt():
    assert hash_password("x" * 12) != hash_password("x" * 12)

@pytest.mark.parametrize("phc", ["", "kaputt", "$argon2id$v=19$m=1$", "$2b$12$abcdefghijklmnopqrstuv"])
def test_verify_never_raises(phc):
    assert verify_password("irgendwas-langes", phc) is False

@pytest.mark.parametrize("pw,msg", [("kurz", "mindestens 12"), ("a" * 257, "höchstens 256"), ("Admin@Example.org", "E-Mail")])
def test_policy(pw, msg):
    with pytest.raises(ValueError, match=msg):
        check_policy(pw, "admin@example.org")

def test_dummy_verify_runs():
    dummy_verify("egal")  # darf nichts werfen

# conftest.py (code_at) und test_totp.py
from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.twofactor.totp import TOTP
from moni.auth import totp

def code_at(secret, t):
    import base64
    return TOTP(base64.b32decode(secret + "=" * (-len(secret) % 8)), 6, SHA1(), 30, enforce_key_length=False).generate(t).decode()

def test_match_window_and_replay():
    s, t = totp.new_secret(), 1_800_000_000
    step = totp.match_step(s, code_at(s, t - 30), t, None)
    assert step == t // 30 - 1
    assert totp.match_step(s, code_at(s, t - 30), t, step) is None          # Wiederverwendung
    assert totp.match_step(s, code_at(s, t - 90), t, None) is None          # außerhalb ±1
    assert totp.match_step(s, " " + code_at(s, t)[:3] + " " + code_at(s, t)[3:], t, None) == t // 30

@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "١٢٣٤٥٦"])
def test_rejects_non_six_ascii_digits(bad):
    assert totp.match_step(totp.new_secret(), bad, 1_800_000_000, None) is None

def test_uri():
    u = totp.provisioning_uri("ABC", "a@b.de")
    assert u.startswith("otpauth://totp/Moni:a%40b.de?") and "secret=ABC" in u and "issuer=Moni" in u

def test_recovery_codes():
    codes = totp.new_recovery_codes()
    assert len(codes) == len(set(codes)) == 10
    assert all(re.fullmatch(r"[a-z2-7]{5}-[a-z2-7]{5}", c) for c in codes)
    assert totp.hash_code(codes[0].upper().replace("-", " ")) == totp.hash_code(codes[0])
```

- [ ] **Step 2: Tests laufen lassen**: Expected FAIL (Module fehlen).
- [ ] **Step 3: Implementieren.** Für `verify_password` gilt: `Argon2id.verify_phc_encoded` in `try` aufrufen und `InvalidKey`, `ValueError` und `TypeError` auf `False` abbilden. Zuerst `phc.startswith("$argon2id$")` prüfen. Die Ziffernprüfung verwendet `re.fullmatch(r"[0-9]{6}", …)` und nicht `str.isdigit`, das auch arabische Ziffern zulässt.
- [ ] **Step 4: Tests**: Expected PASS.
- [ ] **Step 5: Commit** `feat(auth): Argon2id-Passwörter, TOTP und Wiederherstellungscodes`

---

### Task 2: Schema `0002_auth.sql` und `UserStore`

**Files:**
- Create: `backend/moni/manager/migrations/0002_auth.sql`, `backend/moni/auth/users.py`, `backend/tests/test_users.py`
- Modify: `backend/moni/cli.py` (`secret reencrypt` verschlüsselt auch die TOTP-Secrets), `backend/tests/test_cli.py`, `backend/tests/test_db.py` (Version 2)

**Interfaces:**
- Consumes: `SecretBox`, `db.connect/migrate`, `passwords.*`, `totp.*` (Task 1).
- Produces (Schema):

```sql
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,            -- kleingeschrieben, getrimmt
  name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL CHECK (role IN ('admin', 'viewer')),
  kind TEXT NOT NULL CHECK (kind IN ('local', 'oidc')),
  password_hash TEXT,                    -- Argon2id-PHC, nur bei local
  totp_enc TEXT, totp_pending_enc TEXT,  -- SecretBox
  totp_last_step INTEGER,
  oidc_iss TEXT, oidc_sub TEXT,
  disabled INTEGER NOT NULL DEFAULT 0,
  failed_logins INTEGER NOT NULL DEFAULT 0,
  locked_until INTEGER,
  created_at INTEGER NOT NULL,
  last_login INTEGER,
  UNIQUE (oidc_iss, oidc_sub)
);
CREATE TABLE recovery_codes (user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  code_hash TEXT NOT NULL, used_at INTEGER, PRIMARY KEY (user_id, code_hash)) WITHOUT ROWID;
CREATE TABLE sessions (id_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  stage TEXT NOT NULL CHECK (stage IN ('mfa', 'enroll', 'full')), csrf TEXT NOT NULL,
  created_at INTEGER NOT NULL, last_seen INTEGER NOT NULL, ip TEXT) WITHOUT ROWID;
CREATE INDEX sessions_user ON sessions(user_id);
CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  target TEXT, ip TEXT, details TEXT);
CREATE INDEX audit_ts ON audit_log(ts);
```

- Produces (`users.py`):
  - `@dataclass(frozen=True) class User`: `id, email, name, role, kind, totp_enabled: bool, disabled: bool, failed_logins: int, locked_until: int | None, created_at: int, last_login: int | None, oidc_bound: bool`. Ohne Hash und ohne Secret.
  - `class Conflict(Exception)`: für doppelte E-Mails und den Schutz des letzten Admins. Die Meldung ist der Text aus den Global Constraints.
  - `LOCK_AFTER = 5`, `LOCK_SECONDS = 900`
  - `normalize_email(email: str) -> str`: `strip().lower()`. Bei ungültiger Adresse (mehr als 254 Zeichen, nicht `^[^@\s]+@[^@\s]+\.[^@\s]+$`, Steuerzeichen) wirft sie `ValueError("Ungültige E-Mail-Adresse")`.
  - `UserStore(conn, box)`. Die Methoden nutzen `_tx(immediate)` wie `ServerStore` (P2-R1). Die Prüfung auf den letzten Admin läuft in einer `BEGIN IMMEDIATE`-Transaktion.
    - `create(email, name, role, kind, password=None, now=None) -> User`. Bei `local` ist ein Passwort Pflicht und `check_policy` gilt. Bei `oidc` ist ein Passwort verboten (`ValueError("OIDC-Konten haben kein Passwort")`). Eine doppelte E-Mail ergibt `Conflict`. Der Name hat höchstens 100 Zeichen und keine Steuerzeichen.
    - `count() -> int`, `get(user_id) -> User | None`, `by_email(email) -> User | None`, `list() -> list[User]`
    - `update(user_id, *, name=None, role=None, disabled=None) -> User | None`: `Conflict`, wenn danach kein aktiver Admin übrig wäre.
    - `delete(user_id) -> bool`: dieselbe Prüfung.
    - `set_password(user_id, password) -> None`: Policy wird geprüft, nur bei `local`.
    - `check_password(user_id, password) -> bool`
    - `is_locked(user: User, now: int) -> bool`
    - `register_failure(user_id, now) -> bool`: zählt hoch. Beim 5. Fehlversuch setzt sie `locked_until = now + 900`, setzt `failed_logins` auf 0 zurück und liefert `True` (gerade gesperrt).
    - `register_success(user_id, now) -> None`: setzt `failed_logins` auf 0, `locked_until` auf NULL und `last_login` auf `now`.
    - `unlock(user_id) -> bool`
    - `begin_totp(user_id) -> tuple[str, str]`: `(secret, uri)`, legt `totp_pending_enc` ab. Ein aktives TOTP bleibt bis zur Bestätigung gültig.
    - `confirm_totp(user_id, code, now) -> list[str] | None`: Passt der Code zum Pending-Secret, wird das Pending-Secret aktiv, `totp_last_step` gesetzt, die alten Wiederherstellungscodes werden ersetzt und die 10 neuen im Klartext zurückgegeben (nur dieses eine Mal).
    - `verify_second_factor(user_id, code, now) -> str | None`: liefert `"totp"` oder `"recovery"` (der Code wird dann als benutzt markiert) oder `None`. Ein Code mit 6 Ziffern zählt als TOTP, alles andere als Wiederherstellungscode.
    - `regenerate_recovery_codes(user_id) -> list[str]`
    - `reset_totp(user_id) -> None`: löscht `totp_enc`, `totp_pending_enc`, `totp_last_step` und alle Wiederherstellungscodes.
    - `by_oidc(iss, sub) -> User | None`, `bind_oidc(user_id, iss, sub) -> None`
    - `reencrypt_all() -> int`: verschlüsselt `totp_enc` und `totp_pending_enc` mit `box.rotate` neu.
  - Ab hier folgt die CLI-Funktion `secret reencrypt` der Ausgabe `Neu verschlüsselt: <n>`. `n` ist die Summe aus Servern und Benutzern, später kommt OIDC hinzu (Task 7).

- [ ] **Step 1: Failing tests** (Fixture `users`: migrierte DB in `tmp_path`, `UserStore(conn, SecretBox(generate_key()))`)

```python
def test_create_local_and_no_secrets_in_repr(users):
    u = users.create(" Admin@Example.org ", "Ada", "admin", "local", password="ein-langes-passwort")
    assert u.email == "admin@example.org" and u.totp_enabled is False
    assert "argon2" not in repr(u) and users.check_password(u.id, "ein-langes-passwort")

def test_duplicate_email_conflict(users):
    users.create("a@b.de", "", "admin", "local", password="x" * 12)
    with pytest.raises(Conflict, match="bereits vergeben"):
        users.create("A@B.DE", "", "viewer", "local", password="y" * 12)

@pytest.mark.parametrize("email", ["", "kein-at", "a@b", "a b@c.de", "a@b.de\n", "x" * 250 + "@b.de"])
def test_invalid_email(users, email):
    with pytest.raises(ValueError, match="E-Mail"):
        users.create(email, "", "viewer", "local", password="x" * 12)

def test_oidc_has_no_password(users):
    with pytest.raises(ValueError, match="OIDC"):
        users.create("o@b.de", "", "viewer", "oidc", password="x" * 12)

def test_last_admin_protected(users):
    a = users.create("a@b.de", "", "admin", "local", password="x" * 12)
    for op in (lambda: users.delete(a.id), lambda: users.update(a.id, role="viewer"), lambda: users.update(a.id, disabled=True)):
        with pytest.raises(Conflict, match="letzte aktive Admin"):
            op()
    b = users.create("b@b.de", "", "admin", "local", password="x" * 12)
    users.update(b.id, disabled=True)
    with pytest.raises(Conflict):        # b ist deaktiviert, zählt nicht
        users.delete(a.id)
    users.update(b.id, disabled=False)
    assert users.delete(a.id) is True

def test_lockout_after_five(users):
    u = users.create("a@b.de", "", "admin", "local", password="x" * 12)
    assert [users.register_failure(u.id, 1000) for _ in range(5)] == [False] * 4 + [True]
    assert users.is_locked(users.get(u.id), 1000 + 899) and not users.is_locked(users.get(u.id), 1000 + 900)
    users.register_success(u.id, 2000)
    assert users.get(u.id).failed_logins == 0 and users.get(u.id).last_login == 2000

def test_totp_enrolment_and_second_factor(users):
    u = users.create("a@b.de", "", "admin", "local", password="x" * 12)
    secret, uri = users.begin_totp(u.id)
    t = 1_800_000_000
    assert users.confirm_totp(u.id, "000000" if code_at(secret, t) != "000000" else "111111", t) is None
    codes = users.confirm_totp(u.id, code_at(secret, t), t)
    assert len(codes) == 10 and users.get(u.id).totp_enabled
    assert users.verify_second_factor(u.id, code_at(secret, t), t) is None          # derselbe Schritt: Replay
    assert users.verify_second_factor(u.id, code_at(secret, t + 30), t + 30) == "totp"
    assert users.verify_second_factor(u.id, codes[0].upper(), t) == "recovery"
    assert users.verify_second_factor(u.id, codes[0], t) is None                    # nur einmal
    row = users._conn.execute("SELECT totp_enc FROM users").fetchone()[0]
    assert secret not in row

def test_reset_totp_clears_codes(users): ...      # danach totp_enabled False, keine recovery_codes-Zeilen
def test_reencrypt_all_users(tmp_path): ...       # neuer Schlüssel vorn → nach reencrypt nur mit neuem Schlüssel lesbar
def test_migration_v2_on_v1_db_keeps_servers(tmp_path): ...   # v1-DB mit Server → migrate → Server da, users leer, user_version 2
```

- [ ] **Step 2: Tests**: Expected FAIL.
- [ ] **Step 3: Implementieren.** Die Prüfung auf den letzten Admin lautet `SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0 AND id != ?` innerhalb der Transaktion, die auch die Änderung schreibt. `confirm_totp` und `verify_second_factor` setzen `totp_last_step` in derselben Transaktion.
- [ ] **Step 4: Volle Suite**: Expected PASS. Bestehende `test_db`-Erwartungen an `user_version == 1` werden auf die neueste Version umgestellt (`len(MIGRATIONS)`).
- [ ] **Step 5: Commit** `feat(auth): Benutzerschema, UserStore mit Sperre, TOTP und letztem-Admin-Schutz`

---

### Task 3: Sessions, Audit-Log und Settings

**Files:**
- Create: `backend/moni/auth/sessions.py`, `backend/moni/auth/audit.py`, `backend/tests/test_sessions.py`, `backend/tests/test_audit.py`
- Modify: `backend/moni/config.py`, `backend/tests/test_config.py`

**Interfaces:**
- Produces (Settings): `session_idle_minutes: int = 60` (`MONI_SESSION_IDLE_MINUTES`), `session_max_hours: int = 12` (`MONI_SESSION_MAX_HOURS`). Beide müssen ≥ 1 sein (Validierung in der vorhandenen ≥1-Schleife).
- Produces (`sessions.py`):
  - `COOKIE = "moni_session"`, `PRE_AUTH_SECONDS = 600`, `TOUCH_EVERY = 60`
  - `@dataclass(frozen=True) class Session`: `id_hash: str` (`repr=False`), `user_id: int`, `stage: str`, `csrf: str` (`repr=False`), `created_at: int`, `last_seen: int`
  - `SessionStore(conn, settings)` (P2-R1-Transaktionsmuster):
    - `create(user_id, stage, now, ip) -> tuple[Session, str]`: Klartext-ID `secrets.token_urlsafe(32)` (wird nur ins Cookie gesetzt), CSRF `secrets.token_urlsafe(32)`.
    - `get(plain: str, now: int) -> Session | None`: Hash-Lookup. Abgelaufene Sessions werden gelöscht und ergeben `None`. Für `mfa` und `enroll` gilt `now - created_at >= 600`. Für `full` gilt `now - last_seen >= idle*60` oder `now - created_at >= max*3600`. `last_seen` wird nur geschrieben, wenn `now - last_seen >= 60`.
    - `rotate(session: Session, stage: str, now: int, ip) -> tuple[Session, str]`: löscht die alte Session und legt eine neue an (atomar).
    - `delete(id_hash) -> None`, `delete_for_user(user_id, keep: str | None = None) -> int`, `purge_expired(now) -> int`
- Produces (`audit.py`):
  - `RETENTION_DAYS = 365`
  - `record(conn, actor: str, action: str, *, target: str | None = None, ip: str | None = None, details: dict | None = None, now: int | None = None) -> None`: Strings werden auf 256 Zeichen gekürzt, `details` als JSON (`allow_nan=False`) gespeichert. Ohne eigenen Commit, wenn die Verbindung schon in einer Transaktion ist, sonst mit Commit (P2-R1).
  - `entries(conn, limit: int = 100, before: int | None = None) -> list[dict]`: neueste zuerst, Felder `id, ts` (ISO), `actor, action, target, ip, details` (dict oder None).
  - `prune(conn, cutoff_ts: int) -> int`
  - Akteure: `"user:<email>"`, `"token:<id>"`, `"cli"`, `"setup"`, `"anonym"`. Feste Aktionsnamen:
    - Anmeldung: `setup.admin_created`, `login.success`, `login.failure`, `login.locked`, `mfa.failure`, `logout`, `recovery_code.used`
    - Passwort und 2FA: `totp.enabled`, `totp.reset`, `recovery_codes.regenerated`, `password.changed`, `password.reset`
    - Benutzer: `user.created`, `user.updated`, `user.deleted`, `user.unlocked`
    - Tokens: `token.created`, `token.revoked`
    - Server: `server.added`, `server.updated`, `server.removed`
    - OIDC: `oidc.config_changed`, `oidc.login`, `oidc.failure`
    - Schlüssel: `secret.reencrypted`

- [ ] **Step 1: Failing tests**

```python
def test_session_roundtrip_and_hash_only(sessions, user_id):
    s, plain = sessions.create(user_id, "full", 1000, "1.2.3.4")
    assert sessions.get(plain, 1001).user_id == user_id
    assert plain not in repr(s) and s.csrf not in repr(s)
    assert sessions._conn.execute("SELECT COUNT(*) FROM sessions WHERE id_hash=?", (plain,)).fetchone()[0] == 0

def test_idle_and_absolute_expiry(sessions, user_id):      # idle 60 min, max 12 h
    s, p = sessions.create(user_id, "full", 0, None)
    assert sessions.get(p, 3599) and sessions.get(p, 3599 + 3600) is None
    s, p = sessions.create(user_id, "full", 0, None)
    for t in range(0, 12 * 3600, 3000):
        assert sessions.get(p, t)
    assert sessions.get(p, 12 * 3600) is None

def test_pre_auth_stage_expires_after_600s(sessions, user_id):
    _, p = sessions.create(user_id, "mfa", 0, None)
    assert sessions.get(p, 599) and sessions.get(p, 600) is None

def test_touch_throttled(sessions, user_id):
    _, p = sessions.create(user_id, "full", 0, None)
    sessions.get(p, 30); assert _last_seen(sessions) == 0
    sessions.get(p, 61); assert _last_seen(sessions) == 61

def test_rotate_invalidates_old(sessions, user_id):
    s, old = sessions.create(user_id, "mfa", 0, None)
    _, new = sessions.rotate(s, "full", 10, None)
    assert sessions.get(old, 11) is None and sessions.get(new, 11).stage == "full"

def test_delete_for_user_keeps_current(sessions, user_id): ...
def test_user_delete_cascades_sessions(...): ...

def test_audit_record_and_page(conn):
    for i in range(5):
        audit.record(conn, "cli", "user.created", target=f"u{i}", now=i)
    page = audit.entries(conn, limit=2)
    assert [e["target"] for e in page] == ["u4", "u3"]
    assert [e["target"] for e in audit.entries(conn, limit=2, before=page[-1]["id"])] == ["u2", "u1"]
    assert page[0]["ts"].endswith("Z")

def test_audit_truncates_and_prunes(conn): ...     # target 1000 Zeichen → 256; prune(cutoff) löscht ältere

def test_session_settings():                        # Defaults 60/12, "0" → ValueError mit Variablennamen
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün.
- [ ] **Step 5: Commit** `feat(auth): serverseitige Sessions, Audit-Log, Session-Einstellungen`

---

### Task 4: Einheitliche Anmeldung in `deps.py` (Token oder Session, CSRF) und IP-Drosselung

**Files:**
- Modify: `backend/moni/auth/deps.py`, `backend/moni/manager/db.py` (Request-Helfer), `backend/moni/manager/api.py` (`_store` nutzt den Helfer)
- Create: `backend/moni/auth/throttle.py`, `backend/tests/test_deps.py`, `backend/tests/test_throttle.py`
- Modify: `backend/tests/conftest.py` (Manager-Fixtures)

**Interfaces:**
- Consumes: `SessionStore`, `UserStore`, `TokenStore`, `tokens.SCOPES`.
- Produces:
  - `db.request_conn(request) -> ContextManager[sqlite3.Connection]`: öffnet und schließt eine Verbindung im aufrufenden Thread und bildet „locked/busy“ auf 503 mit `Retry-After: 5` ab. Das ist die bisherige Logik aus `manager/api._store`, die dorthin umzieht. `_store` nutzt den Helfer.
  - `@dataclass(frozen=True) class Principal`: `scope: str` (`"read"` oder `"admin"`), `actor: str`, `user_id: int | None`, `session: Session | None` (`repr=False`).
  - `RANK = {"read": 1, "admin": 2}` ersetzt die Inline-Menge (offener Punkt aus P2 T1). Sie wird beim Import gegen `tokens.SCOPES` geprüft.
  - `require_scope(scope) -> Callable[[Request], Principal]`. Die Reihenfolge:
    1. Gibt es einen `Authorization`-Header, gilt die bisherige Bearer-Logik. Ein Header mit falschem Format ergibt 401; ein Cookie wird dann nicht mehr angesehen.
    2. Sonst wird `moni_session` nur in der Rolle `manager` ausgewertet (`request.app.state.settings.role`): Die Session muss die Stufe `full` haben, der Benutzer muss existieren und nicht deaktiviert sein. Andernfalls folgt `401 "Nicht angemeldet"`.
    3. Bei Methoden außer `GET/HEAD/OPTIONS` mit Cookie-Anmeldung wird `X-CSRF-Token` gegen `session.csrf` geprüft (`hmac.compare_digest`). Bei Abweichung folgt `403 "CSRF-Token fehlt oder ist ungültig"`.
    4. Rolle `admin` → Scope `admin`, `viewer` → `read`. Ist der Rang zu niedrig, folgt 403. Für Tokens bleibt der Text `"Token hat nicht den nötigen Scope"`, für Sessions gilt `"Keine Berechtigung"`.

    Die Rolle wird bei jeder Anfrage frisch aus der DB gelesen, damit eine Herabstufung sofort wirkt. Der Rückgabewert lässt sich als Parameter nutzen (`p: Principal = Depends(require_scope("admin"))`). Die bestehenden `dependencies=[read]` bleiben gültig.
  - `require_stage(*stages: str) -> Callable[[Request], tuple[Session, User]]`: Nur Cookie, für die Anmelde-Schritte in Task 5. Bei unpassender Stufe folgt `401 "Nicht angemeldet"`. Die CSRF-Prüfung gilt wie oben.
  - `client_ip(request) -> str | None` (`request.client.host`)
  - `throttle.LoginThrottle(max_failures=20, window=900)`:
    - `retry_after(ip, now) -> int | None`
    - `fail(ip, now) -> None`

    Die Klasse ist threadsicher, hält höchstens 10 000 IPs und verwirft beim Überlauf zuerst abgelaufene Einträge, danach die ältesten.
- Test-Fixtures in `conftest.py`:
  - `mgr_settings`: Manager, frischer Schlüssel, `MONI_RATE_LIMIT_PER_MINUTE=10000`, `HOST_ETC` mit `hostname`-Datei
  - `mgr_app`: `create_app(mgr_settings, collector=Dummy(), poller=Dummy())`
  - `mgr_client`: `TestClient(mgr_app, base_url="https://testserver")`
  - `make_user(role, kind="local", email=None) -> User` (lokale Konten mit festem Testpasswort `TEST_PASSWORD`, ohne E-Mail fortlaufend `u<n>@example.org`)
  - `login_cookie(user, stage="full") -> tuple[str, str]`: `(cookie_value, csrf)` über `SessionStore` direkt

- [ ] **Step 1: Failing tests**

```python
def test_viewer_session_reads_but_cannot_write(mgr_client, make_user, login_cookie):
    cookie, csrf = login_cookie(make_user("viewer"))
    mgr_client.cookies.set("moni_session", cookie)
    assert mgr_client.get("/api/v1/servers").status_code == 200
    r = mgr_client.post("/api/v1/servers", json=NEW_SERVER, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403 and r.json()["detail"] == "Keine Berechtigung"

def test_admin_session_needs_csrf(mgr_client, make_user, login_cookie):
    cookie, csrf = login_cookie(make_user("admin"))
    mgr_client.cookies.set("moni_session", cookie)
    assert mgr_client.post("/api/v1/servers", json=NEW_SERVER).status_code == 403
    assert mgr_client.post("/api/v1/servers", json=NEW_SERVER, headers={"X-CSRF-Token": "falsch"}).status_code == 403
    assert mgr_client.post("/api/v1/servers", json=NEW_SERVER, headers={"X-CSRF-Token": csrf}).status_code == 201

def test_bearer_needs_no_csrf(mgr_client, mgr_app):
    h = {"Authorization": f"Bearer {mgr_app.state.tokens.create('a', 'admin')[1]}"}
    assert mgr_client.post("/api/v1/servers", json=NEW_SERVER, headers=h).status_code == 201

@pytest.mark.parametrize("stage", ["mfa", "enroll"])
def test_pre_auth_session_has_no_access(mgr_client, make_user, login_cookie, stage):
    mgr_client.cookies.set("moni_session", login_cookie(make_user("admin"), stage)[0])
    assert mgr_client.get("/api/v1/servers").status_code == 401

def test_disabled_or_demoted_user_effective_immediately(...): ...   # deaktiviert → 401; herabgestuft → 403 bei POST
def test_bad_authorization_header_does_not_fall_back_to_cookie(...): ...  # "Bearer" ohne Wert + gültiges Cookie → 401
def test_agent_ignores_session_cookie(client, ...): ...             # Agent-App: nur Cookie → 401

def test_throttle():
    t = LoginThrottle(max_failures=3, window=900)
    for _ in range(3):
        assert t.retry_after("1.1.1.1", 0) is None; t.fail("1.1.1.1", 0)
    assert 1 <= t.retry_after("1.1.1.1", 100) <= 800 and t.retry_after("2.2.2.2", 100) is None
    assert t.retry_after("1.1.1.1", 900) is None

def test_throttle_bounded(): ...                                     # 10 001 IPs → len(t._hits) <= 10_000
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün (alle bestehenden Agent- und Manager-API-Tests unverändert grün).
- [ ] **Step 5: Commit** `feat(auth): Anmeldung per Token oder Session mit CSRF-Schutz, IP-Drosselung`

---

### Task 5: Anmelde-API (Ersteinrichtung, Login, 2FA, Logout, eigenes Konto)

**Files:**
- Create: `backend/moni/auth/api.py`, `backend/tests/test_auth_api.py`
- Modify: `backend/moni/app.py` (Router einbinden, Setup-Token, `app.state.throttle`)

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces (`app.py`, nur Manager):
  - Ist `UserStore.count() == 0`, wird `setup_<token_urlsafe(24)>` erzeugt. Es wird einmal mit `log.warning("Ersteinrichtung offen: Setup-Token %s – gilt, bis der erste Admin angelegt ist", tok)` geloggt.
  - `app.state.setup_hash` = SHA-256 des Tokens oder `None`.
  - `app.state.throttle = LoginThrottle()`.
  - `auth.api.router` wird eingebunden.
- Produces (`auth/api.py`, Prefix `/api/v1`, alle Antworten JSON, Cookie wie in D4). Für diese Routen gilt die IP-Drosselung:
  - `POST /setup`
  - `/auth/login`
  - `/auth/login/mfa`
  - `/auth/totp/setup` (wenn `current_code` geprüft wird)
  - `/auth/totp/confirm`
  - `/account/password`
  - `/account/recovery-codes`

  Vor der Prüfung ruft die Route `throttle.retry_after(ip, now)` auf; ist die Grenze erreicht, folgt 429 mit `Retry-After`. Jeder Fehlversuch ruft `throttle.fail(ip, now)` auf.

| Route | Zugang | Body | Ergebnis |
|---|---|---|---|
| `GET /setup` | offen | – | `{"required": bool}` |
| `POST /setup` | offen | `{setup_token, email, name, password}` | `201 {"stage": "enroll"}` + Cookie (Stufe `enroll`), Audit `setup.admin_created` (Akteur `setup`). Falsches Token: `403 "Setup-Token ist ungültig"`. Es gibt schon Benutzer: `409 "Ersteinrichtung ist bereits abgeschlossen"`. Danach ist `setup_hash = None`. Die Zählung und das Anlegen laufen in **einer** `BEGIN IMMEDIATE`-Transaktion. |
| `POST /auth/login` | offen | `{email, password}` | `200 {"stage": "mfa"\|"enroll"}` + Cookie. OIDC-Konto, unbekannt, gesperrt, deaktiviert oder falsch: `401 "Anmeldung fehlgeschlagen"` (D8), Audit `login.failure`, beim Sperren zusätzlich `login.locked`. |
| `POST /auth/login/mfa` | `require_stage("mfa")` | `{code}` | `200 {"stage": "full"}`, Session rotiert, `register_success`, Audit `login.success` (bei Wiederherstellungscode zusätzlich `recovery_code.used`). Falsch: 401, `register_failure`, Audit `mfa.failure`. Bei Sperre wird die Session gelöscht. |
| `POST /auth/totp/setup` | `require_stage("enroll", "full")`, nur `local` | `{current_code?}` | `{"secret", "otpauth_uri"}`. In Stufe `full` mit aktivem TOTP ist `current_code` Pflicht und muss ein gültiger TOTP-Code sein, sonst `401 "Anmeldung fehlgeschlagen"`. |
| `POST /auth/totp/confirm` | `require_stage("enroll", "full")` | `{code}` | `200 {"recovery_codes": [10]}`. Aus `enroll` rotiert die Session zu `full`, `register_success`. Audit `totp.enabled`. |
| `GET /auth/session` | `require_stage("mfa", "enroll", "full")` | – | `{"stage", "csrf_token", "user": {id, email, name, role, kind, totp_enabled}}` |
| `POST /auth/logout` | `require_stage(...)` alle | – | 204, Session gelöscht, Cookie gelöscht, Audit `logout` |
| `POST /account/password` | `require_scope("read")` mit Session, nur `local` | `{current_password, new_password}` | 204. Falsches aktuelles Passwort: 401 und `register_failure`. Andere Sessions werden beendet. Audit `password.changed`. Bearer: `400 "Nur mit Anmeldung im Browser möglich"`. |
| `POST /account/recovery-codes` | wie oben | `{code}` (TOTP) | `{"recovery_codes": [10]}`, Audit `recovery_codes.regenerated` |

Alle Bodies sind Pydantic-Modelle mit `extra="forbid"`. Strings sind auf 1024 Zeichen begrenzt, das schützt vor Argon2-DoS durch Riesen-Bodies.

- [ ] **Step 1: Failing tests** (End-to-End über `mgr_client`; `code_at` aus der conftest)

```python
def test_full_first_run_flow(mgr_client, mgr_app, caplog):
    tok = setup_token_from(caplog)                       # Regex setup_[A-Za-z0-9_-]+
    assert mgr_client.get("/api/v1/setup").json() == {"required": True}
    assert mgr_client.post("/api/v1/setup", json=SETUP | {"setup_token": "setup_falsch"}).status_code == 403
    r = mgr_client.post("/api/v1/setup", json=SETUP | {"setup_token": tok})
    assert r.status_code == 201 and r.json() == {"stage": "enroll"}
    set_cookie = r.headers["set-cookie"].lower()
    assert all(x in set_cookie for x in ("httponly", "secure", "samesite=strict", "path=/"))
    assert mgr_client.get("/api/v1/servers").status_code == 401                 # enroll ≠ full
    csrf = mgr_client.get("/api/v1/auth/session").json()["csrf_token"]
    secret = mgr_client.post("/api/v1/auth/totp/setup", json={}, headers={"X-CSRF-Token": csrf}).json()["secret"]
    codes = mgr_client.post("/api/v1/auth/totp/confirm", json={"code": code_at(secret, time.time())},
                            headers={"X-CSRF-Token": csrf}).json()["recovery_codes"]
    assert len(codes) == 10 and mgr_client.get("/api/v1/servers").status_code == 200
    assert mgr_client.get("/api/v1/setup").json() == {"required": False}
    assert mgr_client.post("/api/v1/setup", json=SETUP | {"setup_token": tok}).status_code == 409

def test_login_mfa_rotates_session(...): ...          # altes Cookie nach /login/mfa → 401 bei /auth/session
def test_generic_failure_for_unknown_wrong_locked(...):
    # unbekannte E-Mail, falsches Passwort, OIDC-Konto, deaktiviert, gesperrt → identisches JSON 401 "Anmeldung fehlgeschlagen"
def test_lockout_blocks_correct_password(...): ...    # 5× falsch → richtiges Passwort → 401; Audit enthält login.locked
def test_mfa_failures_count_towards_lockout(...): ...
def test_ip_throttle_429(...): ...                    # 20 Fehlversuche (verschiedene E-Mails) → 429 mit Retry-After
def test_recovery_code_login_once(...): ...
def test_totp_change_requires_current_code(...): ...
def test_password_change_ends_other_sessions(...): ...
def test_logout(...): ...                             # 204, danach /auth/session 401, Set-Cookie löscht moni_session
def test_concurrent_setup_creates_one_admin(...):
    # zwei Threads POST /setup gleichzeitig mit verschiedenen E-Mails → genau ein 201, ein 409; users.count() == 1
def test_no_secrets_in_logs(caplog, ...):
    # nach komplettem Flow: Passwort, TOTP-Secret, Recovery-Codes, Cookie- und CSRF-Wert stehen in keiner Logzeile
def test_setup_token_not_logged_when_users_exist(...): ...
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün.
- [ ] **Step 5: Commit** `feat(auth): Ersteinrichtung, Login mit Pflicht-2FA, Wiederherstellungscodes, Konto-Endpunkte`

---

### Task 6: Verwaltungs-API (Benutzer, Tokens, Audit), Audit für Server und CLI `user`

**Files:**
- Create: `backend/moni/auth/admin_api.py`, `backend/tests/test_admin_api.py`
- Modify: `backend/moni/manager/api.py` (Audit für Server anlegen, ändern und entfernen, mit Akteur aus `Principal`), `backend/moni/app.py` (Router), `backend/moni/cli.py`, `backend/tests/test_cli.py`

**Interfaces:**
- Consumes: `Principal`, `UserStore`, `SessionStore`, `audit`, `TokenStore`.
- Produces (`admin_api.py`, alle `require_scope("admin")`, Prefix `/api/v1`). Benutzer-JSON: `{id, email, name, role, kind, totp_enabled, disabled, locked: bool, oidc_bound, created_at, last_login}` (ISO oder null).

| Route | Body | Ergebnis |
|---|---|---|
| `GET /users` | – | Liste |
| `POST /users` | `{email, name="", role, kind="local", password?}` | 201. `ValueError` → 422, `Conflict` → 409. Audit `user.created` (details: `role`, `kind`) |
| `PATCH /users/{id}` | `{name?, role?, disabled?}` (`extra="forbid"`, mindestens ein Feld) | 200. Deaktivieren beendet alle Sessions des Benutzers. Audit `user.updated` (details: geänderte Felder) |
| `DELETE /users/{id}` | – | 204/404/409, Audit `user.deleted` |
| `POST /users/{id}/password` | `{password}` | 204, nur `local` (sonst 422), Sessions beendet, Audit `password.reset` |
| `POST /users/{id}/reset-2fa` | – | 204, nur `local`, Sessions beendet, Audit `totp.reset` |
| `POST /users/{id}/unlock` | – | 204, Audit `user.unlocked` |
| `GET /tokens` | – | `[{id, name, scope, created_at, expires_at}]` (nie `hash`) |
| `POST /tokens` | `{name, scope="read", expires_days?}` | `201 {…, "token": "<Klartext, einmalig>"}`. Name 1–64 sichtbare Zeichen, `expires_days` 1–3650. Audit `token.created` (details: `scope`) |
| `DELETE /tokens/{id}` | – | 204/404, Audit `token.revoked` |
| `GET /audit` | Query `limit` (1–1000, Standard 100), `before` (≥ 1) | `{"entries": [...], "next_before": id \| null}` |

- Audit für die Server-Routen in `manager/api.py`: `server.added` (target Name, details `url`, `header_names`), `server.updated` (details `enabled`), `server.removed`. Die Route nutzt `p: Principal = Depends(admin)` statt `dependencies=[admin]`.
- CLI (Manager-Rolle, Akteur `cli`, alle Aktionen mit Audit):
  - `user list`
  - `user reset-password EMAIL --password-file PFAD|-` (Passwort nicht als Argument, damit es nicht in der Shell-History landet)
  - `user reset-2fa EMAIL`
  - `user unlock EMAIL`

  Die Reset-Befehle beenden alle Sessions des Benutzers. Unbekannte E-Mail: Exit 1 mit `Kein Benutzer mit der E-Mail <email>.` Diese CLI ist der Weg aus §6.10, um den letzten Admin wiederherzustellen.
- In der Manager-Rolle schreiben auch die vorhandenen Befehle `server add`, `server remove`, `token create`, `token revoke` und `secret reencrypt` einen Audit-Eintrag mit dem Akteur `cli`. In der Agent-Rolle gibt es keine DB, also auch kein Audit.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.parametrize("path", ["/api/v1/users", "/api/v1/tokens", "/api/v1/audit"])
def test_viewer_cannot_see_admin_lists(viewer_client, path):
    assert viewer_client.get(path).status_code == 403

def test_read_token_cannot_see_admin_lists(...): ...          # Bearer read → 403 "Token hat nicht den nötigen Scope"

def test_user_crud_and_audit(admin_client, csrf):
    r = admin_client.post("/api/v1/users", json={"email": "b@b.de", "role": "viewer", "password": "x" * 12},
                          headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201 and "password" not in r.text and "argon2" not in r.text
    uid = r.json()["id"]
    assert admin_client.patch(f"/api/v1/users/{uid}", json={"role": "admin"}, headers={"X-CSRF-Token": csrf}).json()["role"] == "admin"
    actions = [e["action"] for e in admin_client.get("/api/v1/audit").json()["entries"]]
    assert actions[:2] == ["user.updated", "user.created"]

def test_last_admin_api_409(admin_client, csrf, admin_user):   # sich selbst löschen/herabstufen/deaktivieren → 409
def test_patch_rejects_unknown_and_empty(...): ...             # {"email": ...} → 422, {} → 422
def test_disable_ends_sessions(...): ...
def test_token_api_shows_plaintext_once(admin_client, csrf):
    r = admin_client.post("/api/v1/tokens", json={"name": "grafana"}, headers={"X-CSRF-Token": csrf})
    plain = r.json()["token"]
    assert plain.startswith("moni_") and plain not in admin_client.get("/api/v1/tokens").text
    assert "hash" not in admin_client.get("/api/v1/tokens").json()[0]
def test_audit_paging_and_limits(...): ...                     # limit=0 → 422, limit=1001 → 422, next_before stimmt
def test_server_changes_are_audited(...): ...                  # Akteur "token:<id>" bei Bearer, "user:<email>" bei Session
def test_cli_reset_2fa_and_unlock_audited(...): ...
def test_cli_reset_password_from_stdin(...): ...               # monkeypatch stdin; Policy-Verletzung → Exit 2 mit Meldung
```

Die Fixtures `admin_client`, `viewer_client`, `csrf` und `admin_user` baut die Testdatei aus `make_user` und `login_cookie` (Task 4).

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün.
- [ ] **Step 5: Commit** `feat(auth): Verwaltung von Benutzern, Tokens und Audit-Log per API und CLI`

---

### Task 7: OIDC (Konfiguration, Start, Callback)

**Files:**
- Create: `backend/moni/auth/oidc.py`, `backend/tests/test_oidc.py`
- Modify: `backend/requirements-docker.txt` (`PyJWT==2.15.1`), `backend/moni/app.py` (Router, `app.state.oidc_flows`), `backend/moni/cli.py` (`secret reencrypt` zählt das OIDC-Secret mit)

**Interfaces:**
- Consumes: `SecretBox`, `UserStore.by_oidc/bind_oidc/by_email`, `SessionStore`, `audit`, `require_scope`.
- Produces (`oidc.py`):
  - `_http(method: str, url: str, data: dict | None = None, headers: dict | None = None) -> dict` (D3). Zulässig ist nur `https://`. Nicht-2xx-Antworten, kaputtes JSON, mehr als 1 MB, eine Weiterleitung oder ein Timeout führen zu `OidcError("…")`. **Die einzige Netzwerkstelle, Tests ersetzen sie per `monkeypatch`.**
  - `class OidcError(Exception)`
  - `load_config(conn, box) -> dict | None`, `save_config(conn, box, *, issuer, client_id, client_secret | None, public_url, label) -> None`, `delete_config(conn) -> None`, `reencrypt(conn, box) -> int`. Es gibt einen Discovery-Cache (1 h) und einen JWKS-Cache (1 h; bei unbekannter `kid` wird einmal neu geladen) im Speicher.
  - `class Flows`: im Speicher, `state → (nonce, verifier, created)`, TTL 600 s, höchstens 1000 Einträge, threadsicher. Methoden: `add(now) -> tuple[state, nonce, challenge]`, `pop(state, now) -> tuple[nonce, verifier] | None`.
  - `validate_id_token(id_token: str, *, issuer, client_id, nonce, jwks: dict, algs: list[str], now: float) -> dict`. Zulässige Algorithmen sind die Schnittmenge aus `algs` und `{"RS256","RS384","RS512","PS256","PS384","PS512","ES256","ES384","EdDSA"}`. Geprüft werden `iss`, `aud` (enthält `client_id`; bei mehreren `aud` muss `azp == client_id` sein), `exp`, `iat` (60 s Toleranz) und `nonce`. Fehler ergeben `OidcError`. Der Schlüssel kommt aus `jwt.PyJWKSet.from_dict(jwks)` über `kid`.
- Routen (Prefix `/api/v1/auth/oidc`):

| Route | Zugang | Ergebnis |
|---|---|---|
| `GET ""` | offen | `{"enabled": bool, "label": str \| null}` |
| `GET /config` | admin | `{issuer, client_id, public_url, label, client_secret_set: bool}` oder 404 |
| `PUT /config` | admin | `{issuer, client_id, client_secret?, public_url, label?}`. `issuer` und `public_url` müssen `https://` ohne Query und Fragment sein. Die Discovery wird sofort abgerufen, und ihr `issuer` muss exakt passen, sonst `422 "OIDC-Anbieter nicht erreichbar oder Konfiguration passt nicht"`. Beim ersten Speichern ist das Secret Pflicht. Audit `oidc.config_changed` (ohne Secret). |
| `DELETE /config` | admin | 204, Audit `oidc.config_changed` (details `{"deleted": true}`) |
| `GET /start` | offen | Ohne Konfiguration 404. Sonst `302` zum `authorization_endpoint` mit `response_type=code`, `client_id`, `redirect_uri=<public_url>/api/v1/auth/oidc/callback`, `scope=openid email profile`, `state`, `nonce`, `code_challenge` (S256) und `code_challenge_method=S256`. Dazu das Cookie `moni_oidc=<state>` (`HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth/oidc; Max-Age=600`), damit der Rücksprung vom Anbieter es mitschickt. |
| `GET /callback` | offen | `?code&state` oder `?error`. Zuerst wird `state == Cookie` geprüft (`compare_digest`) und `Flows.pop` aufgerufen. Danach folgt der Token-Austausch (POST `token_endpoint`, `client_secret_basic`, `code_verifier`) und `validate_id_token`. Die Konto-Zuordnung: zuerst `by_oidc(iss, sub)`. Sonst sucht Moni bei `email_verified is True` ein Konto mit `kind="oidc"` und dieser E-Mail, das noch nicht verknüpft ist, und verknüpft es (`bind_oidc`). Das Konto darf nicht deaktiviert sein. Erfolg: Session in der Stufe `full`, `register_success`, Audit `oidc.login`, `303` nach `/`, Cookie `moni_oidc` gelöscht. Jeder Fehler: `303` nach `/login?fehler=oidc`, Audit `oidc.failure` mit `details={"grund": "<kurzer fester Code>"}` (z. B. `state`, `token`, `id_token`, `email_unverified`, `unknown_user`, `disabled`). Nie ein 500, nie Token-Inhalte im Log oder Audit. Die Fehlversuche zählen für die IP-Drosselung. |

- [ ] **Step 1: Failing tests** mit einem Test-Anbieter: RSA-Schlüssel aus `cryptography`, JWKS mit `kid="k1"`. `_http` wird per `monkeypatch` durch eine Funktion ersetzt, die Discovery, JWKS und Token-Antwort aus einem Dict liefert. Die ID-Tokens erzeugt `jwt.encode`.

```python
def test_start_redirect_has_pkce_state_nonce(oidc_client):
    r = oidc_client.get("/api/v1/auth/oidc/start", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert r.status_code == 302 and q["code_challenge_method"] == ["S256"] and q["state"] and q["nonce"]
    assert q["redirect_uri"] == ["https://moni.example.org/api/v1/auth/oidc/callback"]
    assert "samesite=lax" in r.headers["set-cookie"].lower()

def test_happy_path_binds_and_logs_in(oidc_client, idp, make_user):
    u = make_user("viewer", kind="oidc", email="o@example.org")
    r = complete_flow(oidc_client, idp, claims={"email": "o@example.org", "email_verified": True, "sub": "s1"})
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert oidc_client.get("/api/v1/auth/session").json()["stage"] == "full"
    # zweiter Login mit geänderter E-Mail im Token, gleiche sub → weiterhin derselbe Benutzer

@pytest.mark.parametrize("tamper,grund", [
    ("wrong_state", "state"), ("no_cookie", "state"), ("replayed_state", "state"),
    ("wrong_nonce", "id_token"), ("wrong_aud", "id_token"), ("wrong_iss", "id_token"), ("expired", "id_token"),
    ("alg_none", "id_token"), ("alg_hs256_with_jwks_n", "id_token"), ("unknown_kid", "id_token"),
    ("email_unverified", "email_unverified"), ("unknown_email", "unknown_user"), ("local_account", "unknown_user"),
    ("disabled", "disabled"), ("token_endpoint_500", "token"), ("idp_error_param", "anbieter"),
])
def test_tampered_callbacks_never_login(oidc_client, idp, tamper, grund):
    r = run_tampered(oidc_client, idp, tamper)
    assert r.status_code == 303 and r.headers["location"] == "/login?fehler=oidc"
    assert oidc_client.get("/api/v1/auth/session").status_code == 401
    assert last_audit(oidc_client)["details"] == {"grund": grund}

def test_config_never_returns_secret(...): ...        # GET /config → client_secret_set True, Secret nicht im Text, nicht in DB-Klartext
def test_config_rejects_http_and_issuer_mismatch(...): ...
def test_http_helper_rejects_non_https_and_redirects(...): ...   # echte _http: "http://…" → OidcError ohne Netz
def test_flows_ttl_and_bound(): ...
def test_reencrypt_includes_oidc_secret(...): ...
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün.
- [ ] **Step 5: Commit** `feat(auth): OIDC-Anmeldung mit PKCE, ohne automatische Kontoanlage`

---

### Task 8: Wartung, Betrieb und CI

**Files:**
- Modify: `backend/moni/manager/poller.py` (`_maintain`), `backend/tests/test_poller.py`
- Modify: `compose.manager.yaml`, `.env.example`, `.github/workflows/ci.yml`
- Create: `scripts/ci/manager_auth_smoke.py`
- Modify: `docs/superpowers/plans/2026-11-04-moni-2-uebersicht.md` (Status Paket 3)

**Interfaces:**
- `Poller._maintain(now)` ruft im bestehenden Savepoint zusätzlich `SessionStore(conn, settings).purge_expired(now)` und `audit.prune(conn, now - audit.RETENTION_DAYS * 86400)` auf.
- `compose.manager.yaml`: `MONI_SESSION_IDLE_MINUTES` und `MONI_SESSION_MAX_HOURS` als `${VAR:-default}`. `.env.example` dokumentiert beide und weist auf D4 hin (Browser-Anmeldung braucht bis Paket 4 HTTPS über einen Proxy oder `localhost`).
- `scripts/ci/manager_auth_smoke.py BASE_URL SETUP_TOKEN` (nur Standardbibliothek, `urllib`, Cookies per Header, TOTP per `hmac`/`base64`/`struct` nach RFC 6238) läuft so:
  1. `GET /api/v1/setup` muss `required: true` liefern.
  2. `POST /setup` mit einem falschen Token muss 403 ergeben.
  3. `POST /setup` mit dem echten Token.
  4. TOTP einrichten und bestätigen.
  5. `GET /api/v1/servers` mit dem Cookie muss 200 ergeben.
  6. `POST /api/v1/servers` ohne CSRF muss 403 ergeben.
  7. Logout, danach muss `GET /api/v1/auth/session` 401 ergeben.

  Bei Erfolg gibt das Skript `OK Anmeldung` aus und endet mit Exit 0, sonst mit einer klaren Meldung und Exit 1. Es gibt Passwort, Secret und Codes nie aus.
- CI-Schritt „Manager smoke test“, ergänzt nach dem bestehenden Verlaufstest:
  - das Setup-Token mit `grep -oE 'setup_[A-Za-z0-9_-]+' manager.log | head -n1` aus der vorher geschriebenen Logdatei holen (kein Pipe-in-`grep -q`, siehe R7)
  - das Skript mit `http://127.0.0.1:8080` aufrufen
  - danach prüfen, dass die Logs weder das Admin-Passwort des Skripts (fester CI-Wert) noch `recovery` enthalten

- [ ] **Step 1: Failing test** `test_maintenance_purges_sessions_and_old_audit` (abgelaufene Session und Audit-Eintrag älter als 365 Tage werden entfernt, ein frischer Eintrag bleibt).
- [ ] **Step 2–4:** Test rot → Implementieren → volle Suite grün. Danach das Skript lokal gegen `docker compose -f compose.manager.yaml up -d --build` laufen lassen (mit `COMPOSE_PROJECT_NAME=moni-manager` und einem frischen `MONI_SECRET_KEY`). Expected: `OK Anmeldung`.
- [ ] **Step 5: Commit** `feat(manager): Session- und Audit-Aufräumen, Anmelde-Smoke-Test in der CI`

---

### Task 9 (Betreiber, optional): OIDC mit einem echten Anbieter

Nicht durch Subagents. Voraussetzung ist ein Manager, der per HTTPS unter einer Domain erreichbar ist (beim Autor: Pi hinter Traefik).

1. Beim Anbieter (z. B. Google Cloud Console) einen OAuth-Client vom Typ „Webanwendung“ anlegen, mit der Redirect-URI `https://<domain>/api/v1/auth/oidc/callback`.
2. Mit einem Admin-Token die Konfiguration setzen: `PUT /api/v1/auth/oidc/config` mit `issuer=https://accounts.google.com`, `client_id`, `client_secret` und `public_url=https://<domain>`.
3. `POST /api/v1/users` mit `kind="oidc"`, der eigenen Google-Adresse und `role="viewer"`.
4. Im Browser `https://<domain>/api/v1/auth/oidc/start` aufrufen. Erwartet wird ein Rücksprung auf `/` und danach `GET /api/v1/auth/session` → `stage: full`.
