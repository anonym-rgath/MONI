# Moni

> Quelloffenes Monitoring für Linux-Hosts und ihre Docker-Container: Ein schlanker **Agent** misst pro Host, ein
> **Manager** sammelt, speichert Verläufe, alarmiert und bietet eine Weboberfläche. Alles läuft als gehärtete
> Container mit Docker Compose.

[![CI](https://github.com/anonym-rgath/MONI/actions/workflows/ci.yml/badge.svg)](https://github.com/anonym-rgath/MONI/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

## Was Moni ist

- **Agent** (`moni-agent`): Messdienst ohne Oberfläche auf jedem Host. Er liefert CPU, Speicher, Dateisysteme,
  Netzwerk, Temperatur und den Status der Docker-Container über eine verschlüsselte, tokengeschützte API und im
  Prometheus-Format (`/metrics`).
- **Manager** (`moni-manager`): fragt mehrere Agents ab, speichert Verläufe in SQLite (Aufbewahrung einstellbar),
  wertet Alarmregeln aus (Webhook, E-Mail) und zeigt alles in einer Weboberfläche mit Login, Pflicht-2FA, optional OIDC und
  Rollen (Admin, Betrachter).
- Manager und Agent sprechen per **mTLS** miteinander (eigene CA, Kopplung per Kopplungscode). Hinter einem
  Reverse-Proxy oder Cloudflare Tunnel funktioniert auch ein Token-Modus.
- Docker wird nur über einen **Socket-Proxy mit enger Allowlist** angesprochen, nie über den Socket selbst.
  Die Container laufen `read_only`, ohne Capabilities und ohne neue Rechte.
- Images für amd64 und arm64 auf GHCR, signiert (cosign) und mit SBOM.

```
 Browser ──HTTPS──▶ ┌───────────────┐   mTLS, Abruf alle 15 s   ┌──────────────┐
                    │    Manager    │ ────────────────────────▶ │ Agent Host A │
 Prometheus ──────▶ │ UI, SQLite,   │                           └──────────────┘
 (Token)            │ Alarme        │   mTLS                    ┌──────────────┐
                    │               │ ────────────────────────▶ │ Agent Host B │
                    └──────┬────────┘                           └──────────────┘
                           └─▶ Webhook / E-Mail
```

Der Manager misst seinen eigenen Host direkt mit, ein Agent dort ist nicht nötig.

## Schnellstart

Voraussetzung: Linux mit Docker und Compose-Plugin. Prüfen Sie vor dem ersten Start die
[Signatur der Images](docs/installation.md#signatur-der-images-prüfen).

Tragen Sie in `.env` außerdem `DOCKER_GID=<Wert>` ein (`stat -c %g /var/run/docker.sock`, Docker Desktop auf macOS: `0`),
damit auch eigene `docker compose`-Aufrufe funktionieren; die Skripte setzen es selbst.

**Manager:**

```
git clone https://github.com/anonym-rgath/MONI.git && cd MONI
cp .env.example .env && chmod 600 .env
docker run --rm ghcr.io/anonym-rgath/moni-manager:2.0.0 python -m moni.cli secret generate   # Ausgabe als MONI_SECRET_KEY in .env eintragen
./scripts/start.sh manager
docker compose -f compose.manager.yaml logs moni-manager | grep "Setup-Token"
```

Dann `https://localhost:8443` öffnen (Browserwarnung wegen der eigenen CA), mit dem Setup-Token den ersten Admin anlegen
und die 2FA einrichten. Den Master-Schlüssel sichern Sie getrennt von den Daten ([Backup](docs/backup.md)).

**Agent koppeln** (auf dem zu überwachenden Host, mit `MONI_AGENT_BIND=0.0.0.0` in `.env`):

```
./scripts/start.sh agent
docker compose -f compose.agent.yaml exec moni-agent python -m moni.cli pair code
```

Den ausgegebenen Kopplungscode fügen Sie im Manager unter **Verwaltung, Server, Server koppeln** ein.
Details: [Installation](docs/installation.md) und [Kopplung](docs/kopplung.md).

## Dokumentation

| Thema | Seite |
|---|---|
| Voraussetzungen, Variablen, Signatur prüfen, Socket-Proxy | [Installation](docs/installation.md) |
| Agent und Manager verbinden, Zertifikate, Token-Modus | [Kopplung](docs/kopplung.md) |
| Reverse-Proxy, Traefik, Cloudflare Tunnel, OIDC | [Proxy](docs/proxy.md) |
| Tokens, Fehlerformat, Metriken, OpenAPI | [API](docs/api.md) |
| Regeln, Kanäle, Webhook mit HMAC | [Alarme](docs/alarme.md) |
| Sicherung und Wiederherstellung | [Backup](docs/backup.md) |
| Von 1.x und innerhalb von 2.x | [Upgrade](docs/upgrade.md) |
| Skripte, Logs, Admin-Reset | [Betrieb](docs/betrieb.md) |
| Release-Ablauf für Maintainer | [Release](docs/release.md) |
| Änderungen je Version | [CHANGELOG](CHANGELOG.md) |

## Sicherheit und Mitmachen

Schwachstellen melden Sie vertraulich nach [SECURITY.md](SECURITY.md). Beiträge: [CONTRIBUTING.md](CONTRIBUTING.md).

## Lizenz

[Apache License 2.0](LICENSE)
