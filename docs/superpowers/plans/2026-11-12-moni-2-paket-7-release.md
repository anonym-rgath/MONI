# Moni 2.0 – Paket 7: Release und Open-Source-Rahmen – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Moni 2.0.0 wird veröffentlichungsreif. Dazu gehören signierte Multi-Arch-Images auf GHCR mit SBOM, ein Release-Workflow, Trivy und ein End-to-End-Test in der CI, OpenAPI-Abgleich, Eigenkennzahlen und Readiness, Betriebsskripte für beide Rollen, vollständige Doku sowie die geparkten Härtungen aus den Paketen 3–6. Am Ende steht das Ausrollen beim Autor (Pi und SAU-INDEX).

**Architecture:**
- **Erst härten, dann einfrieren.** Die Tasks 1–6 ändern Code und API. Erst danach friert Task 5 die OpenAPI ein, und die Tasks 7–11 bauen den Release-Rahmen darum.
- **Ein Tag, ein Release.** Ein Tag `vX.Y.Z` auf `main` startet `.github/workflows/release.yml`. Der Workflow prüft die Versionen, baut `moni-agent` und `moni-manager` für amd64 und arm64, prüft sie mit Trivy, schiebt sie nach GHCR, signiert sie per cosign (keyless) und erzeugt das GitHub-Release mit den CHANGELOG-Notizen. SBOM und Provenance hängen als Attestierungen am Image.
- **Compose nutzt veröffentlichte Images.** `image:` zeigt auf GHCR mit fester Version. Über eine Umgebungsvariable lässt sich ein lokal gebautes Image einsetzen; die CI nutzt das.
- **Kein neuer Laufzeit-Code ohne Test.** Eigenkennzahlen, Readiness und Härtungen kommen mit Unit-Tests. Der E2E-Test läuft im bestehenden CI-Job `build`.

**Tech Stack:** wie bisher (Python 3.14, FastAPI, SQLite, Vite/React/TypeScript). Neu nur in CI und Release: `docker/setup-qemu-action`, `docker/metadata-action`, `sigstore/cosign-installer`, Trivy als Container-Image, `actionlint` lokal. Für den Socket-Proxy kommt `wollomatic/socket-proxy` hinzu (D4).

**Spec:** [`docs/superpowers/specs/2026-11-04-moni-agent-manager-design.md`](../specs/2026-11-04-moni-agent-manager-design.md) (§7 Open-Source- und Qualitätsrahmen, §8 Übergang, §6.1 Socket-Proxy, §5 amd64/arm64) · Übersicht: [`2026-11-04-moni-2-uebersicht.md`](2026-11-04-moni-2-uebersicht.md) · Vorgänger: [Paket 6](2026-11-11-moni-2-paket-6-oberflaeche.md)

## Ist-Stand (Schnittstellen, auf die dieser Plan baut)

- **Version:** `backend/moni/__init__.py` hat `__version__ = "2.0.0.dev0"`, `frontend/package.json` hat `"version": "2.0.0"`. Es gibt kein `CHANGELOG.md`, keine Tags für 2.x. Auf `main` liegt noch 1.x („linux-monitor“). Draft-PR #41 (`feature/moni-2` → `main`) trägt alle Pakete.
- **OpenAPI:** `create_app` setzt `openapi_url="/api/v1/openapi.json"` und `docs_url="/api/v1/docs"` für beide Rollen. Ein Abgleich fehlt.
- **Metriken:** Der Agent hat `GET /metrics` (`agent/api.py`, `metrics_router`, Scope read, Format aus `agent/prometheus.py`, `CONTENT_TYPE`). Der Manager hat keine Eigenkennzahlen. `servers` speichert `status`, `failures`, `last_error`, `last_seen`. Der Poller (`manager/poller.py`, `Poller.poll_once`, `_fetch_all`, `_fail`) misst keine Dauer.
- **Health:** `GET /api/v1/health` → `{"status":"ok"}` in beiden Rollen. Readiness fehlt.
- **Compose:** `compose.agent.yaml` und `compose.manager.yaml` bauen lokal (`build: target`). Beide bringen `tecnativa/docker-socket-proxy` (Digest-gepinnt) mit `CONTAINERS=1`, `POST=0` mit; der Kommentar dort verspricht eine engere Allowlist in Paket 7. Moni nutzt nur `GET /containers/json?all=true`, `GET /containers/{id}/json` und `GET /containers/{id}/stats?stream=false` (`collect/docker.py`).
- **Dockerfile:** Stage `frontend-builder` (`node:26-alpine`), `agent`, `manager`; Kopfkommentar sagt noch „Linux Monitor“.
- **CI** (`.github/workflows/ci.yml`): Jobs `backend-tests`, `frontend` (Node 24), `build` (Images bauen, Agent-, Manager-, Kopplungs-, Alarmkanal- und Browser-Smoke). Actions per Tag (`@v4`) eingebunden. CodeQL läuft als GitHub-Default-Setup (kein Workflow im Repo); offen sind nur die bewusst belassenen SSRF-Hinweise #1 (`auth/oidc.py:95`) und #3 (`manager/poller.py:102`).
- **CI-Helfer:** `scripts/ci/_moni.py` (`Client`, `login_with_recovery`, `take_recovery`, `expect`, `fail`), `pair_smoke.py`, `alert_smoke.py` (Webhook-Empfänger mit HMAC-Prüfung `verify`, nur Test-Versand), `manager_auth_smoke.py`.
- **Alarme:** Die Standardregel „Server nicht erreichbar“ (`moni_server_up < 1`, kritisch, `for_seconds 0`) gilt für alle Server. Webhook-Payload v1 (`alert_channels.payload`): `version, event (firing|changed|reminder|resolved), severity, rule, server, labels, value, threshold, reason, at, manager, text`. Ein Regel-PATCH an Bewertungsfeldern beendet offene Alarme mit `reason "regel geändert"` – kein echtes „behoben“.
- **Betriebsskripte:** `scripts/{start,stop,redeploy,smoke,diagnose,logs}.sh` arbeiten noch mit dem alten `docker compose` ohne `-f` und dem Dienst `linux-monitor`; sie sind auf dem Branch kaputt.
- **Doku:** `README.md` beschreibt noch „Linux Monitor“. `docs/` enthält nur `superpowers/`. `SECURITY.md` und `CONTRIBUTING.md` fehlen. `LICENSE` (Apache-2.0) ist vorhanden.
- **Dependabot:** Streams `pip`, `npm`, `docker` (Dockerfile). Es fehlen `github-actions` und `docker-compose`.
- **Repo:** `github.com/anonym-rgath/MONI`; GHCR-Namen daher `ghcr.io/anonym-rgath/moni-agent` und `ghcr.io/anonym-rgath/moni-manager`.

## Entscheidungen dieses Plans (vom Planer gesetzt)

