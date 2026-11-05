# Moni 2.0 – Agent & Manager (Design)

Stand: 2026-11-04 · Status: Entwurf zur Freigabe

## 1. Absicht und Warum

Moni ist heute ein Live-Dashboard für **einen** Linux-Host (React + FastAPI, ein
gehärteter Container, keine Datenbank, kein eigener Login – Auth liegt bei
Cloudflare Access davor). Es zeigt nur „jetzt“, merkt sich nichts und meldet
nichts.

Ziel ist ein **professionelles, quelloffenes Monitoring** auf Business-Niveau
(professionelle Qualität, die ein Admin oder eine kleine Firma sicher einsetzen
kann – kein Enterprise-Mandantensystem):

- Auf jedem Server läuft ein schlanker **Moni-Agent** ohne Oberfläche, der seine
  Daten über eine **verschlüsselte, authentisierte API** bereitstellt. Diese API
  ist ein eigenständiges Produkt: Sie muss ohne Weboberfläche nutzbar und in
  Fremdsysteme (Grafana, Prometheus, n8n, eigene Skripte) einbindbar sein.
- Ein **Moni-Manager** (die „Zentrale“) holt die Daten mehrerer Agents ab,
  speichert den Verlauf, wertet Alarmregeln aus und bietet eine saubere
  Weboberfläche mit eigenem Login. Auch der Manager hat eine vollständige API.
- Das Repo bleibt öffentlich und generisch: **keine Abhängigkeit von Cloudflare**
  oder anderen Diensten des Betreibers. Cloudflare Tunnel/Access, Traefik usw.
  sind optionale Betriebsvarianten.

Konkreter Einsatz beim Autor: Manager auf dem Raspberry Pi (Heimnetz), Agent auf
dem SAU-INDEX-Server (Strato). Der Game-Server ist vorerst außen vor.

**Alles erscheint in einer Version** (kein Zwischen-Release).

## 2. Ziele

1. Zwei Rollen aus einer Codebasis: **Agent** und **Manager**.
2. Agent-API (versioniert) mit JSON **und** Prometheus/OpenMetrics-Endpunkt.
3. Sichere Kommunikation Manager ↔ Agent per **mTLS** mit Kopplungsablauf.
4. Verlaufsdaten im Manager (SQLite) mit Verdichtung und Aufbewahrungsfristen.
5. Login: Passwort + TOTP-2FA + Wiederherstellungscodes, zusätzlich **OIDC**
   (Google, Microsoft, Keycloak, Authentik …).
6. Rollen **Admin** und **Betrachter**, Audit-Log.
7. **Alarmierung**: Regeln mit Schwelle und Dauer, Zustände ausgelöst/behoben,
   Versand per Webhook und E-Mail.
8. Oberfläche: Gesamtübersicht aller Server, Detailansicht je Server mit
   Verlaufscharts, Alarme, Verwaltung.
9. Professioneller Open-Source-Rahmen: Releases, signierte Multi-Arch-Images,
   Doku, Security-Policy, CI mit Sicherheits-Scans.
10. Bestehende Qualitäten bleiben: Container-Härtung, Docker-Socket nur über
    Read-only-Proxy, ehrliche `available`/`warnings`-Semantik.

## 3. Nicht-Ziele (diese Version)

- Game-Server bzw. Hosts ohne Docker; Installation ohne Docker (systemd, deb/rpm).
- Mandantenfähigkeit, Hochverfügbarkeit, mehrere Manager im Verbund.
- Push-Modell (Agent sendet an Manager).
- Log-Sammlung, APM/Tracing, HTTP-/Uptime-Checks externer Websites.
- Passkeys/WebAuthn, SMS-2FA.
- Aktionen auf Hosts (Container neu starten o. ä.).

## 4. Anti-Ziele (wäre ein Fehlschlag, auch wenn es „funktioniert“)

- Erfundene oder stille Ersatzwerte (z. B. heute `1500` MHz bei unlesbarem Takt,
  CPU `0 %` beim ersten Aufruf). Unbekannt heißt `null` + `available: false`.
- Der Agent kann irgendetwas am Host verändern. Er ist strikt lesend.
- Betrieb setzt Cloudflare, eine Domain oder die Weboberfläche voraus.
- Geheimnisse (Tokens, Passwörter, TOTP-Secrets, private Schlüssel) im Klartext in
  Logs, API-Antworten (außer einmalig bei Erzeugung) oder in der Datenbank.
