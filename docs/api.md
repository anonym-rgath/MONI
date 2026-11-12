# API

Agent und Manager haben je eine versionierte HTTP-API unter `/api/v1`. Sie ist ein eigenständiges Produkt: Sie ist
ohne Weboberfläche nutzbar und lässt sich in Skripte, Prometheus oder Grafana einbinden.

- Spezifikation (OpenAPI): [`api/openapi-agent.json`](api/openapi-agent.json) und
  [`api/openapi-manager.json`](api/openapi-manager.json). Beide stehen im Repository und werden in den Tests
  mit dem Code abgeglichen.
- Interaktive Dokumentation der laufenden Instanz: `/api/v1/docs`.
- Die Agent-Spezifikation beschreibt die Variante mit TLS (`MONI_TLS=on`). Mit `MONI_TLS=off` fehlen die Routen
  unter `/api/v1/tls/` und `/api/v1/tokens`.

## Anmeldung

Skripte und Fremdsysteme senden einen API-Token als `Authorization: Bearer <TOKEN>`:

```
curl -H "Authorization: Bearer <TOKEN>" https://manager.example.org:8443/api/v1/servers
```

(`-k` bzw. `--cacert` je nach Zertifikat, siehe [Kopplung](kopplung.md#ca-des-managers).) Die Weboberfläche des
Managers nutzt stattdessen ein Session-Cookie mit CSRF-Schutz. `GET /api/v1/health` ist ohne Anmeldung erreichbar
und liefert nur `ok`.

## Tokens und Scopes

- **Scope `read`:** lesende Routen. **Scope `admin`:** zusätzlich ändernde Routen (Server, Benutzer, Alarme, Tokens).
- Am **Agent** sind Tokens immer `read`. Anlegen per CLI
  (`docker compose -f compose.agent.yaml exec moni-agent python -m moni.cli token create --name prometheus --expires-days 90`),
  oder für gekoppelte Agents im Manager (Agent-Tokens). Die Token-Routen des Agents selbst nimmt er nur mit dem Manager-Zertifikat an.
- Am **Manager** erzeugt ein Admin Tokens in der Oberfläche (**Verwaltung, API-Tokens**), per
  `POST /api/v1/tokens` mit `{"name": "...", "scope": "read", "expires_days": 90}` oder per CLI
  (`python -m moni.cli token create --name ... --scope read`).
- Der Token wird **nur bei der Erzeugung einmal angezeigt**, gespeichert wird nur ein Hash. Er hat ein optionales
  Ablaufdatum (1 bis 3650 Tage) und ist einzeln widerrufbar (`token revoke`, `DELETE /api/v1/tokens/{id}`).
- **API-Tokens sind nicht an Benutzer gebunden.** Wer einen Admin deaktiviert, sperrt dessen Tokens nicht. Widerrufen
  Sie sie von Hand, wenn jemand ausscheidet (`GET /api/v1/tokens` zeigt alle, [Audit-Protokoll](betrieb.md) die Erzeuger).

## Fehlerformat

Alle Fehler folgen RFC 9457 (`Content-Type: application/problem+json`):

```json
{"type": "about:blank", "title": "Unauthorized", "status": 401, "detail": "…"}
```

`detail` ist ein deutscher Text ohne Geheimnisse. Typische Status: 401 (keine oder ungültige Anmeldung),
403 (Scope reicht nicht), 422 (ungültige Parameter), 429 (Rate-Limit, `MONI_RATE_LIMIT_PER_MINUTE`), 503 (nicht bereit).

## Metriken und Readiness

| Pfad | Rolle | Anmeldung | Inhalt |
|---|---|---|---|
| `/metrics` | Agent | `read` | Messwerte des Hosts im Prometheus-Format |
| `/api/v1/metrics` | Manager | `read` | Eigenkennzahlen des Managers im Prometheus-Format |
| `/api/v1/ready` | beide | keine | 200, wenn betriebsbereit, sonst 503 |

`/api/v1/ready` ist öffentlich und ohne Details. Beim Agent bedeutet 200, dass es eine erste Messung gibt; beim
Manager, dass die Datenbank antwortet und der Abruf-Dienst läuft.

Eigenkennzahlen des Managers (`/api/v1/metrics`):

| Name | Typ | Labels | Bedeutung |
|---|---|---|---|
| `moni_build_info` | gauge | `role`, `version` | Immer 1 |
| `moni_manager_server_up` | gauge | `server` | 1, wenn der Server zuletzt erreichbar war |
| `moni_manager_poll_duration_seconds` | gauge | `server` | Dauer des letzten Abrufs |
| `moni_manager_poll_errors_total` | counter | `server` | Fehlgeschlagene Abrufe seit Prozessstart |
| `moni_manager_poll_cycle_duration_seconds` | gauge | – | Dauer des letzten Abrufzyklus (erst nach dem ersten Zyklus) |
| `moni_manager_db_size_bytes` | gauge | – | Größe der Datenbankdatei samt WAL |

`moni_manager_poll_errors_total` zählt auch den Fall „Agent liefert keine neuen Daten“. Ein stiller Agent
(läuft, misst aber nicht) erhöht den Zähler also genauso wie ein nicht erreichbarer.

Prometheus-Beispiel (Token als Datei, der Agent mit eigenem Zertifikat; `ca_file` ist die CA aus
[Kopplung](kopplung.md#ca-des-managers)):

```yaml
scrape_configs:
  - job_name: moni-agent
    scheme: https
    metrics_path: /metrics
    authorization:
      credentials_file: /etc/prometheus/moni-agent.token
    tls_config:
      ca_file: /etc/prometheus/moni-ca.crt
    static_configs:
      - targets: ["agent.example.org:9810"]
  - job_name: moni-manager
    scheme: https
    metrics_path: /api/v1/metrics
    authorization:
      credentials_file: /etc/prometheus/moni-manager.token
    tls_config:
      ca_file: /etc/prometheus/moni-ca.crt
    static_configs:
      - targets: ["manager.example.org:8443"]
```

Das Agent-Zertifikat enthält den Host aus der URL, mit der er gekoppelt wurde; passt er nicht zu `targets`, setzen Sie
`tls_config.server_name`. Hinter einem Proxy gilt dessen Zertifikat, dann entfällt `ca_file`.

## OpenAPI neu erzeugen

Die Dateien in `docs/api/` sind erzeugt. Nach einer Änderung an Routen oder Modellen (und bei jedem Versions-Bump)
aus dem Repository-Wurzelverzeichnis:

```
cd backend
uv run --python 3.14 --with-requirements requirements-dev.txt python -m moni.cli openapi --role agent > ../docs/api/openapi-agent.json
uv run --python 3.14 --with-requirements requirements-dev.txt python -m moni.cli openapi --role manager > ../docs/api/openapi-manager.json
```

Der Test `test_openapi_snapshot.py` schlägt fehl, solange die Dateien vom Code abweichen.
