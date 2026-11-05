# Moni 2.0 – Paket 5: Alarmierung – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Manager wertet Alarmregeln (Metrik, Bedingung, Schwelle, Dauer) für alle oder einzelne Server aus. Er kennt die Zustände „ausgelöst“ und „behoben“, erinnert ohne Flut, kann Server stummschalten und benachrichtigt per Webhook (optional HMAC-signiert) und E-Mail (SMTP mit STARTTLS oder TLS). Jeder Kanal hat einen Test-Versand. Der Alarmverlauf ist gespeichert, und alles läuft über die API, ohne Oberfläche.

**Architecture:**
- **Ein einheitliches Regelmodell.** Jede Regel ist eine Metrikregel über die Zahlenreihen, die `metrics_model.flatten` aus dem jüngsten Snapshot erzeugt, ergänzt um die synthetische Metrik `moni_server_up`. Sie hat optional eine Warn- und eine Kritisch-Schwelle, eine Dauer und eine Gruppierung je Label, zum Beispiel je Dateisystem oder je Container.
- **Ablauf je Poller-Zyklus.** Nach jedem Commit des Zyklus läuft eine kurze, eigene DB-Transaktion. Darin berechnet ein reiner Auswerter (`manager/alert_eval.py`) die Zustandswechsel. Diese Wechsel landen als Ereignisse im Verlauf und als Einträge in einer **dauerhaften Outbox**.
- **Versand.** Ein eigener Benachrichtigungs-Thread (`manager/notifier.py`) arbeitet die Outbox mit Wiederholungen ab, ohne DB-Sperre während des Netzwerkzugriffs.
- **Kanäle und Geheimnisse.** Kanäle (`manager/alert_channels.py`) speichern ihre Geheimnisse, also das HMAC-Secret und das SMTP-Passwort, über `SecretBox`.

**Tech Stack:** Python 3.14 und FastAPI. Dazu sqlite3, `urllib` (Webhook), `smtplib`, `ssl`, `hmac` und `email.message` aus der Standardbibliothek. Keine neue Abhängigkeit.

**Spec:** [`docs/superpowers/specs/2026-11-04-moni-agent-manager-design.md`](../specs/2026-11-04-moni-agent-manager-design.md) (§6.8, §6.7 Rollen und Audit, §6.10 Geheimnisse) · Übersicht: [`2026-11-04-moni-2-uebersicht.md`](2026-11-04-moni-2-uebersicht.md) · Vorgänger: [Paket 4](2026-11-05-moni-2-paket-4-mtls-kopplung.md)

## Ist-Stand nach Paket 4 (Schnittstellen, auf die dieser Plan baut)

- **`manager/poller.py`, `Poller.poll_once(now)`:**
  - Ruft zuerst alle Agents parallel ab, ohne DB.
  - Speichert danach unter `BEGIN IMMEDIATE` je Server in einem SAVEPOINT (`record_success` setzt `last_snapshot` und `status='ok'`, `record_failure` setzt `failures` und `status` `unreachable`/`error`). Die Wartung `_maintain` läuft im eigenen SAVEPOINT. Danach folgt `commit()`.
  - Zuletzt läuft `_renew_certs(now)` mit Netzwerkzugriff außerhalb der Transaktion.
  - Der lokale Server ist `is_local=1`.
- **`metrics_model.flatten(snapshot) -> list[Sample(name, labels, value)]`** mit den Metriknamen aus `metrics_model.HELP`:
  - Host: `moni_host_cpu_usage_percent`, `moni_host_memory_usage_percent`, `moni_host_temperature_celsius` (Labels `zone`, `id`).
  - Dateisysteme: `moni_filesystem_usage_percent` (Labels `mountpoint`, `device`, `fstype`).
  - Container:
    - `moni_container_up` (Labels `name`, `image`, `status`)
    - `moni_container_healthy` (`name`, nur mit Healthcheck)
    - `moni_container_restarts` (`name`)
  - Docker: `moni_docker_up`.
- **`manager/tsdb.py`:** Rohpunkte stehen in `series` und `samples_raw(series_id, ts, value)`.
- **Speicher und Verwaltung:**
  - `ServerStore` mit `_tx(immediate)` (P2-R1), `Server`-Dataclass und Spalte `servers.enabled`.
  - `audit.record(conn, actor, action, *, target, ip, details, now)`.
  - `SecretBox`.
  - `secret reencrypt` in `cli.py` (eine `BEGIN IMMEDIATE`-Transaktion über alle Stores plus TLS-Dateien).
- **API-Muster** (`manager/api.py`, `auth/admin_api.py`):
  - `p: Principal = Depends(require_scope("admin"))` bzw. `dependencies=[read]`.
  - `db.request_conn(request)`.
  - Pydantic-Bodies mit `extra="forbid"`.
  - RFC-9457-Fehler.
- **Tests:** `tests/test_route_inventory.py` prüft jede schreibende Route automatisch auf Rolle, CSRF und Anmeldung. Admin-only-GETs, die ein Betrachter nicht sehen darf, stehen ausdrücklich in `test_viewer_cannot_read_admin_lists`.
- **Netzwerk-Muster:** `poller._NoRedirect` und `ProxyHandler({})` (P2-R6). Fehlertexte werden außerhalb von `except` geworfen.

## Entscheidungen dieses Plans (vom Planer gesetzt)

Im Ledger werden sie als Rulings P5-D1 bis P5-D12 geführt.

- **D1 Ein Regelmodell.** Eine Regel besteht aus diesen Feldern:
  - `name`
  - `metric`: ein Name aus `metrics_model.HELP` oder `moni_server_up`
  - `func`: `"value"` oder `"increase"`, für `increase` mit `window_seconds`
  - `op`: `>`, `>=`, `<` oder `<=`
  - `warning` und `critical`: Schwellen, mindestens eine davon gesetzt
  - `for_seconds`
  - `key_labels`: Labels, die eine Alarm-Instanz bilden; leer heißt eine Instanz je Server
  - `label_filter`: exakte Übereinstimmung, z. B. `{"mountpoint": "/"}`
  - `require_prior_ok`: eine Instanz kann erst auslösen, nachdem sie einmal im Zustand ok war (für „Container gestoppt, der vorher lief“)
  - `server_id`: `null` heißt für alle Server
  - `enabled`

  Die Schwere ist `critical`, wenn die Kritisch-Schwelle verletzt ist, sonst `warning`.
