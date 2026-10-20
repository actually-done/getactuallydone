"""Attack 7 (not really an attack - a fault-tolerance check): the verifier itself
crashes (malformed input, a bug, anything short of a clean exit). The Stop hook
must not interpret a crash as "nothing wrong" and silently let the agent claim
the turn is done.

Caught by: `plugin/scripts/stop.sh` always exits 0 with valid JSON and traps every
failure into a halt (`systemMessage`, no `decision: block`) rather than passing it
through - `onFailure` has no effect on Stop hooks (docs/DECISIONS.md, D2), so the
wrapper has to do this itself.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

_STOP_SH = Path(__file__).resolve().parents[2] / "plugin" / "scripts" / "stop.sh"


def _find_working_bash() -> str | None:
    """`shutil.which("bash")` alone can resolve to a broken WSL launcher stub at
    `C:\\Windows\\System32\\bash.exe` ahead of real Git-for-Windows bash on PATH -
    try the known-good Windows locations first, then fall back to PATH lookup
    (the only sane option, and unambiguous, on Linux/macOS CI)."""
    candidates = []
    if platform.system() == "Windows":
        candidates += [
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
        ]
    found = shutil.which("bash")
    if found:
        candidates.append(found)
    for candidate in candidates:
        try:
            subprocess.run(
                [candidate, "--version"], capture_output=True, timeout=10, check=True
            )
            return candidate
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
    return None


_BASH = _find_working_bash()


@pytest.mark.skipif(_BASH is None, reason="requires a working bash")
def test_stop_wrapper_halts_instead_of_passing_on_a_crash(tmp_path: Path) -> None:
    # Malformed JSON on stdin makes `actually-done hook stop` crash with an
    # uncaught exception - a stand-in for "the verifier itself is broken".
    completed = subprocess.run(
        [_BASH, str(_STOP_SH)],
        input="not valid json {",
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr  # the wrapper itself never fails

    body = json.loads(completed.stdout)  # always valid JSON, even on a crash
    assert body != {}  # not a silent pass
    assert "decision" not in body  # a crash halts; it never force-blocks either
    assert "systemMessage" in body  # the human can see why
