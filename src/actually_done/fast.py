"""Fast tier: run only the tests a change could have broken, fast enough for a Stop hook.

Passes immediately if no source file changed. Otherwise runs pytest (with pytest-testmon,
if installed, to narrow to affected tests; the configured unit-test path either way) under
a hard timeout, and caches the pass/fail result by working-tree hash so a second
invocation against the same uncommitted change doesn't re-run anything.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from actually_done import changes, state
from actually_done.config import Config

_FAILED_LINE_RE = re.compile(r"^FAILED (\S+)")
_TAIL_LINES = 60


@dataclass(frozen=True)
class FastResult:
    status: str  # "pass" | "fail" | "error"
    summary: str
    details: str
    failing_tests: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
            "failing_tests": list(self.failing_tests),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FastResult:
        return cls(
            status=data["status"],
            summary=data["summary"],
            details=data["details"],
            failing_tests=tuple(data.get("failing_tests", ())),
        )


def run_fast_tier(repo_root: Path, config: Config) -> FastResult:
    files = changes.changed_files(repo_root)
    if not changes.any_source_changed(files, config.unit_test_path):
        return FastResult(status="pass", summary="no source files changed", details="")

    tree_hash = changes.working_tree_hash(repo_root)
    cached = state.get_cached_result(repo_root, tree_hash)
    if cached is not None:
        return FastResult.from_dict(cached)

    result = _run_pytest(repo_root, config)
    if result.status in ("pass", "fail"):
        state.set_cached_result(repo_root, tree_hash, result.to_dict())
    return result


def _run_pytest(repo_root: Path, config: Config) -> FastResult:
    command = [sys.executable, "-m", "pytest", "-q", config.unit_test_path]
    if find_spec("testmon") is not None:
        command.append("--testmon")

    try:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=config.fast_timeout_seconds,
            env=_pytest_env(),
        )
    except subprocess.TimeoutExpired:
        return FastResult(
            status="error",
            summary=f"verifier timed out after {config.fast_timeout_seconds}s",
            details=f"Command: {' '.join(command)}",
        )
    except OSError as exc:
        return FastResult(status="error", summary=f"could not run pytest: {exc}", details="")

    output = completed.stdout + completed.stderr
    tail = "\n".join(output.splitlines()[-_TAIL_LINES:])

    if completed.returncode == 0:
        return FastResult(status="pass", summary="tests passed", details=tail)

    if completed.returncode == 1:
        failing = _parse_failing_tests(output)
        summary = f"{len(failing)} test(s) failed" if failing else "tests failed"
        return FastResult(
            status="fail", summary=summary, details=tail, failing_tests=tuple(failing)
        )

    return FastResult(
        status="error",
        summary=f"pytest exited with code {completed.returncode} (not a normal test failure)",
        details=tail,
    )


def _pytest_env() -> dict[str, str]:
    """`find_spec("testmon")` only checks the plugin is importable, not that pytest
    will actually register it - if this process inherited
    `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` from whatever called it (e.g. actually-done's
    own full-tier check, which sets it on the sandboxed pytest run it spawns, for a
    different reason: denying a PR's own conftest.py/plugins a say in its result),
    that var would otherwise propagate down to this subprocess too and make
    `--testmon` an unrecognized argument (pytest exits 4), since autoload is how
    testmon's plugin actually gets registered. This subprocess has no reason to
    inherit that lockdown - it only ever runs our own trusted test suite.
    """
    env = dict(os.environ)
    env.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
    return env


def _parse_failing_tests(output: str) -> list[str]:
    return [m.group(1) for line in output.splitlines() if (m := _FAILED_LINE_RE.match(line))]
