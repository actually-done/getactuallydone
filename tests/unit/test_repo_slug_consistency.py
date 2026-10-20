"""Exactly one `actually-done/<name>` GitHub owner/repo string should appear
anywhere in the repo - this project's own home. A second, different one left
over from a rename (or a typo) would silently point someone at the wrong repo
in an install command, a trusted-publisher setting, or a doc link.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = "actually-done/actually-done"
_NON_SOURCE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".actually-done",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}

# Matches an `actually-done/<name>` slug, e.g. in a URL, an install command, or
# a trusted-publisher setting. Excludes `.actually-done/...` (the unrelated
# local state/cache directory `state.py` and `changes.py` write under) via a
# lookbehind that rejects a preceding word character or dot - that directory's
# name is always written as `.actually-done/`, with a literal dot right before
# it, never bare.
_SLUG_RE = re.compile(r"(?<![\w.])actually-done/[A-Za-z0-9_.-]+")


def _tracked_files() -> list[Path]:
    """`git ls-files` when `_REPO_ROOT` is a real git checkout; a plain filesystem
    walk otherwise - e.g. inside full.py's own sandbox, which deliberately
    excludes `.git` from the copy it runs pytest against (`_SANDBOX_EXCLUDES`),
    so this test has no git repo to ask when the full-tier check runs itself.
    """
    completed = subprocess.run(
        ["git", "ls-files"], cwd=_REPO_ROOT, capture_output=True, text=True
    )
    if completed.returncode == 0:
        return [_REPO_ROOT / line for line in completed.stdout.splitlines() if line]
    return [
        p
        for p in _REPO_ROOT.rglob("*")
        if p.is_file()
        and not any(part in _NON_SOURCE_DIRS for part in p.relative_to(_REPO_ROOT).parts)
    ]


def test_only_one_actually_done_owner_repo_string_appears_anywhere() -> None:
    found: dict[str, list[str]] = {}
    this_file = Path(__file__).resolve()
    for path in _tracked_files():
        if path.resolve() == this_file:
            continue  # this test's own pattern/docstring aren't a real reference
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in _SLUG_RE.findall(text):
            slug = match.rstrip(".,)`'\"")
            found.setdefault(slug, []).append(str(path.relative_to(_REPO_ROOT)))

    assert found, f"expected to find at least one {_EXPECTED!r} reference somewhere"
    assert set(found) == {_EXPECTED}, (
        f"found owner/repo string(s) other than {_EXPECTED!r}: "
        f"{ {slug: files for slug, files in found.items() if slug != _EXPECTED} }"
    )
