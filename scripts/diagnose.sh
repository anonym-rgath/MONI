#!/usr/bin/env bash
# Diagnose ohne Geheimnisse: diagnose.sh agent|manager
# Gibt nie die Umgebung der Container oder `compose config` aus (enthaelt MONI_SECRET_KEY).
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_rolle.sh
source "$SCRIPT_DIR/_rolle.sh"
rolle_waehlen "$@"
[ "$ROLLE" = manager ] && export MONI_SECRET_KEY="${MONI_SECRET_KEY:-unbenutzt}"

maskieren() { sed -E '/token|secret|password|code=|pair-v1|bearer/I s/.*/[maskiert]/'; }

echo "Moni ${ROLLE} - Diagnose"
echo "======================="
echo ""
echo "Version:"
compose exec -T "$SERVICE" python -c 'import moni; print(moni.__version__)' 2>&1 | maskieren || true
echo ""
echo "ready:"
curl -sSk -w ' (HTTP %{http_code})\n' "https://127.0.0.1:${PORT}/api/v1/ready" 2>&1 | maskieren || true
echo ""
echo "Dienste:"
compose ps 2>&1 | maskieren || true
echo ""
echo "Letzte 50 Logzeilen:"
compose logs --tail 50 2>&1 | maskieren || true
