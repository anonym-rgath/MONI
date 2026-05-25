# Security TODO

Diese Liste sammelt die Security-Findings aus dem Review, damit sie schrittweise abgearbeitet werden koennen.

## Hoch

- [x] App/API absichern
  - Ausgangslage: Dashboard und API hatten keine eigene Authentisierung; Cloudflare Access schuetzte nur den externen Pfad.
  - Erledigt/Architekturentscheidung: Cloudflare Access ist die verpflichtende Auth-Schicht am Edge und muss fuer `monitor.sau-index.de/*` gelten. Eine zusaetzliche Basic-Auth im Monitor wurde wegen schlechter UX wieder entfernt.

## Mittel

- [x] `pi-monitor` nicht mehr als root betreiben
  - Erledigt: Image nutzt den User `app`; Smoke-Test bestaetigt `Config.User=app`.

- [ ] `docker-socket-proxy` nicht mehr als root betreiben
  - Der Proxy ist durch `POST=0`, internes Netzwerk und `no-new-privileges` gehaertet.
  - Offen: Non-root Betrieb separat pruefen, da Zugriff auf `/var/run/docker.sock` von Socket-Rechten abhaengt.

- [x] `pi-monitor` Dateisystem haerten
  - Erledigt: `read_only: true` fuer `pi-monitor`; notwendiger Runtime-Pfad `/tmp` liegt auf `tmpfs`.
  - Smoke-Test ist mit read-only Root-FS erfolgreich.

- [ ] `docker-socket-proxy` Dateisystem haerten
  - Zurueckgestellt: `read_only`/`tmpfs` am Proxy kann auf dem Pi die Container-Anzeige brechen.
  - Ziel: Separat mit Proxy-Healthcheck und direktem `/containers/json`-Test validieren.

- [x] `pi-monitor` Linux Capabilities reduzieren
  - Erledigt: `cap_drop: ["ALL"]` fuer `pi-monitor`.
  - Smoke-Test ist ohne zusaetzliche Capabilities erfolgreich.

- [ ] `docker-socket-proxy` Linux Capabilities reduzieren
  - Zurueckgestellt: `cap_drop: ["ALL"]` am Proxy separat auf dem Pi validieren.
  - Ziel: Erst wieder aktivieren, wenn die Container-Liste ueber den Proxy stabil funktioniert.

- [ ] Docker- und Base-Images pinnen
  - `tecnativa/docker-socket-proxy:latest`, `node:20-alpine` und `python:3.11-slim` sind Floating Tags.
  - Ziel: Images per Version/Digest pinnen und Update-Prozess dokumentieren.

- [x] Frontend-Abhaengigkeiten reproduzierbar machen
  - Erledigt: `frontend/yarn.lock` erzeugt und Dockerfile auf `yarn install --frozen-lockfile --non-interactive` umgestellt.

- [ ] Veraltete Frontend-Toolchain pruefen
  - `react-scripts` bringt viele deprecated transitive Dependencies mit.
  - Ziel: Migration auf eine gepflegte Build-Toolchain pruefen, z. B. Vite, oder Dependencies gezielt aktualisieren.

- [ ] Host-Mounts minimieren
  - `/proc`, `/sys` und `/etc/hostname` werden read-only in den Container gemountet.
  - Ziel: Pruefen, ob alle Mounts vollstaendig benoetigt werden, und Zugriff so klein wie moeglich halten.

- [x] HTTP-Security-Header setzen
  - Erledigt und per Smoke-Test bestaetigt: `Content-Security-Policy`, `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options` und `Permissions-Policy`.

- [ ] Lokalen Compose-Betrieb robuster machen
  - `docker compose up -d --no-deps pi-monitor` scheitert ohne externes `traefik-network`.
  - Ziel: Lokales Override oder klar dokumentierte Voraussetzung fuer das externe Netzwerk bereitstellen.

## Niedrig

- [x] `.DS_Store` aus dem Repository entfernen
  - Erledigt: Datei aus dem Arbeitsbaum entfernt und `.gitignore` um `.DS_Store` ergaenzt.

- [ ] API-Fehlerverhalten verbessern
  - Backend gibt bei Docker-/Host-Lesefehlern oft stille Defaultwerte zurueck.
  - Ziel: Fehlerzustand im API-Response sichtbar machen, damit das Dashboard falsche Nullen nicht als echte Metriken interpretiert.

- [x] Healthchecks erweitern
  - Erledigt: `scripts/smoke.sh` prueft `/api/`, `/api/metrics/host`, `/api/metrics/containers`, `/api/metrics/all` und statische Frontend-Auslieferung.

- [x] Security-Testcheckliste automatisieren
  - Erledigt: `scripts/smoke.sh` prueft Compose-Config, API, Frontend, Security-Header, Docker-Socket-Proxy-POST-Block und Runtime-Haertung.