- **D2 Auswertung.**
  - Ausgewertet wird nach jedem Poller-Zyklus auf dem jüngsten gespeicherten Snapshot je Server (`last_snapshot`).
  - `moni_server_up` ist 1 bei `status='ok'`, 0 bei `unreachable` oder `error`. Bei `unknown` gibt es keinen Wert.
  - Ist ein Server nicht `ok`, frieren alle anderen Regeln dieses Servers ein: kein Wechsel, kein Pending-Fortschritt.
  - `increase` berechnet die Summe der positiven Zuwächse der Rohpunkte im Fenster. Ein Zähler-Reset, also ein negativer Sprung, zählt als Neustart der Zählung, nicht als Abnahme.
- **D3 Zustände und Ereignisse.**
  - Eine Instanz ist `ok`, `pending` oder `firing`.
  - Ist die Bedingung wahr und war die Instanz `ok`, wird sie `pending` mit `pending_since = now`.
  - Bei `now - pending_since >= for_seconds` wird sie `firing`; Ereignis `firing`.
  - Ändert sich beim Auslösen die Schwere, entsteht das Ereignis `changed`.
  - Wird die Bedingung falsch: aus `firing` wird `ok` mit dem Ereignis `resolved`; aus `pending` wird `ok` ohne Ereignis.
  - Benachrichtigt wird bei `firing`, `resolved`, `changed` und `reminder`.
- **D4 Erinnerung.** Global gilt `remind_minutes`, Standard **240**, `0` heißt aus, höchstens 10080. Es liegt in `meta` unter dem Schlüssel `alerting` und ist per API änderbar. Solange eine Instanz `firing` ist und `now - last_notified >= remind_minutes*60`, entsteht das Ereignis `reminder`.
- **D5 Stummschalten.**
  - Pro Server gibt es `servers.muted_until` (Unix-Sekunden oder NULL).
  - Während der Stummschaltung wird weiter ausgewertet und der Verlauf geschrieben, mit `muted=1`, aber es gehen keine Outbox-Einträge raus.
  - Ist eine Instanz nach dem Ende der Stummschaltung noch `firing` und nie benachrichtigt worden (`last_notified IS NULL`), folgt ein `firing`-Versand.
  - Eine Behebung während der Stummschaltung wird nicht gemeldet.
- **D6 Dauerhafte Outbox.**
  - Tabelle `alert_outbox`, der Thread `Notifier` prüft alle **5 s**.
  - Wiederholungen nach **60, 300, 900 und 3600 s**. Nach dem **5.** Fehlversuch ist der Eintrag `failed` (WARNING im Log, ohne Inhalte).
  - Erfolgreich versandte Einträge werden nie erneut gesendet, auch nicht nach einem Neustart.
  - Einträge älter als 30 Tage löscht die Poller-Wartung.
- **D7 Webhook.**
  - `POST` an die konfigurierte URL, `http` oder `https`. Bei `http` loggt Moni beim Anlegen eine Warnung.
  - Body: JSON-Schema v1 (siehe Task 4) mit Feld `text` (lesbare Zusammenfassung, passt für Slack, Mattermost und Teams-Workflows).
  - Mit Secret zusätzlich die Header `X-Moni-Timestamp: <unix>` und `X-Moni-Signature: sha256=<hex(HMAC_SHA256(secret, f"{ts}.{body}"))>`.
  - Keine Weiterleitungen, kein Proxy aus der Umgebung (P2-R6), Timeout 10 s, Antwortkörper höchstens 64 KB gelesen und ignoriert. Erfolg heißt 2xx.
- **D8 E-Mail.**
  - `smtplib` mit `security` = `starttls` (Standard, Port 587) oder `tls` (Port 465), das Zertifikat geprüft gegen den System-Vertrauensspeicher (`ssl.create_default_context()`). Unverschlüsseltes SMTP gibt es nicht.
  - Anmeldung optional, `from` Pflicht, 1 bis 10 Empfänger, Timeout 15 s, Klartext-Mail auf Deutsch.
- **D9 Weiterleitung.** Jeder aktive Kanal erhält jedes Ereignis, dessen Schwere `>= min_severity` des Kanals ist (`warning` < `critical`). Für `resolved` gilt die Schwere, mit der die Instanz zuletzt ausgelöst hat.
- **D10 Verschwundene Instanzen.** Verschwindet eine Instanz aus den Daten (Container entfernt, Dateisystem ausgehängt), wird sie gelöscht: ohne Ereignis, wenn sie `pending` war, mit `resolved` (Grund `entfallen`), wenn sie `firing` war.
- **D11 Ausgeschaltete Server und Regeln.**
  - Ein deaktivierter Server, eine deaktivierte oder gelöschte Regel und eine Regeländerung lösen offene `firing`-Instanzen mit `resolved` (Grund `deaktiviert` bzw. `regel geändert`) **ohne** Benachrichtigung auf und entfernen die Instanzen.
  - Wird ein Server gelöscht, entfernt `ON DELETE CASCADE` seine Instanzen. Der Verlauf behält Server- und Regelnamen als Text.
- **D12 Standardregeln** (in der Migration angelegt, änderbar und löschbar; Schwellen aus §6.8):

