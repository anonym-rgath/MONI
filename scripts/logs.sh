#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# Linux Monitor - Logs anzeigen
# ==========================

echo "Zeige Linux-Monitor-Logs (STRG+C zum Beenden)..."
echo ""

docker compose logs -f "$@"
