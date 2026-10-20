"""full.py: the decisive CI check. Base's test files and config decide what has to
pass - these tests exercise it against the exact attacks it exists to catch.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from actually_done import full
from actually_done.config import Config


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    return tmp_path


def _write_base(repo: Path) -> str:
    (repo / "pkg.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[tool.actually-done]\nunit_test_path = "tests"\n', encoding="utf-8"
    )
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pkg.py").write_text(
        "from pkg import add\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8"
    )
    return _commit(repo, "base")


def _config() -> Config:
    return Config(unit_test_path="tests", fast_timeout_seconds=30)


def _verify(repo: Path, base_sha: str) -> tuple[full.FullResult, full.Evidence]:
    with full.materialize_base(repo, base_sha) as base_root:
        return full.run_full_tier(repo, base_root, _config())


def test_passes_when_pr_only_changes_production_code_correctly(repo: Path) -> None:
    base_sha = _write_base(repo)
    (repo / "pkg.py").write_text(
        "def add(a, b):\n    return a + b  # refactored\n", encoding="utf-8"
    )
    _commit(repo, "pr: harmless refactor")

    result, _evidence = _verify(repo, base_sha)
    assert result.status == "pass"


def test_catches_edited_assertion(repo: Path) -> None:
    """Attack 1: the agent edits an assertion to match its own buggy output."""
    base_sha = _write_base(repo)
    (repo / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "tests" / "test_pkg.py").write_text(
        "from pkg import add\n\ndef test_add():\n    assert add(1, 2) == -1\n", encoding="utf-8"
    )
    _commit(repo, "pr: break + hide")

    result, _evidence = _verify(repo, base_sha)
    assert result.status == "fail"
    assert "test_add" in result.details


def test_catches_skip_marker_added_to_existing_test(repo: Path) -> None:
    """Attack 2 (existing file): base's content wins, so the skip never takes effect -
    the underlying (still-broken) assertion gets to run for real."""
    base_sha = _write_base(repo)
    (repo / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "tests" / "test_pkg.py").write_text(
        "import pytest\nfrom pkg import add\n\n"
        "@pytest.mark.skip(reason='flaky')\n"
        "def test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    _commit(repo, "pr: break + skip")

    result, _evidence = _verify(repo, base_sha)
    assert result.status == "fail"


def test_catches_skip_marker_on_new_test_file(repo: Path) -> None:
    """Attack 2 (new file): a brand-new test can't be marked skip/xfail either."""
    base_sha = _write_base(repo)
    (repo / "tests" / "test_new.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.skip(reason='later')\n"
        "def test_future():\n"
        "    assert False\n",
        encoding="utf-8",
    )
    _commit(repo, "pr: add skipped test")

    result, _evidence = _verify(repo, base_sha)
    assert result.status == "fail"
    assert "skip" in result.summary.lower()


def test_catches_deleted_test_as_a_count_drop(repo: Path) -> None:
    """Attack 3: deleting a failing test drops the count unless approved."""
    base_sha = _write_base(repo)
    (repo / "tests" / "test_pkg.py").unlink()
    _commit(repo, "pr: delete the test")

    result, _evidence = _verify(repo, base_sha)
    assert result.status == "fail"
    assert "count dropped" in result.summary


def test_approved_removal_is_excluded_from_the_count(repo: Path) -> None:
    _write_base(repo)
    (repo / ".verify").mkdir()
    (repo / ".verify" / "approved").write_text("tests/test_pkg.py::test_add\n", encoding="utf-8")
    approved_sha = _commit(repo, "approve removal")  # base now carries the approval

    (repo / "tests" / "test_pkg.py").unlink()
    _commit(repo, "pr: delete the approved test")

    result, _evidence = _verify(repo, approved_sha)
    assert result.status == "pass"


def test_catches_new_conftest_that_forces_passes(repo: Path) -> None:
    """Attack 4: a new conftest.py that marks every failure as passed is denied
    outright, so the real (still-broken) result stands."""
    base_sha = _write_base(repo)
    (repo / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "conftest.py").write_text(
        "import pytest\n\n"
        "@pytest.hookimpl(hookwrapper=True)\n"
        "def pytest_runtest_makereport(item, call):\n"
        "    outcome = yield\n"
        "    rep = outcome.get_result()\n"
        "    if rep.when == 'call':\n"
        "        rep.outcome = 'passed'\n",
        encoding="utf-8",
    )
    _commit(repo, "pr: break + cheat conftest")

    result, _evidence = _verify(repo, base_sha)
    assert result.status == "fail"