| Name | metric | func | op | warning | critical | for_seconds | key_labels | Sonstiges |
|---|---|---|---|---|---|---|---|---|
| Server nicht erreichbar | moni_server_up | value | < | – | 1 | 0 | [] | |
| CPU-Auslastung | moni_host_cpu_usage_percent | value | > | 75 | 85 | 300 | [] | |
| Arbeitsspeicher | moni_host_memory_usage_percent | value | > | 75 | 85 | 300 | [] | |
| Dateisystem voll | moni_filesystem_usage_percent | value | > | 75 | 85 | 300 | ["mountpoint"] | |
| Temperatur | moni_host_temperature_celsius | value | > | 65 | 75 | 120 | ["zone","id"] | |
| Container gestoppt | moni_container_up | value | < | – | 1 | 60 | ["name"] | require_prior_ok |
| Container unhealthy | moni_container_healthy | value | < | 1 | – | 120 | ["name"] | |
| Restart-Schleife | moni_container_restarts | increase (600 s) | >= | – | 3 | 0 | ["name"] | |
| Docker-API nicht erreichbar | moni_docker_up | value | < | – | 1 | 60 | [] | |

- **Rollen (§6.7):** Betrachter dürfen offene Alarme, den Verlauf und die Regeln lesen. Kanäle und Alarm-Einstellungen sehen und ändern nur Admins. Jede Änderung an Regeln, Kanälen, Einstellungen und Stummschaltungen wird auditiert.

## Global Constraints

- Branch `feature/moni-2`, Draft-PR #41 (nicht mergen). Commits mit Autor-E-Mail `261156405+anonym-rgath@users.noreply.github.com` (repo-lokal gesetzt). Commit-Trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Python **3.14**. Tests: `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests -v`
- **Keine neue Abhängigkeit.**
- Netzwerkzugriffe (Webhook, SMTP) laufen nie, während eine DB-Schreibtransaktion offen ist, und nie im Poller-Thread. Ausnahme ist der Test-Versand über die API, der in der Anfrage selbst läuft, ohne offene Transaktion.
- HMAC-Secrets und SMTP-Passwörter liegen nur verschlüsselt mit `SecretBox` in der DB. Sie erscheinen nie in API-Antworten (dort steht nur `secret_set: bool`), im Audit, im Log oder in Fehlermeldungen. Webhook-URLs können Tokens enthalten (z. B. Slack); sie stehen deshalb ebenfalls nur verschlüsselt in der DB. Die API zeigt nur Schema und Host (`https://hooks.slack.com/…`).
- Fehlertexte im Versand sind fest und deutsch. Ein Antwortkörper, eine SMTP-Antwort oder ein Exception-Text des Empfängers gelangt nie in `last_error`, ins Log oder in die API. Erlaubt sind nur `"Webhook antwortet mit HTTP <n>"`, `"Webhook nicht erreichbar"`, `"SMTP-Anmeldung fehlgeschlagen"`, `"SMTP-Server nicht erreichbar"`, `"SMTP-Server lehnt Nachricht ab"` und `"TLS-Prüfung fehlgeschlagen"`.
- Zeitstempel: in der API UTC ISO 8601 mit `Z`, in SQLite ganze Unix-Sekunden.
- Bestehende Tests bleiben grün; der Routen-Inventartest deckt alle neuen schreibenden Routen ab. Neue Admin-only-GETs kommen in die Liste von `test_viewer_cannot_read_admin_lists`.
- Texte für Menschen auf Deutsch mit korrekten Umlauten.

## Review Focus

1. **Flattern und Dauer:** Ein Wert genau auf der Schwelle; ein Wechsel kurz vor dem Ablauf von `for_seconds` setzt das Pending zurück. Ein nicht erreichbarer Server friert seine Metrikregeln ein, statt sie aufzulösen. Ein Zähler-Reset bei `increase` löst keinen Fehlalarm aus. Tests in Task 2 und Task 3.
2. **Keine Flut:** Erinnerungen kommen nicht öfter als `remind_minutes`. Während einer Stummschaltung wird nichts versandt, danach genau einmal `firing`. Ein Neustart des Managers versendet nichts doppelt. Viele Dateisysteme ergeben getrennte Instanzen, aber kein Ereignis je Zyklus. Tests in Task 3 und Task 5.
3. **Kaputte Kanäle:** Bei SMTP-Timeout, falschem Zertifikat, Anmeldefehler, Webhook-500, Weiterleitung oder langsamem Webhook wiederholt Moni den Versand nach Plan. Der Poller läuft weiter, und kein Geheimnis und kein Antwortinhalt landet in Log, API oder Audit. Tests in Task 4 und Task 5.
4. **Rollen und Geheimnisse:** Ein Betrachter sieht keine Kanäle und keine Einstellungen. Ein Empfänger kann die HMAC-Signatur mit einer unabhängigen Implementierung prüfen. `secret reencrypt` dreht die Kanal-Geheimnisse mit. Tests in Task 4 und Task 6.
5. **Regeländerungen im Betrieb:** Ändert jemand die Schwelle, deaktiviert oder löscht eine Regel, während sie ausgelöst ist, werden die Instanzen ohne Benachrichtigung aufgelöst. Server löschen oder deaktivieren hält Instanzen und Verlauf konsistent. Tests in Task 3 und Task 6.

---

### Task 1: Schema, Regelspeicher und Standardregeln

**Files:**
- Create: `backend/moni/manager/migrations/0004_alerts.sql`, `backend/moni/manager/alert_rules.py`, `backend/tests/test_alert_rules.py`
- Modify: `backend/tests/test_db.py` (Version 4)

**Interfaces:**
- Produces (Schema):

