import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_version", REPO / "scripts" / "ci" / "check_version.py")
check_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_version)


def test_versions_agree(capsys):
    assert check_version.main([]) == 0, capsys.readouterr().out


def test_tag_mismatch_fails(capsys):
    assert check_version.main(["v2.0.1"]) == 1
    assert "moni/__init__.py" in capsys.readouterr().out


def test_tag_without_v_fails():
    assert check_version.main(["2.0.0"]) == 1


def test_changelog_top_section_is_version():
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    sections = re.split(r"(?m)^## \[", text)
    assert sections[1].startswith("2.0.0]")
    assert "### Breaking Changes" in sections[1] and "/api/metrics" in sections[1]


def test_compose_images_pinned_to_version():
    for name, role in (("compose.agent.yaml", "agent"), ("compose.manager.yaml", "manager")):
        text = (REPO / name).read_text(encoding="utf-8")
        assert f"ghcr.io/anonym-rgath/moni-{role}:2.0.0}}" in text, name
