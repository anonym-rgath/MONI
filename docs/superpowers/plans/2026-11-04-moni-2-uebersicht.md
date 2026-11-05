# Moni 2.0 – Gesamtübersicht der Umsetzungspakete

**Spec:** [`docs/superpowers/specs/2026-11-04-moni-agent-manager-design.md`](../specs/2026-11-04-moni-agent-manager-design.md)

Die Spec umfasst mehrere unabhängige Teilsysteme. Deshalb gibt es **einen
Detailplan pro Paket**. Jedes Paket ergibt für sich lauffähige, getestete Software.
Veröffentlicht wird erst, wenn alle sieben Pakete fertig sind (Release 2.0.0,
„alles in einer Version“).

Der Detailplan für ein Paket wird erst geschrieben, wenn das vorherige Paket fertig
ist. So baut er auf den tatsächlich entstandenen Schnittstellen auf und nicht auf
Annahmen.

## Arbeitsweise

- Alle Arbeit liegt auf dem Branch `feature/moni-2`. `main` bleibt unverändert und
  läuft weiter auf dem Pi, bis Paket 7 ausgerollt wird.
- Auf dem Branch ist die alte Oberfläche ab Paket 1 außer Betrieb, weil die
  `/api/metrics/*`-Endpunkte entfallen. Erst Paket 6 bringt die neue Oberfläche.
- Tests laufen lokal mit `uv` gegen Python 3.14:
  `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests -v`

## Pakete und Reihenfolge

| # | Paket | Inhalt (Spec-Abschnitte) | baut auf |
|---|---|---|---|
| 1 | **Fundament + Agent** | Paketstruktur, Sammler mit Ringpuffer, korrigierte Messung (alle FS, Netz, Temperaturen, Raten), Agent-API v1 (`health`, `info`, `snapshot`, `history`), `/metrics`, API-Tokens + CLI, RFC 9457, Rate-Limit, JSON-Logs, `compose.agent.yaml` mit eigenem Socket-Proxy (§6.1, 6.2 Agent-Seite, 6.3 Tokens, 6.4, 6.5, 6.11) | – |
| 2 | **Manager-Kern** | Rolle `manager`, SQLite (WAL, gebündelte Writes), Migrationen + Auto-Backup, Master-Schlüssel und Verschlüsselung, Server-Verwaltung (Token-Modus + Zusatz-Header), Poller mit Lückenfüllung und Erreichbarkeit, Verdichtung/Aufbewahrung, Manager-API v1 lesend, Manager-Tokens, lokaler Agent im Prozess, `compose.manager.yaml` (§6.2 Manager-Seite, 6.6, 6.10 Master-Key) | 1 |
| 3 | **Benutzer & Anmeldung** | Ersteinrichtung mit Setup-Token, Argon2id, TOTP-Pflicht, Wiederherstellungscodes, Sessions/CSRF, Sperre, Rollen Admin/Betrachter, OIDC, Audit-Log, CLI-Admin-Reset (§6.7, 6.10 letzter Punkt) | 2 |
| 4 | **mTLS & Kopplung** | Manager-CA, Agent-TLS (Standard `on`), Kopplungscode, CSR-Kopplung, Zertifikatserneuerung, Reset-Kopplung per CLI, Manager-HTTPS standardmäßig + Proxy-Modus mit vertrauenswürdigen Proxys (§6.3, 6.10 HTTPS) | 2, 3 |
| 5 | **Alarmierung** | Regeln (Schwelle + Dauer), Standardregeln, Zustände, Erinnerungsintervall, Stummschalten, Webhook (HMAC), SMTP, Test-Versand, Alarmverlauf (§6.8) | 2, 3 |
| 6 | **Oberfläche** | Umstieg CRA → Vite, Login/2FA/Ersteinrichtung, Übersicht, Serverdetail mit Zeiträumen, Alarme, Verwaltung (Server koppeln, Benutzer, Tokens, Audit, Konto) (§6.9) | 3, 4, 5 |
| 7 | **Release & Open-Source-Rahmen** | Multi-Arch-Images auf GHCR, cosign, SBOM, SemVer/CHANGELOG, CodeQL, Trivy, E2E-Test (koppeln, Daten, Alarm), OpenAPI-Abgleich in der CI, `scripts/` für beide Rollen, Moni-Eigenkennzahlen, Doku (Installation, Kopplung, Proxy-Varianten, API, Alarme, Backup, Upgrade), `SECURITY.md`, `CONTRIBUTING.md`, Dependabot für Compose, Ausrollen beim Autor (Pi + SAU-INDEX) (§7, §8) | 1–6 |

Die Pakete 4 und 5 sind voneinander unabhängig und können parallel laufen.

## Status

- [x] Paket 1 – Plan: [`2026-11-04-moni-2-paket-1-fundament-agent.md`](2026-11-04-moni-2-paket-1-fundament-agent.md) (umgesetzt, CI grün; Task 9 auf dem Pi offen)
- [x] Paket 2 – Plan: [`2026-11-04-moni-2-paket-2-manager-kern.md`](2026-11-04-moni-2-paket-2-manager-kern.md) (umgesetzt; Task 10 durch Betreiber offen)
- [x] Paket 3 – Plan: [`2026-11-05-moni-2-paket-3-benutzer-anmeldung.md`](2026-11-05-moni-2-paket-3-benutzer-anmeldung.md) (umgesetzt, inkl. mehrerer OIDC-Anbieter mit Microsoft; Task 9 durch Betreiber offen)
- [x] Paket 4 – Plan: [`2026-11-05-moni-2-paket-4-mtls-kopplung.md`](2026-11-05-moni-2-paket-4-mtls-kopplung.md) (umgesetzt; Task 8 durch Betreiber offen)
- [ ] Paket 5 – Plan: [`2026-11-05-moni-2-paket-5-alarmierung.md`](2026-11-05-moni-2-paket-5-alarmierung.md) (geschrieben, Umsetzung offen)
- [ ] Paket 6
- [ ] Paket 7
