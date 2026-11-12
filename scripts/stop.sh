#!/usr/bin/env bash
# Moni stoppen: stop.sh agent|manager (Daten-Volumes bleiben erhalten)
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_rolle.sh
source "$SCRIPT_DIR/_rolle.sh"
rolle_waehlen "$@"

# Der Manager-Schluessel wird zum Aufloesen der Datei gebraucht, nicht benutzt
# Nur damit Compose die Datei aufloest: diese Skripte duerfen nie `up` ausfuehren
[ "$ROLLE" = manager ] && export MONI_SECRET_KEY="${MONI_SECRET_KEY:-unbenutzt}"
compose down
echo -e "${GREEN}Moni ${ROLLE} gestoppt.${NC} Neu starten: ./scripts/start.sh ${ROLLE}"
