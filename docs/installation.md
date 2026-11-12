# Installation

Moni besteht aus zwei Rollen, die jeweils als Docker-Compose-Stack laufen: dem **Agent** (misst einen Host)
und dem **Manager** (Weboberfläche, Verläufe, Alarme). Diese Seite beschreibt die Installation beider Rollen.
Den Agent mit dem Manager zu verbinden steht in [Kopplung](kopplung.md).

## Voraussetzungen

- Linux-Host mit Docker Engine und dem Compose-Plugin (`docker compose`). Architekturen: amd64 und arm64.
- BuildKit, wenn Sie lokal bauen (das Dockerfile nutzt `FROM --platform=$BUILDPLATFORM`). Aktuelle Docker-Versionen bringen es mit.
- Zugriff auf `ghcr.io` zum Laden der Images.
- Der Benutzer, der die Skripte ausführt, darf den Docker-Socket nutzen.
- Docker Desktop auf macOS: Die Einbindung `/:/host/root:ro,rslave` in den Compose-Dateien funktioniert dort
  nicht. Für den Dauerbetrieb nehmen Sie einen Linux-Host.

## Repository holen

```
git clone https://github.com/anonym-rgath/MONI.git
cd MONI
cp .env.example .env
chmod 600 .env
```

`.env` enthält Geheimnisse und gehört nie in ein Repository. Compose liest sie automatisch.
Tragen Sie dort außerdem `DOCKER_GID=<Wert>` ein (Linux: `stat -c %g /var/run/docker.sock`, Docker Desktop auf macOS: `0`,
siehe [`DOCKER_GID`](#docker_gid)). Die Skripte setzen den Wert selbst; mit dem Eintrag funktionieren auch Ihre
eigenen `docker compose`-Aufrufe (`exec`, `logs`, `cp`), die den Wert sonst ebenfalls verlangen.

## Signatur der Images prüfen

Alle Images sind mit cosign (keyless über GitHub-OIDC) signiert. Prüfen Sie die Signatur, bevor Sie ein Image
zum ersten Mal starten:

```
cosign verify ghcr.io/anonym-rgath/moni-agent:2.0.0 \
  --certificate-identity-regexp '^https://github.com/anonym-rgath/MONI/\.github/workflows/release\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Für das Manager-Image ersetzen Sie `moni-agent` durch `moni-manager`. Die Images enthalten außerdem ein SBOM
und eine Provenance-Attestierung. Den Ablauf, der sie erzeugt, beschreibt [Release](release.md).

## Manager installieren

1. Master-Schlüssel erzeugen:

   ```
   docker run --rm ghcr.io/anonym-rgath/moni-manager:2.0.0 python -m moni.cli secret generate
   ```

2. Den ausgegebenen Wert in `.env` als `MONI_SECRET_KEY=` eintragen.
3. Starten:

   ```
   ./scripts/start.sh manager
   ```

4. Den Setup-Token aus dem Log lesen (er steht dort einmalig, solange kein Admin existiert):

   ```
   docker compose -f compose.manager.yaml logs moni-manager | grep "Setup-Token"
   ```

5. `https://localhost:8443` öffnen (Browserwarnung: das Zertifikat stammt aus der eigenen Manager-CA), den ersten
   Admin mit dem Setup-Token anlegen und die Zwei-Faktor-Anmeldung (TOTP) einrichten. Bewahren Sie die
   Wiederherstellungscodes sicher auf.

### Master-Schlüssel

`MONI_SECRET_KEY` verschlüsselt alle umkehrbar gespeicherten Geheimnisse: TOTP-Secrets, SMTP-Passwort,
OIDC-Client-Secret, Webhook-HMAC-Secrets, Agent-Tokens im Token-Modus und den CA-Schlüssel auf der Platte.

- Er liegt in `.env` auf dem Host, **nicht** im Daten-Volume. Sichern Sie ihn getrennt von der Datenbank
  (siehe [Backup](backup.md)). Ohne ihn sind 2FA, Alarmkanäle und Kopplungen verloren.
- Er erscheint nie im Log und nicht in der Ausgabe von `scripts/diagnose.sh`. Geben Sie ihn nicht weiter und
  geben Sie `docker compose config` nicht weiter: das gibt ihn aus.
- Schlüsselwechsel: Erzeugen Sie einen neuen Schlüssel, setzen Sie `MONI_SECRET_KEY=<neu>,<alt>` (der erste
  verschlüsselt, alle entschlüsseln), starten Sie den Manager neu und führen Sie
  `docker compose -f compose.manager.yaml exec moni-manager python -m moni.cli secret reencrypt` aus. Danach setzen Sie
  `MONI_SECRET_KEY=<neu>` und starten neu. Sichern Sie vorher die Datenbank.

### Erstpasswörter und Zugangsdaten übergeben

Neue Benutzer legt ein Admin an und vergibt dabei ein Anfangspasswort. Geben Sie es nur über einen sicheren
Kanal weiter (Passwortmanager, persönlich), nie per E-Mail oder im Chat, und lassen Sie es beim ersten
Anmelden ändern. Passwörter und Tokens übergeben Sie der CLI per Datei oder Standardeingabe
(`--password-file`, `--token-file`, `--pairing-code-file`, `-` für stdin), nie als Argument: Argumente landen in der Shell-History.

## Agent installieren

```
./scripts/start.sh agent
```

Der Agent hört standardmäßig nur auf `127.0.0.1:9810`. Damit ein Manager auf einem anderen Rechner ihn erreicht,
setzen Sie in `.env`:

```
MONI_AGENT_BIND=0.0.0.0
MONI_PUBLIC_URL=https://agent.example.org:9810
```

und lassen den Port 9810 per Firewall nur vom Manager zu. `MONI_PUBLIC_URL` wird in den Kopplungscode
eingebettet; ohne sie geben Sie die URL beim Koppeln selbst an. Weiter mit [Kopplung](kopplung.md).

Manager und Agent auf demselben Host sind möglich. Die Compose-Dateien tragen feste Projektnamen
(`moni-manager` und `moni-agent`), damit sich beide Stacks nicht in die Quere kommen und `docker compose -f compose.<rolle>.yaml …`
immer den richtigen Stack trifft.

## Docker-Socket und Socket-Proxy

Moni liest Container-Informationen über die Docker-API, bekommt aber nie den Socket selbst. Jede Compose-Datei
bringt einen eigenen `docker-socket-proxy` (`wollomatic/socket-proxy`, per Digest gepinnt) mit. Seine Allowlist erlaubt
ausschließlich `GET` auf die Containerliste sowie `inspect` und `stats` einzelner Container. Kein `logs`, kein
`exec`, keine schreibenden Methoden. Nur der jeweilige Moni-Dienst darf sich verbinden, das Netz `socket-proxy` ist `internal`.

### `DOCKER_GID`

Der Proxy läuft als Nutzer `65534` mit der Gruppe des Docker-Sockets. `DOCKER_GID` ist **Pflicht**:

- Linux: `stat -c %g /var/run/docker.sock`
- Docker Desktop auf macOS: `0`

Die Skripte `scripts/*.sh` setzen den Wert selbst, wenn er weder in der Umgebung noch in `.env` steht. Wenn Sie
`docker compose` direkt aufrufen, tragen Sie ihn in `.env` ein. Ein falscher Wert zeigt sich so: Moni meldet Docker
als „nicht erreichbar“ (Alarm „Docker-API nicht erreichbar“), obwohl Container laufen.

### Vorhandenen zentralen Socket-Proxy nutzen

Wer schon einen Proxy mit passender Allowlist betreibt, ersetzt den mitgelieferten per Override. Beispiel
`compose.override.yaml` für den Agent (für den Manager entsprechend mit `moni-manager`):

```yaml
services:
  moni-agent:
    environment:
      - DOCKER_API_BASE=http://mein-socket-proxy:2375
    networks:
      - default
      - zentraler-proxy
  docker-socket-proxy:
    profiles: ["nicht-starten"]

networks:
  zentraler-proxy:
    external: true
```

`compose.override.yaml` liest Compose nur, wenn Sie `docker compose` ohne `-f` aufrufen oder beide Dateien
mit `-f` nennen. Die Skripte rufen `docker compose -f compose.<rolle>.yaml` auf und lesen den Override daher nicht;
starten Sie in dem Fall mit `docker compose -f compose.agent.yaml -f compose.override.yaml up -d`.
Der zentrale Proxy muss dieselbe Allowlist (nur `GET`, nur Container-Liste, `inspect`, `stats`) erzwingen.

## Images lokal bauen

Statt der veröffentlichten Images aus GHCR bauen Sie selbst (BuildKit nötig):

```
docker build --target agent -t moni-agent:local .
docker build --target manager -t moni-manager:local .
```

und setzen in `.env` `MONI_AGENT_IMAGE=moni-agent:local` bzw. `MONI_MANAGER_IMAGE=moni-manager:local`. Mit
`./scripts/redeploy.sh agent --local` (bzw. `manager`) startet der Stack neu, ohne zu ziehen.

## Variablen

Gesetzt werden sie in `.env` (Compose reicht sie an den Container). Ohne Eintrag gilt der Standardwert.

| Variable | Rolle | Standard | Bedeutung |
|---|---|---|---|
| `MONI_SECRET_KEY` | Manager | – (Pflicht) | Master-Schlüssel, siehe [oben](#master-schlüssel) |
| `MONI_MANAGER_IMAGE` | Manager | `ghcr.io/anonym-rgath/moni-manager:2.0.0` | Image des Managers |
| `MONI_AGENT_IMAGE` | Agent | `ghcr.io/anonym-rgath/moni-agent:2.0.0` | Image des Agents |
| `DOCKER_GID` | beide | – (Pflicht) | Gruppe des Docker-Sockets, siehe [oben](#docker_gid) |
| `MONI_TLS` | beide | `on` | `on`: HTTPS (Agent mit mTLS, Manager mit eigenem Zertifikat). `off`: nur hinter einem vertrauenswürdigen Proxy, siehe [Proxy](proxy.md) |
| `MONI_TRUSTED_PROXIES` | beide | leer | IPs/CIDRs vertrauenswürdiger Proxys, kommagetrennt. Wirkt nur bei `MONI_TLS=off`; bei `on` wird sie ignoriert (Warnung im Log) |
| `MONI_MANAGER_BIND` | Manager | `127.0.0.1` | Host-Adresse, an die der Manager seine Ports bindet |
| `MONI_HTTPS_PORT` | Manager | `8443` | Veröffentlichter HTTPS-Port auf dem Host (Ziel der Umleitung von Port 8080, Healthcheck) |
| `MONI_MANAGER_HOSTNAMES` | Manager | Hostname, `localhost`, `127.0.0.1` | Namen und IPs im HTTPS-Zertifikat, kommagetrennt |
| `MONI_AGENT_BIND` | Agent | `127.0.0.1` | Host-Adresse für Port 9810; `0.0.0.0` für einen Manager auf anderem Rechner |
| `MONI_PUBLIC_URL` | Agent | leer | `https://host[:port]`, wird in den Kopplungscode eingebettet; kein Pfad |
| `MONI_COLLECT_INTERVAL` | beide | `15` | Mess- und Abruftakt in Sekunden |
| `MONI_POLL_TIMEOUT` | Manager | `10` | Timeout je Agent-Abruf in Sekunden |
| `MONI_RETENTION_RAW_HOURS` | Manager | `48` | Aufbewahrung der Rohdaten in Stunden |
| `MONI_RETENTION_MINUTE_DAYS` | Manager | `30` | Aufbewahrung der Minutenwerte in Tagen |
| `MONI_RETENTION_HOUR_DAYS` | Manager | `365` | Aufbewahrung der Stundenwerte in Tagen |
| `MONI_SESSION_IDLE_MINUTES` | Manager | `60` | Abmeldung nach Leerlauf in Minuten |
| `MONI_SESSION_MAX_HOURS` | Manager | `12` | Absolute Sitzungsdauer in Stunden |
| `API_CORS_ORIGINS` | beide | leer | Erlaubte Ursprünge für Browser-Zugriffe auf die API, kommagetrennt |
| `DOCKER_STATS_TIMEOUT` | beide | `2.5` | Timeout je Docker-Stats-Abfrage in Sekunden |
| `DOCKER_INSPECT_TIMEOUT` | beide | `1.0` | Timeout je Container-Inspect in Sekunden |
| `MAX_DOCKER_WORKERS` | beide | `8` | Parallele Worker für Container-Metriken |

Die folgenden Variablen setzt das Image bzw. die Compose-Datei. Ändern Sie sie nur, wenn Sie wissen warum.

| Variable | Standard | Bedeutung |
|---|---|---|
| `MONI_ROLE` | `agent` (im Manager-Image `manager`) | Rolle des Prozesses |
| `MONI_DATA_DIR` | `/data` | Datenverzeichnis im Container (das Volume) |
| `MONI_PORT` | `9810` (Manager intern `8001`) | Port des Python-Prozesses im Container |
| `MONI_BIND_HOST` | `0.0.0.0` (Manager intern `127.0.0.1`) | Bind-Adresse des Python-Prozesses |
| `MONI_TRUST_LOCAL_PROXY` | leer (Manager intern `1`) | Weiterleitungs-Header vom nginx im Manager-Image auswerten |
| `MONI_BUFFER_SECONDS` | `7200` | Größe des Ringpuffers im Agent (Sekunden) |
| `MONI_RATE_LIMIT_PER_MINUTE` | `120` | Anfragen pro Minute und Client, bevor die API mit 429 antwortet |

Ungültige Werte verhindern den Start mit einer deutschen Fehlermeldung (`docker compose logs`).

## Prüfen

```
./scripts/smoke.sh agent      # bzw. manager
```

Weitere Skripte: [Betrieb](betrieb.md).
