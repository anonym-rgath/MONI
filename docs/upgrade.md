# Upgrade

Die Änderungen jeder Version stehen im [CHANGELOG](../CHANGELOG.md).

## Von 1.x (Linux Monitor) auf 2.0

2.0 ist eine neue Architektur (Agent und Manager). Beachten Sie:

- **Keine Datenübernahme.** 1.x hatte keine Datenbank und keinen Login; es gibt nichts zu migrieren.
  Installieren Sie 2.0 neu ([Installation](installation.md)) und nehmen Sie die 1.x-Installation danach außer Betrieb.
- **Neue Compose-Dateien:** `compose.agent.yaml` und `compose.manager.yaml`, mit veröffentlichten, festen
  Image-Versionen von GHCR. Der Socket-Proxy ist in jeder Datei enthalten und gehört nicht mehr zu einem externen Projekt.
- **Der Agent spricht standardmäßig TLS** (`MONI_TLS=on`) und wird per Kopplung mit dem Manager verbunden
  ([Kopplung](kopplung.md)). Hinter einem Proxy setzen Sie `MONI_TLS=off` ([Proxy](proxy.md)).
- **`/api/metrics/*` entfallen.** Die Messwerte kommen über `/api/v1/…` und im Prometheus-Format über `/metrics`
  ([API](api.md)). Skripte, die die alten Pfade nutzen, müssen angepasst werden.
- Cloudflare Access ist optional. Der Manager hat eigene Benutzerkonten mit Pflicht-2FA.

Vorgehen: neuen Stack installieren, Agent koppeln, Funktion prüfen (`./scripts/smoke.sh`), alten Stack stoppen.

## Innerhalb von 2.x

1. [CHANGELOG](../CHANGELOG.md) lesen, besonders „Breaking Changes“.
2. Backup ([Backup](backup.md)).
3. Image-Version anheben: in `.env` `MONI_MANAGER_IMAGE` bzw. `MONI_AGENT_IMAGE` auf den neuen Tag setzen
   (Beispiel: `ghcr.io/anonym-rgath/moni-manager:2.1.0`) oder das Repository auf den neuen Tag auschecken, dessen
   Compose-Dateien die neue Version als Standard tragen. Prüfen Sie die Signatur ([Installation](installation.md#signatur-der-images-prüfen)).
4. Aktualisieren: `./scripts/redeploy.sh manager` (bzw. `agent`). Das Skript zieht das Image, startet neu und führt den Smoke-Test aus.
5. Die **Datenbankmigrationen laufen beim Start automatisch**, vorher legt der Manager eine Sicherungskopie an
   (`moni.db.bak-v…`, siehe [Backup](backup.md)). Schlägt eine Migration fehl, bleibt die Datenbank unverändert
   und der Manager startet nicht; die Meldung steht im Log.

Ein Zurück auf eine ältere Version ist nach einer Migration nicht vorgesehen: Spielen Sie dafür das Backup zurück.
Der Agent hat keine Datenbank; Agent und Manager einer Hauptversion arbeiten zusammen, auch wenn der Agent älter ist.
