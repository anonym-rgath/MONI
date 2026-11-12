#!/usr/bin/env bash
# Logs folgen: logs.sh agent|manager [Compose-Optionen/Dienste]
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_rolle.sh
source "$SCRIPT_DIR/_rolle.sh"
# Rohe Logs, nicht weitergeben - zum Teilen diagnose.sh nutzen
rolle_waehlen "$@"
shift

# Nur damit Compose die Datei aufloest: diese Skripte duerfen nie `up` ausfuehren
[ "$ROLLE" = manager ] && export MONI_SECRET_KEY="${MONI_SECRET_KEY:-unbenutzt}"
compose logs -f "$@"
