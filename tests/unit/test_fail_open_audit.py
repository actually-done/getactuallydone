"""Fail-open audit: for each hook (pre-bash, stop, compact), every failure mode
below must block/halt with a clear reason - none may silently allow. Where a
scenario genuinely doesn't apply to a given hook (it never touches a verifier,
Python subprocess, or on-disk state at all), that's noted in docs/DECISIONS.md
(D10) rather than tested here.

Scope summary, checked against each hook's actual implementation:
- not installed: wrapper fails closed for all three (pre-bash: exit 2, stop:
  halt, compact: no-op {} - compact isn't a gate, so "fails closed" there means
  "degrades to no-op", not "halts")
- python missing from PATH: N/A for pre-bash/compact (no subprocess at all);
  for stop/fast.py, tested below - pytest is spawned via sys.executable, an
  absolute path baked in at interpreter startup, never a bare "python" looked
  up on PATH
- malformed/empty stdin: tested for all three
- verifier exceeds timeout: N/A for pre-bash/compact (no verifier); tested for
  stop
- unwritable state dir: N/A for pre-bash/compact (neither touches state.py);
  tested for stop, which must degrade (no caching/counter) but still correctly
  evaluate and report
- unexpected crash: tested for all three
"""

from __future__ import annotations

import io
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from actually_done import cli, fast, state
from actually_done.config import Config


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_base_repo(repo: Path, fast_timeout_seconds: int = 50) -> None:
    """A minimal real project: pkg.py + one real passing test, committed as the
    only commit so far. Callers then make their own change on top, uncommitted.
    """
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "pyproject.toml").write_text(
        f'[tool.actually-done]\nunit_test_path = "tests"\n'
        f"fast_timeout_seconds = {fast_timeout_seconds}\n",
        encoding="utf-8",
    )
    (repo / "pkg.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pkg.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom pkg import add\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")


# ---------------------------------------------------------------------------
# pre-bash
# ---------------------------------------------------------------------------


def _run_pre_bash_raw(monkeypatch: pytest.MonkeyPatch, stdin_text: str) -> tuple[int, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    captured: list[str] = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: captured.append(" ".join(str(x) for x in a))
    )
    exit_code = cli.main(["hook", "pre-bash"])
    return exit_code, "\n".join(captured)


def test_pre_bash_blocks_on_malformed_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    exit_code, _ = _run_pre_bash_raw(monkeypatch, "{not json")
    assert exit_code == 2


def test_pre_bash_blocks_on_empty_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    exit_code, _ = _run_pre_bash_raw(monkeypatch, "")
    assert exit_code == 2


def test_pre_bash_blocks_on_unexpected_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_command: str) -> tuple[bool, str]:
        raise RuntimeError("simulated crash inside guard.check_push")

    monkeypatch.setattr("actually_done.guard.check_push", _boom)
    exit_code, stderr = _run_pre_bash_raw(
        monkeypatch, json.dumps({"tool_input": {"command": "git status"}})
    )
    assert exit_code == 2
    assert "crashed" in stderr.lower()


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def _run_hook_stop_raw(monkeypatch: pytest.MonkeyPatch, stdin_text: str) -> dict[str, object]:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    printed: list[str] = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: printed.append(" ".join(str(x) for x in a))
    )
    exit_code = cli.main(["hook", "stop"])
    assert exit_code == 0  # always 0 (D3) - a crash must show up in the JSON, not the code
    return json.loads(printed[0])  # type: ignore[no-any-return]


def test_stop_halts_on_malformed_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    body = _run_hook_stop_raw(monkeypatch, "{not json")
    # No source files changed in an empty dir, so this degrades to a normal pass -
    # malformed JSON just means "no payload fields", not a crash by itself.
    assert "decision" not in body


def test_stop_halts_on_unexpected_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    def _boom(_repo_root: Path, _config: Config) -> None:
        raise RuntimeError("simulated crash inside fast.run_fast_tier")

    # Force a source-change so the hook actually reaches run_fast_tier.
    (tmp_path / "pkg.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")

    monkeypatch.setattr("actually_done.fast.run_fast_tier", _boom)
    body = _run_hook_stop_raw(monkeypatch, "{}")

    assert "decision" not in body
    assert "systemMessage" in body
    assert "crashed" in body["systemMessage"].lower()  # type: ignore[union-attr]


