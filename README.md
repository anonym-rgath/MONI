# PI-MONI

Pi-Monitor-Repository fuer den Raspberry Pi.

Dieses Projekt enthaelt nur den Pi Monitor, sein Frontend, sein Backend und die Start-/Betriebsskripte. Infrastruktur wie Traefik, Tunnel, DNS, Backups oder allgemeines Pi-Setup liegt im separaten Projekt `PI-SETUP`.

## Inhalt

- `apps/pi-monitor`: React-Frontend, FastAPI-Backend und Dockerfile
- `scripts/start.sh`: Monitor bauen und starten
- `scripts/stop.sh`: Monitor stoppen
- `scripts/logs.sh`: Monitor-Logs verfolgen
- `compose.yaml`: Runtime fuer Monitor plus eingeschraenkten Docker-Socket-Proxy

## Start

```bash
cp .env.example .env
./scripts/start.sh
```

Der Monitor ist danach lokal auf dem konfigurierten Port erreichbar:

```bash
http://<pi-host>:8080
```

Der Port kann in `.env` angepasst werden:

```env
MONITOR_PORT=8080
```

## Betrieb

```bash
./scripts/start.sh
./scripts/stop.sh
./scripts/logs.sh
docker compose ps
```

## Konfiguration

- `MONITOR_PORT`: lokaler HTTP-Port des Monitors
- `API_CORS_ORIGINS`: optionale CORS-Origin-Liste fuer direkte API-Zugriffe im Dev-Modus
- `DOCKER_SOCKET_PROXY_IMAGE`: optionales Image-Pinning fuer den Docker-Socket-Proxy

## Abgrenzung zu PI-SETUP

`PI-MONI` startet keinen Reverse Proxy und keine Infrastruktur-Dienste. Wenn der Monitor per Domain, Cloudflare Tunnel oder Traefik erreichbar sein soll, wird das im Projekt `PI-SETUP` verdrahtet.
