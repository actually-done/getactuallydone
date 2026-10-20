"""changes.py: git-status parsing, source/test classification, and the working-tree hash."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from actually_done import changes


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_src.py").write_text("def test_x(): pass\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    return tmp_path


def test_is_test_file_by_prefix() -> None:
    assert changes.is_test_file("tests/unit/test_foo.py", "tests/unit")


def test_is_test_file_by_name_anywhere() -> None:
    assert changes.is_test_file("src/foo_test.py", "tests/unit")
    assert changes.is_test_file("src/test_foo.py", "tests/unit")


def test_is_source_file_excludes_tests_and_non_python() -> None:
    assert changes.is_source_file("src/foo.py", "tests/unit")
    assert not changes.is_source_file("tests/unit/test_foo.py", "tests/unit")
    assert not changes.is_source_file("README.md", "tests/unit")


def test_changed_files_detects_modification(repo: Path) -> None:
    (repo / "src.py").write_text("x = 2\n", encoding="utf-8")
    files = changes.changed_files(repo)
    assert any(f.path == "src.py" and "M" in f.status for f in files)


def test_changed_files_detects_untracked(repo: Path) -> None:
    (repo / "new_source.py").write_text("y = 1\n", encoding="utf-8")
    files = changes.changed_files(repo)
    assert any(f.path == "new_source.py" for f in files)


def test_changed_files_hides_tool_owned_paths(repo: Path) -> None:
    (repo / ".testmondata").write_text("fake", encoding="utf-8")
    (repo / ".actually-done").mkdir()
    (repo / ".actually-done" / "state.json").write_text("{}", encoding="utf-8")
    files = changes.changed_files(repo)
    assert files == []


def test_changed_files_hides_testmon_wal_sidecar_files(repo: Path) -> None:
    """pytest-testmon's db.py opens .testmondata with `PRAGMA journal_mode = WAL`,
    which can leave `-wal`/`-shm` sidecar files on disk next to it - an exact-name
    match alone missed these, letting them make the working tree look "changed" on
    every subsequent fast-tier run and defeat its cache (found via a real CI
    failure: test_fast.py::test_result_is_cached_by_working_tree_hash).
    """
    (repo / ".testmondata-wal").write_text("fake", encoding="utf-8")
    (repo / ".testmondata-shm").write_text("fake", encoding="utf-8")
    files = changes.changed_files(repo)
    assert files == []


def test_any_source_changed_true_for_py_edit(repo: Path) -> None:
    (repo / "src.py").write_text("x = 2\n", encoding="utf-8")
    files = changes.changed_files(repo)
    assert changes.any_source_changed(files, "tests")


def test_any_source_changed_false_for_test_only_edit(repo: Path) -> None:
    (repo / "tests" / "test_src.py").write_text("def test_x(): pass\n# c\n", encoding="utf-8")
    files = changes.changed_files(repo)
    assert not changes.any_source_changed(files, "tests")


def test_any_source_changed_false_when_nothing_changed(repo: Path) -> None:
    files = changes.changed_files(repo)
    assert files == []
    assert not changes.any_source_changed(files, "tests")


def test_modified_or_removed_test_files_flags_edit(repo: Path) -> None:
    (repo / "tests" / "test_src.py").write_text("def test_x(): assert False\n", encoding="utf-8")
    files = changes.changed_files(repo)
    assert changes.modified_or_removed_test_files(files, "tests") == ["tests/test_src.py"]


def test_modified_or_removed_test_files_ignores_new_test(repo: Path) -> None:
    (repo / "tests" / "test_new.py").write_text("def test_y(): pass\n", encoding="utf-8")
    files = changes.changed_files(repo)
    assert changes.modified_or_removed_test_files(files, "tests") == []


def test_working_tree_hash_stable_when_unchanged(repo: Path) -> None:
    assert changes.working_tree_hash(repo) == changes.working_tree_hash(repo)


def test_working_tree_hash_changes_with_edit(repo: Path) -> None:
    before = changes.working_tree_hash(repo)
    (repo / "src.py").write_text("x = 999\n", encoding="utf-8")
    after = changes.working_tree_hash(repo)
    assert before != after


def test_working_tree_hash_ignores_tool_owned_paths(repo: Path) -> None:
    before = changes.working_tree_hash(repo)
    (repo / ".testmondata").write_text("fake", encoding="utf-8")
    after = changes.working_tree_hash(repo)
    assert before == after


def test_working_tree_hash_ignores_testmon_wal_sidecar_files(repo: Path) -> None:
    before = changes.working_tree_hash(repo)
    (repo / ".testmondata-wal").write_text("fake", encoding="utf-8")
    (repo / ".testmondata-shm").write_text("fake", encoding="utf-8")
    after = changes.working_tree_hash(repo)
    assert before == after