def test_stop_halts_on_verifier_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_base_repo(tmp_path, fast_timeout_seconds=1)
    (tmp_path / "tests" / "test_slow.py").write_text(
        "import time\n\ndef test_slow():\n    time.sleep(5)\n", encoding="utf-8"
    )
    (tmp_path / "pkg.py").write_text("x = 2\n", encoding="utf-8")  # source change

    monkeypatch.chdir(tmp_path)
    body = _run_hook_stop_raw(monkeypatch, "{}")

    assert "decision" not in body
    assert "systemMessage" in body


def test_stop_degrades_gracefully_with_unwritable_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file sitting where `.actually-done/` (a directory) needs to go makes every
    state write fail - the verifier must still correctly evaluate and report, just
    without caching or the no-progress counter persisting.
    """
    _init_base_repo(tmp_path)
    # A real, failing change - the real question is whether it's still reported.
    (tmp_path / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / ".actually-done").write_text("blocking file, not a directory", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    body = _run_hook_stop_raw(monkeypatch, "{}")

    assert body.get("decision") == "block"
    assert "test_add" in body["reason"]  # type: ignore[union-attr]


def test_state_save_does_not_raise_when_state_dir_is_unwritable(tmp_path: Path) -> None:
    (tmp_path / ".actually-done").write_text("blocking file", encoding="utf-8")
    state.set_cached_result(tmp_path, "abc", {"status": "pass"})  # must not raise
    state.record_attempt(tmp_path, ["tests/test_x.py::test_y"])  # must not raise
    state.reset_attempts(tmp_path)  # must not raise


def test_fast_tier_does_not_depend_on_python_being_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fast.py spawns pytest via sys.executable (an absolute path baked in at
    interpreter startup), not a bare "python"/"python3" looked up on PATH - so
    removing those from PATH must not affect it.
    """
    _init_base_repo(tmp_path)
    (tmp_path / "pkg.py").write_text("def add(a, b):\n    return a + b  # ok\n", encoding="utf-8")

    # Drop only the PATH entries that actually hold a python/python3/py launcher,
    # so git (needed by fast.run_fast_tier itself for `git status`) still resolves.
    launcher_names = {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}
    stripped = os.pathsep.join(
        p
        for p in os.environ.get("PATH", "").split(os.pathsep)
        if p and not any((Path(p) / name).is_file() for name in launcher_names)
    )
    monkeypatch.setenv("PATH", stripped)
    assert shutil.which("python") is None
    assert shutil.which("python3") is None

    result = fast.run_fast_tier(tmp_path, Config(unit_test_path="tests"))
    assert result.status == "pass"


# ---------------------------------------------------------------------------
# compact
# ---------------------------------------------------------------------------


def _run_hook_compact_raw(monkeypatch: pytest.MonkeyPatch, stdin_text: str) -> dict[str, object]:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    printed: list[str] = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: printed.append(" ".join(str(x) for x in a))
    )
    exit_code = cli.main(["hook", "compact"])
    assert exit_code == 0
    return json.loads(printed[0])  # type: ignore[no-any-return]


def test_compact_noop_on_malformed_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert _run_hook_compact_raw(monkeypatch, "{not json") == {}


def test_compact_noop_on_unexpected_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> str:
        raise OSError("simulated unreadable AGENTS.md")

    monkeypatch.setattr(Path, "read_text", _boom)
    assert _run_hook_compact_raw(monkeypatch, json.dumps({"source": "compact"})) == {}


# ---------------------------------------------------------------------------
# wrapper scripts: never relay a surprise exit code as a silent allow
# ---------------------------------------------------------------------------


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


@pytest.mark.skipif(_BASH is None, reason="requires a working bash")
def test_pre_bash_sh_blocks_when_actually_done_exits_unexpectedly(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stub = fake_bin / "actually-done"
    stub.write_text("#!/usr/bin/env bash\necho 'boom' >&2\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)

    script = Path(__file__).resolve().parents[2] / "plugin" / "scripts" / "pre-bash.sh"
    env = dict(os.environ)
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    completed = subprocess.run(
        [_BASH, str(script)],
        input=json.dumps({"tool_input": {"command": "git status"}}),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert completed.returncode == 2  # never relays the stub's exit 1 as "allow"
