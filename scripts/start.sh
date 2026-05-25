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
    fi
fi

MONITOR_HOST="$(sed -n 's/^MONITOR_HOST=//p' "$ROOT_DIR/.env" | tail -n 1)"
if [ -z "$MONITOR_HOST" ] || [ "$MONITOR_HOST" = "monitor.example.com" ]; then
    echo -e "${RED}Fehler: MONITOR_HOST ist nicht fuer den produktiven Betrieb gesetzt.${NC}"
    echo "Bitte in .env die echte Domain setzen, z. B.:"
    echo "  MONITOR_HOST=monitor.sau-index.de"
    exit 1
fi

echo -e "${YELLOW}Baue und starte Pi Monitor...${NC}"
docker compose up -d --build

# Status
echo ""
docker compose ps
echo ""
echo -e "${GREEN}Pi Monitor läuft${NC}"
if [ -n "$MONITOR_HOST" ]; then
    echo -e "Endpoint: ${GREEN}http://${MONITOR_HOST}${NC}"
fi
echo ""
