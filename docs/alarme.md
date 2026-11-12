# Alarme

Der Manager wertet Alarmregeln gegen die gesammelten Messwerte aus und benachrichtigt über Kanäle (Webhook,
E-Mail). Regeln, Kanäle und Einstellungen verwalten Admins in der Oberfläche (**Alarme**) oder per API
(`/api/v1/alert-rules`, `/api/v1/alert-channels`, `/api/v1/alert-settings`). Es gibt dafür keine Umgebungsvariablen.

## Regeln

Eine Regel besteht aus:

- **Metrik** und **Vergleich** (`>`, `>=`, `<`, `<=`),
- Schwellen für **Warnung** und **kritisch** (mindestens eine; die Warnschwelle liegt vor der kritischen),
- **Dauer** (`for_seconds`, 0 bis 86400): Der Wert muss so lange die Schwelle verletzen, bevor die Regel auslöst,
- Funktion `value` (aktueller Wert) oder `increase` (Anstieg in einem Fenster von 60 bis 86400 Sekunden),
- optional Labels, nach denen getrennt wird (z. B. je Dateisystem oder Container) und einem Server (sonst alle).

### Standardregeln

Beim ersten Start legt der Manager diese Regeln an (Warnung/kritisch, Dauer). Sie lassen sich ändern oder deaktivieren.

| Regel | Bedingung | Dauer |
|---|---|---|
| Server nicht erreichbar | Server nicht erreichbar (kritisch) | sofort |
| CPU-Auslastung | > 75 % / 85 % | 5 min |
| Arbeitsspeicher | > 75 % / 85 % | 5 min |
| Dateisystem voll | > 75 % / 85 %, je Einhängepunkt | 5 min |
| Temperatur | > 65 °C / 75 °C | 2 min |
| Container gestoppt | Container läuft nicht mehr, nur wenn er vorher lief (kritisch), je Name | 1 min |
| Container unhealthy | Healthcheck schlägt fehl (Warnung), je Name | 2 min |
| Restart-Schleife | mindestens 3 Neustarts in 10 min (kritisch), je Name | sofort |
| Docker-API nicht erreichbar | Docker-API des Hosts antwortet nicht (kritisch) | 1 min |

„Nicht erreichbar“ heißt: der Manager hat den Agent nach dem Abrufen mehrfach nicht erreicht.

## Zustände und Erinnerungen

Eine Regel pro Server (und pro Label) ist **ausgelöst** oder **behoben**. Der Manager benachrichtigt bei beidem.
Ändert sich die Schwere (Warnung zu kritisch), kommt eine weitere Nachricht („Schwere geändert“). Solange ein
Alarm offen bleibt, erinnert der Manager nach der eingestellten Zeit („weiterhin aktiv“). Die Erinnerungszeit
stellen Sie in der Oberfläche ein (Feld „Erinnern, solange ein Alarm offen ist“, API `PUT /api/v1/alert-settings` mit `remind_minutes`; Standard 240 Minuten, 0 bis 10080; 0 schaltet Erinnerungen aus).
Offene Alarme und den Verlauf zeigen die Oberfläche und `GET /api/v1/alerts` bzw. `/api/v1/alerts/history`.

## Stummschalten

Für Wartungsfenster schalten Sie einen Server bis zu einem Zeitpunkt stumm (Oberfläche, oder
`PUT /api/v1/servers/{id}/mute` mit `{"until": "<ISO-8601-Zeitpunkt>"}`, aufheben mit `DELETE`). Währenddessen gehen
keine Erinnerungen raus; Ereignisse landen markiert als stumm im Verlauf.

## Kanäle

Jeder Kanal hat einen Namen, eine Mindest-Schwere (`warning` oder `critical`) und lässt sich per **Testen** (`POST
/api/v1/alert-channels/{id}/test`) prüfen. Geheimnisse (Webhook-URL, HMAC-Secret, SMTP-Passwort) werden mit
dem Master-Schlüssel verschlüsselt gespeichert und nie wieder angezeigt.

### Webhook

Moni sendet `POST` mit `Content-Type: application/json` an die URL (`http` oder `https`, ohne Zugangsdaten und
Fragment; keine Weiterleitungen, kein Proxy aus der Umgebung; Gesamtfrist 10 Sekunden). Das funktioniert mit n8n, ntfy, Slack,
Teams und eigenen Empfängern. Ein Tipp: Steckt ein Token in der URL, ist die URL das Geheimnis.

**Payload, Schema v1:**

```json
{
  "version": 1,
  "event": "firing",
  "severity": "critical",
  "rule": "Dateisystem voll",
  "server": "web1",
  "labels": {"mountpoint": "/"},
  "value": 91.2,
  "threshold": 85,
  "reason": null,
  "at": "2026-11-12T08:15:00Z",
  "manager": "monitor",
  "text": "[KRITISCH] Dateisystem voll auf web1 (mountpoint=/): 91.2 (Schwelle 85) – ausgelöst"
}
```

`event` ist `firing`, `resolved`, `changed`, `reminder` oder `test`. `severity` ist `warning` oder `critical`.
`value`, `threshold` und `reason` können `null` sein. `text` ist eine fertige Kurzfassung für Chat-Empfänger.

**HMAC-Signatur (optional).** Setzen Sie am Kanal ein HMAC-Secret, signiert Moni den Rumpf und sendet zwei Header:

- `X-Moni-Timestamp`: Unix-Zeit in Sekunden
- `X-Moni-Signature`: `sha256=` und der Hex-Wert von HMAC-SHA256 über `"<Timestamp>."` plus den unveränderten Rumpf (Bytes), Schlüssel ist das Secret

Prüfen Sie den **rohen** Rumpf, nicht ein neu serialisiertes JSON, und lehnen Sie alte Zeitstempel ab (Schutz vor Wiedereinspielung):

```python
import hashlib
import hmac
import time

SECRET = b"<HMAC-SECRET>"
MAX_ALTER = 300  # Sekunden


def pruefen(roh_rumpf: bytes, timestamp: str, signatur: str) -> bool:
    if abs(time.time() - int(timestamp)) > MAX_ALTER:
        return False
    erwartet = "sha256=" + hmac.new(SECRET, timestamp.encode() + b"." + roh_rumpf, hashlib.sha256).hexdigest()
    return hmac.compare_digest(erwartet, signatur)
```

### E-Mail (SMTP)

Ein E-Mail-Kanal braucht Host, Port, Verschlüsselung (`starttls` oder `tls`; unverschlüsseltes SMTP gibt es nicht),
Absender, 1 bis 10 Empfänger und optional Benutzername und Passwort. Die Meldungen nennen die Fehlerursache
(Anmeldung, Erreichbarkeit, Ablehnung, TLS-Prüfung) ohne Zugangsdaten; nutzen Sie **Testen**, bevor Sie sich darauf verlassen.