- Ein Update zerstört Verlaufsdaten oder Kopplungen.
- Mehrere Browser-Tabs vervielfachen die Last auf den Hosts (heute der Fall).

## 5. Rahmenbedingungen

- Laufzeit: Docker + Compose, Architekturen **amd64 und arm64** (Pi).
- Manager muss auf einem Raspberry Pi mit SD-Karte laufen: Schreibzugriffe
  bündeln, WAL-Modus.
- Lizenz Apache-2.0; nur Open-Source-Abhängigkeiten.
- Sprache von Oberfläche und Doku: Deutsch (Englisch später möglich, Texte daher
  nicht im Code verstreuen, sondern zentral halten).
- Bestehender Stack bleibt Basis: Python/FastAPI, React, Tailwind, Recharts.

## 6. Architektur

```
                       ┌──────────────────────── Moni-Manager (Pi) ────────────────────────┐
Browser ─HTTPS─▶ nginx ─┤ /        → React-Build (Vite)                                       │
                       │ /api/v1  → FastAPI: Auth, Server, Verlauf, Alarme, Tokens, Audit   │
                       │            ├─ Sammler (Scheduler, 15 s)  ──mTLS──▶ Agents          │
                       │            ├─ lokaler Agent-Teil (misst eigenen Host, ohne Netz)  │
                       │            ├─ Alarm-Auswertung → Webhook / SMTP                    │
                       │            └─ SQLite (Volume)                                      │
                       └────────────────────────────────────────────────────────────────────┘

Moni-Agent (je Server): uvicorn mit TLS, FastAPI
   /api/v1/health · /api/v1/info · /api/v1/snapshot · /api/v1/history · /metrics
   liest /proc, /sys, Docker-API über docker-socket-proxy (read-only)
   Ringpuffer im Speicher (ca. 2 h)
```

### 6.1 Rollen und Images

Eine Codebasis, ein Dockerfile mit zwei Build-Targets:

| | `moni-agent` | `moni-manager` |
|---|---|---|
| Inhalt | Python + Backend (Agent-Teil) | Backend komplett + nginx + Frontend-Build |
| Prozess | uvicorn | nginx + uvicorn (wie heute via supervisor) |
| Persistenz | Volume für Schlüssel/Zertifikate/Token-Hashes | Volume für SQLite + Schlüssel/CA |
| Start | `docker compose -f compose.agent.yaml up -d` | `docker compose -f compose.manager.yaml up -d` |

Die Rolle wird bei der Installation über die Compose-Datei gewählt und ist
wechselbar. Der Manager misst seinen eigenen Host über denselben Code wie der
Agent, direkt im Prozess (kein Netzwerk, keine Kopplung nötig).

Beide Rollen behalten die Härtung von heute: non-root, `read_only`, `cap_drop: ALL`,
`no-new-privileges`, Digest-gepinnte Base-Images, Security-Header (Manager).
Docker-Zugriff ausschließlich über einen Read-only-Socket-Proxy. Weil das Repo
generisch ist, liefert jede Compose-Datei einen eigenen `docker-socket-proxy`
mit (POST=0, nur nötige Endpunkte); wer schon einen zentralen hat (wie der
Autor in PI-SETUP), kann ihn per Override nutzen.

### 6.2 Kommunikation Manager ↔ Agent

- **Pull**: Der Manager fragt, der Agent antwortet. Der Agent baut nie selbst
  Verbindungen auf und kennt keine Zugangsdaten zum Manager.
- Abruf alle **15 s** (konfigurierbar) über `GET /api/v1/snapshot`.
- **Lückenfüllung**: Agent hält einen Ringpuffer (ca. 2 h). Nach Ausfall des
  Managers oder Netzes holt der Manager fehlende Punkte über
  `GET /api/v1/history?since=<ts>` nach. (Puffer liegt im Speicher; Agent-Neustart
  verliert ihn – akzeptiert.)
- **Erreichbarkeit**: Nach 3 verpassten Abrufen gilt ein Server als „nicht
  erreichbar“; das ist ein eigener Alarmtyp.
- **Versionierung**: Pfad `/api/v1`. `GET /api/v1/info` liefert Agent-Version,
  Hostname, unterstützte Fähigkeiten. Der Manager muss mit Agents der gleichen
  Hauptversion umgehen können, auch wenn diese älter sind.

### 6.3 Sicherheit der Verbindung (mTLS) und Kopplung

