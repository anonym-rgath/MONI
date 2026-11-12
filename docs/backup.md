# Backup und Wiederherstellung

Gesichert werden muss nur der **Manager**. Er hält alle Daten. Der Agent ist ersetzbar: Geht sein Volume
verloren, koppeln Sie ihn neu ([Kopplung](kopplung.md#neu-koppeln-pair-reset)); nur seine API-Tokens
(`tokens.json`) sind dann weg.

## Was gehört dazu

| Was | Wo | Hinweis |
|---|---|---|
| Datenbank (Benutzer, Server, Verläufe, Alarme, Audit) | `/data/moni.db` im Volume `moni-manager-data` | SQLite im WAL-Modus |
| CA und Zertifikate | `/data/tls/` im selben Volume | CA-Schlüssel liegt verschlüsselt (`ca.key.enc`) |
| **Master-Schlüssel** `MONI_SECRET_KEY` | `.env` auf dem Host | **getrennt** von den Daten sichern |

**Sichern Sie den Master-Schlüssel an einem anderen Ort als die Daten** (Passwortmanager, verschlüsselter Tresor).
Wer beides hat, kann alle Geheimnisse lesen. Wer den Schlüssel verliert, verliert 2FA, Alarmkanäle, OIDC-Zugang,
Token-Kopplungen und die CA: Das Backup lässt sich ohne ihn nicht sinnvoll einspielen.

## Automatische Sicherung vor Migrationen

Bevor eine neue Moni-Version das Datenbankschema ändert, legt der Manager neben der Datenbank eine Kopie an:
`/data/moni.db.bak-v<Schema-Version>-<Zeitstempel>`. Sie entsteht nur, wenn Migrationen anstehen, nicht bei jedem
Start. Diese Dateien sind **kein** Ersatz für ein Backup: Sie liegen im selben Volume und werden nicht aufgeräumt. Löschen Sie alte
Kopien, wenn das Upgrade gelungen ist.

## Manuelles Backup

### Datenbank im laufenden Betrieb

SQLite kopiert dabei konsistent, auch während der Manager schreibt:

```
docker compose -f compose.manager.yaml exec moni-manager python -c \
  "import sqlite3; s = sqlite3.connect('/data/moni.db'); d = sqlite3.connect('/data/manuell.db'); s.backup(d); d.close()"
docker compose -f compose.manager.yaml cp moni-manager:/data/manuell.db ./moni-$(date +%F).db
docker compose -f compose.manager.yaml exec moni-manager rm /data/manuell.db
```

Das sichert nur die Datenbank. Für ein vollständiges Backup brauchen Sie zusätzlich `/data/tls/`
(sonst fehlt die CA und alle Agents müssen neu gekoppelt werden). Einfacher ist die Kopie des ganzen Volumes:

### Ganzes Volume bei gestopptem Manager

```
./scripts/stop.sh manager
docker volume ls | grep moni-manager-data       # Name des Volumes: moni-manager_moni-manager-data
docker run --rm --user root -v moni-manager_moni-manager-data:/data:ro -v "$PWD":/backup \
  --entrypoint tar ghcr.io/anonym-rgath/moni-manager:2.0.0 czf /backup/moni-data-$(date +%F).tgz -C /data .
./scripts/start.sh manager
```

Die Archivdatei enthält verschlüsselte Geheimnisse, aber auch den HTTPS-Schlüssel im Klartext
(`/data/tls/https.key`). Bewahren Sie sie wie ein Geheimnis auf.

## Wiederherstellen

1. Manager stoppen: `./scripts/stop.sh manager`.
2. **Aktuellen Stand sichern**, bevor Sie etwas überschreiben (das Zurückspielen löscht den Volume-Inhalt, ein
   falsches Archiv oder Datum wäre sonst nicht mehr rückgängig zu machen):

   ```
   docker run --rm --user root -v moni-manager_moni-manager-data:/data:ro -v "$PWD":/backup \
     --entrypoint tar ghcr.io/anonym-rgath/moni-manager:2.0.0 czf /backup/moni-vor-restore-$(date +%F-%H%M).tgz -C /data .
   ```

3. `MONI_SECRET_KEY` in `.env` auf den Schlüssel setzen, der zum Backup gehört.
4. Volume-Inhalt zurückspielen. Beim Archiv:

   ```
   docker run --rm --user root -v moni-manager_moni-manager-data:/data -v "$PWD":/backup \
     --entrypoint sh ghcr.io/anonym-rgath/moni-manager:2.0.0 \
     -c "rm -rf /data/* /data/.[!.]* && tar xzf /backup/moni-data-<DATUM>.tgz -C /data && chown -R app:app /data"
   ```

   Bei nur einer Datenbankdatei kopieren Sie sie als `/data/moni.db` hinein und entfernen vorher `moni.db-wal`
   und `moni.db-shm`, falls vorhanden. `/data/tls/` muss aus demselben Stand stammen.
5. `./scripts/start.sh manager`, dann `./scripts/smoke.sh manager`. Migrationen laufen beim Start, falls das
   Backup von einer älteren Moni-Version stammt.

Eine Datenbank, die neuer ist als die Moni-Version, lehnt der Manager ab („Datenbank-Version … ist neuer“): Starten Sie dann
die passende oder eine neuere Version.
