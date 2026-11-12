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
if grep -E 'moni_[A-Za-z0-9]{20,}|moni-pair-v1' "${TMPDIR:-/tmp}/moni-diagnose.out"; then fail "diagnose.sh gibt ein Token aus"; fi
grep -q 'ready' "${TMPDIR:-/tmp}/moni-diagnose.out" || fail "diagnose.sh nennt keinen Readiness-Status"
echo "OK: diagnose.sh ohne Geheimnisse"
