#!/usr/bin/env bash
# Trivy-Scan eines lokalen Images: scripts/ci/trivy.sh IMAGE (D10).
# Einzige Stelle fuer Trivy-Digest und -Flags; CI und Release-Workflow rufen dieses Skript.
# Trivy laeuft als Container ohne Drittanbieter-Action, der Docker-Socket ist nur lesbar eingebunden.
# Ausnahmen nur ueber .trivyignore (Begruendung und Ablaufdatum je Eintrag).
set -Eeuo pipefail
[ "$#" = 1 ] || { echo "Aufruf: $0 IMAGE" >&2; exit 2; }
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# aquasec/trivy 0.76.1 (2026-10-21), Multi-Arch-Index
TRIVY_IMAGE="aquasec/trivy@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c"

exec docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v "$PWD/.trivyignore:/.trivyignore:ro" \
  "$TRIVY_IMAGE" image \
  --skip-version-check --severity CRITICAL,HIGH --ignore-unfixed --exit-code 1 \
  --ignorefile /.trivyignore "$1"
