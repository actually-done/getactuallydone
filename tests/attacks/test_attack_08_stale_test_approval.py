"""Attack 8: an approval recorded for one version of a test file silently covers
a LATER, different version of that same file once base itself moves on past it -
unless the approval is bound to the file's content, not just the bare id string.

Caught by: full.py's read_approved verifies a SHA-256 of the file's CURRENT
content on base against the hash recorded at approval time (cli.py's
_cmd_approve). A base file that no longer matches that hash voids its approval
entries - the test runs for real again, exactly as if it had never been
approved, so a now-broken version of it keeps failing the check.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import commit, write_base_project

from actually_done import cli, full


def test_full_tier_fails_when_an_approved_test_file_is_later_edited(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    write_base_project(repo)

    digest = full.file_hash(repo / "tests" / "test_pkg.py")
    (repo / ".verify").mkdir()
    (repo / ".verify" / "approved").write_text(
        f"tests/test_pkg.py::test_add {digest}\n", encoding="utf-8"
    )
    commit(repo, "approve tests/test_pkg.py::test_add for a known, now-fixed flake")

    # Base moves on: a later, unrelated merge edits the SAME test in a way that
    # breaks it - the approval, recorded for the OLD content, is still sitting
    # in .verify/approved untouched, now describing a test that no longer exists.
    (repo / "tests" / "test_pkg.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom pkg import add\n\n"
        "def test_add():\n    assert add(1, 2) == 999\n",
        encoding="utf-8",
    )
    later_base_sha = commit(repo, "base: test_pkg.py edited (now broken) after approval")

    # A PR based on this later state - doesn't touch the test file at all.
    (repo / "README.md").write_text("unrelated change\n", encoding="utf-8")
    commit(repo, "pr: unrelated change")

    monkeypatch.chdir(repo)
    exit_code = cli.main(["verify", "--tier", "full", "--base", later_base_sha])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "**fail**" in out
    assert "test_add" in out
