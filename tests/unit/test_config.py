"""Loading [tool.actually-done] from pyproject.toml, with sensible defaults."""

from __future__ import annotations

from pathlib import Path

from actually_done.config import Config, load_config


def test_defaults_when_no_pyproject(tmp_path: Path) -> None:
    assert load_config(tmp_path) == Config()


def test_defaults_when_section_missing(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    assert load_config(tmp_path) == Config()


def test_reads_configured_values(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.actually-done]\nunit_test_path = "tests/fast"\nfast_timeout_seconds = 10\n',
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config.unit_test_path == "tests/fast"
    assert config.fast_timeout_seconds == 10
