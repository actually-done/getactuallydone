"""init.py: scaffolds CI/CODEOWNERS/rules files, never clobbering existing content
unless --force; CLAUDE.md and pyproject.toml are only ever appended to.
"""

from __future__ import annotations

from pathlib import Path

from actually_done.init import run_init


def test_writes_every_expected_file(tmp_path: Path) -> None:
    report = run_init(tmp_path)

    assert (tmp_path / ".github" / "workflows" / "actually-done.yml").is_file()
    assert (tmp_path / "CODEOWNERS").is_file()
    assert (tmp_path / "tests" / "oracle" / ".gitkeep").is_file()
    assert (tmp_path / "AGENTS.md").is_file()
    assert (tmp_path / "CLAUDE.md").is_file()
    assert (tmp_path / "pyproject.toml").is_file()
    assert len(report.written) == 4
    assert len(report.appended) == 2


def test_workflow_pins_actions_by_sha(tmp_path: Path) -> None:
    run_init(tmp_path)
    text = (tmp_path / ".github" / "workflows" / "actually-done.yml").read_text(encoding="utf-8")
    assert "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in text
    assert "permissions:" in text
    assert "contents: read" in text


def test_codeowners_includes_grader_paths(tmp_path: Path) -> None:
    run_init(tmp_path)
    text = (tmp_path / "CODEOWNERS").read_text(encoding="utf-8")
    for pattern in ("conftest.py", "pytest.ini", ".verify/", ".github/", "CLAUDE.md"):
        assert pattern in text


def test_does_not_overwrite_without_force(tmp_path: Path) -> None:
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("custom rules\n", encoding="utf-8")

    report = run_init(tmp_path)

    assert agents_md.read_text(encoding="utf-8") == "custom rules\n"
    assert str(agents_md) in report.skipped


def test_force_overwrites(tmp_path: Path) -> None:
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("custom rules\n", encoding="utf-8")

    run_init(tmp_path, force=True)

    assert agents_md.read_text(encoding="utf-8") != "custom rules\n"


def test_claude_md_appends_import_when_file_exists(tmp_path: Path) -> None:
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# My project\n\nSome existing instructions.\n", encoding="utf-8")

    run_init(tmp_path)

    text = claude_md.read_text(encoding="utf-8")
    assert "Some existing instructions." in text
    assert "@AGENTS.md" in text


def test_claude_md_import_is_idempotent(tmp_path: Path) -> None:
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# My project\n\n@AGENTS.md\n", encoding="utf-8")

    report = run_init(tmp_path)

    assert claude_md.read_text(encoding="utf-8").count("@AGENTS.md") == 1
    assert str(claude_md) in report.skipped


def test_pyproject_appends_section_when_file_exists_without_it(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "demo"\n', encoding="utf-8")

    run_init(tmp_path)

    text = pyproject.read_text(encoding="utf-8")
    assert 'name = "demo"' in text
    assert "[tool.actually-done]" in text


def test_pyproject_section_is_idempotent(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "demo"\n\n[tool.actually-done]\nunit_test_path = "tests/custom"\n',
        encoding="utf-8",
    )

    report = run_init(tmp_path)

    text = pyproject.read_text(encoding="utf-8")
    assert text.count("[tool.actually-done]") == 1
    assert 'unit_test_path = "tests/custom"' in text  # untouched, not reset to the default
    assert str(pyproject) in report.skipped
