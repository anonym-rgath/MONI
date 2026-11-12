# Kopplung von Agent und Manager

Der Manager fragt die Agents ab (Pull). Der Agent baut nie selbst eine Verbindung auf und kennt keine
Zugangsdaten zum Manager. Die Verbindung ist per mTLS abgesichert: Der Manager betreibt eine eigene kleine CA,
der Agent akzeptiert für Manager-Zugriffe nur Client-Zertifikate dieser CA. Voraussetzung ist ein laufender
[Agent](installation.md#agent-installieren) mit `MONI_TLS=on` (Standard), den der Manager im Netz erreicht
(`MONI_AGENT_BIND=0.0.0.0`, Port 9810 per Firewall nur für den Manager offen).

## Ablauf

1. Beim ersten Start erzeugt der Agent ein Schlüsselpaar, ein vorläufiges selbstsigniertes Zertifikat und ein
   einmaliges Kopplungs-Token.
2. Den **Kopplungscode** rufen Sie am Agent ab, solange er ungekoppelt ist:

   ```
   docker compose -f compose.agent.yaml exec moni-agent python -m moni.cli pair code
   ```

   Der Code hat die Form `moni-pair-v1:<Fingerabdruck>:<Token>` und, wenn `MONI_PUBLIC_URL` gesetzt ist,
   `:<URL>` am Ende. Er enthält ein Geheimnis: Übergeben Sie ihn nur über einen sicheren Kanal und teilen Sie
   ihn nicht in Tickets oder Logs. Ist der Agent schon gekoppelt, meldet der Befehl das und gibt keinen Code aus.
3. Im Manager unter **Verwaltung, Server, Server koppeln** (Art „Mit Kopplungscode“) den Code einfügen. Steht die URL nicht im Code, geben Sie sie
   an (`https://agent.example.org:9810`). Alternativ per CLI im Manager-Container (Code über stdin, nie als Argument):

   ```
   docker compose -f compose.manager.yaml exec -T moni-manager \
     python -m moni.cli server add --name web1 --url https://agent.example.org:9810 --pairing-code-file -
   ```

4. Der Manager verbindet sich und prüft den **Fingerabdruck** des Agent-Zertifikats gegen den Code. Er legt das
   Kopplungs-Token vor, der Agent erzeugt einen neuen Schlüssel samt Zertifikatsanfrage (CSR), der Manager
   signiert sie mit seiner CA und übergibt auch sein CA-Zertifikat. Das Kopplungs-Token ist damit verbraucht.
5. Ab jetzt nimmt der Agent für den Manager nur noch Verbindungen mit einem Client-Zertifikat dieser CA an.

## Zertifikate und Erneuerung

Der Manager erneuert Agent-Zertifikate selbst, sobald weniger als 30 Tage Laufzeit bleiben (Agent-Zertifikate
gelten 90 Tage); nach einem Fehlschlag versucht er es frühestens nach einer Stunde wieder. Sein eigenes
Client-Zertifikat erneuert er ebenfalls selbst. Sie müssen nichts tun, solange der Manager die Agents regelmäßig erreicht.

**Ist ein Agent-Zertifikat abgelaufen** (z. B. weil der Manager lange aus war), kann der Manager ihn nicht mehr
erreichen und auch nicht erneuern. Dann hilft nur neu koppeln, siehe nächster Abschnitt.

## Neu koppeln (`pair reset`)

Nötig bei abgelaufenem Zertifikat, nach einem Neuaufbau des Managers oder wenn die Kopplung nicht mehr
funktioniert. Am Agent:

```
docker compose -f compose.agent.yaml exec moni-agent python -m moni.cli pair reset
docker compose -f compose.agent.yaml restart moni-agent
```

`pair reset` verwirft Zertifikat und CA des Agent und gibt einen neuen Kopplungscode aus. Danach fügen Sie den
Code im Manager ein. Entfernen Sie den alten Eintrag des Servers vorher im Manager (**Entfernen**), sonst ist der Name belegt.
Dabei werden Verlauf und Alarme dieses Servers gelöscht.

## CA des Managers

Die CA-Datei ist öffentlich (kein Geheimnis): `GET /api/v1/ca.crt` auf dem Manager. Sie prüft damit selbst
ausgestellte Zertifikate, etwa wenn ein Fremdsystem einen Agent per Token abfragt:

```
curl -k https://manager.example.org:8443/api/v1/ca.crt -o moni-ca.crt
curl --cacert moni-ca.crt -H "Authorization: Bearer <TOKEN>" https://agent.example.org:9810/api/v1/info
```

Der Abruf über `-k` ist *trust on first use*: Sie vertrauen dem ersten Download blind. Prüfen Sie den
Fingerabdruck gegen die CA auf dem Manager-Host (weder Oberfläche noch CLI zeigen ihn an):

```
docker compose -f compose.manager.yaml exec -T moni-manager cat /data/tls/ca.crt | openssl x509 -noout -fingerprint -sha256
openssl x509 -in moni-ca.crt -noout -fingerprint -sha256
```

Beide Zeilen müssen übereinstimmen.

Vertrauen Sie dieser CA **nur gezielt per `--cacert`** (oder dem entsprechenden Parameter Ihres Clients), nie im
System-Zertifikatsspeicher: Sie ist nur für Moni gedacht, und wer sie systemweit vertraut, vertraut ihr für jede
Domain.

**Keine CA-Rotation in 2.0.** Es gibt keinen Befehl, die CA zu ersetzen. Ist der CA-Schlüssel kompromittiert oder
verloren (z. B. Volume ohne Backup), setzen Sie neu auf:

1. Manager stoppen, das Volume `moni-manager-data` verwerfen oder die CA-Dateien daraus entfernen. Dabei gehen
   Benutzer, Verlauf und Alarmregeln verloren, wenn Sie das ganze Volume verwerfen. Sichern Sie zuerst ([Backup](backup.md)).
2. Manager neu starten (er erzeugt eine neue CA und neue Zertifikate) und den Admin neu einrichten.
3. Jeden Agent mit `pair reset` zurücksetzen und neu koppeln.

## Token-Modus (Agent ohne mTLS)

Läuft ein Agent hinter einem Reverse-Proxy oder Tunnel mit `MONI_TLS=off` (siehe [Proxy](proxy.md)), gibt es
kein End-to-End-mTLS. Der Manager authentisiert sich dann mit einem API-Token. Die Oberfläche zeigt je Server die Art der Kopplung
(mTLS oder Token). Ablauf:

1. Token am Agent erzeugen (wird nur einmal angezeigt):

   ```
   docker compose -f compose.agent.yaml exec moni-agent python -m moni.cli token create --name manager
   ```

2. Im Manager als Server mit Token anlegen, Token über stdin:

   ```
   docker compose -f compose.manager.yaml exec -T moni-manager \
     python -m moni.cli server add --name web1 --url https://moni-agent.example.org --token-file - \
     --header CF-Access-Client-Id=<ID> --header CF-Access-Client-Secret=<SECRET>
   ```

   `--header` ist optional und kann mehrfach stehen (z. B. für Zugangs-Proxys). Die Werte werden verschlüsselt gespeichert.

Tokens am Agent sind immer `read`. Mehr zu Tokens: [API](api.md#tokens-und-scopes).