- Der Manager betreibt eine **eigene kleine CA** (Schlüssel im Manager-Volume).
- Der Agent spricht standardmäßig **selbst TLS** – ohne Domain, ohne Let's
  Encrypt, ohne Proxy.
- **Kopplungsablauf**:
  1. Agent startet erstmals, erzeugt Schlüsselpaar + vorläufiges selbstsigniertes
     Zertifikat + **einmaliges Kopplungs-Token** und gibt einen
     **Kopplungscode** aus (Logausgabe und per CLI-Befehl erneut abrufbar,
     solange ungekoppelt): Adresse, Zertifikats-Fingerabdruck, Token in einer Zeile.
  2. Admin fügt den Code im Manager unter „Server hinzufügen“ ein (UI oder API).
  3. Der Manager verbindet sich, prüft den Fingerabdruck, legt das
     Kopplungs-Token vor, übergibt sein CA-Zertifikat und stellt dem Agent ein
     von der Manager-CA signiertes Serverzertifikat aus.
  4. Ab dann akzeptiert der Agent für Manager-Zugriffe nur Client-Zertifikate
     dieser CA; das Kopplungs-Token ist verbraucht.
  5. Neu koppeln (z. B. Manager-Neuaufbau) per CLI-Befehl am Agent, der die
     Kopplung zurücksetzt.
- **Zugriff durch Fremdsysteme** (Prometheus, Skripte): **API-Tokens** am Agent
  über TLS. Tokens haben Rechte (diese Version: nur `read`), optionales
  Ablaufdatum, sind einzeln widerrufbar, werden nur als Hash gespeichert und nur
  bei Erzeugung einmal angezeigt. Verwaltung per CLI am Agent (`moni-agent token
  create|list|revoke`) und – für gekoppelte Agents – über den Manager.
- **Betrieb hinter Reverse-Proxy** (Traefik, Caddy, nginx, Cloudflare Tunnel):
  TLS am Agent ausdrücklich abschaltbar (`MONI_TLS=off`). Dann gibt es kein
  End-to-End-mTLS; der Manager authentisiert sich mit einem Token. Diese
  Variante wird in UI und Logs als „schwächer“ gekennzeichnet und dokumentiert.
- **Zusätzliche HTTP-Header pro Server** im Manager (verschlüsselt gespeichert),
  z. B. `CF-Access-Client-Id`/`-Secret`, damit Agents hinter Zugangs-Proxys wie
  Cloudflare Access erreichbar sind – generisch, ohne Cloudflare-Sonderlogik.
- `GET /api/v1/health` ist ohne Auth erreichbar und verrät nichts außer „ok“.

**Setup des Autors** (entschieden, sicherere Option): Der Agent auf dem
SAU-INDEX-Server wird **über den bestehenden Cloudflare Tunnel** erreicht, es
wird kein Port geöffnet. Modus `MONI_TLS=off`, Schutz durch Cloudflare-Access-
Service-Token (als Zusatz-Header) **plus** Moni-Token. Das mTLS-Verfahren wird
trotzdem vollständig gebaut und im E2E-Test der CI geprüft.

### 6.4 Agent-API

| Pfad | Zweck |
|---|---|
| `GET /api/v1/health` | Lebenszeichen, ohne Auth |
| `GET /api/v1/info` | Version, Hostname, Fähigkeiten, Kopplungsstatus |
| `GET /api/v1/snapshot` | aktueller Stand Host + Container + Docker-Status + `warnings` |
| `GET /api/v1/history?since=` | Punkte aus dem Ringpuffer |
| `GET /metrics` | Prometheus/OpenMetrics-Format, gleiche Daten wie Snapshot |

Konventionen (gelten auch für den Manager):

- Veröffentlichte **OpenAPI-Spezifikation** (aus FastAPI generiert, im Repo
  abgelegt und in der CI auf Abweichung geprüft); Swagger-UI erreichbar.
- Fehler im Format **RFC 9457** (Problem Details).
- Zeitstempel UTC, ISO 8601. Einheiten im Feldnamen (`_bytes`, `_percent`,
  `_seconds`, `_celsius`). Rohwerte statt vorgerundeter MB/GB.
- Unbekannte Werte `null` + `available: false`, nie Ersatzwerte.
- Rate-Limits auf allen authentisierten Endpunkten.

### 6.5 Messumfang

