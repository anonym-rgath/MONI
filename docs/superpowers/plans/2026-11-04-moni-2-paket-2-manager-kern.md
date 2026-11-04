# Moni 2.0 – Paket 2: Manager-Kern – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Rolle `manager` bauen. Sie ruft mehrere Agents ab, speichert Verläufe in SQLite (mit Verdichtung und Aufbewahrung), legt Geheimnisse verschlüsselt ab und stellt alles über eine Manager-API v1 und die CLI bereit, ohne Weboberfläche.

**Architecture:** Gleiche Codebasis wie der Agent. `create_app` entscheidet anhand von `settings.role`, welche Router und Hintergrunddienste laufen. Der Manager startet den bestehenden `Collector` im Prozess (Server „lokal“) und einen `Poller`. Der Poller holt alle 15 s parallel die Snapshots der Remote-Agents, füllt Lücken über `/api/v1/history`, zerlegt jeden Snapshot mit `metrics_model.flatten` in Zeitreihen und schreibt sie gebündelt in SQLite. Verdichtung und Aufbewahrung laufen im selben Thread alle 5 Minuten. Geheimnisse (Agent-Tokens, Zusatz-Header) verschlüsselt `SecretBox` mit `MONI_SECRET_KEY`.

**Tech Stack:** Python 3.14, FastAPI, sqlite3 (Standardbibliothek), `cryptography` (Fernet/MultiFernet), urllib für die Agent-Abrufe.

**Spec:** [`docs/superpowers/specs/2026-11-04-moni-agent-manager-design.md`](../specs/2026-11-04-moni-agent-manager-design.md) (§6.1, §6.2 Manager-Seite, §6.6, §6.10 Master-Key) · Übersicht: [`2026-11-04-moni-2-uebersicht.md`](2026-11-04-moni-2-uebersicht.md) · Vorgänger: [Paket 1](2026-11-04-moni-2-paket-1-fundament-agent.md)

## Ist-Stand nach Paket 1 (Schnittstellen, auf die dieser Plan baut)

- `moni.config.Settings.from_env(env)`: frozen dataclass, Felder siehe `backend/moni/config.py`
- `moni.collector.Collector(settings, client=None)` mit `collect_once()`, `latest()`, `history(since)`, `start()` und `stop()`. Der Snapshot-Vertrag ist `derive()` mit `schema_version = 1`.
- `moni.auth.tokens.TokenStore(path)` mit `create`, `verify`, `list` und `revoke`, `SCOPES = {"read"}`. `moni.auth.deps.require_scope(scope)` vergleicht bisher exakt.
- `moni.agent.api.router` (`/api/v1`: `health`, `info`, `snapshot`, `history`) und `metrics_router` (`/metrics`). `moni.agent.prometheus.render(snapshot)`.
- `moni.errors.install(app)` (RFC 9457), `moni.ratelimit.RateLimitMiddleware`, `moni.app.create_app(settings, collector)`, `moni.cli.main(argv)` mit `token create|list|revoke`.
- Docker: Targets `agent` und `manager`. `manager` = nginx (Proxy `/api` → `127.0.0.1:8001`) + supervisor, startet bisher `python -m moni` mit `MONI_ROLE=agent`. `compose.yaml` ist die alte Manager-Compose-Datei, `compose.agent.yaml` die neue Agent-Compose-Datei.

## Global Constraints

- Branch `feature/moni-2`, Draft-PR #41 (nicht mergen). Commits mit Autor-E-Mail `261156405+anonym-rgath@users.noreply.github.com` (repo-lokal gesetzt). Commit-Trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Python **3.14**. Tests: `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests -v`
- Neue Laufzeit-Abhängigkeit **nur** `cryptography==50.0.2` (in `backend/requirements-docker.txt`). Sonst Standardbibliothek.
- Der Agent bleibt unverändert nutzbar. Alle bestehenden Agent-Tests bleiben grün, und `compose.agent.yaml` funktioniert weiter.
- Unbekannte Werte sind `null` bzw. `available: false`, nie Ersatzwerte. Zeitstempel in der API: UTC ISO 8601 mit `Z` (`timeutil.iso`). In SQLite: ganze Unix-Sekunden (`INTEGER`).
- Geheimnisse (`MONI_SECRET_KEY`, Agent-Tokens, Header-Werte, API-Tokens) stehen nie in Logs, API-Antworten, `repr()` oder Fehlermeldungen. Agent-Token und Header-**Werte** liegen nur verschlüsselt in der DB. Die API zeigt nur die Header-**Namen**.
- Standardwerte: Abruftakt = `MONI_COLLECT_INTERVAL` (15 s); „nicht erreichbar“ nach **3** verpassten Abrufen; Abruf-Timeout **10 s** (`MONI_POLL_TIMEOUT`); Aufbewahrung Rohdaten **48 h** (`MONI_RETENTION_RAW_HOURS`), Minutenwerte **30 Tage** (`MONI_RETENTION_MINUTE_DAYS`), Stundenwerte **365 Tage** (`MONI_RETENTION_HOUR_DAYS`); Verdichtung alle **300 s**; DB-Datei `<MONI_DATA_DIR>/moni.db`.
- Auflösung bei `resolution=auto`: Zeitraum ≤ **6 h** → `raw`, ≤ **7 Tage** → `1m`, sonst `1h`.
- Snapshots sind unveränderlich (Ruling R5 aus Paket 1). Der Manager serialisiert sie und verändert die Puffer-Objekte des `Collector` nie.
- Scope-Hierarchie (Ruling R6 aus Paket 1): `admin` schließt `read` ein.
- Texte für Menschen auf Deutsch mit korrekten Umlauten.

## Review Focus

