#!/usr/bin/env bash
# Logs folgen: logs.sh agent|manager [Compose-Optionen/Dienste]
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_rolle.sh
source "$SCRIPT_DIR/_rolle.sh"
rolle_waehlen "$@"
shift

[ "$ROLLE" = manager ] && export MONI_SECRET_KEY="${MONI_SECRET_KEY:-unbenutzt}"
compose logs -f "$@"
