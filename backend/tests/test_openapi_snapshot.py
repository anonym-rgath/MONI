import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
ROLES = ["agent", "manager"]


def _generated(role: str) -> str:
    return subprocess.run([sys.executable, "-m", "moni.cli", "openapi", "--role", role], cwd=BACKEND,
                          capture_output=True, text=True, check=True, timeout=120).stdout


@pytest.mark.parametrize("role", ROLES)
def test_openapi_matches_committed(role):
    committed = (REPO / "docs" / "api" / f"openapi-{role}.json").read_text(encoding="utf-8")
    assert _generated(role) == committed, (
        f"Die API der Rolle {role} weicht von docs/api/openapi-{role}.json ab. Neu erzeugen (im Repo-Wurzelverzeichnis, "
        f"mit PYTHONPATH=backend): python -m moni.cli openapi --role {role} > docs/api/openapi-{role}.json")


@pytest.mark.parametrize("role", ROLES)
def test_openapi_has_no_example_secrets(role):
    text = (REPO / "docs" / "api" / f"openapi-{role}.json").read_text(encoding="utf-8")
    assert not re.search(r"moni_[A-Za-z0-9]{20,}", text)
