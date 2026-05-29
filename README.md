# Pi Monitor

> Schlankes Echtzeit-Monitoring-Dashboard fuer einen Raspberry Pi: Host-Metriken
> und Docker-Container-Status, ausgeliefert als ein einzelner, gehaerteter Container.

[![CI](https://github.com/anonym-rgath/MONI/actions/workflows/ci.yml/badge.svg)](https://github.com/anonym-rgath/MONI/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Das Projekt enthaelt ausschliesslich den Monitor selbst (Frontend, Backend,
Betriebs-Skripte). Infrastruktur wie Traefik, Cloudflare Tunnel, DNS oder
Backups liegt im separaten Projekt `PI-SETUP`.

---

## Inhaltsverzeichnis

- [Features](#features)
- [Architektur](#architektur)
- [Voraussetzungen](#voraussetzungen)
- [Konfiguration](#konfiguration)
- [API](#api)
- [Betrieb](#betrieb)
- [Sicherheit](#sicherheit)
- [Entwicklung & Tests](#entwicklung--tests)
- [Projektstruktur](#projektstruktur)
- [Lizenz](#lizenz)

---

## Features

**Host-Metriken**

- CPU: Auslastung, Kernanzahl, aktueller Takt
- RAM inkl. Swap
- Disk (Root-Dateisystem)
- Load Average (1 / 5 / 15 Minuten)
- Temperatur (Thermal Zone)
- Uptime, laufende Prozesse, Hostname

**Docker-Container** (pro Container)

- Status und Health
- CPU- und RAM-Nutzung
- Netzwerk-Durchsatz (rx / tx)
- Uptime und Restart-Count

**Dashboard**

- Live-Aktualisierung mit waehlbarem Intervall (2 / 3 / 5 / 10 s)
- CPU- und RAM-Verlaufscharts
- Reliability-Strip: aggregierte Alerts (Schwellen fuer CPU/RAM/Disk/Temperatur,
  gestoppte oder unhealthy Container, Docker-API-Status)

**Robuste API**

- Lesefehler werden ueber `available`-Flags und eine `warnings`-Liste sichtbar
  gemacht, statt sie als falsche Nullwerte zu kaschieren.

---

## Architektur

Alles laeuft in **einem** Multi-Stage-Image. Nginx liefert das statische
React-Build aus und reverse-proxyt `/api` an das FastAPI-Backend; beide Prozesse
werden von Supervisor verwaltet. Der Docker-Socket wird niemals direkt in den
Monitor gemountet, sondern nur lesend ueber einen restriktiven Proxy erreichbar
gemacht.

```
Browser ──HTTPS──▶ Cloudflare Access ──▶ Traefik ──▶ pi-monitor (:80, nginx)
                                                        ├── /      ▶ React-Build (statisch)
                                                        └── /api   ▶ uvicorn :8001 (FastAPI)
                                                                       ├── /proc, /sys, /etc/hostname  (read-only)
                                                                       └── docker-socket-proxy :2375   (internes Netz)
                                                                              └── /var/run/docker.sock  (read-only, POST=0)
```

| Komponente            | Aufgabe                                                        |
| --------------------- | -------------------------------------------------------------- |
| `frontend/`           | React-Dashboard (CRA + CRACO + Tailwind)                       |
| `backend/`            | FastAPI-Backend, liest `/proc`, `/sys` und die Docker-API      |
| `nginx`               | Statische Auslieferung + Reverse-Proxy + Security-Header       |
| `docker-socket-proxy` | Gekapselter, read-only Zugriff auf den Docker-Socket           |

---

## Voraussetzungen

- Docker und das `docker compose`-Plugin
- Ein externes Docker-Netzwerk `traefik-network` (von der Infrastruktur bereitgestellt)
- Fuer den produktiven Betrieb: Traefik als Reverse-Proxy und Cloudflare Access
  als Authentisierung am Edge (siehe [Sicherheit](#sicherheit))

---

## Konfiguration

Alle Werte werden ueber `.env` gesetzt (Vorlage: `.env.example`).

| Variable                    | Pflicht | Default                          | Beschreibung                                              |
| --------------------------- | :-----: | -------------------------------- | --------------------------------------------------------- |
| `MONITOR_HOST`              |   ja    | –                                | Domain, auf die Traefik den Monitor routet                |
| `TRAEFIK_ENTRYPOINT`        |   ja    | `web`                            | Traefik-EntryPoint                                        |
| `API_CORS_ORIGINS`          |  nein   | leer                             | CORS-Origin-Liste fuer direkte API-Zugriffe (Dev)         |
| `DOCKER_SOCKET_PROXY_IMAGE` |  nein   | gepinntes `tecnativa`-Image      | Image-Pinning fuer den Docker-Socket-Proxy                |

**Optionale Feintuning-Parameter** (Defaults greifen, wenn nicht gesetzt):

| Variable                 | Default | Beschreibung                                  |
| ------------------------ | ------- | --------------------------------------------- |
| `DOCKER_STATS_TIMEOUT`   | `2.5`   | Timeout (Sek.) je Docker-Stats-Abfrage        |
| `DOCKER_INSPECT_TIMEOUT` | `1.0`   | Timeout (Sek.) je Container-Inspect           |
| `MAX_DOCKER_WORKERS`     | `8`     | Parallele Worker fuer Container-Metriken      |

> `MONITOR_PORT` ist veraltet und wird ignoriert. Der Container hoert intern
> immer auf Port 80; Traefik routet ebenfalls auf Port 80.

---

## API

Alle Endpunkte liegen unter dem Praefix `/api`.

| Methode | Pfad                      | Beschreibung                                      |
| ------- | ------------------------- | ------------------------------------------------- |
| `GET`   | `/api/`                   | Health-Check (`{"status": "live"}`)               |
| `GET`   | `/api/metrics/host`       | Host-Metriken inkl. `warnings`                    |
| `GET`   | `/api/metrics/containers` | Liste aller Docker-Container                      |
| `GET`   | `/api/metrics/all`        | Kombiniert: `host`, `containers`, `docker`-Status |

Host-Metriken tragen pro Block ein `available`-Flag und melden Lesefehler
gesammelt in `host.warnings`, z. B.:

```json
{
  "memory": { "usage_percent": 24.5, "available": true },
  "temperature": { "celsius": 0.0, "available": false },
  "warnings": ["Temperatur nicht lesbar"]
}
```

---

## Betrieb

| Skript                  | Zweck                                                                        |
| ----------------------- | ---------------------------------------------------------------------------- |
| `scripts/start.sh`      | Image bauen und Monitor starten                                              |
| `scripts/redeploy.sh`   | `git pull --ff-only`, neu bauen, starten und Smoke-Check ausfuehren          |
| `scripts/stop.sh`       | Monitor stoppen                                                              |
| `scripts/logs.sh`       | Logs verfolgen                                                               |
| `scripts/smoke.sh`      | Nach-Deploy-Check: API, Frontend, Security-Header, Proxy-Block, Haertung     |
| `scripts/diagnose.sh`   | Traefik-/Routing-/Container-Diagnose bei 404 oder fehlender Erreichbarkeit   |

```bash
./scripts/redeploy.sh            # Standard: pull + build + smoke
./scripts/redeploy.sh --force    # Container-Recreate erzwingen
./scripts/redeploy.sh --no-smoke # Smoke-Check ueberspringen
```

`scripts/smoke.sh` sollte nach jedem Deployment gruen durchlaufen. Es prueft die
Compose-Konfiguration, alle API-Endpunkte, die statische Frontend-Auslieferung,
die Security-Header, den POST-Block des Docker-Socket-Proxys und die
Runtime-Haertung des `pi-monitor`-Containers.

---

## Sicherheit

**Authentisierung am Edge.** Der Monitor bringt bewusst keine eigene Login-Maske
mit. Authentisierung erfolgt ueber **Cloudflare Access**, das auf den kompletten
Host-Pfad `/*` angewendet werden muss, damit sowohl statische Assets als auch
API-Aufrufe geschuetzt sind.

**Container-Haertung** (per `scripts/smoke.sh` verifiziert):

- Non-root-Betrieb als User `app`
- `read_only`-Root-Dateisystem, beschreibbarer Pfad nur als `tmpfs` (`/tmp`)
- `cap_drop: ALL` und `no-new-privileges`
- Base- und Proxy-Images per `@sha256`-Digest gepinnt (Dependabot haelt sie aktuell)
- HTTP-Security-Header: Content-Security-Policy, X-Content-Type-Options,
  Referrer-Policy, X-Frame-Options, Permissions-Policy

**Docker-Socket.** Der Socket wird nicht direkt in den Monitor gemountet, sondern
nur ueber `docker-socket-proxy` mit `POST=0` in einem rein internen Netz
erreichbar gemacht (read-only Socket-Mount).

---

## Entwicklung & Tests

Backend-Unit-Tests (pytest) decken die nicht-triviale Parsing- und Rechenlogik ab
(`/proc`-Parsing, Docker-Stats-Auswertung, Aggregation, API-Struktur):

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

Die **CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) laeuft bei
jedem Push und Pull Request:

1. **Backend-Tests** mit pytest
2. **Image-Build** (deckt `yarn build` inkl. ESLint und `pip install` ab)
3. **Boot-Smoke**: Container starten und `/api/` pruefen

Damit erhalten auch die woechentlichen **Dependabot**-PRs
([`.github/dependabot.yml`](.github/dependabot.yml)) automatisch ein Build-Gate.

---

## Projektstruktur

```
.
├── backend/
│   ├── server.py                # FastAPI-App: Host- und Container-Metriken
│   ├── requirements-docker.txt  # Laufzeit-Abhaengigkeiten
│   ├── requirements-dev.txt     # Test-Abhaengigkeiten
│   └── tests/                   # pytest-Unit-Tests
├── frontend/                    # React-Dashboard (CRA + CRACO + Tailwind)
│   ├── src/
│   └── package.json
├── scripts/                     # Betriebs-Skripte (start/stop/redeploy/smoke/...)
├── .github/
│   ├── workflows/ci.yml         # CI: Tests + Image-Build + Boot-Smoke
│   └── dependabot.yml
├── Dockerfile                   # Multi-Stage-Image (nginx + supervisor + uvicorn)
├── compose.yaml                 # Monitor + docker-socket-proxy
├── .env.example
└── LICENSE
```

---

## Lizenz

Apache License 2.0. Copyright 2026 Robin Gathmann. Den vollstaendigen Lizenztext
enthaelt die Datei [`LICENSE`](LICENSE).