1. **Ein Agent ist langsam, hängt oder liefert Unsinn** (Timeout, 401, 500, kaputtes JSON, `schema_version != 1`). Die anderen Server werden trotzdem im selben Zyklus abgefragt. Der betroffene Server sammelt Fehlversuche, wird nach 3 Fehlschlägen `unreachable` (bzw. `error` bei 401 oder Schema-Fehler) und erholt sich beim nächsten erfolgreichen Abruf. Test in Task 7.
2. **Lücken und Doppelte.** Nach einem Manager-Neustart oder einer Pause füllt `history?since=` die Lücke. Bereits gespeicherte Punkte werden nicht doppelt geschrieben (`INSERT OR IGNORE`). Ist die Lücke größer als der Puffer des Agents, bleibt sie sichtbar, ohne Fehler. Test in Task 6 und Task 7.
3. **Falscher oder fehlender `MONI_SECRET_KEY`.** Fehlt der Schlüssel oder ist er ungültig, verweigert der Manager den Start mit klarer Meldung. Lässt sich das Geheimnis eines Servers nicht entschlüsseln (Schlüssel gewechselt), wird nur dieser Server `error`. Der Schlüssel erscheint nirgends im Log. Test in Task 1, Task 2 und Task 7.
4. **DB-Fehler** (gesperrt, Platte voll, Migration schlägt fehl). Ein Abrufzyklus mit DB-Fehler wird geloggt, und der Thread läuft weiter. Eine fehlgeschlagene Migration lässt Original und Sicherung unverändert und bricht den Start ab. Test in Task 4 und Task 7.
5. **Abfrage-Randfälle.** `from > to`, unbekannte Metrik, unbekannter Server, leerer Zeitraum und ungültige `resolution` ergeben 422/404 bzw. leere Listen, nie einen 500. `auto` wählt die richtige Auflösung. Test in Task 6 und Task 8.

---

### Task 1: Settings für den Manager, Scope-Hierarchie, Aufräumpunkte aus Paket 1

**Files:**
- Modify: `backend/moni/config.py`, `backend/moni/auth/tokens.py` (nur `SCOPES`), `backend/moni/auth/deps.py`, `backend/moni/agent/api.py` (`info.tls`)
- Modify: `backend/tests/test_config.py`, `backend/tests/test_agent_api.py`, `backend/tests/test_tokens.py`

**Interfaces:**
- Produces:
  - Neue `Settings`-Felder: `secret_key: str` (`field(repr=False)`, Env `MONI_SECRET_KEY`, Standard `""`), `poll_timeout_seconds: float` (`MONI_POLL_TIMEOUT`, `10`), `retention_raw_hours: int` (`48`), `retention_minute_days: int` (`30`), `retention_hour_days: int` (`365`). Die Env-Namen stehen in den Global Constraints.
  - `Settings.db_path -> Path` (Property: `Path(data_dir) / "moni.db"`)
  - Validierung: Bei `role == "manager"` muss `secret_key` gesetzt sein, sonst `ValueError("MONI_SECRET_KEY fehlt – erzeugen mit: python -m moni.cli secret generate")`. Ob der Schlüssel gültig ist, prüft `SecretBox` (Task 2). Nicht-numerische Werte ergeben `ValueError` mit dem Variablennamen, z. B. `"MONI_PORT muss eine ganze Zahl sein"`. `port` muss in 1..65535 liegen, `rate_limit_per_minute`, `max_docker_workers` und alle Aufbewahrungswerte müssen ≥ 1 sein, Timeouts > 0.
  - `tokens.SCOPES = frozenset({"read", "admin"})`
  - `require_scope("read")` akzeptiert die Scopes `read` und `admin`. `require_scope("admin")` akzeptiert nur `admin`.
  - Agent-`/info` liefert `"tls": settings.tls` statt eines festen Werts.

- [ ] **Step 1: Failing tests schreiben**

```python
# test_config.py
def test_manager_requires_secret_key():
    with pytest.raises(ValueError, match="MONI_SECRET_KEY"):
        Settings.from_env({"MONI_ROLE": "manager"})

def test_secret_key_not_in_repr():
    s = Settings.from_env({"MONI_ROLE": "manager", "MONI_SECRET_KEY": "geheim123"})
    assert "geheim123" not in repr(s)

def test_manager_defaults_and_db_path():
    s = Settings.from_env({"MONI_ROLE": "manager", "MONI_SECRET_KEY": "x", "MONI_DATA_DIR": "/d"})
    assert (s.poll_timeout_seconds, s.retention_raw_hours, s.retention_minute_days, s.retention_hour_days) == (10.0, 48, 30, 365)
    assert str(s.db_path) == "/d/moni.db"

@pytest.mark.parametrize("env,name", [({"MONI_PORT": "abc"}, "MONI_PORT"), ({"MONI_PORT": "70000"}, "MONI_PORT"),
                                      ({"MONI_RETENTION_RAW_HOURS": "0"}, "MONI_RETENTION_RAW_HOURS"),
                                      ({"DOCKER_STATS_TIMEOUT": "0"}, "DOCKER_STATS_TIMEOUT")])
def test_invalid_values_name_the_variable(env, name):
    with pytest.raises(ValueError, match=name):
        Settings.from_env(env)

# test_agent_api.py
def test_admin_token_can_read(client, store):
    _, plain = store.create("adm", scope="admin")
    assert client.get("/api/v1/info", headers={"Authorization": f"Bearer {plain}"}).status_code == 200

def test_info_tls_from_settings(client, auth):
    assert client.get("/api/v1/info", headers=auth).json()["tls"] == "off"

# test_tokens.py
def test_admin_scope_allowed(tmp_path):
    tok, _ = TokenStore(tmp_path / "t.json").create("x", scope="admin")
    assert tok.scope == "admin"
```

`test_unknown_scope` aus Paket 1 prüft künftig mit `scope="root"`.

- [ ] **Step 2: Tests laufen lassen** – Expected: FAIL (neue Felder/Scopes fehlen)
- [ ] **Step 3: Implementieren** (Hilfsfunktionen `_int(env, name, default)` und `_float(...)` in `config.py` liefern die benannten Fehlermeldungen)
- [ ] **Step 4: Volle Suite** – Expected: PASS
- [ ] **Step 5: Commit** `feat(manager): Settings fuer Manager, Scope admin ⊇ read, benannte Env-Fehler`

---

### Task 2: Geheimnis-Verschlüsselung (`moni/secretbox.py`) und CLI `secret`

**Files:**
- Create: `backend/moni/secretbox.py`, `backend/tests/test_secretbox.py`
- Modify: `backend/requirements-docker.txt` (`cryptography==50.0.2`), `backend/moni/cli.py`, `backend/tests/test_tokens.py` (CLI-Tests) bzw. neue `backend/tests/test_cli.py`