- **D1 Version 2.0.0 und CHANGELOG.** `__version__ = "2.0.0"`. `CHANGELOG.md` folgt „Keep a Changelog“, Abschnitt `## [2.0.0] – <Datum des Releases>` mit Unterabschnitt **Breaking Changes**: `/api/metrics/*` entfallen; der Agent spricht standardmäßig TLS; Compose-Dateien heißen `compose.agent.yaml` / `compose.manager.yaml`; Cloudflare Access ist optional. Eine Prüfung hält `moni/__init__.py`, `frontend/package.json`, den obersten CHANGELOG-Abschnitt und die Image-Tags in beiden Compose-Dateien gleich.
- **D2 Image-Tags.** Pro Release `X.Y.Z`, `X.Y`, `X` und `latest`, nur aus Tags `vX.Y.Z` ohne Suffix. Pre-Releases (`v2.0.0-rc.1`) bekommen nur ihren eigenen Tag und kein `latest`.
- **D3 Signatur und SBOM.** cosign keyless über GitHub-OIDC, signiert wird der Digest. SBOM (SPDX) und Provenance (`mode=max`) erzeugt `docker/build-push-action` als Attestierung. Die Prüfanleitung für Nutzer steht in `docs/installation.md`:
  `cosign verify ghcr.io/anonym-rgath/moni-agent:2.0.0 --certificate-identity-regexp '^https://github.com/anonym-rgath/MONI/\.github/workflows/release\.yml@refs/tags/v' --certificate-oidc-issuer https://token.actions.githubusercontent.com`
- **D4 Socket-Proxy mit echter Allowlist.** `tecnativa/docker-socket-proxy` kann nur ganze Bereiche freigeben (`CONTAINERS=1` öffnet auch `logs`, `archive`, `export`). Beide Compose-Dateien wechseln deshalb auf `wollomatic/socket-proxy` (Digest-gepinnt) mit
  `-allowGET=^/(v[0-9.]+/)?containers/(json|[a-zA-Z0-9_.-]+/(json|stats))$`, ohne andere Methoden, `-allowfrom=<Dienstname von Moni>`, nur im internen Netz. Die Flags werden gegen die README der gepinnten Version geprüft. Wer einen zentralen Proxy hat (beim Autor PI-SETUP), nutzt ihn per Override; `docs/installation.md` zeigt das.
- **D5 Compose-Images.** `image: ${MONI_AGENT_IMAGE:-ghcr.io/anonym-rgath/moni-agent:2.0.0}` bzw. `${MONI_MANAGER_IMAGE:-ghcr.io/anonym-rgath/moni-manager:2.0.0}`; `build:` entfällt. Lokal bauen: `docker build --target agent -t moni-agent:local .` und `MONI_AGENT_IMAGE=moni-agent:local`. Die CI setzt die Variablen auf die `:ci`-Images.
- **D6 Eigenkennzahlen.** Der Manager bekommt `GET /api/v1/metrics` (Scope read, Prometheus-Textformat, `prometheus.CONTENT_TYPE`). Er liegt unter `/api/v1`, damit nginx nichts Neues braucht. Labels tragen nur den Servernamen, nie URL, Header oder Token:
  - `moni_build_info{role,version} 1` (auch im Agent-`/metrics`),
  - `moni_manager_server_up{server}` (1 bei `status == "ok"`, sonst 0),
  - `moni_manager_poll_duration_seconds{server}` (Dauer des letzten Abrufs, fehlt vor dem ersten Abruf),
  - `moni_manager_poll_errors_total{server}` (Zähler seit Prozessstart, Counter),
  - `moni_manager_poll_cycle_duration_seconds` (Dauer des letzten Zyklus),
  - `moni_manager_db_size_bytes` (Datenbankdatei plus `-wal`).
  Die Werte hält der Poller im Speicher (`PollStats`), nur die DB-Größe liest der Endpunkt selbst.
- **D7 Readiness.** `GET /api/v1/ready` ohne Anmeldung, in beiden Rollen. Agent: bereit, sobald der Sammler einen Snapshot hat. Manager: `SELECT 1` gelingt und der Poller-Thread lebt. Antwort `{"status":"ready"}` mit 200, sonst RFC 9457 mit 503 und `detail` „Noch nicht bereit“. Keine weiteren Details, weil der Endpunkt öffentlich ist.
- **D8 OpenAPI-Abgleich.** `python -m moni.cli openapi --role agent|manager` gibt die Spezifikation als JSON aus (`sort_keys=True`, `indent=2`, Zeilenende `\n`). Die Dateien `docs/api/openapi-agent.json` und `docs/api/openapi-manager.json` liegen im Repo. Ein pytest vergleicht beide mit dem erzeugten Stand und nennt bei Abweichung den Befehl zum Neuerzeugen. Damit läuft der Abgleich im CI-Job `backend-tests` mit.
- **D9 E2E-Alarm ohne Regeländerung.** Der Test löst die Standardregel „Server nicht erreichbar“ echt aus: Agent stoppen → `firing`, Agent starten → `resolved` mit `reason null`. Danach muss der Agent ohne neue Kopplung wieder Daten liefern (Kopplung überlebt den Neustart).
- **D10 Trivy ohne Drittanbieter-Action.** Trivy läuft als Container `aquasec/trivy@sha256:…` (Digest-gepinnt) mit `--severity CRITICAL,HIGH --ignore-unfixed --exit-code 1`. Ausnahmen nur über `.trivyignore`, jede mit Begründung und Ablaufdatum als Kommentar. Grund: Tags von Actions lassen sich verschieben, ein Digest nicht.
- **D11 Actions per Commit-SHA.** Alle `uses:` in `ci.yml` und `release.yml` zeigen auf einen vollen Commit-SHA mit Versionskommentar (`# v4.2.2`). Dependabot hält sie über den Stream `github-actions` aktuell.
- **D12 Node 26 überall.** Die CI wechselt von Node 24 auf 26 wie das Dockerfile (Node 26 ist seit Oktober 2026 LTS). Der Stage `frontend-builder` läuft mit `--platform=$BUILDPLATFORM`; das Ergebnis ist architekturunabhängig, das spart QEMU-Zeit.
- **D13 CodeQL bleibt Default-Setup.** Es gibt keinen eigenen Workflow. `SECURITY.md` nennt die zwei bewusst belassenen SSRF-Hinweise und warum: Admin-konfigurierte Agent-URL und OIDC-Discovery. Verwerfen kann sie nur der Autor.
- **D14 Release nur mit Zustimmung.** Merge von PR #41 nach `main` und das Setzen des Tags `v2.0.0` sind Task 12 und brauchen die ausdrückliche Zustimmung des Autors. Kein Task davor pusht Tags oder Images.

## Global Constraints

- Niemals `.env` lesen oder ausgeben. Geheimnisse nie in Logs, CI-Ausgaben, Doku-Beispielen oder Metrik-Labels.
- Lizenz Apache-2.0; nur Open-Source-Abhängigkeiten. Keine neue Laufzeit-Abhängigkeit im Backend (Prometheus-Format von Hand wie `agent/prometheus.py`).
- Texte für Menschen auf Deutsch. Im Frontend stehen sie in `src/texte/de.ts`, im Backend als feste Strings.
- Images und Actions nur mit Digest bzw. Commit-SHA. Kein `latest` ohne Digest.
- Härtung bleibt: non-root, `read_only`, `cap_drop: ALL`, `no-new-privileges`, Socket-Proxy ohne Schreibrechte, internes Netz.
- Ports in Compose binden standardmäßig nur `127.0.0.1`.
- Backend-Tests: `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests -q` grün und ohne `StarletteDeprecationWarning`. Frontend: `cd frontend && npm run lint && npm run typecheck && npm test && npm run build` grün.
- Routen-Inventartest (`test_route_inventory.py`): neue Routen nur bewusst in die Allow-Liste.
- Doku-Beispiele nutzen `example.org`, Platzhalter wie `<TOKEN>` und nie echte Hostnamen des Autors.
- Kein Push von Tags oder Images vor Task 12. Kein Merge nach `main` ohne Zustimmung.