Der Sammler misst einmal zentral im Takt (nicht pro Anfrage). Anfragen lesen den
letzten Messstand – das behebt die heutige CPU-Berechnung über ein globales
`_prev_cpu` (falsche Fenster bei mehreren Clients, 0 % beim ersten Aufruf) und die
Lastvervielfachung durch mehrere Tabs.

**Host**: CPU gesamt (%), Kerne, Takt; Load 1/5/15; RAM und Swap; **alle echten
Dateisysteme** (nicht nur Root; Pseudo-FS ausgefiltert); Netzwerk je Interface
(Bytes gesamt + Rate); Temperatur (alle Thermal-Zonen, Maximum hervorgehoben);
Uptime; Prozessanzahl; Hostname.

**Container**: Name, Image, Status, Health, CPU %, RAM (Nutzung, Limit, %),
Netzwerk (Bytes + **Rate**, heute immer 0), Restart-Count, Uptime.

**Host-Zugriff**: Wie beim Industriestandard (Prometheus `node_exporter`) wird das
Host-Dateisystem **read-only** eingebunden (`/:/host/root:ro`, dazu `/proc` und
`/sys` wie heute). Der Prozess bleibt Nicht-root mit `cap_drop: ALL`; er sieht nur,
was die Dateirechte erlauben. Wie die Mount-Tabelle ohne Root ermittelt wird,
entscheidet der Builder; es muss **am echten Host verifiziert** werden.

**Bekannter Fehler von heute**: `get_disk_info` nutzt `/host/proc/1/root`, was als
Nicht-root mit `cap_drop: ALL` vermutlich scheitert und still auf das
Container-Dateisystem zurückfällt (nicht am Host geprüft). Dieser stille Rückfall
entfällt; scheitert die Messung, gilt `available: false`.

Neue Metriken nur, wenn sie ohne Root und ohne weitere Host-Mounts lesbar sind.

### 6.6 Speicherung (Manager)

- SQLite im Volume, WAL-Modus, Schreibvorgänge pro Abrufzyklus gebündelt.
- Verdichtung und Aufbewahrung (per Umgebungsvariable änderbar):
  - Rohdaten (15 s): 48 h
  - Minutenwerte (min/avg/max): 30 Tage
  - Stundenwerte (min/avg/max): 365 Tage
- Abfrage-API wählt die Auflösung passend zum Zeitraum.
- **Schema-Migrationen** versioniert, laufen beim Start automatisch; vorher
  automatische Sicherungskopie der DB-Datei.
- Dokumentierte Backup-/Restore-Anleitung (DB + CA/Schlüssel).
- Zielgröße: bis ca. 25 Server à ~20 Container auf einem Pi; grob 50–100 MB pro
  Server bei obiger Aufbewahrung. Kein hartes Limit im Code. Darüber hinaus ist
  der vorgesehene Weg, die Agents per `/metrics` an Prometheus/VictoriaMetrics
  anzubinden.

### 6.7 Anmeldung und Benutzer (Manager)

- Lokale Konten: Passwort (**Argon2id**), **TOTP-2FA verpflichtend**,
  10 Einmal-Wiederherstellungscodes (gehasht gespeichert).
- **OIDC** (generisch, beliebiger Anbieter): Konten werden nicht automatisch
  angelegt; ein Admin legt den Benutzer (E-Mail) vorher an und weist die Rolle
  zu. Bei OIDC-Konten verlässt sich Moni auf die MFA des Anbieters.
- Erster Start: Ersteinrichtung legt den ersten Admin an (nur solange kein
  Benutzer existiert; per einmaligem Setup-Token aus dem Log abgesichert).
- Sessions: serverseitig, Cookie `HttpOnly`, `Secure`, `SameSite=Strict`,
  Leerlauf- und absolute Laufzeit, CSRF-Schutz für ändernde Anfragen.
- Sperre/Verzögerung nach Fehlversuchen (pro Konto und pro IP).
- Rollen: **Admin** (alles) und **Betrachter** (nur lesen, keine Tokens/Benutzer/
  Alarmkanäle sehen oder ändern).
- **Manager-API-Tokens** wie beim Agent (Hash, Rechte `read`/`admin`, Ablauf,
  Widerruf), damit auch der Manager ohne Oberfläche nutzbar und in eine spätere
  „große Oberfläche“ einbindbar ist.
