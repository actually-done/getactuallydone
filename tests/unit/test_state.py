"""Local cache + no-progress attempt counter persisted under .actually-done/."""

from __future__ import annotations

from pathlib import Path

from actually_done import state


def test_cache_round_trip(tmp_path: Path) -> None:
    assert state.get_cached_result(tmp_path, "abc") is None
    state.set_cached_result(tmp_path, "abc", {"status": "pass"})
    assert state.get_cached_result(tmp_path, "abc") == {"status": "pass"}


def test_cache_is_capped(tmp_path: Path) -> None:
    for i in range(15):
        state.set_cached_result(tmp_path, f"hash{i}", {"n": i})
    assert state.get_cached_result(tmp_path, "hash0") is None
    assert state.get_cached_result(tmp_path, "hash14") == {"n": 14}


def test_record_attempt_increments_on_same_failure(tmp_path: Path) -> None:
    assert state.record_attempt(tmp_path, ["tests/test_a.py::test_x"]) == 1
    assert state.record_attempt(tmp_path, ["tests/test_a.py::test_x"]) == 2
    assert state.record_attempt(tmp_path, ["tests/test_a.py::test_x"]) == 3


def test_record_attempt_resets_on_different_failure(tmp_path: Path) -> None:
    state.record_attempt(tmp_path, ["tests/test_a.py::test_x"])
    state.record_attempt(tmp_path, ["tests/test_a.py::test_x"])
    assert state.record_attempt(tmp_path, ["tests/test_b.py::test_y"]) == 1


def test_reset_attempts(tmp_path: Path) -> None:
    state.record_attempt(tmp_path, ["tests/test_a.py::test_x"])
    state.record_attempt(tmp_path, ["tests/test_a.py::test_x"])
    state.reset_attempts(tmp_path)
    assert state.record_attempt(tmp_path, ["tests/test_a.py::test_x"]) == 1
