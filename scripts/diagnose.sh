#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

echo "Pi Monitor - Diagnose"
echo "====================="
echo ""

if [ -f "$ROOT_DIR/.env" ]; then
    echo ".env:"
    sed -n 's/^\(MONITOR_HOST\|TRAEFIK_ENTRYPOINT\)=/\1=/p' "$ROOT_DIR/.env"
else
    echo ".env fehlt"
fi

echo ""
echo "Compose labels:"
docker compose config 2>/dev/null | sed -n '/labels:/,/networks:/p'

echo ""
echo "Services:"
docker compose ps

echo ""
echo "pi-monitor Netzwerke:"
docker inspect pi-monitor --format '{{json .NetworkSettings.Networks}}' 2>/dev/null || true

echo ""
echo "pi-monitor Traefik Labels:"
docker inspect pi-monitor --format '{{range $k, $v := .Config.Labels}}{{println $k "=" $v}}{{end}}' 2>/dev/null \
    | sort \
    | sed -n '/traefik/p' || true

echo ""
echo "Interner Healthcheck:"
docker compose exec -T pi-monitor curl -fsS -D - http://127.0.0.1/api/ || true

echo ""
echo "Host-Header-Test im Container:"
MONITOR_HOST="$(sed -n 's/^MONITOR_HOST=//p' "$ROOT_DIR/.env" 2>/dev/null | tail -n 1)"
if [ -n "$MONITOR_HOST" ]; then
    docker compose exec -T pi-monitor curl -fsS -H "Host: $MONITOR_HOST" -D - http://127.0.0.1/ | head -n 20 || true
else
    echo "MONITOR_HOST leer, Host-Header-Test uebersprungen"
fi

echo ""
echo "Traefik Host-Header-Test auf dem Host:"
if [ -n "$MONITOR_HOST" ]; then
    curl -fsS -H "Host: $MONITOR_HOST" -D - http://127.0.0.1/ -o /tmp/pi-monitor-traefik-index.html | head -n 20 || true

    STATIC_ASSET="$(sed -n 's/.*src="\(\/static\/js\/[^"]*\.js\)".*/\1/p' /tmp/pi-monitor-traefik-index.html | head -n 1)"
    if [ -n "$STATIC_ASSET" ]; then
        echo ""
        echo "Traefik Static-Asset-Test auf dem Host ($STATIC_ASSET):"
        curl -fsS -H "Host: $MONITOR_HOST" -D - "http://127.0.0.1$STATIC_ASSET" -o /dev/null | head -n 20 || true
    else
        echo ""
        echo "Kein statisches JS-Asset im Traefik-Response gefunden."
    fi
else
    echo "MONITOR_HOST leer, Traefik Host-Header-Test uebersprungen"
fi

echo ""
echo "Hinweis:"
echo "Wenn die internen Checks 200 liefern, aber extern 404 kommt, liegt der Fehler vor pi-monitor:"
echo "- falscher Hostname"
echo "- falscher Traefik EntryPoint"
echo "- pi-monitor nicht im traefik-network"
echo "- Traefik liest den Docker Provider/Labels nicht"
echo "- Cloudflare Tunnel/Access routet nicht alle Pfade, insbesondere /static/* und /api/*"