```sql
ALTER TABLE servers ADD COLUMN muted_until INTEGER;
CREATE TABLE alert_rules (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, metric TEXT NOT NULL,
  func TEXT NOT NULL DEFAULT 'value' CHECK (func IN ('value','increase')), window_seconds INTEGER,
  op TEXT NOT NULL CHECK (op IN ('>','>=','<','<=')), warning REAL, critical REAL,
  for_seconds INTEGER NOT NULL DEFAULT 0, key_labels TEXT NOT NULL DEFAULT '[]', label_filter TEXT NOT NULL DEFAULT '{}',
  require_prior_ok INTEGER NOT NULL DEFAULT 0, server_id INTEGER REFERENCES servers(id) ON DELETE CASCADE,
  enabled INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  CHECK (warning IS NOT NULL OR critical IS NOT NULL));
CREATE TABLE alert_instances (
  rule_id INTEGER NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
  server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  key TEXT NOT NULL,                    -- kanonisches JSON der key_labels-Werte
  state TEXT NOT NULL CHECK (state IN ('ok','pending','firing')), severity TEXT, value REAL,
  pending_since INTEGER, fired_at INTEGER, last_notified INTEGER, seen_ok INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (rule_id, server_id, key)) WITHOUT ROWID;
CREATE TABLE alert_events (
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, rule_id INTEGER, rule_name TEXT NOT NULL,
  server_id INTEGER, server_name TEXT NOT NULL, key TEXT NOT NULL, event TEXT NOT NULL
  CHECK (event IN ('firing','resolved','changed','reminder')), severity TEXT, value REAL, threshold REAL,
  reason TEXT, muted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX alert_events_ts ON alert_events(ts);
CREATE TABLE alert_channels (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK (kind IN ('webhook','email')),
  config TEXT NOT NULL,                 -- JSON ohne Geheimnisse (siehe Task 4)
  secret_enc TEXT,                      -- SecretBox: JSON {"url"?, "hmac"?, "password"?}
  min_severity TEXT NOT NULL DEFAULT 'warning' CHECK (min_severity IN ('warning','critical')),
  enabled INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL);
CREATE TABLE alert_outbox (
  id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES alert_events(id) ON DELETE CASCADE,
  channel_id INTEGER NOT NULL REFERENCES alert_channels(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed')),
  attempts INTEGER NOT NULL DEFAULT 0, next_try INTEGER NOT NULL, last_error TEXT, created_at INTEGER NOT NULL);
CREATE INDEX alert_outbox_due ON alert_outbox(status, next_try);
-- dazu INSERT der 9 Standardregeln aus D12 (created_at/updated_at = strftime('%s','now'))
```

- Produces (`alert_rules.py`):
  - `KNOWN_METRICS = frozenset(metrics_model.HELP) | {"moni_server_up"}`
  - `@dataclass(frozen=True) class Rule`: alle Spalten, `key_labels: tuple[str, ...]`, `label_filter: dict[str, str]`, bools als bool.
  - `validate(data: dict) -> dict`: prüft und normalisiert. Fehlertexte:
    - `"Unbekannte Metrik"`
    - `"Mindestens eine Schwelle angeben"`
    - `"Fenster nur bei increase (60 bis 86400 Sekunden)"`
    - `"Dauer muss zwischen 0 und 86400 Sekunden liegen"`
    - `"Name muss 1 bis 64 Zeichen lang sein"`
    - `"Labels: höchstens 5, nur [a-z_]+"`
    - `"Schwellen müssen endliche Zahlen sein"`
    - `"Warnschwelle muss vor der kritischen Schwelle liegen"`: bei `>`/`>=` muss `warning < critical` sein, bei `<`/`<=` `warning > critical`.
  - `RuleStore(conn)` (P2-R1-Muster): `list()`, `get(id)`, `create(data, now) -> Rule`, `update(id, data, now) -> Rule | None`, `delete(id) -> bool`. Bei doppeltem Namen `Conflict("Regelname ist bereits vergeben")`, wiederverwendet aus `moni.auth.users.Conflict`. `server_id` muss existieren.

- [ ] **Step 1: Failing tests**

```python
def test_migration_seeds_default_rules(conn):
    rules = {r.name: r for r in RuleStore(conn).list()}
    assert len(rules) == 9
    disk = rules["Dateisystem voll"]
    assert (disk.metric, disk.op, disk.warning, disk.critical, disk.for_seconds, disk.key_labels) == \
        ("moni_filesystem_usage_percent", ">", 75, 85, 300, ("mountpoint",))
    assert rules["Container gestoppt"].require_prior_ok and rules["Restart-Schleife"].window_seconds == 600

@pytest.mark.parametrize("patch,msg", [({"metric": "erfunden"}, "Unbekannte Metrik"),
    ({"warning": None, "critical": None}, "Mindestens eine Schwelle"), ({"func": "value", "window_seconds": 60}, "Fenster"),
    ({"op": ">", "warning": 90, "critical": 80}, "Warnschwelle"), ({"warning": float("nan")}, "endliche"),
    ({"key_labels": ["Name!"]}, "Labels")])
def test_validate_rejects(patch, msg): ...

def test_crud_and_unique_name(conn): ...
def test_rule_for_deleted_server_cascades(conn): ...
def test_migration_v4_keeps_existing_data(tmp_path): ...    # v3-DB mit Server + Benutzer → migrate → beides da, 9 Regeln
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün.
- [ ] **Step 5: Commit** `feat(alerts): Schema, Regelmodell und Standardregeln`

---

### Task 2: Reiner Auswerter (`manager/alert_eval.py`)

**Files:**
- Create: `backend/moni/manager/alert_eval.py`, `backend/tests/test_alert_eval.py`

**Interfaces:**
- Consumes: `Rule` (Task 1).
- Produces:
  - `@dataclass class Instance`: `key: str`, `state`, `severity: str | None`, `value: float | None`, `pending_since: int | None`, `fired_at: int | None`, `last_notified: int | None`, `seen_ok: bool`
  - `@dataclass(frozen=True) class Transition`: `key`, `event: str | None` (`firing`/`resolved`/`changed`/`reminder`/None), `instance: Instance | None` (None heißt Instanz löschen), `severity`, `value`, `threshold`, `reason: str | None`
  - `instance_key(labels: dict[str, str], key_labels: tuple[str, ...]) -> str`: kanonisches JSON, sortiert.
  - `breach(rule, value) -> tuple[str | None, float | None]`: Schwere und verletzte Schwelle; kritisch hat Vorrang.
  - `evaluate(rule: Rule, values: dict[str, float] | None, current: dict[str, Instance], now: int, remind_seconds: int) -> list[Transition]`:
    - `values` bildet Instanz-Schlüssel auf den aktuellen Wert ab. `None` heißt eingefroren: keine Transitionen außer `reminder`.
    - Schlüssel, die in `current` stehen, aber nicht in `values`, folgen D10.
    - Gibt es mehrere Proben mit demselben Schlüssel (`key_labels` gruppiert mehrere Serien), entscheidet bei `>`/`>=` das Maximum und bei `<`/`<=` das Minimum.
    - `require_prior_ok`: Ein Wert ohne Verletzung setzt `seen_ok=True`. Eine Verletzung ohne `seen_ok` bleibt `ok`, ohne Pending.
    - `for_seconds = 0` löst im selben Aufruf aus.
  - `increase(points: list[tuple[int, float]]) -> float`: Summe der positiven Differenzen aufeinanderfolgender Punkte (D2).
  - `values_for(rule, samples: list[Sample]) -> dict[str, float]`: filtert nach `metric` und `label_filter` und gruppiert nach `instance_key`. Für `increase` übergibt der Aufrufer (Task 3) die Punkte je Schlüssel, nicht Proben.

- [ ] **Step 1: Failing tests** (Hilfsfunktion `rule(**over)` baut eine `Rule` mit sinnvollen Defaults)

```python
def test_pending_then_firing_after_duration():
    r = rule(op=">", warning=75, critical=85, for_seconds=300)
    t = evaluate(r, {K: 80}, {}, 1000, 0)
    assert t[0].event is None and t[0].instance.state == "pending" and t[0].instance.pending_since == 1000
    cur = {K: t[0].instance}
    assert evaluate(r, {K: 80}, cur, 1299, 0)[0].instance.state == "pending"
    t = evaluate(r, {K: 80}, cur, 1300, 0)
    assert t[0].event == "firing" and t[0].severity == "warning" and t[0].threshold == 75

