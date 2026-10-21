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


def _verify_with_python(
    repo: Path, base_sha: str, python: str
) -> tuple[full.FullResult, full.Evidence]:
    with full.materialize_base(repo, base_sha) as base_root:
        return full.run_full_tier(repo, base_root, _config(), python=python)


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
    # Bound to the file's content at approval time (full.read_approved verifies
    # this SHA-256 against base's current content - docs/DECISIONS.md, D18),
    # not just the bare id string.
    digest = full.file_hash(repo / "tests" / "test_pkg.py")
    (repo / ".verify" / "approved").write_text(
        f"tests/test_pkg.py::test_add {digest}\n", encoding="utf-8"
    )
    approved_sha = _commit(repo, "approve removal")  # base now carries the approval

    (repo / "tests" / "test_pkg.py").unlink()
    _commit(repo, "pr: delete the approved test")

    result, _evidence = _verify(repo, approved_sha)
    assert result.status == "pass"


def test_approved_edit_lets_the_prs_fixed_version_run_for_real(repo: Path) -> None:
    """A three-field approval (`approve --to <ref>`) unlocks exactly one
    pre-approved version of a locked file - the PR's content, matching the
    approved new hash exactly, actually RUNS (not base's old content, and not
    deselected either). Proves a human can approve a genuine fix to an
    existing test and have it actually verified, not just silently skipped -
    the common_tests lock normally forces base's content for any file that
    exists on both sides, which is exactly what this entry narrowly lifts.

    Base's OWN test starts genuinely broken (checks the wrong value) - the
    kind of bug an edit approval exists to let a human fix for real - so the
    result unambiguously shows which content ran: base's would fail, the
    approved fix passes. Equal tests_collected either way (both define exactly
    one test), so pass/fail is the only thing that can tell them apart.
    """
    (repo / "pkg.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[tool.actually-done]\nunit_test_path = "tests"\n', encoding="utf-8"
    )
    (repo / "tests").mkdir()
    test_file = repo / "tests" / "test_pkg.py"
    test_file.write_text(
        "from pkg import add\n\ndef test_add():\n    assert add(1, 2) == 999\n",
        encoding="utf-8",
    )
    _commit(repo, "base (test itself has a bug: checks the wrong value)")
    old_hash = full.file_hash(test_file)

    fixed_content = "from pkg import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    # Hash the file AFTER writing it, not the raw string - write_text applies
    # platform newline translation (CRLF on Windows), so hashing the string
    # directly would record a different hash than what's actually on disk.
    test_file.write_text(fixed_content, encoding="utf-8")
    new_hash = full.file_hash(test_file)
    _git(repo, "checkout", "--", "tests/test_pkg.py")  # restore base's original for now

    (repo / ".verify").mkdir()
    (repo / ".verify" / "approved").write_text(
        f"tests/test_pkg.py::test_add {old_hash} {new_hash}\n", encoding="utf-8"
    )
    approved_sha = _commit(repo, "approve the fix to tests/test_pkg.py::test_add")

    test_file.write_text(fixed_content, encoding="utf-8")
    _commit(repo, "pr: apply the approved fix")

    result, evidence = _verify(repo, approved_sha)
    assert result.status == "pass"  # base's still-broken content would fail (999 != 3)
    assert evidence.tests_collected == 1


def test_approved_edit_fails_if_the_pr_changes_it_again(repo: Path) -> None:
    """Attack: approve an edit, then the PR changes the test AGAIN to something
    nobody signed off on (here, a no-op `assert True` that would hide the same
    bug an edited assertion would) - must fail, not silently fall back to
    base's content as if the mismatched approval entry weren't there at all.
    """
    _write_base(repo)
    old_hash = full.file_hash(repo / "tests" / "test_pkg.py")

    approved_content = (
        "from pkg import add\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n    assert add(2, 2) == 4\n"
    )
    test_file = repo / "tests" / "test_pkg.py"
    test_file.write_text(approved_content, encoding="utf-8")
    approved_new_hash = full.file_hash(test_file)
    _git(repo, "checkout", "--", "tests/test_pkg.py")  # restore base's original for now

    (repo / ".verify").mkdir()
    (repo / ".verify" / "approved").write_text(
        f"tests/test_pkg.py::test_add {old_hash} {approved_new_hash}\n", encoding="utf-8"
    )
    approved_sha = _commit(repo, "approve an edit to tests/test_pkg.py::test_add")

    (repo / "tests" / "test_pkg.py").write_text(
        "def test_add():\n    assert True\n", encoding="utf-8"
    )
    _commit(repo, "pr: weaken the approved test instead of applying the real fix")

    result, _evidence = _verify(repo, approved_sha)
    assert result.status == "fail"
    assert "approved-edit" in result.summary
    assert "tests/test_pkg.py::test_add" in result.details


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


