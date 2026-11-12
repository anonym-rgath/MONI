#!/usr/bin/env bash
# Aktualisieren: redeploy.sh agent|manager [--local] [--force] [--no-smoke]
# Zieht das Image (pull) und startet neu; es wird nie gebaut.
# --local: kein pull, nutzt MONI_AGENT_IMAGE bzw. MONI_MANAGER_IMAGE wie gesetzt (lokal gebautes Image).
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_rolle.sh
source "$SCRIPT_DIR/_rolle.sh"
rolle_waehlen "$@"
shift

LOCAL=0; FORCE=(); SMOKE=1
for arg in "$@"; do
    case "$arg" in
        --local) LOCAL=1 ;;
        --force|--force-recreate) FORCE=(--force-recreate) ;;
        --no-smoke) SMOKE=0 ;;
        *) echo "Unbekanntes Flag: $arg" >&2; rolle_hilfe ;;
    esac
done

env_pruefen
if [ "$LOCAL" = 1 ]; then
    echo -e "${YELLOW}[1/3] --local: kein pull, nutze das gesetzte Image${NC}"
else
    echo -e "${YELLOW}[1/3] Image ziehen...${NC}"
    compose pull
fi
echo -e "${YELLOW}[2/3] Neu starten...${NC}"
compose up -d ${FORCE[@]+"${FORCE[@]}"}
compose ps
if [ "$SMOKE" = 1 ]; then
    echo -e "${YELLOW}[3/3] Smoke-/Security-Check${NC}"
    "$SCRIPT_DIR/smoke.sh" "$ROLLE"
else
    echo -e "${YELLOW}[3/3] Smoke-Check uebersprungen (--no-smoke)${NC}"
fi
echo -e "${GREEN}Redeploy fertig.${NC}"
