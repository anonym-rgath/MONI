#!/usr/bin/env python3
"""Versionspruefung (D1): check_version.py [TAG]

Liest die Version aus backend/moni/__init__.py, frontend/package.json, dem ersten "## ["-Abschnitt
von CHANGELOG.md (daher kein "[Unreleased]"-Abschnitt) und den Image-Defaults in compose.agent.yaml
und compose.manager.yaml. Exit 0, wenn alle gleich sind, sonst Exit 1 mit der Liste der Abweichungen.

Mit TAG (z. B. v2.0.0) gilt: TAG == "v" + Version, exakt. Auch Vorabversionen stehen deshalb in
allen Quellen (v2.0.0-rc.1 -> "2.0.0-rc.1"); der Release-Workflow nimmt sie dann (D2) nur mit
eigenem Tag und ohne latest. Ohne TAG gilt die Version aus __init__.py als Soll.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEMVER = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?")


def _first(pattern: str, path: str, flags: int = 0) -> str | None:
    m = re.search(pattern, (ROOT / path).read_text(encoding="utf-8"), flags)
    return m.group(1) if m else None


def sources() -> dict[str, str | None]:
    pkg = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    return {
        "moni/__init__.py": _first(r'__version__\s*=\s*"([^"]+)"', "backend/moni/__init__.py"),
        "frontend/package.json": pkg.get("version"),
        "CHANGELOG.md": _first(r"^## \[([^\]]+)\]", "CHANGELOG.md", re.M),
        "compose.agent.yaml": _first(r"\$\{MONI_AGENT_IMAGE:-ghcr\.io/anonym-rgath/moni-agent:([^}]+)\}", "compose.agent.yaml"),
        "compose.manager.yaml": _first(r"\$\{MONI_MANAGER_IMAGE:-ghcr\.io/anonym-rgath/moni-manager:([^}]+)\}", "compose.manager.yaml"),
    }


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print("Aufruf: check_version.py [TAG]")
        return 2
    found = sources()
    errors: list[str] = []
    if argv:
        if not argv[0].startswith("v"):
            print(f"FEHLER: Tag {argv[0]!r} beginnt nicht mit 'v'")
            return 1
        expected = argv[0][1:]
        if not SEMVER.fullmatch(expected):
            errors.append(f"Tag {argv[0]!r} ist keine Version vX.Y.Z[-Suffix]")
    else:
        expected = found["moni/__init__.py"] or ""
    for name, value in found.items():
        if value != expected:
            errors.append(f"{name}: {value!r}, erwartet {expected!r}")
    for e in errors:
        print("FEHLER:", e)
    if not errors:
        print(f"OK: alle Quellen tragen {expected}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
