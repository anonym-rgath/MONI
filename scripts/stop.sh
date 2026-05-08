#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# Farben für Output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo ""
echo -e "${YELLOW}============================================${NC}"
echo -e "${YELLOW}   Pi Monitor - Stop Script${NC}"
echo -e "${YELLOW}============================================${NC}"
echo ""

# Prüfe ob Docker installiert ist
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Fehler: Docker ist nicht installiert!${NC}"
    exit 1
fi

# Prüfe ob Docker Compose Plugin vorhanden ist
if ! docker compose version &> /dev/null; then
    echo -e "${RED}Fehler: Docker Compose Plugin ist nicht installiert!${NC}"
    exit 1
fi

echo -e "${YELLOW}Stoppe alle Services...${NC}"
echo ""

docker compose down

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}   Pi Monitor gestoppt${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "Zum erneuten Starten:"
echo "  ./scripts/start.sh"
echo ""
