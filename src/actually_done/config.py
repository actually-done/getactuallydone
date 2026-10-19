"""Loads `[tool.actually-done]` from a project's pyproject.toml, with sensible defaults."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_UNIT_TEST_PATH = "tests/unit"
DEFAULT_FAST_TIMEOUT_SECONDS = 50


@dataclass(frozen=True)
class Config:
    unit_test_path: str = DEFAULT_UNIT_TEST_PATH
    fast_timeout_seconds: int = DEFAULT_FAST_TIMEOUT_SECONDS


def load_config(repo_root: Path) -> Config:
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return Config()
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    table = data.get("tool", {}).get("actually-done", {})
    return Config(
        unit_test_path=str(table.get("unit_test_path", DEFAULT_UNIT_TEST_PATH)),
        fast_timeout_seconds=int(table.get("fast_timeout_seconds", DEFAULT_FAST_TIMEOUT_SECONDS)),
    )