- **Audit-Log** für sicherheitsrelevante Vorgänge: Login (Erfolg/Fehlschlag),
  2FA-Änderungen, Benutzer-, Rollen-, Token-, Server-, Regel- und Kanaländerungen.
  Einsehbar für Admins, per API abrufbar.

### 6.8 Alarmierung (Manager)

- Regeln: Metrik + Bedingung + Schwelle + **Dauer** („Disk > 90 % seit 10 min“),
  gültig für alle Server oder einzelne; Schweregrad Warnung/Kritisch.
- Mitgelieferte Standardregeln: Server nicht erreichbar, CPU, RAM, Disk (je FS),
  Temperatur, Container gestoppt (der vorher lief), Container unhealthy,
  Restart-Schleife, Docker-API nicht erreichbar. Schwellen wie heute im Frontend
  (Warnung 75 %, kritisch 85 %; Temperatur 65/75 °C) als Ausgangswerte.
- Zustände **ausgelöst** und **behoben**; Benachrichtigung bei beidem; keine
  Wiederholungsflut (erneute Erinnerung frühestens nach konfigurierbarer Zeit).
- **Stummschalten** pro Server bis zu einem Zeitpunkt (Wartung).
- Kanäle: **Webhook** (JSON, optional HMAC-signiert – deckt n8n, ntfy, Slack,
  Teams ab) und **E-Mail** (SMTP, STARTTLS/TLS). Test-Button je Kanal.
- Alarmverlauf gespeichert und über UI/API abrufbar.

### 6.9 Oberfläche (Manager)

- **Übersicht**: alle Server als Kacheln/Zeilen mit Ampelstatus, Kernwerten,
  offenen Alarmen, „zuletzt gesehen“.
- **Serverdetail**: Host-Kennzahlen, Verlaufscharts mit Zeitraumwahl
  (1 h / 24 h / 7 d / 30 d / 1 J), Dateisysteme, Netzwerk, Container-Liste mit
  Details und Verlauf.
- **Alarme**: offen und Verlauf; Regeln und Kanäle verwalten.
- **Verwaltung**: Server koppeln/entfernen, Benutzer, Tokens, Audit-Log, eigenes
  Konto (Passwort, 2FA, Wiederherstellungscodes).
- Live-Aktualisierung aus den Daten des Managers (kein Direktzugriff auf Agents).
- Optik: Weiterentwicklung des heutigen dunklen Stils; responsiv; Tastatur- und
  Kontrast-tauglich.
- Build mit **Vite** statt Create React App (CRA ist eingestellt).

### 6.10 Geheimnisse und HTTPS des Managers

- **Master-Schlüssel** `MONI_SECRET_KEY` (Env-Variable oder Docker-Secret, Pflicht
  für den Manager). Damit werden alle umkehrbar gespeicherten Geheimnisse in der
  DB verschlüsselt: TOTP-Secrets, SMTP-Passwort, OIDC-Client-Secret,
  Webhook-HMAC-Secrets, Manager-Tokens für Agents im Modus `MONI_TLS=off`, sowie
  der CA-Schlüssel auf der Platte. Der Schlüssel liegt **nicht** im Daten-Volume;
  `scripts/start.sh` erzeugt ihn beim ersten Start in die `.env`. Die Backup-Doku
  sagt deutlich: Schlüssel getrennt von DB sichern; ohne ihn sind 2FA, Kanäle und
  Kopplungen verloren. Schlüsselwechsel per CLI (entschlüsseln/neu verschlüsseln).
- **HTTPS des Managers**: Standard ist HTTPS mit selbst erzeugtem Zertifikat aus
  der Manager-CA (Browserwarnung, wie bei Proxmox/Portainer), damit `Secure`-
  Cookies ohne Domain funktionieren. Hinter einem Reverse-Proxy schaltet
  `MONI_TLS=off` auf HTTP und wertet `X-Forwarded-Proto` nur von konfigurierten
  vertrauenswürdigen Proxys aus. OIDC setzt eine echte Domain voraus (Doku-Hinweis).
  Beim Autor läuft der Manager weiter auf dem Pi hinter Traefik mit Domain.
- Letzten Admin wiederherstellen (Passwort/2FA zurücksetzen) per CLI im Container.

### 6.11 Code-Struktur (Backend)

`server.py` wird in Module aufgeteilt: Messung (Host/Docker), Sammler/Scheduler,
Agent-API, Manager-API, Auth, Speicher/Migrationen, Alarme, Krypto (CA/mTLS).
Gemeinsamer Code für Agent und Manager; die Rolle entscheidet beim Start, welche
Router und Hintergrunddienste aktiv sind.

