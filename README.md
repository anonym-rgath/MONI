# PI-MONI

Pi-Monitor-Repository fuer den Raspberry Pi.

Dieses Projekt enthaelt nur den Pi Monitor, sein Frontend, sein Backend und die Start-/Betriebsskripte. Infrastruktur wie Traefik, Tunnel, DNS, Backups oder allgemeines Pi-Setup liegt im separaten Projekt `PI-SETUP`.

## Inhalt

- `frontend/`: React-Frontend
- `backend/`: FastAPI-Backend
- `Dockerfile`: Multi-stage Image fuer Frontend, Backend und Nginx
- `scripts/start.sh`: Monitor bauen und starten
- `scripts/stop.sh`: Monitor stoppen
- `scripts/logs.sh`: Monitor-Logs verfolgen
- `compose.yaml`: Runtime fuer Monitor plus eingeschraenkten Docker-Socket-Proxy

## Start

```bash
./scripts/start.sh
```

Das Startskript erstellt `.env` bei Bedarf aus `.env.example` und ergaenzt fehlende Werte in bestehenden `.env`-Dateien.

Der Monitor ist danach ueber Traefik erreichbar:

```bash
http://monitor.sau-index.de
```

Die Domain kann in `.env` angepasst werden:

```env
MONITOR_HOST=monitor.sau-index.de
```

## Betrieb

```bash
./scripts/start.sh
./scripts/stop.sh
./scripts/logs.sh
docker compose ps
```

## Konfiguration

- `MONITOR_HOST`: Domain, auf die Traefik fuer den Monitor routet
- `TRAEFIK_ENTRYPOINT`: Traefik-EntryPoint, standardmaessig `web`
- `API_CORS_ORIGINS`: optionale CORS-Origin-Liste fuer direkte API-Zugriffe im Dev-Modus
- `DOCKER_SOCKET_PROXY_IMAGE`: optionales Image-Pinning fuer den Docker-Socket-Proxy

## Abgrenzung zu PI-SETUP

`PI-MONI` startet keinen Reverse Proxy und keine Infrastruktur-Dienste. Wenn der Monitor per Domain, Cloudflare Tunnel oder Traefik erreichbar sein soll, wird das im Projekt `PI-SETUP` verdrahtet.
