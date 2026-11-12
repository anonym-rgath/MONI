# Changelog

Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), die Versionen folgen
[Semantic Versioning](https://semver.org/lang/de/). Der oberste Abschnitt muss zur Version in
`backend/moni/__init__.py` passen (`scripts/ci/check_version.py`).

## [2.0.0-rc.1] – 2026-11-12

Moni wird in zwei Rollen geteilt: den **Agent** (Messdienst pro Host) und den **Manager**
(Weboberfläche, Verläufe, Alarme für mehrere Agents). Beide Images entstehen aus einem Dockerfile.

### Breaking Changes

- Die Endpunkte `/api/metrics/*` entfallen. Der Agent liefert Messwerte über die versionierte API
  `/api/v1/…` und Prometheus-Format über `/metrics`.
- Der Agent spricht standardmäßig TLS (`MONI_TLS=on`). Wer ihn ohne TLS betreibt, setzt `MONI_TLS=off`.
- Die Compose-Dateien heißen `compose.agent.yaml` und `compose.manager.yaml`. Sie nutzen veröffentlichte
  Images von GHCR mit fester Version.
- Cloudflare Access ist optional. Der Manager hat eigene Benutzerkonten und braucht es nicht mehr.

### Hinzugefügt

- Paket 1, Agent: zentraler Sammler mit Ringpuffer, korrigierte und erweiterte Messung, API v1 und
  `/metrics`, tokengeschützt, per `compose.agent.yaml` startbar.
- Paket 2, Manager-Kern: Abruf mehrerer Agents, Verläufe in SQLite mit Verdichtung und Aufbewahrung,
  verschlüsselte Geheimnisse, Manager-API v1 und CLI.
- Paket 3, Benutzer und Anmeldung: Ersteinrichtung per Setup-Token, Passwort (Argon2id) mit
  verpflichtender TOTP-2FA und Wiederherstellungscodes, Sessions mit CSRF-Schutz, Sperre nach
  Fehlversuchen, Rollen Admin und Betrachter, OIDC-Anmeldung, Audit-Log.
- Paket 4, mTLS-Kopplung: eigene CA im Manager, Kopplung per Kopplungscode, Abruf nur per mTLS mit
  automatischer Zertifikatserneuerung, HTTPS für die Oberfläche, Betrieb hinter Reverse-Proxy
  (`MONI_TLS=off`).
- Paket 5, Alarmierung: Regeln mit Schwelle und Dauer, Zustände „ausgelöst“ und „behoben“,
  Erinnerungen, Stummschaltung, Webhook (optional HMAC-signiert) und E-Mail, Test-Versand, Alarmverlauf.
- Paket 6, Oberfläche: neue Weboberfläche mit Anmeldung, Übersicht, Serverdetail, Alarmen, Verwaltung
  und Konto; helles und dunkles Design, geprüft mit axe gegen WCAG 2.2 AA.
- Paket 7, Release: signierte Multi-Arch-Images (amd64, arm64) auf GHCR mit SBOM, Release-Workflow,
  Trivy und End-to-End-Test in der CI, OpenAPI-Abgleich, Eigenkennzahlen und Readiness,
  Betriebsskripte für beide Rollen, Dokumentation.

### Sicherheit

- mTLS zwischen Manager und Agent, eigene CA, Zertifikate erneuern sich selbst.
- Pflicht-2FA (TOTP) für Benutzerkonten, Argon2id für Passwörter, Sperre nach Fehlversuchen.
- Signierte Images (cosign, keyless über GitHub-OIDC) mit SBOM und Provenance; Trivy bricht bei
  behebbaren CRITICAL- und HIGH-Funden ab.
- Alle GitHub Actions sind per Commit-SHA gepinnt, Basis-Images per Digest.
- Der Docker-Socket wird nur über einen Socket-Proxy mit enger Allowlist eingebunden.

Prüfen der Signatur:

```
cosign verify ghcr.io/anonym-rgath/moni-agent:2.0.0 \
  --certificate-identity-regexp '^https://github.com/anonym-rgath/MONI/\.github/workflows/release\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Getestet mit cosign 3.x (ältere Versionen finden die Signatur ggf. nicht).