## Review Focus

1. **Update hält Daten und Kopplung.** Ein Agent-Neustart (und ein Image-Wechsel mit demselben Volume) darf weder die Kopplung noch den Verlauf verlieren. Abgedeckt durch D9 im E2E-Test (Task 9, Step 1: nach dem Start wieder `status ok` ohne neue Kopplung, Verlauf ohne Verlust).
2. **arm64-Image startet wirklich.** QEMU-gebaute Images können an nativen Rädern scheitern (`cryptography`, `argon2-cffi`). Release-Workflow (Task 10) startet das arm64-Image vor dem Push mit `python -c "import moni.app, cryptography, argon2"`, sonst Abbruch.
3. **Falscher Tag veröffentlicht nichts.** Ein Tag, dessen Version nicht zu `__version__`, `package.json`, CHANGELOG und den Compose-Tags passt, bricht vor jedem Push ab (Task 10, `test_release_meta.py` und Schritt `check_version.py`).
4. **Socket-Proxy sperrt, Moni sieht trotzdem Container.** Nach dem Wechsel liefert `logs`/`archive`/`exec` 403, `POST` 403, aber Container-Kennzahlen bleiben `available: true` (Task 7, CI-Schritt prüft beides).
5. **Eigenkennzahlen verraten nichts.** `/api/v1/metrics` verlangt Anmeldung und enthält keine URLs, Header-Namen oder Fehlertexte (Task 4, `test_metrics_no_secrets`).

---

### Task 1: Härtung Kopplung und TLS (Nachlauf Paket 4)

**Files:**
- Modify: `backend/moni/agent/tls.py`, `backend/moni/agent/pairing_api.py`, `backend/moni/manager/pairing.py`, `backend/moni/manager/ca.py`, `backend/moni/pki.py`, `backend/moni/config.py`, `backend/moni/auth/api.py`, `backend/moni/auth/throttle.py`, `backend/moni/manager/poller.py` (nur `_renew_certs`)
- Test: `backend/tests/test_agent_tls.py`, `test_pairing.py`, `test_pki.py`, `test_manager_ca.py`, `test_config.py`, `test_throttle.py`, `test_poller.py`

**Interfaces:**
- Produces: neue Ausnahme `agent.tls.TlsError(Exception)` mit festem deutschem Text; `TlsState.ensure(now)` stellt nach einem Absturz in `install()` her; `pairing.add_server` hält eine Gesamtfrist ein (Signatur unverändert); `auth.throttle.throttle_key` (aus `auth/api.py` verschoben, Aufrufer angepasst).

Die Punkte stammen aus dem Plan zu Paket 4, Abschnitt „Geparkte Punkte → Paket 7“; dort steht der Kontext.

- [ ] **Step 1: Failing tests schreiben**
  - `test_install_crash_between_cert_and_key_recovers`: `install()` nach dem Schreiben von `cert.pem` abbrechen (Monkeypatch wirft) → neues `TlsState(...).ensure()` hat ein gültiges Paar aus `cert.pem` und `pending_key.pem`, `paired is True`.
  - `test_ensure_mismatched_pair_gives_german_hint`: nicht passendes Paar → `ensure()` wirft `TlsError` mit Text, der `python -m moni.cli pair reset` enthält, kein `ssl.SSLError`.
  - `test_pairing_total_deadline`: Agent antwortet je Schritt knapp unter dem Socket-Timeout → `add_server` bricht nach der Gesamtfrist mit `PairingError` (Status 504) ab.
  - `test_trusted_proxies_with_tls_on_warns`: `MONI_TLS=on` und `MONI_TRUSTED_PROXIES=10.0.0.1` → `Settings.from_env` loggt eine Warnung (caplog, Text enthält `MONI_TRUSTED_PROXIES` und „ignoriert“).
  - `test_san_for_host_rejects_garbage` (Leerzeichen, `*`, leere Labels, >253 Zeichen → `ValueError`), `test_issue_rejects_empty_sans`, `test_client_context_requires_cert_and_key_together`.
  - `test_write_private_mode_0600_even_with_umask_000` (`pki.write_private`).
  - `test_renew_respects_stop`: gesetztes `_stop` → `_renew_certs` kehrt vor dem ersten Agent-Aufruf zurück.
- [ ] **Step 2:** Tests laufen lassen, sie schlagen fehl.
- [ ] **Step 3: Umsetzen**
  - `install()`: `cert.pem` zuerst, `pending_key.pem` erst ganz am Ende umbenennen; `ensure()` erkennt die Zwischenzustände.
  - Gesamtfrist = `poll_timeout_seconds * 3` über alle Kopplungsschritte.
  - Toten `_OPENER` entfernen. Ruling: Die beiden heutigen `_OPENER` in `poller.py` (von `auth/oidc.py` genutzt) und `alert_channels.py` sind in Gebrauch und bleiben; gibt es keinen ungenutzten, entfällt der Punkt mit Vermerk im Bericht.
  - CA-Schlüssel im Agent einmal laden und behalten. Die doppelte `NewToken`-Validierung zusammenführen. `throttle_key` nach `auth/throttle.py`. `os.fchmod(fd, 0o600)` in `pki.write_private`.
- [ ] **Step 4:** Volle Backend-Suite grün.
- [ ] **Step 5: Commit** `fix(tls): Kopplung und Zertifikate härten (Nachlauf Paket 4)`

### Task 2: Härtung Alarmierung (Nachlauf Paket 5)

**Files:**
- Create: `backend/moni/manager/migrations/0005_alert_outbox_index.sql`
- Modify: `backend/moni/manager/alert_channels.py`, `alert_eval.py`, `alerting.py`, `alert_rules.py`, `notifier.py`, `alert_api.py`
- Test: `backend/tests/test_alert_channels.py`, `test_alerting.py`, `test_alert_rules.py`, `test_notifier.py`, `test_alert_api.py`, `test_db.py`, `test_cli.py`

**Interfaces:**
- Produces: `RuleStore.transaction()` (öffentlich, ersetzt `_tx()` in `alert_api.py`); Index `alert_outbox_event_id` auf `alert_outbox(event_id)`.

Die Punkte stammen aus dem Plan zu Paket 5, „Geparkte Punkte → Paket 7“.

