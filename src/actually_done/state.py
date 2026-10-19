"""Local state for the fast tier: a result cache keyed by working-tree hash, and a
no-progress counter for consecutive identical test failures. Stored at
`<repo_root>/.actually-done/state.json`, never committed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_STATE_DIR = ".actually-done"
_STATE_FILE = "state.json"
_MAX_CACHE_ENTRIES = 10


class StateUnwritable(OSError):
    """The `.actually-done/` state dir can't be written to.

    Raised by `record_attempt` specifically (not swallowed there, unlike the cache):
    if the no-progress counter can never persist across invocations, it can never
    reach its threshold either, and the Stop hook would block the same failure
    forever. Claude Code's own 8-consecutive-block cap doesn't save us here - it
    resets on every tool call, and the agent keeps calling tools while it tries to
    fix the failure. The caller must halt instead of block when this is raised.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(f"state dir not writable: {path}")
        self.path = path


def _state_path(repo_root: Path) -> Path:
    return repo_root / _STATE_DIR / _STATE_FILE


def _load(repo_root: Path) -> dict[str, Any]:
    path = _state_path(repo_root)
    data: dict[str, Any]
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}
    data.setdefault("attempt_count", 0)
    data.setdefault("last_failing_set", [])
    data.setdefault("cache", {})
    return data


def _save(repo_root: Path, data: dict[str, Any]) -> None:
    path = _state_path(repo_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError as exc:
        raise StateUnwritable(path) from exc


def get_cached_result(repo_root: Path, tree_hash: str) -> dict[str, Any] | None:
    result = _load(repo_root)["cache"].get(tree_hash)
    return result if isinstance(result, dict) else None


def set_cached_result(repo_root: Path, tree_hash: str, result: dict[str, Any]) -> None:
    """Best-effort: caching is an optimization, not a correctness requirement, so an
    unwritable state dir degrades silently here - a real test result still gets
    reported and the verifier still runs (just without the cache hit next time).
    """
    data = _load(repo_root)
    cache: dict[str, Any] = data["cache"]
    cache[tree_hash] = result
    if len(cache) > _MAX_CACHE_ENTRIES:
        for key in list(cache)[: len(cache) - _MAX_CACHE_ENTRIES]:
            del cache[key]
    try:
        _save(repo_root, data)
    except StateUnwritable:
        pass


def record_attempt(repo_root: Path, failing_tests: list[str]) -> int:
    """Update the no-progress counter. Returns the new consecutive-same-failure count.

    Raises `StateUnwritable` if the count can't be persisted - deliberately not
    swallowed here, unlike `set_cached_result`: a counter that silently never
    increments is worse than no counter at all, since the caller would otherwise
    keep blocking the same failure forever with no way out. The caller must halt.
    """
    data = _load(repo_root)
    current = sorted(set(failing_tests))
    if current and current == data["last_failing_set"]:
        data["attempt_count"] += 1
    else:
        data["attempt_count"] = 1
    data["last_failing_set"] = current
    _save(repo_root, data)
    return int(data["attempt_count"])


def reset_attempts(repo_root: Path) -> None:
    """Best-effort, like `set_cached_result`: this only runs on a pass, so a failed
    reset doesn't risk the infinite-block scenario `record_attempt` guards against.
    """
    data = _load(repo_root)
    data["attempt_count"] = 0
    data["last_failing_set"] = []
    try:
        _save(repo_root, data)
    except StateUnwritable:
        pass
