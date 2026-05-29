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
- `scripts/redeploy.sh`: Nach `git pull` neu bauen, starten und pruefen
- `scripts/smoke.sh`: Nach-Deploy-Check fuer API, Frontend, Security-Header und Runtime-Haertung
- `scripts/diagnose.sh`: Traefik-/Routing-/Container-Diagnose bei 404 oder fehlender Erreichbarkeit
- `compose.yaml`: Runtime fuer Monitor plus eingeschraenkten Docker-Socket-Proxy

## Start

```bash
./scripts/start.sh
```

Das Startskript erstellt `.env` bei Bedarf aus `.env.example` und ergaenzt fehlende Werte in bestehenden `.env`-Dateien.

Der Monitor ist danach ueber Traefik erreichbar:

```bash
http://monitor.example.com
```

Die Domain kann in `.env` angepasst werden:

```env
MONITOR_HOST=monitor.example.com
```

Fuer den produktiven Betrieb muss `MONITOR_HOST` auf die echte Traefik-Domain gesetzt werden. Die Scripts brechen mit dem Beispielwert `monitor.example.com` ab, damit kein gesunder Container mit falscher Traefik-Route deployt wird.

## Betrieb

```bash
./scripts/start.sh
./scripts/redeploy.sh
./scripts/stop.sh
./scripts/logs.sh
./scripts/smoke.sh
./scripts/diagnose.sh
docker compose ps
```

`scripts/smoke.sh` sollte nach Deployments gruen durchlaufen. Es prueft API-Endpunkte, statische Frontend-Auslieferung, Security-Header, den Docker-Socket-Proxy-POST-Block und die Runtime-Haertung des `pi-monitor` Containers.

`scripts/redeploy.sh` nutzt `git pull --ff-only`, baut `pi-monitor` neu, startet den Container und fuehrt danach automatisch `scripts/smoke.sh` aus. Mit `--force` wird ein Recreate erzwungen; mit `--no-smoke` wird der Smoke-Check uebersprungen.

## Security

Der Monitor ist fuer den Betrieb hinter Traefik und Cloudflare Access gedacht. Cloudflare Access muss auf den kompletten Host-Pfad `/*` angewendet werden, damit statische Assets und API-Aufrufe geschuetzt sind. Der Monitor selbst erzwingt keine eigene Login-Maske; Authentisierung gehoert in diesem Setup an den Edge. Der Docker-Socket wird nicht direkt gemountet, sondern nur ueber `docker-socket-proxy` mit deaktivierten POST-Requests erreichbar gemacht.

## Konfiguration

- `MONITOR_HOST`: Domain, auf die Traefik fuer den Monitor routet
- `TRAEFIK_ENTRYPOINT`: Traefik-EntryPoint, standardmaessig `web`
- `API_CORS_ORIGINS`: optionale CORS-Origin-Liste fuer direkte API-Zugriffe im Dev-Modus
- `DOCKER_SOCKET_PROXY_IMAGE`: optionales Image-Pinning fuer den Docker-Socket-Proxy

Optionale Feintuning-Parameter (Defaults greifen, wenn nicht gesetzt):

- `DOCKER_STATS_TIMEOUT`: Timeout in Sekunden je Docker-Stats-Abfrage (Default `2.5`)
- `DOCKER_INSPECT_TIMEOUT`: Timeout in Sekunden je Container-Inspect (Default `1.0`)
- `MAX_DOCKER_WORKERS`: parallele Worker fuer Container-Metriken (Default `8`)

`MONITOR_PORT` ist veraltet und wird nicht mehr verwendet. Der Container hoert intern immer auf Port 80; Traefik routet auf `traefik.http.services.pi-monitor.loadbalancer.server.port=80`.

## Abgrenzung zu PI-SETUP

`PI-MONI` startet keinen Reverse Proxy und keine Infrastruktur-Dienste. Wenn der Monitor per Domain, Cloudflare Tunnel oder Traefik erreichbar sein soll, wird das im Projekt `PI-SETUP` verdrahtet.

## Lizenz

Apache License 2.0. Copyright 2026 Robin Gathmann. Den vollstaendigen Lizenztext enthaelt die Datei `LICENSE`.
