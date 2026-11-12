import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_docs", REPO / "scripts" / "ci" / "check_docs.py")
check_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_docs)


def _repo(tmp_path, readme="# Moni\n", install="MONI_A MONI_B\n"):
    (tmp_path / "docs").mkdir()
    (tmp_path / "backend/moni").mkdir(parents=True)
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    (tmp_path / "docs/installation.md").write_text(install, encoding="utf-8")
    (tmp_path / "compose.x.yaml").write_text("- MONI_A=${MONI_A:-1}\n", encoding="utf-8")
    (tmp_path / "backend/moni/config.py").write_text('g("MONI_B", "x")\n', encoding="utf-8")
    return tmp_path


def test_clean_repo_passes(tmp_path, capsys):
    root = _repo(tmp_path, "# Moni\n[a](docs/installation.md) [b](https://example.org/x) [c](#moni)\n")
    assert check_docs.main(root) == 0


def test_broken_link_fails(tmp_path, capsys):
    root = _repo(tmp_path, "[a](docs/gibtsnicht.md)\n")
    assert check_docs.main(root) == 1
    assert "gibtsnicht.md" in capsys.readouterr().out


def test_broken_anchor_fails(tmp_path, capsys):
    root = _repo(tmp_path, "[a](docs/installation.md#nope)\n")
    assert check_docs.main(root) == 1
    assert "#nope" in capsys.readouterr().out


def test_links_in_code_are_ignored(tmp_path):
    root = _repo(tmp_path, "```\n[a](kaputt.md)\n```\n`[b](kaputt2.md)`\n")
    assert check_docs.main(root) == 0


def test_missing_variable_fails(tmp_path, capsys):
    root = _repo(tmp_path, install="MONI_A\n")
    assert check_docs.main(root) == 1
    assert "MONI_B" in capsys.readouterr().out


def test_slug_rules_match_github():
    assert check_docs.slug("Größe & Zähler (neu): `MONI_TLS`") == "größe--zähler-neu-moni_tls"
    assert check_docs.slug("Schritt 1 – Start") == "schritt-1--start"


def test_duplicate_headings_get_suffix(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# Foo\n## Foo\n```\n# nicht\n```\n", encoding="utf-8")
    assert check_docs.anchors(p) == {"foo", "foo-1"}


def test_real_repo_docs_are_consistent(capsys):
    assert check_docs.main() == 0, capsys.readouterr().out
