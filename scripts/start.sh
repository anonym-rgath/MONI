#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# Farben
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo -e "${GREEN}   Pi Monitor - Start${NC}"
echo ""

# Docker Check
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Docker nicht installiert!${NC}"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo -e "${RED}Docker Compose Plugin nicht installiert!${NC}"
    exit 1
fi

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

echo -e "${YELLOW}Baue und starte Pi Monitor...${NC}"
docker compose up -d --build

# Status
echo ""
docker compose ps
echo ""
echo -e "${GREEN}Pi Monitor läuft${NC}"
MONITOR_ENDPOINT="$(docker compose port pi-monitor 80 || true)"
if [ -n "$MONITOR_ENDPOINT" ]; then
    echo -e "Endpoint: ${GREEN}${MONITOR_ENDPOINT}${NC}"
fi
echo ""
