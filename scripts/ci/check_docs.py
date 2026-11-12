#!/usr/bin/env python3
"""Dokumentationsprüfung: check_docs.py

Exit 0, wenn
  (a) jeder relative Link in README.md, SECURITY.md, CONTRIBUTING.md und docs/*.md auf eine vorhandene
      Datei zeigt und ein Anker (#überschrift) auf eine Überschrift der Zielseite, und
  (b) jede Variable MONI_*, die in compose.*.yaml oder backend/moni/config.py vorkommt, in
      docs/installation.md steht.
Sonst Exit 1 mit der Liste der Fehler. Code-Blöcke und Inline-Code werden nicht auf Links geprüft.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_FENCE = re.compile(r"^\s*(```|~~~)")
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_VAR = re.compile(r"\bMONI_[A-Z0-9_]*[A-Z0-9]")


def _prose(text: str) -> list[str]:
    """Zeilen außerhalb von Code-Blöcken, Inline-Code entfernt."""
    out, fenced = [], False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            out.append(re.sub(r"`[^`]*`", "", line))
    return out


def slug(heading: str) -> str:
    """GitHub-Anker: klein, Satzzeichen weg (Umlaute, Ziffern, _ und - bleiben), Leerzeichen zu -."""
    h = re.sub(r"`([^`]*)`", r"\1", heading.strip().lower())
    return re.sub(r"[^\w\- ]", "", h).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    out = set()
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        m = None if fenced else re.match(r"#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            s = slug(m.group(1))
            n = seen.get(s, 0)
            seen[s] = n + 1
            out.add(s if n == 0 else f"{s}-{n}")
    return out


def doc_files(root: Path) -> list[Path]:
    names = [root / n for n in ("README.md", "SECURITY.md", "CONTRIBUTING.md")]
    return [p for p in names + sorted((root / "docs").glob("*.md")) if p.exists()]


def check_links(root: Path) -> list[str]:
    errors = []
    for f in doc_files(root):
        rel = f.relative_to(root)
        for line in _prose(f.read_text(encoding="utf-8")):
            for target in _LINK.findall(line):
                if re.match(r"[a-z][a-z0-9+.-]*:", target, re.I):  # http:, https:, mailto: ...
                    continue
                path, _, anchor = target.partition("#")
                dest = (f.parent / path).resolve() if path else f
                if not dest.exists():
                    errors.append(f"{rel}: Link {target} zeigt auf keine vorhandene Datei")
                elif anchor and dest.suffix == ".md" and anchor not in anchors(dest):
                    errors.append(f"{rel}: Link {target}: Überschrift #{anchor} fehlt")
    return errors


def check_vars(root: Path) -> list[str]:
    found = set()
    for f in [*root.glob("compose.*.yaml"), root / "backend/moni/config.py"]:
        found |= set(_VAR.findall(f.read_text(encoding="utf-8")))
    doc = (root / "docs/installation.md").read_text(encoding="utf-8")
    return [f"docs/installation.md: Variable {v} fehlt" for v in sorted(found) if v not in set(_VAR.findall(doc))]


def main(root: Path = ROOT) -> int:
    errors = check_links(root) + check_vars(root)
    for e in errors:
        print(e)
    if not errors:
        print("OK Dokumentation")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
