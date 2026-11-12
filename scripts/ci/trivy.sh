#!/usr/bin/env bash
# Trivy-Scan eines lokalen Images: scripts/ci/trivy.sh IMAGE [PLATFORM] (D10).
# Einzige Stelle fuer Trivy-Digest und -Flags; CI und Release-Workflow rufen dieses Skript.
# Trivy laeuft als Container ohne Drittanbieter-Action, der Docker-Socket ist eingebunden (:ro schraenkt die API nicht ein, Trivy liest nur das Image).
# Mit PLATFORM (z. B. linux/arm64) wird genau diese Plattform per `docker save --platform` exportiert und das Archiv
# gescannt: Trivys --platform wird bei Daemon-Images mit containerd-Store nicht beachtet (scannt die Host-Plattform).
# Ausnahmen nur ueber .trivyignore (Begruendung und Ablaufdatum je Eintrag).
set -Eeuo pipefail
[ "$#" = 1 ] || [ "$#" = 2 ] || { echo "Aufruf: $0 IMAGE [PLATFORM]" >&2; exit 2; }
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

# aquasec/trivy 0.76.1 (2026-10-21), Multi-Arch-Index
TRIVY_IMAGE="aquasec/trivy@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c"
FLAGS=(--skip-version-check --severity CRITICAL,HIGH --ignore-unfixed --exit-code 1 --ignorefile /.trivyignore)

if [ "$#" = 2 ]; then
  tmp=$(mktemp -d "$PWD/.trivy-scan.XXXXXX")
  trap 'rm -rf "$tmp"' EXIT
  docker save --platform "$2" -o "$tmp/image.tar" "$1"
  docker run --rm -v "$tmp:/scan:ro" -v "$PWD/.trivyignore:/.trivyignore:ro" \
    "$TRIVY_IMAGE" image "${FLAGS[@]}" --input /scan/image.tar
else
  docker run --rm \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    -v "$PWD/.trivyignore:/.trivyignore:ro" \
    "$TRIVY_IMAGE" image "${FLAGS[@]}" "$1"
fi
