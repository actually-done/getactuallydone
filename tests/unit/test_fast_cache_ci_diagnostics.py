"""Temporary diagnostic, in its own new file on purpose: full.py's sandbox replaces
any test FILE that already exists on base with base's own content, so a new test
added inside an existing file (e.g. test_fast.py) is invisible to the real
full-tier CI run - only a genuinely new file path gets the PR's own content there.

test_fast.py::test_result_is_cached_by_working_tree_hash fails on real Linux CI
(never locally on Windows) only once fast.py's env-inheritance fix and changes.py's
testmon-WAL-sidecar fix (docs/DECISIONS.md, D15) let it reach its final assertion at
all - previously masked by an earlier crash in the same test. The plain `assert
False` it produces gives no detail to root-cause further from here. This duplicates
that test's exact scenario and reports the raw `git status --porcelain`, every
non-.git file on disk, and the exact subprocess calls made, if a cache miss happens
again - meant to be deleted once the real CI run shows what untracked file is
actually leaking through.
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


def test_cache_hit_diagnostics_for_a_flaky_linux_ci_failure(
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

    if any("pytest" in cmd for cmd in calls):
        status_raw = subprocess.run(
            ["git", "status", "--porcelain=v1"], cwd=repo, capture_output=True, text=True
        ).stdout
        all_entries = sorted(
            str(p.relative_to(repo)) for p in repo.rglob("*") if ".git" not in p.parts
        )
        pytest.fail(
            "cache missed on the second call - raw `git status --porcelain=v1`:\n"
            f"{status_raw!r}\nall non-.git entries on disk:\n{all_entries!r}\n"
            f"subprocess calls made during the second run:\n{calls!r}"
        )
