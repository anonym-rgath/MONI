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
    # Projektname steht in der Compose-Datei (name:), Agent und Manager kommen sich so nicht in die Quere
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

# Reine Abbildung: url_bauen <MONI_TLS-Wert> <host:port>; Schema aus MONI_TLS, Wildcard-Adressen -> 127.0.0.1
url_bauen() {
    local scheme=https host="${2%:*}" port="${2##*:}"
    [ "$1" = off ] && scheme=http
    case "$host" in 0.0.0.0|::|'[::]') host=127.0.0.1 ;; esac
    echo "${scheme}://${host}:${port}"
}

# Basis-URL aus dem laufenden Container: MONI_TLS (nur diese Variable) und veroeffentlichter Port.
# Rueckgabe 1 mit deutscher Meldung, wenn der Container nicht laeuft.
laufende_url() {
    local cid tls cport hp
    cid="$(compose ps -q "$SERVICE" 2>/dev/null)"
    if [ -z "$cid" ]; then echo "$SERVICE laeuft nicht. Starte zuerst ./scripts/start.sh $ROLLE" >&2; return 1; fi
    tls="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$cid" | grep -m1 '^MONI_TLS=' || true)"
    tls="${tls#MONI_TLS=}"
    if [ "$ROLLE" = agent ]; then cport=9810; elif [ "${tls:-on}" = off ]; then cport=80; else cport=443; fi
    hp="$(compose port "$SERVICE" "$cport" 2>/dev/null | head -n 1 || true)"
    if [ -z "$hp" ]; then echo "Kein veroeffentlichter Port fuer $SERVICE:$cport gefunden" >&2; return 1; fi
    url_bauen "${tls:-on}" "$hp"
}

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

# .env aus .env.example anlegen/ergaenzen (ohne Werte auszugeben)
env_pruefen() {
    if [ ! -f .env ]; then
        [ -f .env.example ] || fail ".env.example fehlt in $ROOT_DIR"
        cp .env.example .env
        chmod 600 .env
        warn ".env aus .env.example erstellt, bitte vor dem produktiven Einsatz pruefen"
    fi
    if [ "$ROLLE" = manager ] && [ -z "${MONI_SECRET_KEY:-}" ] && ! grep -qE '^[[:space:]]*MONI_SECRET_KEY=.' .env; then
        fail "MONI_SECRET_KEY fehlt: docker run --rm <manager-image> python -m moni.cli secret generate, dann in .env eintragen"
    fi
}
