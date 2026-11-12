#!/usr/bin/env bash
# Selbsttest der Betriebsskripte. Erwartet fuer die letzten Pruefungen einen laufenden Agent-Stack.
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

fail() { echo "FAIL: $*"; exit 1; }

# Exit-Code eines Aufrufs, ohne dass set -e abbricht
rc() { local r=0; "$@" >/dev/null 2>&1 || r=$?; echo "$r"; }

[ "$(rc ./start.sh)" = 2 ] || fail "start.sh ohne Rolle muss Exit 2 liefern"
[ "$(rc ./start.sh foo)" = 2 ] || fail "start.sh foo muss Exit 2 liefern"
[ "$(rc ./smoke.sh)" = 2 ] || fail "smoke.sh ohne Rolle muss Exit 2 liefern"
echo "OK: Rollenpruefung"

./smoke.sh agent || fail "smoke.sh agent gegen den laufenden Agent"
echo "OK: smoke.sh agent"

./diagnose.sh agent > "${TMPDIR:-/tmp}/moni-diagnose.out" 2>&1 || true
if grep -qE 'moni_[A-Za-z0-9]{20,}|moni-pair-v1' "${TMPDIR:-/tmp}/moni-diagnose.out"; then fail "diagnose.sh gibt ein Token aus"; fi
grep -q 'ready' "${TMPDIR:-/tmp}/moni-diagnose.out" || fail "diagnose.sh nennt keinen Readiness-Status"
echo "OK: diagnose.sh ohne Geheimnisse"

# URL-Auswahl (kein Stack noetig)
# shellcheck source=scripts/_rolle.sh
source ./_rolle.sh
url() { ROLLE="$1" basis_url; }
[ "$(url agent)" = https://127.0.0.1:9810 ] || fail "URL agent Standard"
[ "$(MONI_TLS=off url agent)" = http://127.0.0.1:9810 ] || fail "URL agent TLS off"
[ "$(url manager)" = https://127.0.0.1:8443 ] || fail "URL manager Standard"
[ "$(MONI_TLS=off url manager)" = http://127.0.0.1:8080 ] || fail "URL manager TLS off"
[ "$(MONI_HTTPS_PORT=9443 MONI_MANAGER_BIND=0.0.0.0 url manager)" = https://127.0.0.1:9443 ] || fail "URL manager Port/Bind"
[ "$(MONI_AGENT_BIND=10.0.0.5 url agent)" = https://10.0.0.5:9810 ] || fail "URL agent Bind"
echo "OK: URL-Auswahl"
