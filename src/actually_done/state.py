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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def get_cached_result(repo_root: Path, tree_hash: str) -> dict[str, Any] | None:
    result = _load(repo_root)["cache"].get(tree_hash)
    return result if isinstance(result, dict) else None


def set_cached_result(repo_root: Path, tree_hash: str, result: dict[str, Any]) -> None:
    data = _load(repo_root)
    cache: dict[str, Any] = data["cache"]
    cache[tree_hash] = result
    if len(cache) > _MAX_CACHE_ENTRIES:
        for key in list(cache)[: len(cache) - _MAX_CACHE_ENTRIES]:
            del cache[key]
    _save(repo_root, data)


def record_attempt(repo_root: Path, failing_tests: list[str]) -> int:
    """Update the no-progress counter. Returns the new consecutive-same-failure count."""
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
    data = _load(repo_root)
    data["attempt_count"] = 0
    data["last_failing_set"] = []
    _save(repo_root, data)
