# Lokale Testumgebung (Docker Desktop)

Manager und Agent laufen auf einem Rechner mit Docker Desktop, mit den veröffentlichten Images. So lässt sich der
ganze Ablauf ausprobieren: Ersteinrichtung, Kopplung per mTLS, Daten, Alarme. **Nur zum Testen**, produktiv gelten
[Installation](../docs/installation.md) und die Compose-Dateien im Hauptordner.

## Was läuft

| Container | Aufgabe | Erreichbar |
|---|---|---|
| `moni-manager-moni-manager-1` | Zentrale: Weboberfläche, API, Verlauf (SQLite), Alarme, eigene CA | <https://localhost:8443> |
| `moni-manager-docker-socket-proxy-1` | liest für den Manager die Container des eigenen Hosts, nur lesend | nur intern |
| `moni-agent-moni-agent-1` | Agent: misst den Host, API mit TLS | im Netz `moni-lokal` als `https://moni-agent:9810` |
| `moni-agent-docker-socket-proxy-1` | dasselbe für den Agent | nur intern |

Auf Docker Desktop messen beide die Linux-VM von Docker, nicht den Mac selbst. Deshalb fehlen dort etwa
Temperaturen; Moni zeigt das als Hinweis statt erfundener Werte.

Unterschiede zur echten Installation stehen in den zwei Override-Dateien in diesem Ordner: Das Host-Dateisystem wird
ohne `rslave` eingebunden (kennt Docker Desktop nicht), und beide Stacks hängen zusätzlich am Netz `moni-lokal`,
damit der Manager den Agent per Namen erreicht. Auf echten Servern liegen Manager und Agent auf verschiedenen
Rechnern, der Manager erreicht den Agent dann über dessen Adresse.

## Einmal vorbereiten

Alle Befehle im Hauptordner des Repositorys ausführen.

```
docker network create moni-lokal
```

`lokal/.env` (nicht im Git) enthält einen eigenen Master-Schlüssel nur für diese Umgebung und `DOCKER_GID=0`
(Docker Desktop). Neu anlegen, falls sie fehlt:

```
umask 077
printf 'MONI_SECRET_KEY=%s\nDOCKER_GID=0\n' "$(docker run --rm ghcr.io/anonym-rgath/moni-manager:2.0.0 python -m moni.cli secret generate | tail -n1)" > lokal/.env
```

Damit die Befehle kurz bleiben, zwei Hilfsfunktionen für die aktuelle Shell:

```
manager() { docker compose --env-file lokal/.env -f compose.manager.yaml -f lokal/compose.manager.lokal.yaml "$@"; }
agent()   { docker compose --env-file lokal/.env -f compose.agent.yaml -f lokal/compose.agent.lokal.yaml "$@"; }
```

## Starten und stoppen

```
manager up -d
agent up -d
```

Status: `manager ps`, `agent ps`. Logs: `manager logs -f moni-manager`. Stoppen ohne Datenverlust: `manager stop`,
`agent stop`. Alles löschen (Daten, Kopplung, Benutzer): `manager down -v` und `agent down -v`.

## 1. Ersteinrichtung

Der Manager schreibt beim ersten Start ein einmaliges Setup-Token ins Log:

```
manager logs moni-manager | grep -i setup
```

<https://localhost:8443> öffnen. Der Browser warnt, weil das Zertifikat von Monis eigener CA stammt; für den Test
bestätigen. Setup-Token, E-Mail, Name und Passwort eingeben, dann 2FA mit einer Authenticator-App einrichten und die
Wiederherstellungscodes sichern.

## 2. Agent koppeln

Der ungekoppelte Agent schreibt seinen Kopplungscode ins Log:

```
agent logs moni-agent | grep moni-pair-v1
```

In der Oberfläche **Verwaltung → Server → Server koppeln**: Name frei wählen, URL `https://moni-agent:9810`, den Code
`moni-pair-v1:…` einfügen. Was dabei passiert: Der Manager prüft den Fingerabdruck im Code gegen das vorläufige
Zertifikat des Agents, der Agent erzeugt einen privaten Schlüssel (der den Agent nie verlässt) und eine
Zertifikatsanfrage, die CA des Managers signiert sie. Ab dann spricht der Manager nur noch per mTLS mit dem Agent.
Mehr dazu in [Kopplung](../docs/kopplung.md).

Nach etwa 15 Sekunden erscheinen die ersten Werte in der Übersicht.

## 3. Alarm ausprobieren

Die Standardregel „Server nicht erreichbar“ gilt für alle Server. Agent anhalten:

```
agent stop moni-agent
```

Nach einigen Abrufen steht der Alarm unter **Alarme → Offen**. Wieder starten mit `agent start moni-agent`: Der Alarm
wird behoben, und der Agent liefert ohne neue Kopplung weiter Daten. Einen Webhook- oder E-Mail-Kanal legen Sie unter
**Alarme → Kanäle** an, mit „Testnachricht senden“ lässt er sich prüfen.

## 4. API und Kennzahlen

Unter **Verwaltung → API-Tokens** ein Token mit Scope `read` erzeugen, dann zum Beispiel:

```
curl -sk -H "Authorization: Bearer <TOKEN>" https://localhost:8443/api/v1/servers
curl -sk -H "Authorization: Bearer <TOKEN>" https://localhost:8443/api/v1/metrics
```

Die interaktive API-Doku liegt unter <https://localhost:8443/api/v1/docs>.

## Aufräumen

```
manager down -v
agent down -v
docker network rm moni-lokal
rm lokal/.env
```
