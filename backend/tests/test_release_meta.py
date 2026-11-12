import importlib.util
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_version", REPO / "scripts" / "ci" / "check_version.py")
check_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_version)


def _copy_sources(tmp_path, monkeypatch):
    for rel in ("backend/moni/__init__.py", "frontend/package.json", "frontend/package-lock.json", "CHANGELOG.md", "compose.agent.yaml", "compose.manager.yaml"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text((REPO / rel).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(check_version, "ROOT", tmp_path)
    return tmp_path / "CHANGELOG.md"


def test_unreleased_changelog_fails_with_tag_only(tmp_path, monkeypatch, capsys):
    changelog = _copy_sources(tmp_path, monkeypatch)
    text = changelog.read_text(encoding="utf-8")
    changelog.write_text(re.sub(r"(?m)^(## \[2\.0\.0\] – ).*$", r"\1unveröffentlicht", text), encoding="utf-8")
    assert check_version.main([]) == 0
    assert check_version.main(["v2.0.0"]) == 1
    assert "unveröffentlicht" in capsys.readouterr().out
    changelog.write_text(re.sub(r"(?m)^(## \[2\.0\.0\] – ).*$", r"\g<1>2026-12-01", text), encoding="utf-8")
    assert check_version.main(["v2.0.0"]) == 0


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


def test_package_lock_version_mismatch_fails(tmp_path, monkeypatch, capsys):
    _copy_sources(tmp_path, monkeypatch)
    lock = tmp_path / "frontend/package-lock.json"
    data = json.loads(lock.read_text(encoding="utf-8"))
    data["packages"][""]["version"] = "1.9.9"
    lock.write_text(json.dumps(data), encoding="utf-8")
    assert check_version.main([]) == 1
    assert "package-lock.json" in capsys.readouterr().out
