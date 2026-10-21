"""A duplicate of test_fast.py's own caching test, kept in its own file on purpose:
full.py's sandbox replaces any test FILE that already exists on base with base's own
content, so once this merges, test_fast.py's version is permanently frozen to
whatever's on base and a future regression there wouldn't actually be re-checked by
this project's own full-tier CI. This one, in a file with no base counterpart yet,
keeps getting the PR's own content and so is the one that actually re-verifies this
behavior on every real CI run going forward.

Found via a real CI-only cache-miss flake that never reproduced locally on Windows
(docs/DECISIONS.md, D15): `__pycache__/` directories, created as a side effect of
importing the fixture's own code, were untracked and unexcluded, busting the cache
on every run. A local/global `.gitignore` hid this on a dev machine - git never
reports an already-ignored path as untracked - while a clean CI checkout saw it for
real. Fixed in changes.py's `_is_tool_owned`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from actually_done import fast
from actually_done.config import Config


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "pkg.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pkg.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom pkg import add\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    return tmp_path


def _config() -> Config:
    return Config(unit_test_path="tests", fast_timeout_seconds=30)


def test_result_is_cached_by_working_tree_hash(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    first = fast.run_fast_tier(repo, _config())
    assert first.status == "fail"

    real_run = subprocess.run
    calls: list[list[str]] = []

    def spy(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return real_run(cmd, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", spy)
    second = fast.run_fast_tier(repo, _config())

    assert second.status == "fail"
    assert second.failing_tests == first.failing_tests
    assert all("pytest" not in cmd for cmd in calls), (
        f"cache missed - pytest ran again. calls: {calls!r}"
    )
