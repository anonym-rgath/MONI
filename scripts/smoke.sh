#!/usr/bin/env bash
# Smoke-/Security-Check des laufenden Stacks: smoke.sh agent|manager
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_rolle.sh
source "$SCRIPT_DIR/_rolle.sh"
rolle_waehlen "$@"
[ "$ROLLE" = manager ] && export MONI_SECRET_KEY="${MONI_SECRET_KEY:-unbenutzt}"

echo "Moni ${ROLLE} - Smoke/Security Check"
echo "===================================="

cid="$(compose ps -q "$SERVICE")"
proxy="$(compose ps -q docker-socket-proxy)"
[ -n "$cid" ] || fail "$SERVICE laeuft nicht. Starte zuerst ./scripts/start.sh $ROLLE"
[ -n "$proxy" ] || fail "docker-socket-proxy laeuft nicht"
ok "Container laufen"

url="https://127.0.0.1:${PORT}/api/v1"
for endpoint in health ready; do
    good=0
    for _ in $(seq 1 30); do
        if curl -fsSk "$url/$endpoint" >/dev/null 2>&1; then good=1; break; fi
        sleep 1
    done
    [ "$good" = 1 ] || fail "$url/$endpoint antwortet nicht (Readiness: 503 solange nicht bereit)"
    ok "/api/v1/$endpoint antwortet"
done

inspect="$(docker inspect --format 'User={{.Config.User}} ReadonlyRootfs={{.HostConfig.ReadonlyRootfs}} CapDrop={{json .HostConfig.CapDrop}} SecurityOpt={{json .HostConfig.SecurityOpt}}' "$cid")"
[[ "$inspect" == User=app\ * ]] || fail "$SERVICE laeuft nicht als User app: $inspect"
[[ "$inspect" == *'ReadonlyRootfs=true'* ]] || fail "Root-Dateisystem ist nicht read-only: $inspect"
[[ "$inspect" == *'CapDrop=["ALL"]'* ]] || fail "Capabilities nicht vollstaendig entfernt: $inspect"
[[ "$inspect" == *'no-new-privileges:true'* ]] || fail "no-new-privileges fehlt: $inspect"
ok "$SERVICE Runtime-Haertung aktiv"

pinspect="$(docker inspect --format 'SecurityOpt={{json .HostConfig.SecurityOpt}} Ports={{json .HostConfig.PortBindings}}' "$proxy")"
[[ "$pinspect" == *'no-new-privileges:true'* ]] || fail "docker-socket-proxy ohne no-new-privileges: $pinspect"
[[ "$pinspect" == *'Ports={}'* ]] || fail "docker-socket-proxy veroeffentlicht einen Port: $pinspect"
ok "docker-socket-proxy gehaertet, ohne Port"

health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid")"
case "$health" in
    healthy) ok "Docker-Healthcheck ist healthy" ;;
    starting) warn "Docker-Healthcheck ist noch starting" ;;
    *) fail "Docker-Healthcheck ist nicht healthy: ${health:-unbekannt}" ;;
esac

echo ""
ok "Smoke/Security Check abgeschlossen"
