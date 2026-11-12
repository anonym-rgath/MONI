#!/usr/bin/env bash
# Diagnose ohne Geheimnisse: diagnose.sh agent|manager
# Gibt nie die Umgebung der Container oder `compose config` aus (enthaelt MONI_SECRET_KEY).
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_rolle.sh
source "$SCRIPT_DIR/_rolle.sh"
rolle_waehlen "$@"
# Nur damit Compose die Datei aufloest: diese Skripte duerfen nie `up` ausfuehren
[ "$ROLLE" = manager ] && export MONI_SECRET_KEY="${MONI_SECRET_KEY:-unbenutzt}"

# Schluesselwoerter und Inhalte (Token, Kopplungs-/Wiederherstellungscodes) maskieren
maskieren() { sed -E '/token|secret|password|code=|pair-v1|bearer|recovery|wiederherstellung|moni_[A-Za-z0-9]{20,}/I s/.*/[maskiert]/'; }

echo "Moni ${ROLLE} - Diagnose"
echo "======================="
echo ""
echo "Version:"
compose exec -T "$SERVICE" python -c 'import moni; print(moni.__version__)' 2>&1 | maskieren || true
echo ""
echo "ready:"
if BASIS_URL="$(laufende_url)"; then
    curl -sSk -w ' (HTTP %{http_code})\n' "${BASIS_URL}/api/v1/ready" 2>&1 | maskieren || true
else
    echo "nicht ermittelbar (Container laeuft nicht)"
fi
echo ""
echo "Dienste:"
compose ps 2>&1 | maskieren || true
echo ""
echo "Letzte 50 Logzeilen:"
compose logs --tail 50 2>&1 | maskieren || true