## 7. Open-Source- und Qualitätsrahmen

- **Releases**: SemVer, Tag → CI baut Multi-Arch-Images (amd64/arm64) für
  `moni-agent` und `moni-manager`, veröffentlicht auf **GHCR**, **cosign**-
  signiert, mit **SBOM**. `CHANGELOG.md` gepflegt. Compose-Dateien nutzen
  standardmäßig die veröffentlichten Images (lokaler Build bleibt möglich).
- **Repo-Dateien**: `README.md` (neu strukturiert), `SECURITY.md`,
  `CONTRIBUTING.md`, Doku für Installation, Kopplung, Proxy-Varianten (inkl.
  Cloudflare Tunnel und Traefik als Beispiele), API, Alarmierung, Backup, Upgrade.
- **CI**: Backend-Unit- und Integrationstests, Frontend-Lint/Build,
  **End-to-End-Test** (Manager + Agent im Compose-Verbund: koppeln, Daten
  fließen, Alarm löst aus und wird behoben), OpenAPI-Abgleich, **CodeQL**,
  **Trivy**-Image-Scan, Dependabot bleibt.
- **Betrieb**: strukturierte JSON-Logs (ohne Geheimnisse), Health-/Readiness-
  Endpunkte, Moni-Eigenkennzahlen (Abrufdauer, Fehler je Agent, DB-Größe).

## 8. Übergang vom heutigen Stand

- Heutige Endpunkte `/api/metrics/*` entfallen (einziger Nutzer ist das eigene
  Frontend). Im CHANGELOG als Breaking Change vermerken.
- Bisheriges Deployment des Autors (Traefik + Cloudflare Access vor dem Pi)
  bleibt als dokumentierte Variante möglich; Cloudflare Access ist danach
  optional, nicht mehr nötig.
- `scripts/` (start/stop/redeploy/smoke/diagnose/logs) werden auf beide Rollen
  angepasst; `smoke.sh` prüft weiterhin die Härtung.

## 9. Dem Builder überlassen

- Konkrete Bibliotheken innerhalb der Vorgaben (z. B. für TOTP, OIDC, Argon2,
  X.509/CA, Scheduler, Migrationen) – bevorzugt verbreitete, gepflegte Pakete
  bzw. Standardbibliothek.
- Datenbankschema im Detail.
- Exaktes Format des Kopplungscodes.
- Visuelles Feinlayout der Oberfläche.
- Aufteilung in Umsetzungspakete und Reihenfolge.

## 10. Entscheidungen dieser Designphase

Vom Auftraggeber entschieden:
- Agent + Manager, mehrere Server in einer Zentrale; Manager beim Autor auf dem Pi.
- Agent ohne Oberfläche, API-first, auch für Fremdsysteme.
- Eigener Login mit Passwort + 2FA; Google bzw. allgemein OIDC.
- Unabhängig von Cloudflare, öffentliches Open-Source-Repo.
- Business-Standard-Umfang laut Abschnitt 6–7, **alles in einer Version**.
- Game-Server vorerst außen vor.
- SAU-INDEX-Agent über den bestehenden Cloudflare Tunnel (kein offener Port).
- Host-Dateisystem read-only einbinden (`node_exporter`-Muster).

Vom Designer gesetzt (zur Prüfung):
- Pull statt Push; 15-s-Takt; Ringpuffer 2 h im Agent.
- mTLS mit Manager-CA und Kopplungscode; Tokens für Fremdsysteme.
- SQLite statt Prometheus/TSDB; Aufbewahrung 48 h / 30 d / 365 d.
- Zwei Rollen Admin/Betrachter; OIDC ohne automatische Kontoanlage.
- Webhook + E-Mail als Alarmkanäle; Stummschalten pro Server.
- Vite statt CRA; Produktname „Moni“, Images `moni-agent` / `moni-manager`.
- Master-Schlüssel als Env/Docker-Secret außerhalb des Daten-Volumes.
- Manager standardmäßig HTTPS mit eigenem Zertifikat, Proxy-Modus per Schalter.
- Kopplung per CSR (privater Agent-Schlüssel verlässt den Agent nie);
  Zertifikatserneuerung automatisch durch den Manager vor Ablauf.
- Nur Docker-Installation; jede Compose-Datei bringt eigenen Socket-Proxy mit.
