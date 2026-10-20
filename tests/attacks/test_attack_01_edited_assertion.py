"""Attack 1: the agent breaks production code, then edits the test's assertion to
match its own buggy output, so the test it wrote itself still passes.

Caught by: `full.py`'s sandbox always uses base's content for any test file that
exists in both trees (solution.md §3, "editing an assertion changes nothing"), so
the PR's edited assertion is discarded before pytest ever runs - the original,
still-correct assertion runs against the broken code and fails for real.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import commit, write_base_project

from actually_done import cli


def test_full_tier_fails_on_edited_assertion(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    base_sha = write_base_project(repo)

    (repo / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "tests" / "test_pkg.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom pkg import add\n\n"
        "def test_add():\n    assert add(1, 2) == -1\n",  # edited to match the bug
        encoding="utf-8",
    )
    commit(repo, "pr: break add(), edit the test to match")

    monkeypatch.chdir(repo)
    exit_code = cli.main(["verify", "--tier", "full", "--base", base_sha])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "**fail**" in out
    assert "test_add" in out
