# Betrieb hinter einem Reverse-Proxy

Standardmäßig spricht Moni selbst TLS und braucht keinen Proxy. Ein Proxy (Traefik, Caddy, nginx, Cloudflare
Tunnel) ist optional, etwa für eine echte Domain mit Zertifikat von Let's Encrypt oder wenn auf dem Agent-Host
kein Port geöffnet werden soll.

## `MONI_TLS=off` und `MONI_TRUSTED_PROXIES`

Mit `MONI_TLS=off` liefert die Rolle HTTP, und der Proxy davor schließt TLS ab:

```
MONI_TLS=off
MONI_TRUSTED_PROXIES=203.0.113.10,198.51.100.0/24
```

`MONI_TRUSTED_PROXIES` sind die Adressen (IPs oder CIDRs, kommagetrennt), von denen Moni `X-Forwarded-For`
auswertet. Sie sorgen dafür, dass Sperre nach Fehlversuchen und Rate-Limit pro echtem Client wirken. Tragen Sie
nur Proxys ein, die Sie kontrollieren. Welche Adresse der Proxy aus Sicht von Moni hat, sehen Sie im
Zugriffslog (`./scripts/logs.sh manager`).

**Warnung:** Bei `MONI_TLS=on` wird `MONI_TRUSTED_PROXIES` ignoriert; Moni schreibt dazu eine Warnung ins Log.
Wer einen Proxy vorschaltet, muss `MONI_TLS=off` setzen.

Folgen von `MONI_TLS=off`:

- **Manager:** nginx im Container lauscht auf HTTP. Compose veröffentlicht das auf Host-Port **8080**
  (`${MONI_MANAGER_BIND:-127.0.0.1}:8080`). Der Proxy leitet dorthin. Der Manager läuft weiter mit
  `MONI_MANAGER_BIND=127.0.0.1`, wenn der Proxy auf demselben Host läuft; sonst binden Sie ihn an die Adresse, die der Proxy erreicht, und
  sperren den Port per Firewall für alle anderen. Das Session-Cookie ist immer `Secure`, die Anmeldung im Browser braucht also
  HTTPS am Proxy.
- **Agent:** lauscht auf Port 9810 per HTTP (`${MONI_AGENT_BIND:-127.0.0.1}:9810`). Es gibt kein mTLS und keine
  Kopplung; der Manager nutzt den [Token-Modus](kopplung.md#token-modus-agent-ohne-mtls). Die Routen für
  Kopplung und Tokens am Agent entfallen (siehe [API](api.md)), Tokens legen Sie per CLI an.
- Ohne mTLS ist die Verbindung schwächer abgesichert als im Standard; die Oberfläche zeigt je Server die Art der Kopplung.

## Feste Zertifikatspfade im Manager (TLS an)

Mit `MONI_TLS=on` terminiert nginx im Manager-Image selbst. Er nutzt `/data/tls/https.crt` und
`/data/tls/https.key` (aus der Manager-CA, Namen aus `MONI_MANAGER_HOSTNAMES`). Die Pfade sind fest; ein eigenes
Zertifikat (z. B. von Let's Encrypt) wird nicht eingebunden. Wollen Sie eines, terminieren Sie TLS im Proxy
und setzen `MONI_TLS=off`.

## Beispiel Traefik (Manager)

Traefik läuft hier außerhalb des Compose-Stacks und erreicht den Manager über HTTP auf Port 8080. Dynamische Konfiguration
(File-Provider), `<MANAGER-ADRESSE>` ist die Adresse, auf die `MONI_MANAGER_BIND` zeigt:

```yaml
http:
  routers:
    moni:
      rule: Host(`moni.example.org`)
      entryPoints: [websecure]
      tls:
        certResolver: letsencrypt
      service: moni
  services:
    moni:
      loadBalancer:
        servers:
          - url: http://<MANAGER-ADRESSE>:8080
```

und in `.env` des Managers `MONI_TLS=off`, `MONI_TRUSTED_PROXIES=<Adresse von Traefik>`. Traefik setzt
`X-Forwarded-For` und `X-Forwarded-Proto` von sich aus.

## Beispiel Cloudflare Tunnel (Agent ohne offenen Port)

Der Agent muss keinen Port ins Internet öffnen: Der Tunnel (`cloudflared`) baut die Verbindung nach außen auf.
Der Agent bleibt auf `127.0.0.1:9810` (Standard von `MONI_AGENT_BIND`) und läuft mit

```
MONI_TLS=off
MONI_TRUSTED_PROXIES=127.0.0.1
```

`cloudflared` auf demselben Host zeigt mit einem Ingress-Eintrag auf den Agent:

```yaml
ingress:
  - hostname: moni-agent.example.org
    service: http://127.0.0.1:9810
  - service: http_status:404
```

Die Proxy-Adresse, die der Agent sieht, hängt davon ab, wie `cloudflared` läuft (auf dem Host, im Container).
Prüfen Sie sie im Log und passen Sie `MONI_TRUSTED_PROXIES` an. Schützen Sie den Hostnamen zusätzlich mit
Cloudflare Access (Service-Token). Der Manager sendet dessen Zugangsdaten als zusätzliche Header, siehe
[Token-Modus](kopplung.md#token-modus-agent-ohne-mtls). So sind zwei Hürden aktiv: Access plus das Moni-Token.

## OIDC braucht eine echte Domain

Die OIDC-Anmeldung (Google, Microsoft, Keycloak, Authentik und andere) leitet den Browser zurück an eine feste
Adresse des Managers. Dafür braucht der Manager eine echte Domain mit gültigem HTTPS-Zertifikat, also
einen Proxy wie oben. Die öffentliche Adresse muss mit `https://` beginnen und ohne Pfad sein. Tragen Sie sie in der Verwaltung
unter **Anmeldung, Öffentliche URL des Managers** ein, daraus entsteht die Redirect-URI jedes Anbieters; Benutzer
legt ein Admin vorher mit ihrer E-Mail-Adresse an, Konten entstehen nicht automatisch.