- [ ] **Step 1: Failing tests schreiben**
  - `test_webhook_total_timeout`: Empfänger schickt 1 Byte pro Sekunde → Versand endet nach `timeout` (±1 s) mit dem festen Text aus `_SEND_TEXTS`, nicht erst nach Ende der Antwort.
  - `test_previous_channels_anchor_event_id`: Uhr läuft zurück (Ereignis mit kleinerem `ts`, größerer ID) → `resolved` geht nur an die Kanäle des eigenen Vorfalls.
  - `test_migration_0005_creates_index`: `PRAGMA index_list(alert_outbox)` enthält `alert_outbox_event_id`.
  - `test_snapshot_parsed_once_per_server`: zwei Regeln, ein Server → `json.loads` auf `last_snapshot` genau einmal (Spy).
  - `test_broken_meta_alerting_skips_not_stops`: kaputtes `meta.alerting` → Warnung im Log, Regeln werden mit Standardwerten ausgewertet.
  - `test_label_regex_fullmatch` (`"cpu\n"` abgewiesen), `test_unknown_func_message` (Text nennt die Funktion, nicht „Fenster…“), `test_label_filter_limits` (höchstens 5 Einträge, Werte bis 128 Zeichen → sonst 422).
  - `test_store_drops_secrets_of_other_kind`: Kanal `webhook` mit `password` → gespeichertes Geheimnis enthält nur `hmac_secret`.
  - `test_next_try_per_item`: Backoff zählt ab dem Versuch des Eintrags, nicht ab Batch-Beginn.
  - Testlücken: Seed-Test prüft alle 9 Standardregeln vollständig. Dazu ein Test für einen IntegrityError, der nicht UNIQUE ist (→ 500 bleibt aus, Fehler wird weitergereicht). Der Mute-Test mit naiver Zeit erwartet 422 mit dem Zeitzonentext. Der CLI-Test vergleicht die Ausgabe exakt.
- [ ] **Step 2:** Tests schlagen fehl.
- [ ] **Step 3:** Umsetzen. Gesamtfrist im Webhook per `time.monotonic()`-Deadline beim Lesen. SMTP-Fehlertexte für SMTPUTF8 und fehlende Auth-Methode genauer fassen, nur aus `_SEND_TEXTS`.
- [ ] **Step 4:** Volle Backend-Suite grün.
- [ ] **Step 5: Commit** `fix(alerts): Versand und Auswertung härten (Nachlauf Paket 5)`

### Task 3: Härtung Anmeldung, CLI und Poller (Nachlauf Paket 3 und 6)

**Files:**
- Modify: `backend/moni/cli.py`, `backend/moni/auth/oidc.py`, `backend/moni/auth/users.py`, `backend/moni/auth/passwords.py`, `backend/moni/manager/poller.py`, `backend/moni/manager/api.py`, `backend/tests/conftest.py` bzw. `pyproject`/`pytest.ini` (Warnung)
- Test: `backend/tests/test_cli.py`, `test_oidc.py`, `test_users.py`, `test_poller.py`, `test_manager_api.py`

**Interfaces:**
- Produces: `AgentError.status: int | None` (HTTP-Status der Agent-Antwort); `manager/api.py` erkennt Agent-Token-404 über `e.status == 404` statt über den Text.

- [ ] **Step 1: Failing tests schreiben**
  - `test_token_create_audit_failure_no_orphan`: `audit.record` wirft `sqlite3.Error` → Exit-Code 1, deutsche Meldung ohne Traceback, kein Token in der DB, kein Token auf stdout.
  - `test_user_group_refused_in_agent_role`: `MONI_ROLE=agent`, `python -m moni.cli user list` → Exit-Code 2, Text „nur in der Manager-Rolle“, keine `moni.db` angelegt.
  - `test_oidc_discovery_failure_cached`: Discovery wirft zweimal hintereinander → nur ein Netzaufruf innerhalb von 30 s, dann wieder.
  - `test_argon2_outside_write_lock`: `hash_password` läuft, bevor `BEGIN IMMEDIATE` ausgeführt wird (Reihenfolge per Spy).
  - `test_agent_token_404_via_status`: Agent antwortet 404 mit beliebigem Text → API liefert 404.
- [ ] **Step 2:** Tests schlagen fehl.
- [ ] **Step 3:** Umsetzen. Aufräumen: doppelte Imports, toter `except`-Zweig und Grenzwert-Tests laut Plan 3. Die `StarletteDeprecationWarning` an der Ursache beheben, nicht per Filter.
- [ ] **Step 4:** `pytest -q -W error::DeprecationWarning` grün.
- [ ] **Step 5: Commit** `fix(auth,cli): Nachlauf Paket 3 und 6`

### Task 4: Eigenkennzahlen und Readiness

**Files:**
- Create: `backend/moni/manager/selfmetrics.py`
- Modify: `backend/moni/manager/poller.py`, `backend/moni/manager/api.py`, `backend/moni/agent/api.py`, `backend/moni/agent/prometheus.py`, `backend/moni/collector.py` (falls nötig für „hat Snapshot“), `backend/tests/test_route_inventory.py`
- Test: `backend/tests/test_selfmetrics.py`, `test_agent_api.py`, `test_manager_api.py`

**Interfaces:**
- Produces:
  - `PollStats` mit `record(server: str, seconds: float, ok: bool)`, `cycle(seconds: float)` und `snapshot() -> dict`; liegt als `app.state.poll_stats` vor.
  - `selfmetrics.render(stats: dict, servers: list[Server], db_path: Path, version: str) -> str`.
  - Routen `GET /api/v1/metrics` (Manager, read) und `GET /api/v1/ready` (beide Rollen, ohne Anmeldung).

