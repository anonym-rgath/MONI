# Betrieb

## Skripte

Alle Skripte liegen in `scripts/` und nehmen die Rolle als erstes Argument: `scripts/<name>.sh agent|manager`.
Ohne Rolle beenden sie sich mit Exit 2.

| Skript | Zweck |
|---|---|
| `start.sh` | Stack starten (legt `.env` aus `.env.example` an, wenn sie fehlt; beim Manager muss `MONI_SECRET_KEY` gesetzt sein) |
| `stop.sh` | Stack stoppen (`docker compose down`); die Daten-Volumes bleiben |
| `redeploy.sh` | Image ziehen und neu starten; Optionen `--local` (nicht ziehen, lokales Image), `--force` (Container neu erzeugen), `--no-smoke` |
| `smoke.sh` | Prüft den laufenden Stack: Container, Health und Readiness, Härtung |
| `diagnose.sh` | Zustand ohne Geheimnisse, **zum Teilen geeignet** (Version, Readiness, Status, maskierte Logzeilen) |
| `logs.sh` | Folgt den **rohen** Logs |
| `selftest.sh` | Selbsttest der Skripte (für Entwickler) |

Die Skripte setzen `DOCKER_GID` selbst (siehe [Installation](installation.md#docker_gid)).

**Teilen Sie nie die Ausgabe von `logs.sh`, `docker compose logs` oder `docker compose config`.** Sie können Setup-Token,
Kopplungscodes oder den Master-Schlüssel enthalten. Für Fehlermeldungen in Issues nehmen Sie `diagnose.sh`.

## Logs

Moni schreibt strukturierte **JSON-Logs** (`ts`, `level`, `logger`, `msg`) nach stdout, Docker rotiert sie (10 MB,
3 Dateien). Moni schreibt keine Passwörter, Tokens, Kopplungscodes oder Schlüssel ins Log. Einzige Ausnahme ist der
**Setup-Token** beim ersten Start des Managers (siehe [Installation](installation.md#manager-installieren)), er gilt nur, bis der erste Admin
existiert. Die Zugriffslogs des Managers enthalten keine Query-Strings.

## Admin zurücksetzen

Ist der letzte Admin ausgesperrt, setzen Sie im Manager-Container per CLI zurück. Das Passwort kommt aus einer Datei oder stdin, nie als Argument:

```
printf '%s' '<NEUES-PASSWORT>' | docker compose -f compose.manager.yaml exec -T moni-manager \
  python -m moni.cli user reset-password admin@example.org --password-file -
docker compose -f compose.manager.yaml exec moni-manager python -m moni.cli user reset-2fa admin@example.org
docker compose -f compose.manager.yaml exec moni-manager python -m moni.cli user unlock admin@example.org
docker compose -f compose.manager.yaml exec moni-manager python -m moni.cli user list
```

`reset-password` und `reset-2fa` gehen nur für lokale Konten und beenden alle Sitzungen des Benutzers; `unlock`
hebt die Sperre nach Fehlversuchen auf. Jeder dieser Schritte steht im Audit-Protokoll. Das Passwort steht kurz in der Shell-History,
wenn Sie `printf` wie oben tippen: Nutzen Sie stattdessen eine Datei mit Rechten 600 und löschen Sie sie danach.

Weitere CLI-Befehle: `server add|list|remove`, `token create|list|revoke`, `secret generate|reencrypt`,
`pair code|reset` (Agent), `openapi`.

## Eigenkennzahlen und Readiness

- `GET /api/v1/ready` (öffentlich): 200 bzw. 503, geeignet für Healthchecks und Überwachung von außen.
- `GET /api/v1/metrics` (Manager, Token `read`): Abrufdauer und Fehler je Server, Zyklusdauer, Datenbankgröße.
  Namen und Prometheus-Beispiel in der [API-Doku](api.md#metriken-und-readiness).

Sinnvolle Alarme auf die Kennzahlen: `moni_manager_server_up == 0`, steigendes `moni_manager_poll_errors_total`,
stark wachsendes `moni_manager_db_size_bytes` (prüfen Sie dann die Aufbewahrung, `MONI_RETENTION_*`).

## Audit-Protokoll

Admins sehen sicherheitsrelevante Vorgänge (Anmeldungen, 2FA-Änderungen, Benutzer, Rollen, Tokens, Server, Regeln,
Kanäle) unter **Verwaltung, Audit-Protokoll** und per `GET /api/v1/audit`. CLI-Aktionen erscheinen mit dem Akteur `cli`.
