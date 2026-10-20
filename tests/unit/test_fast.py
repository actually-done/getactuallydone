"""fast.py: the fast tier runs only when a source file changed, with a hash-keyed cache."""

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


def test_passes_immediately_when_nothing_changed(repo: Path) -> None:
    result = fast.run_fast_tier(repo, _config())
    assert result.status == "pass"
    assert result.summary == "no source files changed"


def test_passes_immediately_when_only_test_file_changed(repo: Path) -> None:
    (repo / "tests" / "test_pkg.py").write_text("def test_noop():\n    pass\n", encoding="utf-8")
    result = fast.run_fast_tier(repo, _config())
    assert result.status == "pass"
    assert result.summary == "no source files changed"


def test_runs_and_passes_on_source_change(repo: Path) -> None:
    (repo / "pkg.py").write_text("def add(a, b):\n    return a + b  # comment\n", encoding="utf-8")
    result = fast.run_fast_tier(repo, _config())
    assert result.status == "pass"


def test_runs_and_fails_on_broken_source(repo: Path) -> None:
    (repo / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    result = fast.run_fast_tier(repo, _config())
    assert result.status == "fail"
    assert any("test_add" in t for t in result.failing_tests)


def test_not_broken_by_an_inherited_plugin_autoload_lockdown(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller that already set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 (actually-done's
    own full-tier check does this on the sandboxed pytest run it spawns, to deny a
    PR's own conftest.py/plugins a say in its result - see full.py's _pytest_env)
    must not break this subprocess's own --testmon flag by inheriting it: autoload
    is how testmon's plugin actually gets registered, so inheriting a disabled
    autoload makes pytest reject --testmon outright (exit 4, a usage error) -
    surfacing as status "error" here instead of a real pass/fail. Caught for real
    on this project's own CI, which runs this test from inside exactly that
    sandboxed, autoload-disabled environment.
    """
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    (repo / "pkg.py").write_text("def add(a, b):\n    return a + b  # ok\n", encoding="utf-8")
    result = fast.run_fast_tier(repo, _config())
    assert result.status == "pass", result.details


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
    assert all("pytest" not in cmd for cmd in calls)  # cache hit: pytest never ran again


def test_cache_hit_diagnostics_for_a_flaky_linux_ci_failure(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Temporary: test_result_is_cached_by_working_tree_hash fails only on real
    Linux CI, never locally on Windows, and only AFTER fast.py's env/testmon-WAL
    fixes let it reach this assertion at all (D15). This duplicates that test's
    scenario but reports exactly what the real untracked-file state looked like
    on failure, since the plain `assert False` from the original gives no detail
    to go on. Delete once D15's remaining caching flake is actually diagnosed.
    """
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
