#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

fail() {
    echo -e "${RED}FAIL:${NC} $*"
    exit 1
}

ok() {
    echo -e "${GREEN}OK:${NC} $*"
}

warn() {
    echo -e "${YELLOW}WARN:${NC} $*"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "$1 ist nicht installiert"
}

compose_exec() {
    docker compose exec -T pi-monitor "$@"
}

curl_pi_monitor() {
    compose_exec curl -fsS "$@"
}

wait_for_running() {
    service_name="$1"
    container_name="$2"

    for _ in $(seq 1 30); do
        if [ "$(docker inspect --format '{{.State.Running}}' "$container_name" 2>/dev/null || true)" = "true" ]; then
            return 0
        fi
        sleep 1
    done

    fail "$service_name laeuft nicht. Starte zuerst ./scripts/start.sh"
}

require_command docker
docker compose version >/dev/null 2>&1 || fail "Docker Compose Plugin ist nicht installiert"

echo "Pi Monitor - Smoke/Security Check"
echo "================================="
echo ""

docker compose config >/dev/null
ok "Compose-Konfiguration ist gueltig"

wait_for_running "pi-monitor" "pi-monitor"
wait_for_running "docker-socket-proxy" "docker-socket-proxy"

ok "Container laufen"

curl_pi_monitor http://127.0.0.1/api/ >/dev/null
ok "API Root antwortet"

curl_pi_monitor http://127.0.0.1/api/metrics/host >/dev/null
ok "Host-Metriken antworten"

curl_pi_monitor http://127.0.0.1/api/metrics/containers >/dev/null
ok "Container-Metriken antworten"

curl_pi_monitor http://127.0.0.1/api/metrics/all >/dev/null
ok "Kombinierter Metrics-Endpunkt antwortet"

headers="$(compose_exec curl -fsS -D - -o /tmp/pi-monitor-smoke-index.html http://127.0.0.1/)"
printf '%s\n' "$headers" | grep -qi '^Content-Security-Policy:' \
    || fail "Content-Security-Policy Header fehlt"
printf '%s\n' "$headers" | grep -qi '^X-Content-Type-Options: nosniff' \
    || fail "X-Content-Type-Options Header fehlt"
printf '%s\n' "$headers" | grep -qi '^Referrer-Policy: no-referrer' \
    || fail "Referrer-Policy Header fehlt"
printf '%s\n' "$headers" | grep -qi '^X-Frame-Options: DENY' \
    || fail "X-Frame-Options Header fehlt"
printf '%s\n' "$headers" | grep -qi '^Permissions-Policy:' \
    || fail "Permissions-Policy Header fehlt"
ok "Frontend antwortet mit Security-Headern"

post_status="$(
    compose_exec curl -sS -o /tmp/pi-monitor-proxy-post.out -w '%{http_code}' \
        -X POST http://docker-socket-proxy:2375/containers/create || true
)"
if [ "$post_status" != "403" ]; then
    fail "Docker-Socket-Proxy blockt POST nicht wie erwartet (HTTP $post_status)"
fi
ok "Docker-Socket-Proxy blockt POST Requests"

inspect="$(docker inspect --format 'User={{.Config.User}} ReadonlyRootfs={{.HostConfig.ReadonlyRootfs}} CapDrop={{json .HostConfig.CapDrop}} SecurityOpt={{json .HostConfig.SecurityOpt}}' pi-monitor)"
printf '%s\n' "$inspect" | grep -q 'User=app' \
    || fail "pi-monitor laeuft nicht als User app: $inspect"
printf '%s\n' "$inspect" | grep -q 'ReadonlyRootfs=true' \
    || fail "pi-monitor Root-Dateisystem ist nicht read-only: $inspect"
printf '%s\n' "$inspect" | grep -q '"ALL"' \
    || fail "pi-monitor droppt nicht alle Capabilities: $inspect"
printf '%s\n' "$inspect" | grep -q 'no-new-privileges:true' \
    || fail "pi-monitor nutzt nicht no-new-privileges: $inspect"
ok "pi-monitor Runtime-Haertung aktiv"

proxy_inspect="$(docker inspect --format 'SecurityOpt={{json .HostConfig.SecurityOpt}} NetworkMode={{.HostConfig.NetworkMode}}' docker-socket-proxy)"
printf '%s\n' "$proxy_inspect" | grep -q 'no-new-privileges:true' \
    || fail "docker-socket-proxy nutzt nicht no-new-privileges: $proxy_inspect"
ok "docker-socket-proxy Basishaertung aktiv"

if docker inspect --format '{{json .NetworkSettings.Networks}}' docker-socket-proxy | grep -q 'traefik-network'; then
    fail "docker-socket-proxy haengt im traefik-network; er sollte nur intern erreichbar sein"
fi
ok "docker-socket-proxy ist nicht im Traefik-Netz"

health="$(docker inspect --format '{{.State.Health.Status}}' pi-monitor 2>/dev/null || true)"
if [ "$health" = "healthy" ]; then
    ok "Docker Healthcheck ist healthy"
elif [ "$health" = "starting" ]; then
    warn "Docker Healthcheck ist noch starting"
else
    fail "Docker Healthcheck ist nicht healthy: ${health:-unbekannt}"
fi

echo ""
ok "Smoke/Security Check abgeschlossen"