**Interfaces:**
- Produces:
  - `class SecretBox(key_spec: str)`. `key_spec` ist eine kommagetrennte Liste von Fernet-Schlüsseln. Der **erste** verschlüsselt, alle entschlüsseln (`MultiFernet`). Ungültiges Format wirft `ValueError("MONI_SECRET_KEY ist ungültig (erwartet Fernet-Schlüssel, ggf. kommagetrennt)")`, ohne den Wert zu nennen.
  - `SecretBox.encrypt(plaintext: str) -> str` (Fernet-Token als str)
  - `SecretBox.decrypt(token: str) -> str`. Schlägt die Entschlüsselung fehl, wird `SecretError("Geheimnis nicht entschlüsselbar")` geworfen (eigene Exception, ohne Inhalt).
  - `SecretBox.rotate(token: str) -> str`: neu verschlüsseln mit dem ersten Schlüssel
  - `generate_key() -> str`
  - CLI: `secret generate` gibt einen neuen Schlüssel aus (eine Zeile), Exit 0.

- [ ] **Step 1: Failing tests**

```python
from moni.secretbox import SecretBox, SecretError, generate_key

def test_roundtrip_and_no_plaintext():
    box = SecretBox(generate_key())
    tok = box.encrypt("agent-token-123")
    assert "agent-token-123" not in tok and box.decrypt(tok) == "agent-token-123"

def test_key_rotation():
    old, new = generate_key(), generate_key()
    tok = SecretBox(old).encrypt("x")
    both = SecretBox(f"{new},{old}")
    assert both.decrypt(tok) == "x"
    assert SecretBox(new).decrypt(both.rotate(tok)) == "x"

def test_wrong_key_raises_secret_error():
    tok = SecretBox(generate_key()).encrypt("x")
    with pytest.raises(SecretError):
        SecretBox(generate_key()).decrypt(tok)

def test_invalid_key_message_hides_value():
    with pytest.raises(ValueError) as e:
        SecretBox("kein-schluessel-xyz")
    assert "kein-schluessel-xyz" not in str(e.value)

def test_cli_secret_generate(capsys):
    assert cli.main(["secret", "generate"]) == 0
    SecretBox(capsys.readouterr().out.strip())  # gültig
```

- [ ] **Step 2: Tests laufen lassen** – Expected: FAIL
- [ ] **Step 3: Implementieren**
- [ ] **Step 4: Volle Suite** – Expected: PASS
- [ ] **Step 5: Commit** `feat(manager): SecretBox mit Schluesselrotation und CLI secret generate`

---

### Task 3: Gemeinsames Metrik-Modell (`moni/metrics_model.py`)

Die Logik „Snapshot → Zahlenreihen mit Labels“ steckt heute in `prometheus.render`. Sie wird herausgelöst, damit Prometheus-Ausgabe und Manager-Speicher dieselben Namen und Labels nutzen. Das ist reine Umstrukturierung, die `/metrics`-Ausgabe bleibt byte-identisch.

**Files:**
- Create: `backend/moni/metrics_model.py`, `backend/tests/test_metrics_model.py`
- Modify: `backend/moni/agent/prometheus.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Sample: name: str; labels: tuple[tuple[str, str], ...]; value: float`
  - `flatten(snapshot: dict) -> list[Sample]` liefert dieselben Namen, Labels (in derselben Reihenfolge) und Werte wie `render` heute, inklusive `moni_snapshot_timestamp_seconds`. `None`-Werte werden ausgelassen, Booleans werden 1/0.
  - `HELP: dict[str, str]` wandert mit nach `metrics_model` (Prometheus importiert es).
- Consumes: Snapshot-Vertrag (`collector.derive`)

- [ ] **Step 1: Failing tests** – `test_flatten_matches_render`: Für den `SNAP` aus `test_prometheus.py` ergibt `flatten` mindestens `Sample("moni_host_cpu_usage_percent", (), 12.5)` und `Sample("moni_filesystem_size_bytes", (("mountpoint", "/"), ("device", "/dev/root"), ("fstype", "ext4")), 4096000)`. Kein Sample darf den Wert `None` haben. Die bestehenden `test_prometheus.py`-Tests bleiben unverändert grün.
- [ ] **Step 2: Tests laufen lassen** – Expected: FAIL (`ImportError`)
- [ ] **Step 3: `flatten` implementieren, `render` darauf umstellen** (gruppiert nach Name, HELP/TYPE wie bisher)
- [ ] **Step 4: Volle Suite** – Expected: PASS
- [ ] **Step 5: Commit** `refactor(agent): Metrik-Modell aus Prometheus-Renderer herausloesen`

---

### Task 4: Datenbank, Migrationen und Sicherung (`moni/manager/db.py`)

**Files:**
- Create: `backend/moni/manager/__init__.py`, `backend/moni/manager/db.py`, `backend/moni/manager/migrations/0001_init.sql`, `backend/tests/test_db.py`

**Interfaces:**
- Produces:
  - `connect(path: Path) -> sqlite3.Connection` setzt `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`, `row_factory=sqlite3.Row` und `check_same_thread=False` nicht. Jeder Thread bzw. jede Anfrage öffnet eine eigene Verbindung.
  - `migrate(path: Path) -> int` gibt die erreichte Version zurück (`PRAGMA user_version`). Gibt es ausstehende Migrationen **und** existiert die DB-Datei mit Version > 0, wird vorher mit der sqlite3-Backup-API eine Kopie `moni.db.bak-v<alt>-<YYYYmmddHHMMSS>` angelegt. Jede Migration läuft in einer Transaktion. Bei einem Fehler: Rollback, `RuntimeError("Migration <n> fehlgeschlagen: …")`, Version unverändert.
  - `MIGRATIONS_DIR` mit Dateien `NNNN_name.sql`, die aufsteigend ausgeführt werden
  - Schema `0001_init.sql`:

