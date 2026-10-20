"""Shared fixture-repo helpers for the attack suite.

Each attack test imports `git`, `commit`, and `write_base_project` directly from
this module - pytest's default ("prepend") import mode puts this file's directory
on `sys.path`, so a bare `from conftest import ...` resolves within `tests/attacks/`
without needing `__init__.py` files. The `repo` fixture is a bare, empty,
git-initialized temp directory; `write_base_project` turns it into a minimal real
project with one real passing test, which is what every attack starts from.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def write_base_project(repo: Path) -> str:
    """A minimal pkg.py + one real passing test + actually-done config. Returns the
    base commit's SHA, for use as `actually-done verify --tier full --base <sha>`.
    """
    (repo / "pkg.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[tool.actually-done]\nunit_test_path = "tests"\n', encoding="utf-8"
    )
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pkg.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom pkg import add\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    return commit(repo, "base: minimal project")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "attacker@example.com")
    git(tmp_path, "config", "user.name", "Agent")
    return tmp_path
