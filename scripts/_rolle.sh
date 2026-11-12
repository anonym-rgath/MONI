# Gemeinsame Auswahl von Rolle und Compose-Datei. Wird mit `source` geladen, nicht ausgefuehrt.
# Aufruf im Skript: source "$SCRIPT_DIR/_rolle.sh"; rolle_waehlen "$@"; shift
# shellcheck shell=bash
# shellcheck disable=SC2034  # SERVICE, PORT, ROLLE, COMPOSE_FILE nutzen die ladenden Skripte

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

fail() { echo -e "${RED}FAIL:${NC} $*"; exit 1; }
ok() { echo -e "${GREEN}OK:${NC} $*"; }
warn() { echo -e "${YELLOW}WARN:${NC} $*"; }

rolle_hilfe() {
    echo "Nutzung: $0 agent|manager [Optionen]" >&2
    exit 2
}

rolle_waehlen() {
    case "${1:-}" in
        agent|manager) ROLLE="$1" ;;
        *) rolle_hilfe ;;
    esac
    ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
    cd "$ROOT_DIR" || exit 1
    COMPOSE_FILE="compose.${ROLLE}.yaml"
    SERVICE="moni-${ROLLE}"
    # Beide Stacks liegen im selben Verzeichnis: der Manager bekommt ein eigenes Projekt (wie in der CI)
    [ "$ROLLE" = manager ] && export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-moni-manager}"
    if [ "$ROLLE" = agent ]; then PORT=9810; else PORT="${MONI_HTTPS_PORT:-8443}"; fi
    command -v docker >/dev/null 2>&1 || fail "docker ist nicht installiert"
    docker compose version >/dev/null 2>&1 || fail "Docker Compose Plugin ist nicht installiert"
    # Gruppe des Docker-Sockets fuer den Socket-Proxy; Docker Desktop (macOS): 0
    if [ -z "${DOCKER_GID:-}" ] && ! grep -qE '^[[:space:]]*DOCKER_GID=.' .env 2>/dev/null; then
        if [ "$(uname -s)" = Darwin ]; then DOCKER_GID=0; else DOCKER_GID="$(stat -c %g /var/run/docker.sock)"; fi
    fi
    [ -z "${DOCKER_GID:-}" ] || export DOCKER_GID
}

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

# .env aus .env.example anlegen/ergaenzen (ohne Werte auszugeben)
env_pruefen() {
    if [ ! -f .env ]; then
        [ -f .env.example ] || fail ".env.example fehlt in $ROOT_DIR"
        cp .env.example .env
        warn ".env aus .env.example erstellt, bitte vor dem produktiven Einsatz pruefen"
    fi
    if [ "$ROLLE" = manager ] && [ -z "${MONI_SECRET_KEY:-}" ] && ! grep -qE '^[[:space:]]*MONI_SECRET_KEY=.' .env; then
        fail "MONI_SECRET_KEY fehlt: docker run --rm <manager-image> python -m moni.cli secret generate, dann in .env eintragen"
    fi
}