```sql
CREATE TABLE servers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  url TEXT,                      -- NULL beim lokalen Server
  is_local INTEGER NOT NULL DEFAULT 0,
  token_enc TEXT,                -- SecretBox-Token
  headers_enc TEXT,              -- SecretBox-Token eines JSON-Objekts
  header_names TEXT NOT NULL DEFAULT '[]',  -- JSON-Liste, nur Namen (für die API)
  enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'unknown',   -- unknown|ok|unreachable|error
  failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  last_seen INTEGER,             -- Unix-Sekunden des letzten erfolgreichen Abrufs
  last_ts INTEGER,               -- Zeitstempel des jüngsten gespeicherten Punkts
  last_snapshot TEXT,            -- JSON des jüngsten Snapshots
  agent_info TEXT,               -- JSON von /api/v1/info
  created_at INTEGER NOT NULL
);
CREATE TABLE series (
  id INTEGER PRIMARY KEY,
  server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  labels TEXT NOT NULL,          -- kanonisches JSON: Liste von [key, value]
  UNIQUE (server_id, name, labels)
);
CREATE TABLE samples_raw (series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, value REAL NOT NULL, PRIMARY KEY (series_id, ts)) WITHOUT ROWID;
CREATE TABLE samples_1m (series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, min REAL NOT NULL, avg REAL NOT NULL, max REAL NOT NULL, count INTEGER NOT NULL,
  PRIMARY KEY (series_id, ts)) WITHOUT ROWID;
CREATE TABLE samples_1h (series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, min REAL NOT NULL, avg REAL NOT NULL, max REAL NOT NULL, count INTEGER NOT NULL,
  PRIMARY KEY (series_id, ts)) WITHOUT ROWID;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

- [ ] **Step 1: Failing tests**

```python
def test_fresh_migrate(tmp_path):
    p = tmp_path / "moni.db"
    assert db.migrate(p) == 1
    c = db.connect(p)
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {
        "servers", "series", "samples_raw", "samples_1m", "samples_1h", "meta"}
    assert not list(tmp_path.glob("moni.db.bak-*"))       # frische DB: keine Sicherung nötig

def test_migrate_is_idempotent(tmp_path):
    p = tmp_path / "moni.db"; db.migrate(p)
    assert db.migrate(p) == 1 and not list(tmp_path.glob("moni.db.bak-*"))

def test_backup_before_upgrade_and_failed_migration_keeps_state(tmp_path, monkeypatch):
    p = tmp_path / "moni.db"; db.migrate(p)
    bad = tmp_path / "mig"; bad.mkdir()
    for f in db.MIGRATIONS_DIR.glob("*.sql"):
        (bad / f.name).write_text(f.read_text())
    (bad / "0002_kaputt.sql").write_text("CREATE TABLE x (a INT); SELEC kaputt;")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", bad)
    with pytest.raises(RuntimeError, match="Migration 2"):
        db.migrate(p)
    assert db.connect(p).execute("PRAGMA user_version").fetchone()[0] == 1
    assert "x" not in {r[0] for r in db.connect(p).execute("SELECT name FROM sqlite_master")}
    assert len(list(tmp_path.glob("moni.db.bak-v1-*"))) == 1
