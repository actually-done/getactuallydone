"""plugin/scripts/session-start-check.sh: warns (never blocks - SessionStart can't)
when actually-done isn't on PATH, or when Claude Code is older than the onFailure
floor (2.1.295) the push guard depends on (docs/DECISIONS.md, D11). Must never crash:
every branch, including an unparseable `claude --version`, prints valid JSON.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "plugin" / "scripts" / "session-start-check.sh"


def _find_working_bash() -> str | None:
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
            subprocess.run([candidate, "--version"], capture_output=True, timeout=10, check=True)
            return candidate
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
    return None


_BASH = _find_working_bash()
pytestmark = pytest.mark.skipif(_BASH is None, reason="requires a working bash")


def _make_stub(bin_dir: Path, name: str, body: str) -> None:
    stub = bin_dir / name
    stub.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    stub.chmod(0o755)


def _path_without(*names: str) -> str:
    """The real PATH (so `bash`, `grep`, `cut`, `timeout`, etc. still resolve), with
    any directory that holds a real `actually-done`/`claude` removed - otherwise a
    dev or CI machine that has either installed would find the real one after the
    stub directory, defeating the "not on PATH" tests.
    """
    kept = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        if any((Path(entry) / name).exists() for name in names):
            continue
        if any((Path(entry) / f"{name}.exe").exists() for name in names):
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


def _run(fake_bin: Path, path_suffix: str) -> dict[str, object]:
    env = dict(os.environ)
    env["PATH"] = str(fake_bin) + os.pathsep + path_suffix
    completed = subprocess.run(
        [_BASH, str(_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr  # never crashes the session
    return json.loads(completed.stdout)  # type: ignore[no-any-return]


def test_warns_when_actually_done_not_on_path(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    body = _run(fake_bin, _path_without("actually-done"))
    assert "systemMessage" in body
    assert "not on PATH" in body["systemMessage"]  # type: ignore[operator]
    assert "uv tool install actually-done" in body["systemMessage"]  # type: ignore[operator]


def test_warns_when_claude_not_on_path(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _make_stub(fake_bin, "actually-done", "exit 0")
    body = _run(fake_bin, _path_without("claude"))
    assert "systemMessage" in body
    assert "could not find 'claude'" in body["systemMessage"]  # type: ignore[operator]


def test_warns_when_claude_version_fails(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _make_stub(fake_bin, "actually-done", "exit 0")
    _make_stub(fake_bin, "claude", "exit 1")
    body = _run(fake_bin, _path_without("claude"))
    assert "systemMessage" in body
    assert "failed or timed out" in body["systemMessage"]  # type: ignore[operator]


def test_warns_on_unparseable_version(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _make_stub(fake_bin, "actually-done", "exit 0")
    _make_stub(fake_bin, "claude", 'echo "garbage output"')
    body = _run(fake_bin, _path_without("claude"))
    assert "systemMessage" in body
    assert "could not parse a version number" in body["systemMessage"]  # type: ignore[operator]


@pytest.mark.parametrize("version", ["2.1.100", "2.0.999", "1.9.999", "2.1.294"])
def test_warns_when_version_below_floor(tmp_path: Path, version: str) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _make_stub(fake_bin, "actually-done", "exit 0")
    _make_stub(fake_bin, "claude", f'echo "{version} (Claude Code)"')
    body = _run(fake_bin, _path_without("claude"))
    assert "systemMessage" in body
    message = body["systemMessage"]  # type: ignore[index]
    assert version in message
    assert "2.1.295" in message
    assert "upgrade" in message.lower()


@pytest.mark.parametrize("version", ["2.1.295", "2.1.300", "2.2.0", "3.0.0"])
def test_silent_when_version_at_or_above_floor(tmp_path: Path, version: str) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _make_stub(fake_bin, "actually-done", "exit 0")
    _make_stub(fake_bin, "claude", f'echo "{version} (Claude Code)"')
    assert _run(fake_bin, _path_without("claude")) == {}


# ---------------------------------------------------------------------------
# Limiter detection: stock macOS has no `timeout` at all (only `gtimeout` via
# coreutils), and on Windows a `timeout` on PATH may resolve to
# C:\Windows\System32\timeout.exe - a different program entirely (it waits N
# seconds or for a keypress; it does not run-and-kill a command). These tests
# use a *slow* claude stub and check actual elapsed time, not just the output
# shape - matching output alone doesn't prove which code path enforced the
# timeout, only that it eventually produced the right warning somehow.
# ---------------------------------------------------------------------------

_FAKE_WINDOWS_TIMEOUT_BODY = (
    'echo "ERROR: Invalid value for timeout (/T) specified." >&2\nexit 1'
)
_SLOW_CLAUDE_SECONDS = 8  # > the script's hardcoded 5s limit, < test patience


def _find_real_gnu_limiter() -> str | None:
    """A real GNU-coreutils `timeout`/`gtimeout` on *this* (test-running) machine,
    to copy as a stand-in "gtimeout" binary - mirrors the shell script's own
    detection logic, but is only ever used to set up a fixture, never asserted on.
    """
    for candidate in ("timeout", "gtimeout"):
        found = shutil.which(candidate)
        if not found:
            continue
        try:
            result = subprocess.run(
                [found, "--version"], capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if "GNU coreutils" in result.stdout:
            return found
    return None


_REAL_GNU_LIMITER = _find_real_gnu_limiter()


def test_timeout_enforced_with_whatever_limiter_is_really_on_path(tmp_path: Path) -> None:
    import time

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _make_stub(fake_bin, "actually-done", "exit 0")
    _make_stub(
        fake_bin, "claude", f'sleep {_SLOW_CLAUDE_SECONDS}\necho "2.1.100 (Claude Code)"'
    )
    start = time.monotonic()
    body = _run(fake_bin, _path_without("claude"))
    elapsed = time.monotonic() - start
    assert elapsed < _SLOW_CLAUDE_SECONDS - 1, (
        f"took {elapsed:.1f}s - the {_SLOW_CLAUDE_SECONDS}s sleep wasn't actually cut short"
    )
    assert "failed or timed out" in body["systemMessage"]  # type: ignore[index]


@pytest.mark.skipif(
    _REAL_GNU_LIMITER is None, reason="no real GNU timeout/gtimeout on this machine to copy"
)
def test_timeout_enforced_via_gtimeout_when_timeout_is_not_gnu(tmp_path: Path) -> None:
    import shutil as shutil_mod
    import time

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _make_stub(fake_bin, "actually-done", "exit 0")
    _make_stub(
        fake_bin, "claude", f'sleep {_SLOW_CLAUDE_SECONDS}\necho "2.1.100 (Claude Code)"'
    )
    _make_stub(fake_bin, "timeout", _FAKE_WINDOWS_TIMEOUT_BODY)  # shadows the real one
    # Preserve the source's extension (".exe" on Windows) - a copy literally named
    # "gtimeout" with no extension isn't a recognizable Windows executable at all,
    # even though `command -v` might still report finding a file by that name.
    gtimeout_dest = fake_bin / f"gtimeout{Path(_REAL_GNU_LIMITER).suffix}"  # type: ignore[arg-type]
    shutil_mod.copy(_REAL_GNU_LIMITER, gtimeout_dest)  # type: ignore[arg-type]
    gtimeout_dest.chmod(0o755)

    start = time.monotonic()
    body = _run(fake_bin, _path_without("claude"))
    elapsed = time.monotonic() - start
    assert elapsed < _SLOW_CLAUDE_SECONDS - 1, (
        f"took {elapsed:.1f}s - gtimeout wasn't actually used to cut the sleep short"
    )
    assert "failed or timed out" in body["systemMessage"]  # type: ignore[index]


def test_timeout_enforced_by_pure_bash_fallback_when_no_limiter_exists(tmp_path: Path) -> None:
    import time

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _make_stub(fake_bin, "actually-done", "exit 0")
    _make_stub(
        fake_bin, "claude", f'sleep {_SLOW_CLAUDE_SECONDS}\necho "2.1.100 (Claude Code)"'
    )
    _make_stub(fake_bin, "timeout", _FAKE_WINDOWS_TIMEOUT_BODY)  # shadows the real one

    start = time.monotonic()
    body = _run(fake_bin, _path_without("claude"))
    elapsed = time.monotonic() - start
    assert elapsed < _SLOW_CLAUDE_SECONDS - 1, (
        f"took {elapsed:.1f}s - the pure-bash fallback didn't actually kill the sleep"
    )
    assert "failed or timed out" in body["systemMessage"]  # type: ignore[index]