def test_failing_test_evidence_survives_output_truncation(repo: Path) -> None:
    """full.py's own evidence only keeps the raw pytest output's last 80 lines
    (_tail) - a failure's traceback can get pushed out entirely by other output.
    The per-test failing_tests list, parsed straight from the JUnit report, is
    rendered unconditionally instead, so the test id and a real summary of what
    broke survive regardless of how much other output precedes them.
    """
    base_sha = _write_base(repo)
    (repo / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    _commit(repo, "pr: hidden break")

    result, evidence = _verify(repo, base_sha)
    assert result.status == "fail"
    assert evidence.failing_tests
    test_id, first_line = evidence.failing_tests[0]
    assert "test_add" in test_id
    assert first_line  # a real, non-empty summary of what broke, not a blank line

    markdown = full.render_evidence_markdown(evidence, result)
    assert "**Failing tests:**" in markdown
    assert test_id in markdown
    assert first_line in markdown


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


def test_zero_tests_collected_fails_instead_of_passing_vacuously(repo: Path) -> None:
    """A misconfigured/wrong unit_test_path (or a PR that repoints its own config to
    one that finds nothing on base either) must not look like "all good" just
    because nothing failed - nothing ran at all, so this has to fail, not pass.
    Distinct from an approved removal (test_approved_removal_is_excluded_from_the_count),
    where baseline_count is positive before the approval brings it down to zero.
    """
    (repo / "pkg.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[tool.actually-done]\nunit_test_path = "tests"\n', encoding="utf-8"
    )
    (repo / "tests").mkdir()  # exists, but has no test_*.py files in it at all
    base_sha = _commit(repo, "base: no tests")
    (repo / "pkg.py").write_text(
        "def add(a, b):\n    return a + b  # harmless comment\n", encoding="utf-8"
    )
    _commit(repo, "pr: still no tests")

    result, evidence = _verify(repo, base_sha)
    assert result.status == "fail"
    assert evidence.tests_collected == 0


def test_runs_against_base_tests_even_when_base_has_no_pyproject_toml(repo: Path) -> None:
    """`run_full_tier` takes an already-resolved Config - it never reads base's
    pyproject.toml itself, so base lacking one entirely (e.g. the very first PR
    that introduces this project, or any project that just doesn't have one) must
    not change the result: base's real tests still get found and run for real.
    """
    (repo / "pkg.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    # Deliberately no pyproject.toml committed on base at all.
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pkg.py").write_text(
        "from pkg import add\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8"
    )
    base_sha = _commit(repo, "base: no pyproject.toml, but real tests")
    (repo / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    _commit(repo, "pr: hidden break")

    result, evidence = _verify(repo, base_sha)
    assert result.status == "fail"
    assert evidence.tests_collected == 1
    assert "test_add" in result.details


def test_run_full_tier_actually_uses_the_given_python_not_sys_executable(repo: Path) -> None:
    """D20's grader-isolation design needs run_full_tier to execute the PR's test
    suite with a DIFFERENT interpreter than the grader's own (sys.executable) -
    proven here two ways with a real venv that deliberately has no pytest
    installed: (1) every subprocess command actually run names that venv's own
    interpreter, not sys.executable; (2) because that interpreter genuinely has
    no pytest, nothing gets collected on either side, which - correctly, per the
    zero-tests guard above - fails the check rather than silently passing. A
    silent fallback to sys.executable (which does have pytest) would show
    sys.executable's own path in the commands and pass normally instead.
    """
    import sys
    import venv

    base_sha = _write_base(repo)
    (repo / "README.md").write_text("harmless\n", encoding="utf-8")
    _commit(repo, "pr: unrelated change")

    # Created after the commits, outside the repo's tracked tree entirely - it
    # must never influence changes-detection or get swept into the sandbox.
    venv_dir = repo.parent / "pytestless-venv"
    venv.create(venv_dir, with_pip=False)
    bin_dir = "Scripts" if sys.platform == "win32" else "bin"
    exe = "python.exe" if sys.platform == "win32" else "python"
    other_python = venv_dir / bin_dir / exe
    assert other_python.is_file(), "venv creation didn't produce the expected interpreter"

    result, evidence = _verify_with_python(repo, base_sha, str(other_python))

    assert evidence.commands, "expected at least one pytest subprocess to have been attempted"
    for command, _returncode in evidence.commands:
        assert command.startswith(str(other_python)), (
            f"command used sys.executable or something else, not the given python: {command!r}"
        )
        assert sys.executable not in command

    assert result.status == "fail"
    assert "zero tests" in result.summary


def test_approve_cli_round_trip(tmp_path: Path) -> None:
    # Each entry is bound to a SHA-256 of the file it names, not just a bare id
    # (docs/DECISIONS.md, D18) - read_approved needs real files to hash against.
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    (tmp_path / "tests" / "test_b.py").write_text("def test_y():\n    pass\n", encoding="utf-8")
    digest_a = full.file_hash(tmp_path / "tests" / "test_a.py")
    digest_b = full.file_hash(tmp_path / "tests" / "test_b.py")

    (tmp_path / ".verify").mkdir()
    assert full.read_approved(tmp_path, tmp_path) == set()
    (tmp_path / ".verify" / "approved").write_text(
        f"tests/test_a.py::test_x {digest_a}\n# a comment\n\ntests/test_b.py::test_y {digest_b}\n",
        encoding="utf-8",
    )
    assert full.read_approved(tmp_path, tmp_path) == {
        "tests/test_a.py::test_x",
        "tests/test_b.py::test_y",
    }


def test_read_approved_ignores_a_bare_id_with_no_hash(tmp_path: Path) -> None:
    """A legacy/malformed entry (no recorded hash) is not honored at all - fails
    closed, exactly as if that id had never been approved, rather than treating
    an unparseable line as a blanket approval. read_approval_errors surfaces it
    separately, so run_full_tier can fail loudly instead of staying silent.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    (tmp_path / ".verify").mkdir()
    (tmp_path / ".verify" / "approved").write_text("tests/test_a.py::test_x\n", encoding="utf-8")
    assert full.read_approved(tmp_path, tmp_path) == set()
    assert full.read_approval_errors(tmp_path, tmp_path) == ["tests/test_a.py::test_x"]


def test_read_approved_voids_a_stale_hash_when_file_content_changes(tmp_path: Path) -> None:
    """The exact attack this binding closes: an approval recorded for one
    version of a file must not keep covering a later, different version of the
    same file (see tests/attacks/test_attack_08_stale_test_approval.py for the
    full end-to-end scenario against run_full_tier).
    """
    (tmp_path / "tests").mkdir()
    test_file = tmp_path / "tests" / "test_a.py"
    test_file.write_text("def test_x():\n    pass\n", encoding="utf-8")
    digest = full.file_hash(test_file)

    (tmp_path / ".verify").mkdir()
    (tmp_path / ".verify" / "approved").write_text(
        f"tests/test_a.py::test_x {digest}\n", encoding="utf-8"
    )
    assert full.read_approved(tmp_path, tmp_path) == {"tests/test_a.py::test_x"}

    test_file.write_text("def test_x():\n    assert False\n", encoding="utf-8")
    assert full.read_approved(tmp_path, tmp_path) == set()


def test_legacy_bare_id_approval_fails_the_check_loudly(repo: Path) -> None:
    """A malformed/legacy-format .verify/approved entry (pre-D18: a bare id,
    no recorded hash) must not be silently dropped - run_full_tier fails loudly
    instead, so a human finds out their approval isn't in effect right away,
    not later when an unrelated count-drop or a since-fixed test surprises them.
    """
    _write_base(repo)
    (repo / ".verify").mkdir()
    (repo / ".verify" / "approved").write_text("tests/test_pkg.py::test_add\n", encoding="utf-8")
    base_sha = _commit(repo, "base: legacy bare-id approval entry")
    (repo / "README.md").write_text("harmless\n", encoding="utf-8")
    _commit(repo, "pr: unrelated change")

    result, _evidence = _verify(repo, base_sha)
    assert result.status == "error"
    assert "legacy approval format" in result.summary
    assert "tests/test_pkg.py::test_add" in result.details