```

- [ ] **Step 2: Tests laufen lassen** – Expected: FAIL
- [ ] **Step 3: Implementieren**

Die SQL-Dateien werden als einzelne Statements ausgeführt (`sqlite3.complete_statement` bzw. Trennung an `;` am Zeilenende), **nicht** mit `executescript`, denn das committet implizit und macht einen Rollback unmöglich. Die Migrationsdateien müssen ins Image: `backend/moni/manager/migrations/*.sql` liegt im Paket, `COPY backend/moni` deckt das ab.
- [ ] **Step 4: Volle Suite** – Expected: PASS
- [ ] **Step 5: Commit** `feat(manager): SQLite mit WAL, versionierten Migrationen und Sicherung`

---

### Task 5: Server-Verwaltung (`moni/manager/servers.py`) und CLI `server`

**Files:**
- Create: `backend/moni/manager/servers.py`, `backend/tests/test_servers.py`
- Modify: `backend/moni/cli.py`, `backend/tests/test_cli.py`

**Interfaces:**
- Consumes: `db.connect`, `SecretBox`
- Produces:
  - `@dataclass(frozen=True) class Server`: `id`, `name`, `url: str | None`, `is_local: bool`, `header_names: list[str]`, `enabled: bool`, `status: str`, `failures: int`, `last_error: str | None`, `last_seen: int | None`, `last_ts: int | None`. **Kein** Token- oder Header-Wert.
  - `ServerStore(conn: sqlite3.Connection, box: SecretBox)` mit:
    - `ensure_local(name: str) -> Server`: legt den lokalen Server (`is_local=1`, `url=None`) an, falls er fehlt
    - `add(name: str, url: str, token: str, headers: dict[str, str] | None = None) -> Server`. Validierung: `url` beginnt mit `http://` oder `https://` und hat einen Host. `name` ist nicht leer, höchstens 64 Zeichen und eindeutig. `token` ist nicht leer. Header-Namen entsprechen `^[A-Za-z0-9-]+$`, und `Authorization` ist als Zusatz-Header verboten. Fehler werfen `ValueError` mit deutscher Meldung.
    - `list() -> list[Server]`, `get(server_id: int) -> Server | None`, `delete(server_id: int) -> bool` (der lokale Server ist nicht löschbar → `ValueError`), `set_enabled(server_id, enabled) -> bool`
    - `credentials(server_id) -> tuple[str, dict[str, str]]`: entschlüsselt. Bei falschem Schlüssel `SecretError`.
    - `record_success(server_id, now: int, last_ts: int, snapshot_json: str, info_json: str | None) -> None`: `status='ok'`, `failures=0`, `last_error=NULL`
    - `record_failure(server_id, now: int, error: str, fatal: bool = False) -> str`: `failures+1`, `last_error=error`. Status wird `error`, wenn `fatal`, sonst `unreachable` ab `failures >= 3`, sonst bleibt der bisherige Status. Gibt den neuen Status zurück.
    - `reencrypt_all() -> int`: rotiert alle `token_enc`/`headers_enc` (für den Schlüsselwechsel)
  - CLI (braucht `MONI_SECRET_KEY` und `MONI_DATA_DIR`, ruft vorher `db.migrate` auf):
    - `server add --name N --url U --token-file PATH [--header NAME=WERT ...]` (Token aus Datei bzw. `-` für stdin, **nie** als Argument, damit er nicht in der Shell-History landet)
    - `server list` (ohne Geheimnisse)
    - `server remove ID`
    - `secret reencrypt` (gibt die Anzahl aus)

- [ ] **Step 1: Failing tests**

```python
@pytest.fixture
def store(tmp_path):
    p = tmp_path / "moni.db"; db.migrate(p)
    return ServerStore(db.connect(p), SecretBox(generate_key()))

def test_add_hides_secrets(store):
    s = store.add("sau", "https://moni.example.org", "tok-123", {"CF-Access-Client-Id": "id-9"})
    assert s.header_names == ["CF-Access-Client-Id"] and "tok-123" not in repr(s)
    raw = store._conn.execute("SELECT token_enc, headers_enc FROM servers WHERE id=?", (s.id,)).fetchone()
    assert "tok-123" not in raw[0] and "id-9" not in raw[1]
    assert store.credentials(s.id) == ("tok-123", {"CF-Access-Client-Id": "id-9"})

@pytest.mark.parametrize("name,url,token,headers", [
    ("", "https://a", "t", None), ("x", "ftp://a", "t", None), ("x", "https://", "t", None),
    ("x", "https://a", "", None), ("x", "https://a", "t", {"Authorization": "y"}), ("x", "https://a", "t", {"Bad Name": "y"})])
def test_add_validation(store, name, url, token, headers):
    with pytest.raises(ValueError):
        store.add(name, url, token, headers)

def test_duplicate_name(store):
    store.add("a", "https://a", "t")
    with pytest.raises(ValueError):
        store.add("a", "https://b", "t")

def test_failure_counting(store):
    s = store.add("a", "https://a", "t")
    assert [store.record_failure(s.id, 100 + i, "Timeout") for i in range(3)] == ["unknown", "unknown", "unreachable"]
    store.record_success(s.id, 200, 195, "{}", None)
    assert store.get(s.id).status == "ok" and store.get(s.id).failures == 0
    assert store.record_failure(s.id, 210, "401", fatal=True) == "error"

def test_local_server_not_deletable(store):
    loc = store.ensure_local("pi")
    assert store.ensure_local("pi").id == loc.id
    with pytest.raises(ValueError):
        store.delete(loc.id)

def test_wrong_key_credentials(tmp_path, store):
    s = store.add("a", "https://a", "t")
    other = ServerStore(store._conn, SecretBox(generate_key()))
    with pytest.raises(SecretError):
        other.credentials(s.id)

def test_cli_server_add_list_remove(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path)); monkeypatch.setenv("MONI_SECRET_KEY", generate_key())
    (tmp_path / "tok").write_text("tok-abc\n")
    assert cli.main(["server", "add", "--name", "sau", "--url", "https://m.example", "--token-file", str(tmp_path / "tok"),
                     "--header", "CF-Access-Client-Id=id1"]) == 0
    assert cli.main(["server", "list"]) == 0
    out = capsys.readouterr().out
    assert "sau" in out and "tok-abc" not in out and "id1" not in out
```

- [ ] **Step 2: Tests laufen lassen** – Expected: FAIL
- [ ] **Step 3: Implementieren**
- [ ] **Step 4: Volle Suite** – Expected: PASS
- [ ] **Step 5: Commit** `feat(manager): Server-Verwaltung mit verschluesselten Zugangsdaten und CLI`

---

### Task 6: Zeitreihen-Speicher (`moni/manager/tsdb.py`)

**Files:**
- Create: `backend/moni/manager/tsdb.py`, `backend/tests/test_tsdb.py`

**Interfaces:**
- Consumes: `metrics_model.flatten`, `timeutil.parse_iso`
- Produces:
  - `write_snapshots(conn, server_id: int, snapshots: list[dict]) -> int | None`: zerlegt jeden Snapshot mit `flatten` (ohne `moni_snapshot_timestamp_seconds`), legt fehlende `series` an und schreibt `samples_raw` mit `INSERT OR IGNORE`. Der Zeitstempel ist `int(parse_iso(snapshot["timestamp"]).timestamp())`. Gibt den größten geschriebenen Zeitstempel zurück. **Keine** eigene Transaktion, der Aufrufer bündelt.
  - `rollup(conn, now: int) -> None`: verdichtet alle **abgeschlossenen** Minuten (`ts < now // 60 * 60`) aus `samples_raw` nach `samples_1m` und alle abgeschlossenen Stunden aus `samples_1m` nach `samples_1h`. `INSERT OR REPLACE` für Buckets, die seit dem letzten Lauf Nachzügler bekommen haben. Stundenwerte entstehen aus den Minutenwerten: `min` = Minimum der `min`, `max` = Maximum der `max`, `avg` = nach `count` **gewichteter** Mittelwert, `count` = Summe. Effizient: nur Buckets ab dem Wert `meta.rollup_1m_until`/`rollup_1h_until` minus 1 h Nachlauf.
  - `prune(conn, now: int, settings: Settings) -> None`: löscht Rohdaten, Minuten- und Stundenwerte älter als die Aufbewahrung.
  - `list_series(conn, server_id) -> list[dict]` mit `{"metric": name, "labels": {k: v}}`
  - `query(conn, server_id, metric: str, start: int, end: int, resolution: str = "auto", labels: dict[str, str] | None = None) -> dict`. Rückgabe: `{"resolution": "raw|1m|1h", "series": [{"labels": {...}, "points": [[ts_iso, value]] (raw) bzw. [[ts_iso, min, avg, max]] (1m/1h)}]}`. Es werden nur Serien zurückgegeben, deren Labels die übergebenen `labels` enthalten. `start > end` oder eine unbekannte `resolution` wirft `ValueError`.
  - `pick_resolution(start: int, end: int) -> str`: ≤ 6 h → `raw`, ≤ 7 d → `1m`, sonst `1h`

- [ ] **Step 1: Failing tests** (T = 1793793600 = 2026-11-04T12:00:00Z; der Server kommt aus `ServerStore.add`)

```python
def snap(ts, cpu): return {"schema_version": 1, "timestamp": iso(datetime.fromtimestamp(ts, timezone.utc)),
                           "host": {"cpu": {"usage_percent": cpu}}, "containers": [], "docker": {"reachable": True}}

def test_write_is_idempotent(conn, sid):
    assert tsdb.write_snapshots(conn, sid, [snap(T, 10.0), snap(T + 15, 20.0)]) == T + 15
    tsdb.write_snapshots(conn, sid, [snap(T + 15, 99.0)])          # Doppelt → ignoriert
    r = tsdb.query(conn, sid, "moni_host_cpu_usage_percent", T, T + 60, "raw")
    assert r["series"][0]["points"] == [["2026-11-04T12:00:00Z", 10.0], ["2026-11-04T12:00:15Z", 20.0]]

def test_rollup_only_complete_buckets(conn, sid):
    tsdb.write_snapshots(conn, sid, [snap(T + i * 15, v) for i, v in enumerate([10.0, 20.0, 30.0, 40.0, 50.0])])
    tsdb.rollup(conn, now=T + 70)           # Minute T ist abgeschlossen, Minute T+60 nicht
    r = tsdb.query(conn, sid, "moni_host_cpu_usage_percent", T, T + 120, "1m")
    assert r["series"][0]["points"] == [["2026-11-04T12:00:00Z", 10.0, 25.0, 40.0]]
    tsdb.rollup(conn, now=T + 3600 + 5)     # Stunde T abgeschlossen
    h = tsdb.query(conn, sid, "moni_host_cpu_usage_percent", T, T + 7200, "1h")
    assert h["series"][0]["points"] == [["2026-11-04T12:00:00Z", 10.0, 30.0, 50.0]]

def test_prune(conn, sid, settings):
    tsdb.write_snapshots(conn, sid, [snap(T, 1.0), snap(T + 49 * 3600, 2.0)])
    tsdb.prune(conn, now=T + 49 * 3600, settings=settings)       # 48 h Rohdaten
    r = tsdb.query(conn, sid, "moni_host_cpu_usage_percent", T - 10, T + 50 * 3600, "raw")
    assert [p[1] for p in r["series"][0]["points"]] == [2.0]

@pytest.mark.parametrize("span,res", [(6 * 3600, "raw"), (6 * 3600 + 1, "1m"), (7 * 86400, "1m"), (7 * 86400 + 1, "1h")])
def test_pick_resolution(span, res):
    assert tsdb.pick_resolution(T, T + span) == res

def test_query_errors_and_empty(conn, sid):
    with pytest.raises(ValueError): tsdb.query(conn, sid, "x", T + 10, T)
    with pytest.raises(ValueError): tsdb.query(conn, sid, "x", T, T + 10, "5m")
    assert tsdb.query(conn, sid, "unbekannt", T, T + 10)["series"] == []

def test_label_filter(conn, sid):
    s = snap(T, 1.0); s["containers"] = [{"name": "web", "image": "i", "status": "running", "health": "none", "cpu_percent": 5.0},
                                         {"name": "db", "image": "i", "status": "running", "health": "none", "cpu_percent": 7.0}]
    tsdb.write_snapshots(conn, sid, [s])
    r = tsdb.query(conn, sid, "moni_container_cpu_usage_percent", T, T + 10, "raw", {"name": "db"})
    assert [x["labels"]["name"] for x in r["series"]] == ["db"] and r["series"][0]["points"][0][1] == 7.0
```

- [ ] **Step 2: Tests laufen lassen** – Expected: FAIL
- [ ] **Step 3: Implementieren**

Die Labels werden kanonisch als `json.dumps(list(sample.labels), separators=(",", ":"))` gespeichert. Die Serien-IDs werden pro Aufruf in einem Dict gecacht.
- [ ] **Step 4: Volle Suite** – Expected: PASS
- [ ] **Step 5: Commit** `feat(manager): Zeitreihen-Speicher mit Verdichtung, Aufbewahrung und Abfrage`

---

### Task 7: Abruf der Agents (`moni/manager/poller.py`)

**Files:**
- Create: `backend/moni/manager/poller.py`, `backend/tests/test_poller.py`

**Interfaces:**
- Consumes: `ServerStore`, `tsdb.write_snapshots|rollup|prune`, `db.connect`, `Collector`, `SecretBox`, `Settings`
- Produces:
  - `class AgentError(Exception)` mit Attributen `message: str` (deutsch, ohne Geheimnisse) und `fatal: bool` (`True` bei 401/403 und bei `schema_version != 1`)
  - `class AgentClient(url: str, token: str, headers: dict[str, str], timeout: float)` mit `get(path: str, params: dict | None = None) -> dict`. Er nutzt urllib mit `Authorization: Bearer` plus Zusatz-Headern und wirft `AgentError`: Timeout/Verbindungsfehler → `"Agent nicht erreichbar"`, 401/403 → `"Agent lehnt Token ab"` (fatal), anderer HTTP-Fehler → `"Agent antwortet mit HTTP <code>"`, kaputtes JSON → `"Ungültige Antwort vom Agent"`. Der Pfad wird an `url.rstrip("/")` angehängt.
  - `fetch_new(client: AgentClient, last_ts: int | None, now: int, interval: int) -> list[dict]`. Ist `last_ts` `None` oder gilt `now - last_ts <= 2 * interval`, wird nur `/api/v1/snapshot` geholt. Sonst kommt `/api/v1/history?since=<iso(last_ts)>` (Lückenfüllung) und danach `/snapshot`. Jeder Snapshot mit `schema_version != 1` wirft `AgentError(fatal=True, "Snapshot-Schema <n> wird nicht unterstützt")`. Snapshots mit `timestamp <= last_ts` werden verworfen.
  - `class Poller(settings: Settings, box: SecretBox, collector: Collector, client_factory=AgentClient)` mit:
    - `poll_once(now: int) -> None`: (1) Der lokale Server bekommt `collector.latest()`, falls vorhanden und neuer als `last_ts`. (2) Alle aktivierten Remote-Server werden **parallel** abgefragt (`ThreadPoolExecutor`, max. 8), jeweils mit `poll_timeout_seconds`. (3) **Eine** DB-Transaktion schreibt alle Ergebnisse (`write_snapshots` und `record_success`/`record_failure`). `SecretError` beim Entschlüsseln → `record_failure(fatal=True, "Zugangsdaten nicht entschlüsselbar")`. (4) Alle 300 s kommen `rollup` und `prune` dazu.
    - `start()` / `stop()` / `_run_one_cycle()`, gebaut wie beim `Collector`: Ausnahmen werden geloggt, der Thread stirbt nie. Er hat eine eigene DB-Verbindung, die im Thread geöffnet wird.
    - `info` (`/api/v1/info`) wird beim ersten erfolgreichen Abruf und danach alle 300 s geholt und als `agent_info` gespeichert.

- [ ] **Step 1: Failing tests** – Fake-Client über `client_factory`, der pro URL ein Skript aus Antworten bzw. `AgentError` abarbeitet. Dazu **ein** Integrationstest mit echtem Agent: `create_app(Settings(...role agent...), collector=<gemessener Collector>)` läuft per `uvicorn.Server` in einem Thread auf einem freien Port, und `AgentClient` holt `/api/v1/snapshot` mit einem echten Token und einem Zusatz-Header.

```python
def test_slow_agent_does_not_block_others(poller, servers):      # Review Focus 1
    # Fake: "a" wirft AgentError("Agent nicht erreichbar") nach Timeout, "b" liefert Snapshot
    poller.poll_once(now=T)
    assert servers.get(b).status == "ok" and servers.get(a).failures == 1

def test_three_failures_unreachable_then_recovers(poller, servers): ...
    # 3x Fehler → "unreachable"; dann Erfolg → "ok", failures 0

def test_401_is_fatal_error(poller, servers): ...                 # AgentError(fatal=True) → status "error"

def test_schema_mismatch_is_error(poller, servers): ...           # schema_version 2 → "error", nichts geschrieben

def test_gap_fill_uses_history_and_skips_known(poller, servers, conn):   # Review Focus 2
    # last_ts = T; now = T + 300 → Fake erwartet Aufruf /api/v1/history mit since=2026-11-04T12:00:00Z,
    # liefert Punkte T (bekannt) und T+15..T+285; danach /snapshot T+300 → gespeichert ohne Duplikat
    assert count_raw_points(conn, sid, "moni_host_cpu_usage_percent") == 21

def test_wrong_secret_key_marks_only_that_server(...): ...       # Review Focus 3

def test_db_error_is_logged_and_thread_survives(poller, monkeypatch, caplog): # Review Focus 4
    monkeypatch.setattr(tsdb, "write_snapshots", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    poller._run_one_cycle(); poller._run_one_cycle()
    assert "Abrufzyklus fehlgeschlagen" in caplog.text

def test_local_server_stored(poller, servers, collector_with_snapshot): ...

def test_real_agent_roundtrip(tmp_path): ...                      # Integration über HTTP, Header kommt an
```

Die mit `...` markierten Tests haben die im Kommentar stehenden Assertions. Ihre genauen Werte legt der Implementierer anhand von Fake-Skripten fest.

- [ ] **Step 2: Tests laufen lassen** – Expected: FAIL
- [ ] **Step 3: Implementieren**
- [ ] **Step 4: Volle Suite** – Expected: PASS
- [ ] **Step 5: Commit** `feat(manager): Poller mit paralleler Abfrage, Lueckenfuellung und Fehlerzaehlung`

---

### Task 8: Manager-API v1 (`moni/manager/api.py`)

**Files:**
- Create: `backend/moni/manager/api.py`, `backend/tests/test_manager_api.py`

**Interfaces:**
- Consumes: `require_scope`, `ServerStore`, `tsdb`, `db.connect` (eine Verbindung pro Anfrage über eine FastAPI-Dependency, im `finally` geschlossen), `app.state.box`
- Produces `router` (`/api/v1`):

| Methode/Pfad | Scope | Antwort |
|---|---|---|
| `GET /health` | – | `{"status": "ok"}` |
| `GET /info` | read | `{"version", "role": "manager", "api_version": "v1", "hostname", "tls", "server_count"}` |
| `GET /servers` | read | Liste: `id, name, url, is_local, enabled, status, failures, last_error, last_seen (iso\|null), header_names, warnings (Anzahl aus last_snapshot), agent_version (aus agent_info)` |
| `POST /servers` | admin | Body `{"name", "url", "token", "headers"?}` → 201 + Server; `ValueError` → 422 |
| `GET /servers/{id}` | read | wie Listeneintrag + `agent_info` |
| `PATCH /servers/{id}` | admin | Body `{"enabled": bool}` → Server |
| `DELETE /servers/{id}` | admin | 204; lokaler Server → 422 |
| `GET /servers/{id}/snapshot` | read | gespeicherter `last_snapshot`; keiner → 404 `"Noch kein Snapshot"` |
| `GET /servers/{id}/series` | read | `tsdb.list_series` |
| `GET /servers/{id}/history?metric=&from=&to=&resolution=auto&label=k:v` | read | `tsdb.query`; `from`/`to` ISO, Standard: letzte 1 h; `label` mehrfach erlaubt; Fehler → 422 |

Unbekannter Server → 404. Token-Werte und Header-Werte kommen in keiner Antwort vor.

- [ ] **Step 1: Failing tests**, u. a.:

```python
def test_read_token_cannot_write(client, read_auth):
    assert client.post("/api/v1/servers", json={"name": "a", "url": "https://a", "token": "t"}, headers=read_auth).status_code == 403

def test_create_list_never_leaks(client, admin_auth):
    r = client.post("/api/v1/servers", json={"name": "a", "url": "https://a", "token": "geheim-t",
                                             "headers": {"CF-Access-Client-Secret": "geheim-h"}}, headers=admin_auth)
    assert r.status_code == 201
    body = client.get("/api/v1/servers", headers=admin_auth).text
    assert "geheim-t" not in body and "geheim-h" not in body and "CF-Access-Client-Secret" in body

@pytest.mark.parametrize("qs", ["metric=x&from=2026-11-04T13:00:00Z&to=2026-11-04T12:00:00Z",
                                "metric=x&resolution=5m", "metric=x&from=gestern", "from=2026-11-04T12:00:00Z"])
def test_history_422(client, read_auth, local_id, qs):                    # Review Focus 5
    assert client.get(f"/api/v1/servers/{local_id}/history?{qs}", headers=read_auth).status_code == 422

def test_unknown_server_404(client, read_auth):
    assert client.get("/api/v1/servers/999/snapshot", headers=read_auth).status_code == 404

def test_local_not_deletable(client, admin_auth, local_id):
    assert client.delete(f"/api/v1/servers/{local_id}", headers=admin_auth).status_code == 422
```

- [ ] **Step 2: Tests laufen lassen** – Expected: FAIL
- [ ] **Step 3: Implementieren**
- [ ] **Step 4: Volle Suite** – Expected: PASS
- [ ] **Step 5: Commit** `feat(manager): Manager-API v1 mit Server-Verwaltung und Verlaufsabfrage`

---

### Task 9: Rolle verdrahten, Container, Compose und CI

**Files:**
- Modify: `backend/moni/app.py`, `backend/tests/test_app_roles.py` (neu)
- Modify: `Dockerfile` (Stufe `manager`), `compose.yaml` → **umbenennen** in `compose.manager.yaml` (`git mv`) und neu aufbauen, `.github/workflows/ci.yml`, `.env.example`

**Interfaces:**
- `create_app(settings=None, collector=None, poller=None)`:
  - `role == "agent"`: unverändert
  - `role == "manager"`: `db.migrate(settings.db_path)` (Fehler → Start bricht ab), `SecretBox(settings.secret_key)` (ungültig → Start bricht ab), `ServerStore.ensure_local(<hostname>)`, eigener `Collector`, `Poller`. Beide starten und stoppen im `lifespan`, sofern sie nicht als Argument übergeben wurden. Router: `moni.manager.api.router`. Kein `/metrics` und keine Agent-Router. `FastAPI(title="Moni Manager")`.
- Dockerfile `manager`: supervisor setzt `MONI_ROLE=manager`, `MONI_PORT=8001`, `MONI_DATA_DIR=/data`. `/data` gehört `app`, und `MONI_SECRET_KEY` kommt aus der Container-Umgebung (supervisord gibt ihn weiter). Der Shortcut-Kommentar aus Paket 1 entfällt. Der Healthcheck bleibt `/api/v1/health`.
- `compose.manager.yaml`:
  - Dienste `moni-manager` und `docker-socket-proxy`, aufgebaut wie `compose.agent.yaml` (Host-Mounts inkl. `/:/host/root:ro,rslave`, `HOST_*`, Härtung, Log-Rotation, eigenes internes Netz)
  - Volume `moni-manager-data:/data`
  - `MONI_SECRET_KEY=${MONI_SECRET_KEY:?MONI_SECRET_KEY muss gesetzt sein (python -m moni.cli secret generate)}`
  - Port `"${MONI_MANAGER_BIND:-127.0.0.1}:8080:80"`
  - Traefik-Labels und externe Netze gehören **nicht** hinein. Die Proxy-Varianten kommen in Paket 7 als Doku bzw. Override.
- `.env.example`: `MONI_SECRET_KEY=` (leer) mit Kommentar zur Erzeugung, die alten `MONITOR_HOST`/`TRAEFIK_ENTRYPOINT` entfallen.
- CI: zusätzlicher Smoke-Schritt für den Manager. Er erzeugt `MONI_SECRET_KEY` mit `docker run --rm moni-manager:ci python -m moni.cli secret generate`, führt `docker compose -f compose.manager.yaml up -d --build` aus und wartet auf Health. Dann erzeugt er ein Admin-Token (`exec -T moni-manager python -m moni.cli token create --name ci --scope admin`; die CLI bekommt dafür `--scope read|admin`, Standard `read`). Nach 35 s muss `GET /api/v1/servers` den lokalen Server mit `status == "ok"` zeigen, und `GET /api/v1/servers/<id>/history?metric=moni_host_memory_usage_percent` muss mindestens 2 Punkte liefern. Zum Schluss: kein Token und kein `MONI_SECRET_KEY` in `docker compose logs` (Logs in eine Datei schreiben, dann `grep -qF`), Härtungsprüfung wie beim Agent und `down -v` im `if: always()`.

- [ ] **Step 1: Failing tests** – `test_app_roles.py`: Mit `role=manager` und gültigem Schlüssel liefert `/api/v1/info` die Rolle `manager` und `/metrics` 404. Mit ungültigem Schlüssel wirft `create_app` einen `ValueError`. Die Agent-Rolle bleibt unverändert (bestehende Tests).
- [ ] **Step 2: Tests laufen lassen** – Expected: FAIL
- [ ] **Step 3: App, CLI `--scope`, Dockerfile, Compose, CI und `.env.example` anpassen**
- [ ] **Step 4: Prüfen**
  - Volle Testsuite grün
  - `docker build --target manager -t moni-manager:dev .` erfolgreich
  - `docker compose -f compose.manager.yaml config` gültig (mit gesetztem `MONI_SECRET_KEY`)
  - `ci.yml` ohne doppelte Schlüssel (Duplikat-Prüfung wie in Paket 1)
  - Lokaler Einzelcontainer-Smoke des Manager-Images (read_only, tmpfs `/tmp`, Volume `/data`, `MONI_SECRET_KEY` gesetzt): `/api/v1/health` 200, `/api/v1/servers` ohne Token 401 und mit Admin-Token 200 mit dem lokalen Server
- [ ] **Step 5: Commit** `feat(manager): Rolle manager verdrahtet, compose.manager.yaml, CI-Smoke`

---

### Task 10: Prüfung mit echtem Agent (Schritt für den Betreiber)

Optional, sobald Task 9 aus Paket 1 auf dem Pi durchgelaufen ist:

- [ ] Den Manager auf dem Pi oder Mac mit `compose.manager.yaml` starten und den SAU-INDEX-Agent per `server add` (Tunnel-URL, Agent-Token aus Datei, `--header CF-Access-Client-Id=…`, `--header CF-Access-Client-Secret=…`) koppeln.
- [ ] Nach 1 Minute zeigt `GET /api/v1/servers` beide Server mit `ok`, und `history` liefert Punkte.
- [ ] Den Agent kurz stoppen: Nach etwa 45 s steht er auf `unreachable`. Nach dem Neustart ist er wieder `ok`, und die Lücke ist aus dem Puffer gefüllt.

---

## Selbst-Review (erledigt)

- **Spec-Abdeckung:**
  - §6.1 (Rolle manager, Images, Compose): Task 9
  - §6.2 Manager-Seite (Pull 15 s, Lückenfüllung, 3 Fehlversuche, Versionierung/Info): Task 7
  - §6.3 Zusatz-Header und Token-Modus: Task 5 und Task 7. mTLS und Kopplungscode folgen in Paket 4.
  - §6.6 (SQLite, WAL, Bündelung, Verdichtung, Aufbewahrung, Auflösungswahl, Migrationen, Sicherung): Task 4 und Task 6. Die Backup-Doku folgt in Paket 7.
  - §6.7 nur Manager-API-Tokens mit `read`/`admin`: Task 1 und Task 9. Benutzer, Login und Audit folgen in Paket 3.
  - §6.10 Master-Schlüssel inkl. Wechsel per CLI: Task 2 und Task 5. HTTPS des Managers folgt in Paket 4.
- **Typen:**
  - `Server`/`ServerStore`, `tsdb.*`, `AgentClient`/`AgentError` und `Poller` werden in Task 7–9 mit denselben Signaturen genutzt.
  - Snapshot-Zeit ist überall `int` (Unix-Sekunden) in der DB und ISO in der API.
- **Review Focus:** Alle fünf Punkte haben Tests in Task 1, 2, 4, 6, 7 und 8.
- **Offen gelassen** (dem Implementierer): exaktes SQL der Verdichtung, Fake-Skript-Format in `test_poller.py`, Formatierung der CLI-Listen.