def test_exactly_at_threshold_with_strict_op_does_not_breach(): ...      # 75 bei ">" 75 → ok
def test_dip_below_resets_pending(): ...
def test_escalation_emits_changed_and_resolution_resolved(): ...
def test_reminder_respects_interval_and_zero_disables(): ...
def test_frozen_values_keep_state_but_allow_reminder(): ...
def test_require_prior_ok():
    r = rule(metric="moni_container_up", op="<", critical=1, require_prior_ok=True, for_seconds=0)
    assert evaluate(r, {K: 0}, {}, 0, 0)[0].instance.state == "ok"                  # nie gelaufen → kein Alarm
    seen = evaluate(r, {K: 1}, {}, 0, 0)[0].instance
    assert evaluate(r, {K: 0}, {K: seen}, 15, 0)[0].event == "firing"
def test_vanished_firing_instance_resolved_with_reason_entfallen(): ...
def test_vanished_pending_instance_dropped_silently(): ...
def test_group_by_uses_max_for_greater_ops(): ...                           # zwei Serien mit gleichem Schlüssel
@pytest.mark.parametrize("pts,expected", [([(0, 1), (60, 2), (120, 4)], 3), ([(0, 5), (60, 0), (120, 2)], 2), ([], 0)])
def test_increase_handles_resets(pts, expected): ...
def test_lower_than_ops_mirror(): ...                                     # "<" mit warning 20, critical 10
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün.
- [ ] **Step 5: Commit** `feat(alerts): Zustandsautomat für Alarmregeln`

---

### Task 3: Auswertung im Poller, Stummschalten, Verlauf und Outbox-Einträge

**Files:**
- Create: `backend/moni/manager/alerting.py`, `backend/tests/test_alerting.py`
- Modify: `backend/moni/manager/poller.py`, `backend/moni/manager/servers.py` (`muted_until`, `set_muted`), `backend/tests/test_poller.py`

**Interfaces:**
- Consumes: `RuleStore`, `evaluate`, `values_for`, `increase`, `instance_key` (Tasks 1–2), `metrics_model.flatten`, `tsdb` (Rohpunkte).
- Produces:
  - `alerting.settings(conn) -> dict` (`{"remind_minutes": int}` aus `meta.alerting`, Standard 240) und `alerting.save_settings(conn, remind_minutes)`. Gültig sind 0 bis 10080, sonst `ValueError("Erinnerung muss zwischen 0 und 10080 Minuten liegen")`.
  - `alerting.run(conn, now) -> int` (Anzahl neuer Ereignisse). Der Aufrufer öffnet `BEGIN IMMEDIATE`, `run` arbeitet ohne Netzwerk. Für jede aktive Regel und jeden passenden Server (`enabled=1`, `server_id` der Regel oder alle):
    - Werte bestimmen nach D2. `moni_server_up` kommt aus `status`. Für `increase` liest `run` je Serie die Rohpunkte im Fenster `[now - window, now]` aus `samples_raw` und gruppiert sie nach Schlüssel (die Summe gruppierter Serien je Schlüssel).
    - `evaluate` aufrufen und die Transitionen anwenden:
      - die Instanz schreiben bzw. löschen;
      - für jedes Ereignis eine Zeile in `alert_events` mit `muted = muted_until > now`;
      - nur wenn nicht stummgeschaltet: je passendem Kanal (D9) eine Zeile in `alert_outbox` mit `next_try = now`, dazu `last_notified = now` auf der Instanz.
    - Nach dem Ende einer Stummschaltung gilt D5 (`firing` mit `last_notified IS NULL`).
    - Aufräumen nach D11: Instanzen deaktivierter Server, deaktivierter oder gelöschter Regeln und von Regeln mit geändertem `updated_at`. Eine Regeländerung zählt, wenn `updated_at > fired_at` bzw. `> pending_since`.
  - `alerting.prune(conn, now)`: löscht Ereignisse älter als 365 Tage und Outbox-Einträge älter als 30 Tage.
  - `Poller`: Nach dem Commit des Zyklus und **vor** `_renew_certs` läuft `alerting.run` in einer eigenen kurzen `BEGIN IMMEDIATE`-Transaktion. Bei einem Fehler: Rollback, ERROR-Log mit dem Typnamen; der Zyklus gilt als erfolgreich. `alerting.prune` läuft in `_maintain` mit.
  - `ServerStore.set_muted(server_id, until: int | None) -> bool`; `Server.muted_until`.