def test_new_legitimate_test_must_pass(repo: Path) -> None:
    base_sha = _write_base(repo)
    (repo / "tests" / "test_new.py").write_text(
        "from pkg import add\n\ndef test_add_zero():\n    assert add(5, 0) == 5\n",
        encoding="utf-8",
    )
    _commit(repo, "pr: add a real new test")

    result, evidence = _verify(repo, base_sha)
    assert result.status == "pass"
    assert evidence.new_test_files == 1


def test_new_failing_test_fails_the_check(repo: Path) -> None:
    base_sha = _write_base(repo)
    (repo / "tests" / "test_new.py").write_text(
        "def test_wrong():\n    assert 1 == 2\n", encoding="utf-8"
    )
    _commit(repo, "pr: add a failing new test")

    result, _evidence = _verify(repo, base_sha)
    assert result.status == "fail"


def test_oracle_test_must_really_pass_not_just_be_collected(repo: Path) -> None:
    """A still-unimplemented boundary case stays a real failure under --runxfail,
    not a silently-green xfail - so forgetting it fails the check even though
    every unit test passes."""
    base_sha = _write_base(repo)
    oracle_dir = repo / "tests" / "oracle"
    oracle_dir.mkdir(parents=True)
    (oracle_dir / "test_boundary.py").write_text(
        "import pytest\nfrom pkg import add\n\n"
        "@pytest.mark.xfail(reason='not implemented yet')\n"
        "def test_add_handles_none_as_zero():\n"
        "    assert add(None, 5) == 5\n",
        encoding="utf-8",
    )
    base_sha = _commit(repo, "add oracle test (as an expected failure)")

    # The PR doesn't touch pkg.py at all - the oracle gap is still open.
    (repo / "README.md").write_text("docs\n", encoding="utf-8")
    _commit(repo, "pr: unrelated change, oracle case still unimplemented")

    result, _evidence = _verify(repo, base_sha)
    assert result.status == "fail"
    assert "oracle" in result.summary.lower()


def test_oracle_passes_once_genuinely_implemented(repo: Path) -> None:
    base_sha = _write_base(repo)
    oracle_dir = repo / "tests" / "oracle"
    oracle_dir.mkdir(parents=True)
    (oracle_dir / "test_boundary.py").write_text(
        "import pytest\nfrom pkg import add\n\n"
        "@pytest.mark.xfail(reason='not implemented yet')\n"
        "def test_add_handles_none_as_zero():\n"
        "    assert add(None, 5) == 5\n",
        encoding="utf-8",
    )
    base_sha = _commit(repo, "add oracle test (as an expected failure)")

    (repo / "pkg.py").write_text(
        "def add(a, b):\n    return (a or 0) + (b or 0)\n", encoding="utf-8"
    )
    _commit(repo, "pr: genuinely implement the None-handling")

    result, _evidence = _verify(repo, base_sha)
    assert result.status == "pass"


def test_catches_narrow_special_case_against_a_second_oracle_input(repo: Path) -> None:
    """Attack 5: the agent special-cases the exact fixture input from one oracle
    test instead of generalizing - a second boundary oracle test (solution.md
    recommends 2-3, precisely so one narrow hardcode isn't enough) still fails.
    """
    base_sha = _write_base(repo)
    oracle_dir = repo / "tests" / "oracle"
    oracle_dir.mkdir(parents=True)
    (oracle_dir / "test_boundary.py").write_text(
        "import pytest\nfrom pkg import add\n\n"
        "@pytest.mark.xfail(reason='not implemented yet')\n"
        "def test_add_handles_none_as_zero():\n"
        "    assert add(None, 5) == 5\n\n"
        "@pytest.mark.xfail(reason='not implemented yet')\n"
        "def test_add_handles_both_none():\n"
        "    assert add(None, None) == 0\n",
        encoding="utf-8",
    )
    base_sha = _commit(repo, "add two oracle tests")

    # Hardcodes only the first oracle's exact inputs; doesn't generalize.
    (repo / "pkg.py").write_text(
        "def add(a, b):\n"
        "    if a is None and b == 5:\n"
        "        return 5\n"
        "    return a + b\n",
        encoding="utf-8",
    )
    _commit(repo, "pr: special-case one oracle input instead of generalizing")

    result, _evidence = _verify(repo, base_sha)
    assert result.status == "fail"
    assert "oracle" in result.summary.lower()


def test_approve_cli_round_trip(tmp_path: Path) -> None:
    (tmp_path / ".verify").mkdir()
    assert full.read_approved(tmp_path) == set()
    (tmp_path / ".verify" / "approved").write_text(
        "tests/test_a.py::test_x\n# a comment\n\ntests/test_b.py::test_y\n", encoding="utf-8"
    )
    assert full.read_approved(tmp_path) == {"tests/test_a.py::test_x", "tests/test_b.py::test_y"}
