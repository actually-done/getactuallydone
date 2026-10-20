"""Git-diff inspection: which files changed in the working tree, and what kind they are.

Everything here reads `git status --porcelain=v1 -z` against the working tree (uncommitted
changes plus untracked files). Paths this tool itself creates (`.testmondata` and its SQLite
WAL-mode sidecar files, the `.actually-done/` cache dir) are filtered out unconditionally,
independent of whatever the target repo's `.gitignore` says — otherwise our own state would
make the working tree look "changed" on every subsequent run and defeat the hash-based cache
in state.py.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

# pytest-testmon's db.py opens its SQLite file with `PRAGMA journal_mode = WAL`, which can
# leave `-wal`/`-shm` (and, pre-WAL, `-journal`) sidecar files next to `.testmondata` - an
# exact-name match alone missed these, so a prefix match covers the whole family.
_TOOL_OWNED_FILE_PREFIXES = (".testmondata",)
_TOOL_OWNED_DIR_PREFIX = ".actually-done/"


@dataclass(frozen=True)
class ChangedFile:
    status: str  # a two-character git porcelain status code, e.g. " M", "??", "A "
    path: str


def changed_files(repo_root: Path) -> list[ChangedFile]:
    """Files that differ from HEAD, or are untracked, in the working tree."""
    raw = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    ).stdout
    return [f for f in _parse_porcelain_z(raw) if not _is_tool_owned(f.path)]


def _is_tool_owned(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized.startswith(_TOOL_OWNED_DIR_PREFIX):
        return True
    return any(normalized.startswith(prefix) for prefix in _TOOL_OWNED_FILE_PREFIXES)


def _parse_porcelain_z(raw: bytes) -> list[ChangedFile]:
    parts = raw.split(b"\0")
    files: list[ChangedFile] = []
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if not entry:
            continue
        status = entry[:2].decode()
        path = entry[3:].decode()
        files.append(ChangedFile(status=status, path=path))
        if status[0] == "R" or status[1] == "R":
            # A rename carries a second, null-separated "from" path we don't need.
            i += 1
    return files


def is_test_file(path: str, test_path_prefix: str) -> bool:
    normalized = path.replace("\\", "/")
    prefix = test_path_prefix.replace("\\", "/").rstrip("/")
    if normalized.startswith(f"{prefix}/"):
        return True
    name = normalized.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py")


def is_source_file(path: str, test_path_prefix: str) -> bool:
    normalized = path.replace("\\", "/")
    if not normalized.endswith(".py"):
        return False
    return not is_test_file(normalized, test_path_prefix)


def any_source_changed(files: list[ChangedFile], test_path_prefix: str) -> bool:
    return any(is_source_file(f.path, test_path_prefix) for f in files)


def modified_or_removed_test_files(files: list[ChangedFile], test_path_prefix: str) -> list[str]:
    """Existing test files that were edited or deleted (not newly added ones)."""
    result = []
    for f in files:
        if not is_test_file(f.path, test_path_prefix):
            continue
        if "M" in f.status or "D" in f.status:
            result.append(f.path)
    return result


def working_tree_hash(repo_root: Path) -> str:
    """A hash that's stable iff the working tree's changed-vs-HEAD content is unchanged."""
    digest = hashlib.sha256()
    for f in sorted(changed_files(repo_root), key=lambda c: c.path):
        digest.update(f.status.encode())
        digest.update(b"\0")
        digest.update(f.path.encode())
        digest.update(b"\0")
        full_path = repo_root / f.path
        if full_path.is_file():
            digest.update(full_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