- [ ] **Step 1: Failing tests schreiben**
  - `test_metrics_requires_auth`: ohne Token 401.
  - `test_metrics_content`: zwei Server, ein Fehler → Ausgabe enthält `moni_build_info{role="manager",version="…"} 1`, `moni_manager_server_up{server="lokal"} 1`, `moni_manager_poll_errors_total{server="b"} 1`, `# TYPE moni_manager_poll_errors_total counter`, `moni_manager_db_size_bytes` > 0. Content-Type wie `prometheus.CONTENT_TYPE`.
  - `test_metrics_no_secrets`: Server mit URL `https://geheim.example.org`, Header `X-Auth` und `last_error` „Boom“ → keiner der drei Strings in der Ausgabe.
  - `test_metrics_label_escaping`: Servername mit `"` und `\` wird nach Prometheus-Regeln maskiert.
  - `test_ready_manager_ok` (200, `{"status":"ready"}`) und `test_ready_manager_poller_dead` (503, `detail` „Noch nicht bereit“).
  - `test_ready_agent_before_first_snapshot` (503) und danach (200).
  - `test_agent_metrics_build_info`.
- [ ] **Step 2:** Tests schlagen fehl.
- [ ] **Step 3:** Umsetzen. Die Dauer misst der Poller um `_fetch` mit `time.monotonic()`. Die Formatfunktionen übernimmt er aus `agent/prometheus.py`, ohne Kopie. Beide Routen kommen bewusst in die Allow-Liste des Inventartests.
- [ ] **Step 4:** Volle Backend-Suite grün.
- [ ] **Step 5: Commit** `feat(ops): Eigenkennzahlen und Readiness`

### Task 5: OpenAPI-Abgleich

**Files:**
- Modify: `backend/moni/cli.py`
- Create: `docs/api/openapi-agent.json`, `docs/api/openapi-manager.json`, `backend/tests/test_openapi_snapshot.py`

**Interfaces:**
- Consumes: alle Routen nach den Tasks 1–4.
- Produces: `python -m moni.cli openapi --role agent|manager` (stdout, ohne Datenverzeichnis und ohne Netz).

- [ ] **Step 1: Failing test** `test_openapi_matches_committed[agent|manager]`: erzeugte JSON == Datei; Fehlermeldung enthält `python -m moni.cli openapi --role <rolle> > docs/api/openapi-<rolle>.json`. Dazu `test_openapi_has_no_example_secrets`: kein String der Form `moni_[A-Za-z0-9]{20,}` in den Dateien.
- [ ] **Step 2:** Test schlägt fehl (Dateien fehlen).
- [ ] **Step 3:** CLI-Befehl umsetzen. Er baut die App ohne Lifespan, also ohne Poller und DB. Dateien erzeugen und committen.
- [ ] **Step 4:** Suite grün. Gegenprobe: eine Route-Beschreibung ändern → Test rot, zurücksetzen.
- [ ] **Step 5: Commit** `feat(api): OpenAPI-Abgleich in den Tests`

### Task 6: Frontend aufräumen (Nachlauf Paket 6)

**Files:**
- Create: `frontend/src/ui/Meldungen.tsx` (zusammengeführte Helfer für Rückfrage, Fehlertext, Statusmeldung)
- Modify: `frontend/src/api/client.ts`, `frontend/src/auth/Sitzung.tsx`, `frontend/src/auth/Waechter.tsx`, `frontend/src/seiten/alarme/*`, `frontend/src/seiten/server/*`, `frontend/src/seiten/verwaltung/*`, `frontend/src/texte/de.ts`, `frontend/package.json`/`package-lock.json` (nur Dev-Kette für den Audit-Fund)
- Test: Vitest-Dateien neben den geänderten Komponenten

**Interfaces:**
- Produces: `api(pfad, { signal })` reicht `AbortSignal` an `fetch` durch; `abmelden()` bricht nach 10 s ab und meldet trotzdem lokal ab.

- [ ] **Step 1: Failing tests schreiben**
  - `abmelden während laufender Abfrage zeigt keine „Sitzung abgelaufen“-Meldung`.
  - `401 auf den Logout-Aufruf selbst endet abgemeldet ohne Fehlerseite`.
  - `abmelden hängt → nach 10 s lokal abgemeldet` (Fake-Timer).
  - `Keine Berechtigung setzt document.title` auf den Text aus `de.ts`.
  - `Kanal-PATCH sendet nur geänderte Felder` (MSW prüft den Body).
  - `Fehler beim Laden der Erinnerungs-Einstellung wird angezeigt` (Live-Region).
  - Serverdetail: `Spitze stammt aus dem Maximum`, `Zusammenfassung bleibt, wenn eine Reihe ausfällt`, `last_error erscheint einmal`, `Legende zeigt Strichmuster`.
- [ ] **Step 2:** Tests schlagen fehl.
- [ ] **Step 3:** Umsetzen. Bei `Innen` die Kopplung über `useLocation` an den Ablauf nach dem Login lösen. Die drei Helferfamilien nach `ui/Meldungen.tsx` zusammenführen, Aufrufer anpassen. Den `npm audit`-Fund (minimatch, nur Dev) per Update oder `overrides` beheben.
- [ ] **Step 4:** `npm run lint && npm run typecheck && npm test && npm run build` grün; `npm audit --omit=dev` ohne Fund.
- [ ] **Step 5: Commit** `refactor(frontend): Nachlauf Paket 6`

### Task 7: Socket-Proxy mit Allowlist, Compose auf veröffentlichte Images

**Files:**
- Modify: `compose.agent.yaml`, `compose.manager.yaml`, `Dockerfile`, `.github/workflows/ci.yml` (nur Image-Variablen und neue Proxy-Prüfung), `scripts/ci/README` falls vorhanden
- Test: CI-Schritt „Socket-Proxy sperrt“ im Job `build`

**Interfaces:**
- Produces: Umgebungsvariablen `MONI_AGENT_IMAGE`, `MONI_MANAGER_IMAGE` (D5); Proxy-Dienstname bleibt `docker-socket-proxy`, `DOCKER_API_BASE` bleibt `http://docker-socket-proxy:2375`.

- [ ] **Step 1: CI-Prüfung zuerst** (neuer Schritt nach „Agent smoke test“). Aus dem Agent-Container per Python-`urllib` gegen `DOCKER_API_BASE`:
  - `GET /containers/json` → 200;
  - `GET /containers/<id>/logs?stdout=1` → 403;
  - `POST /containers/<id>/stop` → 403 (bzw. 405);
  - `GET /images/json` → 403.
  Außerdem enthält `snap.json` Container mit `available: true`. Lokal gegen den alten Proxy: Die `logs`-Prüfung schlägt fehl.
- [ ] **Step 2: Umsetzen.** Proxy nach D4 in beiden Compose-Dateien: Digest pinnen, Flags gegen die README der Version prüfen, `read_only`, `cap_drop: ALL`, kein Port nach außen. `image:` nach D5, `build:` entfernen. Dockerfile: `FROM --platform=$BUILDPLATFORM` für `frontend-builder`, Kopfkommentar „Moni“. CI: `MONI_AGENT_IMAGE=moni-agent:ci` und `MONI_MANAGER_IMAGE=moni-manager:ci` als Job-`env`.
- [ ] **Step 3: Lokal prüfen.** Beide Images bauen und beide Stacks mit den Variablen starten. `docker compose -f compose.agent.yaml config` ohne Warnung; die Prüfung aus Step 1 grün.
- [ ] **Step 4: Commit** `feat(compose): Socket-Proxy mit Allowlist, Images aus GHCR`

### Task 8: Betriebsskripte für beide Rollen

**Files:**
- Modify: `scripts/start.sh`, `stop.sh`, `redeploy.sh`, `smoke.sh`, `diagnose.sh`, `logs.sh`
- Create: `scripts/_rolle.sh` (gemeinsame Auswahl von Rolle und Compose-Datei)
- Test: `scripts/selftest.sh` (läuft in der CI im Job `build`)

**Interfaces:**
- Produces: Aufruf `scripts/<name>.sh agent|manager [Optionen]`. Ohne oder mit falscher Rolle: Exit 2 und Hilfe. `redeploy.sh` zieht das Image (`pull`), es baut nicht; `--local` nutzt `MONI_*_IMAGE`. `smoke.sh` prüft Health, Readiness (D7) und die Härtung des laufenden Containers: User `app`, `ReadonlyRootfs true`, `CapDrop [ALL]`, `no-new-privileges`, Proxy ohne Port. `diagnose.sh` gibt Version, `ready`, Status der Dienste und die letzten 50 Logzeilen aus und maskiert Zeilen mit `token|secret|password|code=`.

- [ ] **Step 1: Selbsttest schreiben** `scripts/selftest.sh`: `start.sh` ohne Rolle → Exit 2; `start.sh foo` → Exit 2; `smoke.sh agent` gegen den laufenden CI-Agent → Exit 0; `diagnose.sh agent | grep -E 'moni_[A-Za-z0-9]{20,}'` findet nichts.
- [ ] **Step 2:** Selbsttest schlägt fehl.
- [ ] **Step 3:** Skripte umschreiben, alte Bezüge auf `linux-monitor` und Traefik-Labels entfernen. `shellcheck` sauber.
- [ ] **Step 4:** Selbsttest lokal grün, als CI-Schritt nach dem Proxy-Check eintragen.
- [ ] **Step 5: Commit** `feat(scripts): Betriebsskripte für Agent und Manager`

### Task 9: CI – E2E-Alarm, Trivy, gepinnte Actions, Dependabot

**Files:**
- Create: `scripts/ci/e2e_alert.py`, `.trivyignore`
- Modify: `.github/workflows/ci.yml`, `.github/dependabot.yml`, `scripts/ci/pair_smoke.py`, `scripts/ci/_moni.py`

**Interfaces:**
- Consumes: `_moni.Client`, `login_with_recovery`, `take_recovery`; `alert_smoke.verify` (HMAC-Prüfung, in `_moni.py` verschieben und von beiden nutzen).
- Produces: `e2e_alert.py MANAGER_URL STATE_FILE LISTEN_PORT AGENT_NAME --stop-cmd CMD --start-cmd CMD` mit `--selftest`. Die Befehle laufen per `subprocess.run(shlex.split(cmd), check=True)` ohne Shell.

- [ ] **Step 1: E2E-Skript mit Selbsttest schreiben.** Ablauf:
  1. Webhook-Kanal (`min_severity warning`) anlegen.
  2. Warten, bis der gekoppelte Agent `status ok` hat und `history?metric=moni_host_memory_usage_percent` mindestens 2 Punkte liefert (Daten fließen; Frist 90 s).
  3. `--stop-cmd` ausführen (in der CI `docker compose -f compose.agent.yaml stop moni-agent`). Warten auf einen Webhook mit `event "firing"`, `rule "Server nicht erreichbar"`, `server AGENT_NAME` und gültiger HMAC (Frist 120 s).
  4. `--start-cmd` ausführen. Warten auf `event "resolved"` mit `reason null` und danach auf `status ok` ohne erneute Kopplung (Frist 120 s); die Zahl der Verlaufspunkte ist danach nicht kleiner als vor dem Stopp.
  5. Zum Schluss prüft es, dass `GET /alerts/history?server_id=…` beide Ereignisse enthält.
  Bei jedem Fehlschlag wird `--start-cmd` trotzdem ausgeführt, damit die Aufräumschritte der CI einen laufenden Agent vorfinden. `--selftest` prüft die Ereignis-Erkennung mit erfundenen Payloads.
- [ ] **Step 2: CI anpassen**
  - Neuer Schritt „E2E Alarm“ nach „Alarmkanal“. Manager mit `MONI_COLLECT_INTERVAL=5` starten (nur CI).
  - Nachlauf Paket 3/4: Die `recovery`-Prüfung im Log sucht nach den echten Codes aus `$RUNNER_TEMP/ci-recovery`, nicht nach dem Wort. Vor der Prüfung „Code nicht erneut geloggt“ startet der Agent neu. `pair_smoke.py` bekommt getrennte Fristen je Schritt. Die Ziffern-Regex erfasst auch Codes in URLs.
  - Neuer Schritt „Trivy“ nach dem Image-Build: `docker run --rm -v /var/run/docker.sock:/var/run/docker.sock:ro aquasec/trivy@sha256:<digest> image --severity CRITICAL,HIGH --ignore-unfixed --exit-code 1 moni-agent:ci` (dito Manager). Dazu `.trivyignore` mit Kopfkommentar zur Regel aus D10.
  - Alle `uses:` auf Commit-SHA mit Versionskommentar (D11). Node 26 (D12). Den Kopfkommentar des Workflows um E2E und Trivy ergänzen.
  - `dependabot.yml`: Streams `github-actions` (`/`) und `docker-compose` (`/`), wöchentlich montags, Präfix `deps(ci)` bzw. `deps(compose)`. Kopfkommentar ohne ASCII-Umschrift.
- [ ] **Step 3:** `actionlint .github/workflows/ci.yml` sauber; `python3 scripts/ci/e2e_alert.py --selftest` grün. Den E2E-Ablauf lokal gegen beide Stacks einmal durchspielen.
- [ ] **Step 4: Commit** `ci: E2E-Alarm, Trivy, Actions per SHA, Dependabot für Actions und Compose`

### Task 10: Release-Workflow, Versionsprüfung, CHANGELOG

**Files:**
- Create: `.github/workflows/release.yml`, `scripts/ci/check_version.py`, `CHANGELOG.md`, `backend/tests/test_release_meta.py`
- Modify: `backend/moni/__init__.py` (`2.0.0`)

**Interfaces:**
- Produces: `check_version.py [TAG]` → Exit 0, wenn alle Quellen aus D1 dieselbe Version tragen und (falls angegeben) `TAG == "v" + version`; sonst Exit 1 mit der Liste der Abweichungen.

- [ ] **Step 1: Failing tests schreiben**
  - `test_versions_agree`: ruft `check_version.main([])` auf → 0.
  - `test_tag_mismatch_fails`: `main(["v2.0.1"])` → 1, Ausgabe nennt `moni/__init__.py`.
  - `test_changelog_top_section_is_version`: erster `## [`-Abschnitt ist `[2.0.0]` und enthält `### Breaking Changes` mit `/api/metrics`.
  - `test_compose_images_pinned_to_version`: beide Compose-Dateien enthalten `:2.0.0}` im Image-Default.
- [ ] **Step 2:** Tests schlagen fehl.
- [ ] **Step 3: Umsetzen.**
  - `CHANGELOG.md` nach D1 mit allen Paketen in Kurzform, Datum als `unveröffentlicht`. Task 12 setzt es beim Tag.
  - `release.yml`:
    - **Auslöser und Rechte:** `on: push: tags: ['v*.*.*']` und als Trockenlauf `pull_request` mit `paths: [.github/workflows/release.yml, Dockerfile, backend/requirements-docker.txt]`. `workflow_dispatch` geht erst, wenn die Datei auf `main` liegt, und taugt daher nicht als Probe vor dem Merge. Trockenlauf heißt: nur bauen und prüfen, kein Login, kein Push, keine Signatur, kein Release (`if: startsWith(github.ref, 'refs/tags/v')`). Rechte auf Workflow-Ebene `contents: read`. Erst der Job `publish`, nur bei Tags, bekommt `contents: write`, `packages: write`, `id-token: write` und `attestations: write`.
    - **Schritte:** checkout → `check_version.py "$GITHUB_REF_NAME"` (nur bei Tag) → QEMU → Buildx → je Target `agent`/`manager`:
      1. Build für `linux/amd64,linux/arm64` mit `sbom: true` und `provenance: mode=max`.
      2. Im Trockenlauf nur bauen; bei einem Tag mit GHCR-Login `push`.
      3. Tags über `docker/metadata-action` nach D2.
    - **Vor dem Push:** Smoke je Plattform (`docker run --platform linux/arm64 … python -c "import moni.app, cryptography, argon2"`) und Trivy wie in Task 9.
    - **Nach dem Push:** `cosign sign --yes <image>@<digest>`, dann `cosign verify` mit der Identität aus D3 als Selbstprüfung. Zuletzt `gh release create "$GITHUB_REF_NAME" --notes-file <CHANGELOG-Abschnitt>`, dabei `--prerelease` bei Suffix.
    - **Gleichzeitigkeit:** `concurrency: release-${{ github.ref }}`, kein `cancel-in-progress`.
- [ ] **Step 4: Prüfen.** `actionlint` sauber und Suite grün. Mit dem Push auf den PR-Branch (etablierter Draft-PR-Ablauf) läuft der Trockenlauf im PR #41: Beide Plattformen müssen bauen, Smoke und Trivy bestehen, und die Schritte für Push, Signatur und Release müssen als übersprungen erscheinen.
- [ ] **Step 5: Commit** `feat(release): Multi-Arch-Release mit cosign, SBOM und Versionsprüfung`

### Task 11: Doku, README, SECURITY.md, CONTRIBUTING.md

**Files:**
- Create:
  - `docs/installation.md`, `docs/kopplung.md`, `docs/proxy.md`, `docs/api.md`;
  - `docs/alarme.md`, `docs/backup.md`, `docs/upgrade.md`, `docs/betrieb.md`, `docs/release.md`;
  - `SECURITY.md`, `CONTRIBUTING.md`, `scripts/ci/check_docs.py`.
- Modify: `README.md`, `.github/workflows/ci.yml` (Schritt `check_docs.py` im Job `backend-tests`)

**Interfaces:**
- Produces: `check_docs.py` → Exit 0, wenn (a) jeder relative Link in `README.md`, `SECURITY.md`, `CONTRIBUTING.md` und `docs/*.md` auf eine vorhandene Datei/Überschrift zeigt und (b) jede Variable `MONI_*`, die in `compose.*.yaml` oder `backend/moni/config.py` vorkommt, in `docs/installation.md` steht.

- [ ] **Step 1: Prüfskript mit Test schreiben** (`backend/tests/test_check_docs.py`): Sie schlagen fehl bei einem kaputten Link und bei einer fehlenden Variable und laufen auf dem echten Repo nach Step 3 grün.
- [ ] **Step 2: Inhalte** (Deutsch, Beispiele mit `example.org`, Platzhaltern):
  - `README.md`: Was Moni ist, Rollen-Bild, Schnellstart (Manager in 5 Befehlen, Agent koppeln), Links in die Doku, Badges (CI, Lizenz). Den alten „Linux Monitor“-Inhalt ersetzen.
  - `installation.md`: Voraussetzungen (Docker, amd64/arm64), Master-Schlüssel erzeugen und außerhalb des Volumes halten, Variablen-Tabelle aller `MONI_*`, Signatur prüfen (D3), Socket-Proxy und Override für einen zentralen Proxy (D4), lokal bauen (D5). Hinweise: `/:/host/root:ro,rslave` geht nicht mit Docker Desktop auf macOS; Erstpasswörter sicher übergeben.
  - `kopplung.md`: Kopplungscode, CSR-Ablauf, `pair reset`, Zertifikatserneuerung, abgelaufenes Zertifikat heißt neu koppeln, CA nur gezielt per `--cacert` nutzen, keine CA-Rotation in 2.0 und wie man notfalls neu aufsetzt. Token-Modus für Agents ohne mTLS.
  - `proxy.md`: `MONI_TLS=off` mit `MONI_TRUSTED_PROXIES`, Warnung bei `MONI_TLS=on`, feste nginx-Zertifikatspfade `/data/tls`. Beispiele Traefik und Cloudflare Tunnel (Agent ohne offenen Port). OIDC braucht eine echte Domain.
  - `api.md`: Tokens und Scopes; API-Tokens sind nicht an Benutzer gebunden (Deaktivieren eines Admins sperrt seine Tokens nicht). Dazu Fehlerformat RFC 9457, Links auf `docs/api/openapi-*.json` und `/api/v1/docs`, `/metrics` (Agent), `/api/v1/metrics` und `/api/v1/ready` (Manager) mit Prometheus-Beispiel.
  - `alarme.md`: Regeln, Standardregeln, Zustände, Erinnerung, Stummschalten, Webhook-Payload v1 mit HMAC-Prüfbeispiel (Python), SMTP.
  - `backup.md`: automatische Sicherung vor Migrationen, manuelles Backup der SQLite (`sqlite3 .backup` bzw. Volume-Kopie bei gestopptem Container), Master-Schlüssel getrennt sichern, Wiederherstellen.
  - `upgrade.md`: von 1.x (linux-monitor) auf 2.0: kein Datenübernahme-Pfad, neue Compose-Dateien, Agent jetzt mit TLS, `/api/metrics/*` entfallen. Innerhalb 2.x: Image-Tag anheben, Migrationen laufen automatisch.
  - `betrieb.md`: Skripte aus Task 8, Logs (JSON, ohne Geheimnisse), Admin-Reset per CLI, Eigenkennzahlen.
  - `release.md` (für Maintainer): Ablauf aus Task 10, Trockenlauf im PR. Einmalige GitHub-Einrichtung: GHCR-Pakete auf „public“, Branch-Schutz für `main`, Tag-Schutz `v*`, Private Vulnerability Reporting einschalten.
  - `SECURITY.md`: unterstützte Versionen, Meldeweg über GitHub Private Vulnerability Reporting, Reaktionszeit, Umfang. Bekannte, bewusst belassene CodeQL-Hinweise (D13).
  - `CONTRIBUTING.md`: Entwicklung lokal (uv, npm), Tests, Stil, Commit-Präfixe, OpenAPI neu erzeugen (D8), Texte zentral, Sicherheitsregeln (keine Geheimnisse in Logs).
- [ ] **Step 3:** `python3 scripts/ci/check_docs.py` grün; CI-Schritt eintragen.
- [ ] **Step 4: Commit** `docs: Installation, Kopplung, Proxy, API, Alarme, Backup, Upgrade, Security`

### Task 12 (Betreiber, mit Zustimmung): Release veröffentlichen und beim Autor ausrollen

Dieser Task ändert `main`, veröffentlicht Images und betrifft die Produktivsysteme des Autors. Jeder Schritt braucht seine ausdrückliche Zustimmung (D14). Die Einrichtungsschritte werden laufend in `docs/release.md` (allgemein) festgehalten. Hostnamen oder Geheimnisse des Autors kommen nicht ins Repo.

- [ ] **Step 1: Probelauf.** Zuerst `v2.0.0-rc.1` nach dem Ablauf in `docs/release.md` („Probelauf“) veröffentlichen: alle fünf Versionsquellen und die OpenAPI-Dateien auf `2.0.0-rc.1`, Tag setzen, danach `cosign verify`, Tags (nur `2.0.0-rc.1`, kein `latest`) und Pre-release-Markierung prüfen, alles auf `2.0.0` zurücksetzen und ungetaggte rc-Digests in GHCR aufräumen. Tags sind geschützt: Ein Fehler im Publish-Pfad darf nicht erst bei `v2.0.0` auffallen.
- [ ] **Step 2:** PR #41 aus dem Draft-Status nehmen, CI grün. Der Autor verwirft die CodeQL-Hinweise #1 und #3 („Won't fix“) und merged nach `main`.
- [ ] **Step 3:** `CHANGELOG.md`: Datum für `[2.0.0]` setzen (Commit auf `main` über PR oder direkt, nach Wahl des Autors).
- [ ] **Step 4:** Tag `v2.0.0` auf `main` setzen und pushen → `release.yml` läuft. Danach GHCR-Pakete auf „public“ stellen. Abschließend `cosign verify` nach D3 von einem fremden Rechner ausführen.
- [ ] **Step 5: Pi (Manager).** Alten `linux-monitor` stoppen (Volume behalten, bis 2.0 läuft). `compose.manager.yaml` mit Override für Traefik/zentralen Socket-Proxy (PI-SETUP), Master-Schlüssel als Secret. Ersteinrichtung mit 2FA, `scripts/smoke.sh manager`.
- [ ] **Step 6: SAU-INDEX (Agent).** `compose.agent.yaml` über den bestehenden Cloudflare Tunnel, kein offener Port. Kopplung per Code in der Oberfläche, `scripts/smoke.sh agent`. Im Manager Daten und Standardalarme prüfen.
- [ ] **Step 7:** Echte Alarmkanäle (Webhook/SMTP) testen. Die offenen Betreiber-Schritte der Pakete 1–6 abhaken oder bewusst verwerfen. Übersicht auf „Paket 7 umgesetzt“ setzen.

## Nachlauf aus der Umsetzung (2026-11-12)

Umgesetzt sind die Tasks 1–11, jeder mit eigenem Review, dazu ein Abschlussreview mit einer Fixwelle (Commits `a9ac8d1` bis `d524e39`). Task 12 liegt beim Betreiber.

### Rulings

- **P7-R1:** Trivy läuft nur über `scripts/ci/trivy.sh` (eine Quelle für Digest und Flags), in CI und Release. Mit Plattform-Argument scannt es einen `docker save --platform`-Export, weil Trivy `--platform` bei Daemon-Images mit containerd-Store ignoriert.
- **P7-R2:** Die CI startet den Manager mit `MONI_COLLECT_INTERVAL=5`.
- **P7-R3:** Die Starlette-Warnung behebt der Wechsel der Testabhängigkeit von `httpx` auf `httpx2==2.16.0` (vom Autor freigegeben). Die CI läuft mit `-W error::DeprecationWarning`.
- **P7-R4:** Die Webhook-Gesamtfrist umfasst TCP-Connect, TLS-Handshake, Antwortköpfe und Rumpf. Nur die DNS-Auflösung bleibt außerhalb (im Code vermerkt).
- **P7-R5:** `PollStats` wird nur im Haupt-Thread geschrieben. Fehler zählt `_fail`, deaktivierte Server erscheinen nicht in den Kennzahlen.
- **P7-R6:** `info.version` bleibt in `docs/api/openapi-*.json`. Bei jedem Versionssprung werden die Dateien neu erzeugt.
- **P7-R7:** Weiterleitungen nach der Anmeldung kommen nur vom Wächter (kein Timer). Bestätigt der Server das Abmelden nicht, zeigt die Anmeldeseite einen Hinweis, dass die Server-Sitzung bis zum Ablauf weiterlebt.
- **P7-R8:** Dev-Abhängigkeiten nur in Versionen, die mindestens 14 Tage alt sind (`@isaacs/nonrecursive-glob-match` 1.0.3, `brace-expansion` 5.0.12 per `overrides`).
- **P7-R9:** `DOCKER_GID` ist Pflicht in beiden Compose-Dateien. Die Skripte setzen es automatisch (Linux per `stat`, macOS 0).
- **P7-R10:** Socket-Proxy `wollomatic/socket-proxy` 1.13.2 mit Allowlist `^/(v[0-9.]+/)?containers/(json|[a-zA-Z0-9][a-zA-Z0-9_.-]*/(json|stats))$`. Die CI prüft Pfadtricks und fremde Clients (strikt 403). `-allowfrom` wird je Anfrage aufgelöst.
- **P7-R11:** Die Skripte lesen Schema und Port vom laufenden Container (`docker compose port`, nur `MONI_TLS` aus dem Container-Env), nicht aus `.env`. `diagnose.sh` maskiert auch inhaltsbasiert.
- **P7-R12:** Die Laufzeit-Images haben kein pip und kein setuptools mehr, zlib1g wird aktualisiert. `.trivyignore` ist leer.
- **P7-R13:** Der Punkt „Ziffern-Regex soll auch URLs erfassen“ aus Paket 4 entfällt, er ist nicht zuordenbar.
- **P7-R14:** Der Release-Ablauf ist: Push nur per Digest, dann je Plattform Pull, Architekturprüfung, Smoke und Trivy, dann cosign sign und verify, zuletzt Tags per `imagetools`. Rechte: `publish` hat `contents: read`, `packages: write`, `id-token: write`; `release` hat nur `contents: write`.
- **P7-R15:** Der Smoke importiert `moni.app` und `moni.auth.passwords` (es gibt kein Paket `argon2`).
- **P7-R16:** `check_version.py TAG` lehnt eine CHANGELOG-Überschrift „unveröffentlicht“ ab und prüft auch `package-lock.json`.
- **P7-R17:** Die Projektnamen stehen fest in den Compose-Dateien (`moni-agent`, `moni-manager`).
- **P7-R18:** Vor `v2.0.0` steht ein Probelauf mit `v2.0.0-rc.1` (Task 12, Step 1; Ablauf in `docs/release.md`).

### Geparkte Punkte

- **Vor dem Release prüfen:**
  - `docs/backup.md`: `umask 077` auf dem Host wirkt nicht auf den Container-Prozess, das Archiv entsteht vermutlich mit 0644. Nach dem Lauf `chmod 600` auf dem Host ergänzen.
  - `SECURITY.md`: Reaktionszeit („binnen 7 Tagen“) und „nur die neueste 2.x“ sind Annahmen und vom Autor zu bestätigen.
  - Ob cosign 2.x die v3-Signaturen findet, ist unbestätigt; die Doku sagt „getestet mit cosign 3.x“.
- **Später (Auswahl, vollständig im Ledger der Umsetzung):**
  - Die Kopplungsfrist kann um einen Socket-Timeout überziehen.
  - `TlsState.install()` prüft nicht selbst, ob der Agent schon gekoppelt ist.
  - `store_password`-Race: 204 und Audit-Eintrag für einen gerade gelöschten Benutzer.
  - Die Ersteinrichtung hasht im Schreib-Lock.
  - Der `zlib1g`-Upgrade-Schritt entfällt, sobald der Basis-Digest den Fix enthält.
  - Dependabot erkennt `${VAR:-image}` in Compose evtl. nicht.
  - `redeploy --local`, `start.sh` und Selftest haben kleinere Annahmen (Loopback-Bind, keine Skalierung).
  - Transitive Python-Abhängigkeiten sind nicht gepinnt, die CI kann dadurch ohne Repo-Änderung rot werden.