- [ ] **Step 1: Failing tests** (echte migrierte DB mit Servern, Snapshots über `record_success` gesetzt, ein Webhook-Kanal direkt per SQL)

```python
def test_cpu_alert_fires_after_duration_and_creates_outbox(db, server, channel):
    set_snapshot(db, server, cpu=90)
    alerting.run(db, 0); alerting.run(db, 299)
    assert outbox(db) == []
    alerting.run(db, 300)
    ev = events(db)[-1]
    assert (ev["event"], ev["severity"], ev["rule_name"]) == ("firing", "critical", "CPU-Auslastung")
    assert [o["channel_id"] for o in outbox(db)] == [channel]

def test_unreachable_server_fires_server_rule_and_freezes_others(...): ...
def test_disk_per_mountpoint_instances(...): ...
def test_restart_loop_from_raw_points_and_reset_safe(...): ...
def test_muted_server_records_but_does_not_queue_then_notifies_once_after(...): ...
def test_reminder_after_remind_minutes_only(...): ...
def test_min_severity_routing(...): ...                      # Kanal critical bekommt keine Warnung
def test_rule_change_resolves_silently(...): ...
def test_disabled_server_resolves_silently(...): ...
def test_alerting_error_does_not_break_poll_cycle(...): ...  # run wirft → Serverdaten des Zyklus bleiben, ERROR geloggt
def test_prune_retention(...): ...
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün.
- [ ] **Step 5: Commit** `feat(alerts): Auswertung je Zyklus, Stummschalten, Verlauf und Outbox`

---

### Task 4: Kanäle (Webhook, E-Mail), Speicher und Versand

**Files:**
- Create: `backend/moni/manager/alert_channels.py`, `backend/tests/test_alert_channels.py`
- Modify: `backend/moni/cli.py` (`secret reencrypt` zählt Kanäle mit)

**Interfaces:**
- Consumes: `SecretBox`, `poller._NoRedirect` (Muster für einen Opener ohne Proxy und ohne Weiterleitungen).
- Produces:
  - `ChannelStore(conn, box)` (P2-R1). Methoden: `list() -> list[Channel]`, `get(id)`, `create(data, now)`, `update(id, data)`, `delete(id)`, `secrets(id) -> dict`, `reencrypt_all() -> int`.
    - `Channel` hat kein Geheimnis-Feld; dafür gibt es `secret_set: bool` und `url_hint: str | None` (nur Schema und Host).
    - Webhook-`data`: `{name, kind: "webhook", url, hmac_secret?, min_severity?, enabled?}`. `url` ist http(s), ohne Zugangsdaten, nur sichtbares ASCII, höchstens 2048 Zeichen. URL und `hmac_secret` landen in `secret_enc`, `config` bleibt `{}`.
    - E-Mail-`data`: `{name, kind: "email", host, port, security: "starttls"|"tls", username?, password?, from_addr, to: [..1-10], min_severity?, enabled?}`. Adressen werden mit `moni.auth.users.normalize_email` geprüft. `config` enthält alles außer `password`.
    - Beim Update ohne `password`/`hmac_secret`/`url` bleibt der gespeicherte Wert. Ändert sich `host`, entfällt `password`, analog zu Paket 3 (Task 7, Secret-Fix): `ValueError("Passwort fehlt")`, wenn sich bei gesetztem Passwort der Host ändert und kein neues Passwort mitkommt.
  - `payload(event: dict, manager_name: str) -> dict` (Schema v1):
    `{"version": 1, "event": "firing|resolved|changed|reminder|test", "severity", "rule", "server", "labels": {..}, "value", "threshold", "reason", "at": ISO, "manager": name, "text": "<deutsche Zusammenfassung>"}`
    Beispiel für `text`: `"[KRITISCH] CPU-Auslastung auf pi: 92.5 (Schwelle 85) – ausgelöst"`.
  - `sign(secret: str, ts: int, body: bytes) -> str` → `"sha256=" + hex`.
  - `send_webhook(url, body: dict, hmac_secret: str | None, timeout=10) -> None`. Bei einem Fehler wirft die Funktion `SendError(<fester Text aus den Global Constraints>)`, außerhalb von `except`.
  - `send_email(cfg: dict, password: str | None, subject: str, text: str, timeout=15) -> None`:
    - `starttls` heißt `SMTP(host, port)`, `ehlo`, `starttls(context=default)`, `ehlo`.
    - `tls` heißt `SMTP_SSL(context=default)`.
    - `SMTPAuthenticationError`, `SMTPRecipientsRefused` und `ssl.SSLCertVerificationError` werden auf die festen Texte abgebildet.
  - `deliver(channel: Channel, secrets: dict, event: dict, manager_name: str) -> None` wählt Webhook oder E-Mail. Betreff der Mail: `text` ohne den Teil nach dem Gedankenstrich.

- [ ] **Step 1: Failing tests** (Webhook gegen einen echten `http.server` in einem Thread auf 127.0.0.1; SMTP gegen einen kleinen `socketserver`-basierten Fake-SMTP oder per `monkeypatch` von `smtplib.SMTP`, mit Assertions auf die Aufrufreihenfolge `starttls` vor `login`)

```python
def test_webhook_signature_verifiable_independently(webhook_server):
    send_webhook(webhook_server.url, {"a": 1}, "geheim")
    req = webhook_server.requests[0]
    ts, body = req.headers["X-Moni-Timestamp"], req.body
    expected = "sha256=" + hmac.new(b"geheim", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert req.headers["X-Moni-Signature"] == expected and json.loads(body) == {"a": 1}

@pytest.mark.parametrize("behaviour,msg", [("500", "Webhook antwortet mit HTTP 500"), ("redirect", "Webhook antwortet mit HTTP 302"),
                                           ("slow", "Webhook nicht erreichbar"), ("refused", "Webhook nicht erreichbar")])
def test_webhook_errors_are_fixed_texts_without_body(webhook_server, behaviour, msg): ...  # Antwortkörper "GEHEIM" nie in str(e)/__context__
def test_email_starttls_before_login_and_cert_checked(fake_smtp): ...
def test_email_errors_mapped(fake_smtp): ...
def test_channel_secrets_encrypted_and_never_in_channel(conn): ...  # URL/hmac/password nicht im Klartext der DB, nicht in repr/Channel
def test_host_change_requires_new_password(conn): ...
def test_payload_text_german(): ...
def test_reencrypt_includes_channels(...): ...
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün.
- [ ] **Step 5: Commit** `feat(alerts): Webhook mit HMAC und E-Mail per SMTP`

---

### Task 5: Benachrichtigungs-Thread mit Outbox und Wiederholungen

**Files:**
- Create: `backend/moni/manager/notifier.py`, `backend/tests/test_notifier.py`
- Modify: `backend/moni/app.py` (Start und Stopp im Lifespan, nur Manager)

**Interfaces:**
- Consumes: `ChannelStore`, `deliver`, `SendError` (Task 4), Outbox und Ereignisse (Task 3).
- Produces:
  - `RETRY = (60, 300, 900, 3600)` und `MAX_ATTEMPTS = 5`.
  - `Notifier(settings, box, deliver=alert_channels.deliver)` mit `start()`, `stop()` und `run_once(now) -> int` (Anzahl verarbeiteter Einträge). `run_once` arbeitet so:
    1. Eine kurze Lese-Verbindung holt höchstens 20 fällige `pending`-Einträge (`next_try <= now`), sortiert nach `id`, samt Ereignis-, Kanal- und Server-Daten.
    2. Die Verbindung wird **geschlossen bzw. die Transaktion beendet, bevor** zugestellt wird.
    3. Für jeden Eintrag läuft `deliver`.
    4. Danach schreibt `run_once` das Ergebnis in einer kurzen `BEGIN IMMEDIATE`-Transaktion:
       - Erfolg: `status='sent'`.
       - Fehler: `attempts+1`, `last_error=<fester Text>` und `next_try = now + RETRY[min(attempts-1, 3)]`; ab dem 5. Fehlversuch `status='failed'` und eine WARNING `Benachrichtigung über <kanal> endgültig fehlgeschlagen`.
    5. Deaktivierte Kanäle: Ihre Einträge werden ohne Versand auf `failed` mit `"Kanal deaktiviert"` gesetzt.
  - Lifespan: `Notifier` läuft nur in der Rolle Manager, Takt 5 s. `stop()` wartet höchstens 15 s.

- [ ] **Step 1: Failing tests**

```python
def test_success_marks_sent_and_never_resends(db, notifier, fake_deliver): ...
def test_retry_schedule_and_final_failure(db, notifier, failing_deliver):
    for now, expected_next in [(0, 60), (60, 360), (360, 1260), (1260, 4860)]:
        notifier.run_once(now); assert row()["next_try"] == expected_next
    notifier.run_once(4860); assert row()["status"] == "failed" and row()["attempts"] == 5
def test_no_db_transaction_during_delivery(db, notifier): ...   # deliver prüft conn.in_transaction aller offenen Verbindungen / Schreibsperre frei
def test_restart_does_not_resend_sent(db): ...                  # neuer Notifier auf derselben DB → 0 Versand
def test_error_texts_only_fixed(db, notifier): ...              # SendError-Text landet in last_error, sonst nichts
def test_disabled_channel_marks_failed_without_delivery(...): ...
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün.
- [ ] **Step 5: Commit** `feat(alerts): dauerhafte Outbox mit Wiederholungen`

---

### Task 6: Alarm-API (Alarme, Verlauf, Regeln, Kanäle, Test-Versand, Stummschalten, Einstellungen)

**Files:**
- Create: `backend/moni/manager/alert_api.py`, `backend/tests/test_alert_api.py`
- Modify: `backend/moni/app.py` (Router), `backend/tests/test_route_inventory.py` (Admin-GETs und Bodies)

**Interfaces:**
- Consumes: Tasks 1–5, `require_scope`, `Principal`, `audit`, `db.request_conn`.
- Produces (Prefix `/api/v1`):

| Route | Zugang | Ergebnis |
|---|---|---|
| `GET /alerts` | read | offene Instanzen (`pending` und `firing`) mit `rule`, `server`, `labels`, `state`, `severity`, `value`, `since`, `muted` |
| `GET /alerts/history?limit=100&before=&server_id=` | read | `{"entries": [...], "next_before"}` (`limit` 1–1000) |
| `GET /alert-rules` | read | Liste der Regeln |
| `POST /alert-rules` | admin | 201; Audit `rule.created` |
| `PATCH /alert-rules/{id}` | admin | 200; Audit `rule.updated` (Details: geänderte Feldnamen) |
| `DELETE /alert-rules/{id}` | admin | 204; Audit `rule.deleted` |
| `GET /alert-channels` | admin | Liste ohne Geheimnisse (`secret_set`, `url_hint`) |
| `POST /alert-channels` | admin | 201; Audit `channel.created` (Details: `kind`) |
| `PATCH /alert-channels/{id}` | admin | 200; Audit `channel.updated` |
| `DELETE /alert-channels/{id}` | admin | 204; Audit `channel.deleted` |
| `POST /alert-channels/{id}/test` | admin | `{"ok": bool, "error": str \| null}`. Sendet sofort ein `test`-Ereignis; nur feste Fehlertexte. Höchstens 6 pro Minute je Kanal, darüber 429. Audit `channel.tested`. |
| `PUT /servers/{id}/mute` | admin | `{"until": ISO}` (Zukunft, höchstens 30 Tage), 200; Audit `server.muted` |
| `DELETE /servers/{id}/mute` | admin | 204; Audit `server.unmuted` |
| `GET /alert-settings` | admin | `{"remind_minutes"}` |
| `PUT /alert-settings` | admin | 200; Audit `alerting.settings_changed` |

  - Alle Bodies sind Pydantic mit `extra="forbid"`. `ValueError` und `Conflict` führen zu 422 bzw. 409. Die Server-JSON aus `manager/api.py` erhält zusätzlich `muted_until` (ISO oder null).
  - `test_viewer_cannot_read_admin_lists` bekommt `/api/v1/alert-channels` und `/api/v1/alert-settings` dazu; `BODIES` im Routen-Inventartest wird um gültige Bodies ergänzt.

- [ ] **Step 1: Failing tests**

```python
def test_viewer_sees_alerts_and_rules_but_not_channels(viewer_client):
    assert viewer_client.get("/api/v1/alerts").status_code == 200
    assert viewer_client.get("/api/v1/alert-rules").status_code == 200
    assert viewer_client.get("/api/v1/alert-channels").status_code == 403

def test_channel_secret_never_returned_or_audited(admin_client, csrf):
    r = admin_client.post("/api/v1/alert-channels", json={"name": "slack", "kind": "webhook",
        "url": "https://hooks.slack.com/services/T/B/GEHEIM", "hmac_secret": "s3cret"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201 and "GEHEIM" not in r.text and "s3cret" not in r.text
    assert r.json()["url_hint"] == "https://hooks.slack.com/…" and r.json()["secret_set"] is True
    assert "GEHEIM" not in admin_client.get("/api/v1/audit").text

def test_test_send_reports_fixed_error(...): ...       # Webhook liefert 500 mit Körper "INTERN" → {"ok": false, "error": "Webhook antwortet mit HTTP 500"}
def test_test_send_rate_limited(...): ...
def test_mute_validation_and_audit(...): ...           # Vergangenheit/ > 30 Tage → 422
def test_rule_crud_audited_and_validation(...): ...
def test_history_paging(...): ...
def test_settings_validation(...): ...
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Suite grün (inklusive Routen-Inventartest).
- [ ] **Step 5: Commit** `feat(alerts): API für Alarme, Regeln, Kanäle und Stummschalten`

---

### Task 7: CI-Prüfung und Doku-Variablen

**Files:**
- Create: `scripts/ci/alert_smoke.py`
- Modify: `.github/workflows/ci.yml`, `.env.example`, `docs/superpowers/plans/2026-11-04-moni-2-uebersicht.md` (nur Link, kein Abhaken)

**Interfaces:**
- `scripts/ci/alert_smoke.py MANAGER_URL STATE_FILE LISTEN_PORT` (nur Standardbibliothek; nutzt `scripts/ci/_moni.py` zum Anmelden mit dem Wiederherstellungscode aus `STATE_FILE`):
  1. Einen HTTP-Empfänger auf `0.0.0.0:LISTEN_PORT` in einem Thread starten. Er speichert Header und Body.
  2. Einen Webhook-Kanal `ci` mit `url=http://<runner-ip>:LISTEN_PORT/hook` und `hmac_secret` (fester CI-Wert) anlegen. Die Runner-IP aus Sicht des Manager-Containers ist das Docker-Bridge-Gateway; das Skript bekommt sie als Umgebungsvariable `HOOK_HOST`.
  3. `POST /alert-channels/{id}/test` aufrufen. Erwartet wird `{"ok": true}`.
  4. Der Empfänger hat eine Anfrage mit `event == "test"` erhalten, und die HMAC-Signatur ist unabhängig nachgerechnet gültig.
  5. Ausgabe `OK Alarmkanal`.
- CI-Schritt „Alarmkanal“ nach „Kopplung (mTLS)“: `HOOK_HOST=$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}')` (alternativ `host.docker.internal` per `extra_hosts`; das sichere Vorgehen ist im Skript dokumentiert), danach das Skript aufrufen. Danach prüfen, dass das CI-HMAC-Secret nicht in `manager.log` steht (R7: erst in eine Datei schreiben).
- `.env.example`: keine neuen Variablen. Ein Hinweis, dass Kanäle und Regeln per API bzw. später per Oberfläche verwaltet werden, und dass SMTP nur mit STARTTLS oder TLS funktioniert.

- [ ] **Step 1:** Skript und CI anpassen. Lokal ausführen (Compose-Stack wie in Paket 4; die `rslave`-Zeile nur in einer Kopie im Scratchpad entfernen). Expected: `OK Alarmkanal`.
- [ ] **Step 2:** Volle Suite grün.
- [ ] **Step 3: Commit** `ci: Alarmkanal mit HMAC-Prüfung im Smoke-Test`

---

### Task 8 (Betreiber, optional): Echte Kanäle beim Autor

Nicht durch Subagents.

1. Einen Webhook-Kanal anlegen, z. B. ntfy (`https://ntfy.sh/<eigenes-thema>`) oder einen n8n-Webhook, und `POST /alert-channels/{id}/test` aufrufen. Die Nachricht muss ankommen.
2. Optional einen E-Mail-Kanal über einen SMTP-Anbieter anlegen (Port 587 mit STARTTLS) und testen.
3. Den Pi für 10 Minuten stummschalten (`PUT /servers/{id}/mute`), eine Regel künstlich auslösen, z. B. die Schwelle von „CPU-Auslastung“ auf 1 setzen. Während der Stummschaltung kommt keine Nachricht, danach genau eine. Anschließend die Schwelle zurücksetzen. Die Behebung kommt nicht, weil eine Regeländerung still auflöst (D11).
