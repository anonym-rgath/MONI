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
    BASIS_URL="$(basis_url)"
    command -v docker >/dev/null 2>&1 || fail "docker ist nicht installiert"
    docker compose version >/dev/null 2>&1 || fail "Docker Compose Plugin ist nicht installiert"
    # Gruppe des Docker-Sockets fuer den Socket-Proxy; Docker Desktop (macOS): 0
    if [ -z "${DOCKER_GID:-}" ] && ! grep -qE '^[[:space:]]*DOCKER_GID=.' .env 2>/dev/null; then
        if [ "$(uname -s)" = Darwin ]; then DOCKER_GID=0; else
            [ -S /var/run/docker.sock ] || fail "Docker-Socket nicht gefunden, DOCKER_GID manuell setzen"
            DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
        fi
    fi
    [ -z "${DOCKER_GID:-}" ] || export DOCKER_GID
}

# Wert aus Umgebung oder .env (nur der angefragte Schluessel wird gelesen, nie ausgegeben)
env_wert() {
    local v="${!1:-}"
    [ -n "$v" ] || v="$(sed -n "s/^[[:space:]]*$1=//p" .env 2>/dev/null | tail -n 1)"
    echo "${v:-$2}"
}

# Basis-URL der API auf dem Host: Schema und Port je nach MONI_TLS, Bind 0.0.0.0 -> 127.0.0.1
basis_url() {
    local tls bind port scheme=https
    tls="$(env_wert MONI_TLS on)"
    if [ "$ROLLE" = agent ]; then
        bind="$(env_wert MONI_AGENT_BIND 127.0.0.1)"; port=9810
    else
        bind="$(env_wert MONI_MANAGER_BIND 127.0.0.1)"; port="$(env_wert MONI_HTTPS_PORT 8443)"
    fi
    if [ "$tls" = off ]; then
        scheme=http
        [ "$ROLLE" = manager ] && port=8080  # Host-Port auf Container-Port 80
    fi
    [ "$bind" = 0.0.0.0 ] && bind=127.0.0.1
    echo "${scheme}://${bind}:${port}"
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
