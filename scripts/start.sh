#!/usr/bin/env bash
# Moni starten: start.sh agent|manager
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_rolle.sh
source "$SCRIPT_DIR/_rolle.sh"
rolle_waehlen "$@"

env_pruefen
echo -e "${YELLOW}Starte Moni ${ROLLE}...${NC}"
compose up -d
echo ""
compose ps
echo ""
BASIS_URL="$(laufende_url)" || BASIS_URL="(URL nicht ermittelbar)"
echo -e "${GREEN}Moni ${ROLLE} laeuft${NC} (${BASIS_URL}, Pruefung: ./scripts/smoke.sh ${ROLLE})"
