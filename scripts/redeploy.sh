#!/usr/bin/env bash

# Pi Monitor - Redeploy-Script nach git pull
# ==========================================
# Workflow:
#   1. git pull (Fast-Forward, bricht bei Konflikt ab)
#   2. .env aus .env.example pruefen/ergaenzen
#   3. pi-monitor neu bauen + neu starten
#   4. Status anzeigen
#   5. Smoke-/Security-Check ausfuehren
#
# Optional: --force erzwingt Container-Recreate, auch wenn Image-Hash gleich
# bleibt. Nuetzlich falls Compose den Build aus Cache nicht als "neu" erkennt.
# Optional: --no-smoke ueberspringt den Smoke-/Security-Check.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

FORCE_RECREATE=""
RUN_SMOKE=1

for arg in "$@"; do
    case "$arg" in
        --force|--force-recreate)
            FORCE_RECREATE="--force-recreate"
            ;;
        --no-smoke)
            RUN_SMOKE=0
            ;;
        *)
            echo -e "${RED}Unbekanntes Flag: $arg${NC}"
            echo "Nutzung: $0 [--force] [--no-smoke]"
            exit 1
            ;;
    esac
done

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}   Pi Monitor Redeploy${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""

if ! command -v docker >/dev/null 2>&1; then
    echo -e "${RED}Docker nicht installiert!${NC}"
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo -e "${RED}Docker Compose Plugin nicht installiert!${NC}"
    exit 1
fi

echo -e "${YELLOW}[1/5] git pull (--ff-only)...${NC}"
if ! git pull --ff-only; then
    echo ""
    echo -e "${RED}git pull fehlgeschlagen.${NC}"
    echo "Wenn lokale Commits ungepushte Aenderungen enthalten oder der Branch divergiert ist,"
    echo "manuell pruefen mit: git status / git log --oneline --branches"
    exit 1
fi

echo ""
echo -e "${YELLOW}[2/5] .env pruefen...${NC}"
if [ ! -f "$ROOT_DIR/.env" ]; then
    if [ ! -f "$ROOT_DIR/.env.example" ]; then
        echo -e "${RED}Fehler: .env.example fehlt im Repo-Root: $ROOT_DIR/.env.example${NC}"
        echo "Bitte .env.example committen/pullen oder manuell eine .env im Repo-Root anlegen."
        exit 1
    fi

    echo -e "${YELLOW}Erstelle .env aus .env.example...${NC}"
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    echo -e "${YELLOW}Bitte pruefe .env vor dem produktiven Einsatz.${NC}"
fi

if [ -f "$ROOT_DIR/.env.example" ]; then
    added_env_keys=0
    while IFS= read -r env_line; do
        case "$env_line" in
            ""|\#*) continue ;;
        esac

        env_key="${env_line%%=*}"
        if ! grep -qE "^[[:space:]]*${env_key}=" "$ROOT_DIR/.env"; then
            echo "$env_line" >> "$ROOT_DIR/.env"
            added_env_keys=1
        fi
    done < "$ROOT_DIR/.env.example"

    if [ "$added_env_keys" -eq 1 ]; then
        echo -e "${YELLOW}Fehlende Werte aus .env.example wurden in .env ergaenzt.${NC}"
    else
        echo "  .env ist vollstaendig."
    fi
fi

MONITOR_HOST="$(sed -n 's/^MONITOR_HOST=//p' "$ROOT_DIR/.env" | tail -n 1)"
if [ -z "$MONITOR_HOST" ] || [ "$MONITOR_HOST" = "monitor.example.com" ]; then
    echo -e "${RED}Fehler: MONITOR_HOST ist nicht fuer den produktiven Betrieb gesetzt.${NC}"
    echo "Bitte in .env die echte Domain setzen, z. B.:"
    echo "  MONITOR_HOST=monitor.sau-index.de"
    exit 1
fi

if grep -qE '^[[:space:]]*MONITOR_PORT=' "$ROOT_DIR/.env"; then
    echo -e "${YELLOW}Hinweis: MONITOR_PORT ist veraltet und wird ignoriert.${NC}"
    echo "Der Container hoert intern immer auf Port 80; Traefik routet ebenfalls auf Port 80."
fi

echo ""
echo -e "${YELLOW}[3/5] pi-monitor neu bauen + starten...${NC}"
if [ -n "$FORCE_RECREATE" ]; then
    echo "  (force-recreate aktiv)"
fi

docker compose up -d --build $FORCE_RECREATE pi-monitor

echo ""
echo -e "${YELLOW}[4/5] Status:${NC}"
docker compose ps docker-socket-proxy pi-monitor

echo ""
if [ "$RUN_SMOKE" -eq 1 ]; then
    echo -e "${YELLOW}[5/5] Smoke-/Security-Check:${NC}"
    "$ROOT_DIR/scripts/smoke.sh"
else
    echo -e "${YELLOW}[5/5] Smoke-/Security-Check uebersprungen (--no-smoke).${NC}"
fi

echo ""
echo -e "${GREEN}Redeploy fertig.${NC}"
echo ""
