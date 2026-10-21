"""CLI wiring for `actually-done hook pre-bash`, `hook stop`, and `verify --tier fast`."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from actually_done import cli


def _run_pre_bash(
    monkeypatch: pytest.MonkeyPatch, command: str, tool_name: str | None = None
) -> tuple[int, str]:
    tool_input: dict[str, object] = {"command": command}
    payload: dict[str, object] = {"tool_input": tool_input}
    if tool_name is not None:
        payload["tool_name"] = tool_name
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    captured: list[str] = []
    monkeypatch.setattr(
        "builtins.print",
        lambda *args, **kwargs: captured.append(" ".join(str(a) for a in args)),
    )
    exit_code = cli.main(["hook", "pre-bash"])
    return exit_code, "\n".join(captured)


def test_pre_bash_blocks_push(monkeypatch: pytest.MonkeyPatch) -> None:
    exit_code, stderr = _run_pre_bash(monkeypatch, "git push origin main")
    assert exit_code == 2
    assert "push" in stderr.lower()


def test_pre_bash_allows_status(monkeypatch: pytest.MonkeyPatch) -> None:
    exit_code, stderr = _run_pre_bash(monkeypatch, "git status")
    assert exit_code == 0
    assert stderr == ""


def test_pre_bash_blocks_push_via_powershell_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    exit_code, stderr = _run_pre_bash(
        monkeypatch, 'Start-Process git -ArgumentList "push","origin"', tool_name="PowerShell"
    )
    assert exit_code == 2
    assert "push" in stderr.lower()


def test_pre_bash_allows_powershell_non_push(monkeypatch: pytest.MonkeyPatch) -> None:
    exit_code, stderr = _run_pre_bash(monkeypatch, "Get-ChildItem", tool_name="PowerShell")
    assert exit_code == 0
    assert stderr == ""


def test_pre_bash_blocks_when_command_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # This hook is matched to the Bash tool, so a payload with no tool_input.command
    # means something is wrong, not that the command is safe - fail closed.
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_input": {}})))
    captured: list[str] = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: captured.append(" ".join(str(x) for x in a))
    )
    assert cli.main(["hook", "pre-bash"]) == 2
    assert captured


def test_pre_bash_blocks_on_empty_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert cli.main(["hook", "pre-bash"]) == 2


def test_pre_bash_blocks_on_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not valid json {"))
    assert cli.main(["hook", "pre-bash"]) == 2


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.actually-done]\nunit_test_path = "tests"\nfast_timeout_seconds = 30\n',
        encoding="utf-8",
    )
    (tmp_path / "pkg.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pkg.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom pkg import add\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run_hook_stop(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]
) -> tuple[int, list[str]]:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    printed: list[str] = []
    monkeypatch.setattr(
        "builtins.print",
        lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    exit_code = cli.main(["hook", "stop"])
    return exit_code, printed


def test_hook_stop_allows_when_nothing_changed(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exit_code, printed = _run_hook_stop(monkeypatch, {})
    assert exit_code == 0
    assert json.loads(printed[0]) == {}


def test_hook_stop_respects_stop_hook_active(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    exit_code, printed = _run_hook_stop(monkeypatch, {"stop_hook_active": True})
    assert exit_code == 0
    assert json.loads(printed[0]) == {}


def test_hook_stop_blocks_on_fixable_failure(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    exit_code, printed = _run_hook_stop(monkeypatch, {})
    assert exit_code == 0
    body = json.loads(printed[0])
    assert body["decision"] == "block"
    assert "test_add" in body["reason"]


def test_hook_stop_halts_on_edited_test_file(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "tests" / "test_pkg.py").write_text(
        "def test_add():\n    assert True\n", encoding="utf-8"
    )
    exit_code, printed = _run_hook_stop(monkeypatch, {})
    assert exit_code == 0
    body = json.loads(printed[0])
    assert "decision" not in body
    assert "systemMessage" in body


def test_hook_stop_halts_after_three_identical_failures(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    for _ in range(2):
        _exit_code, printed = _run_hook_stop(monkeypatch, {})
        assert json.loads(printed[0])["decision"] == "block"
    _exit_code, printed = _run_hook_stop(monkeypatch, {})
    body = json.loads(printed[0])
    assert "decision" not in body
    assert "systemMessage" in body


def test_hook_stop_uses_payload_cwd_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other_repo = tmp_path / "proj"
    other_repo.mkdir()
    _git(other_repo, "init", "-q")
    _git(other_repo, "config", "user.email", "test@example.com")
    _git(other_repo, "config", "user.name", "Test")
    (other_repo / "pyproject.toml").write_text(
        '[tool.actually-done]\nunit_test_path = "tests"\n', encoding="utf-8"
    )
    (other_repo / "pkg.py").write_text("x = 1\n", encoding="utf-8")
    (other_repo / "tests").mkdir()
    (other_repo / "tests" / "test_pkg.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    _git(other_repo, "add", ".")
    _git(other_repo, "commit", "-q", "-m", "initial")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    exit_code, printed = _run_hook_stop(monkeypatch, {"cwd": str(other_repo)})
    assert exit_code == 0
    assert json.loads(printed[0]) == {}


def test_verify_fast_tier_reports_failure(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    exit_code = cli.main(["verify", "--tier", "fast"])
    assert exit_code == 1
    assert "test_add" in capsys.readouterr().out


def test_verify_full_tier_requires_base(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["verify", "--tier", "full"])
    assert exit_code == 2
    assert "--base" in capsys.readouterr().err


def test_verify_full_tier_catches_hidden_break(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "tests" / "test_pkg.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom pkg import add\n\n"
        "def test_add():\n    assert add(1, 2) == -1\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pr: break + hide")

    exit_code = cli.main(["verify", "--tier", "full", "--base", base_sha])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "full-tier evidence" in out
    assert "test_add" in out


def test_verify_full_tier_still_uses_base_tests_when_pr_deletes_pyproject_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A PR deleting its own pyproject.toml can't escape verification this way -
    config just falls back to defaults (config.py), and base's real tests (found
    at the default unit_test_path) still get checked for real.
    """
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.actually-done]\nunit_test_path = "tests/unit"\n', encoding="utf-8"
    )
    (tmp_path / "pkg.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    tests_dir = tmp_path / "tests" / "unit"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_pkg.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom pkg import add\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()

    (tmp_path / "pyproject.toml").unlink()
    (tmp_path / "pkg.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "pr: delete pyproject.toml + hidden break")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["verify", "--tier", "full", "--base", base_sha])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "test_add" in out


def test_approve_writes_verify_approved(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["approve", "tests/test_pkg.py::test_add"])
    assert exit_code == 0
    content = (repo / ".verify" / "approved").read_text(encoding="utf-8")
    assert "tests/test_pkg.py::test_add" in content
    assert "approved 1 test id(s)" in capsys.readouterr().out


def test_approve_is_additive(repo: Path) -> None:
    # approve now needs a real file to hash (it binds each entry to the file's
    # current content - see cli.py's _cmd_approve), so each id names a real file.
    (repo / "tests" / "a.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    (repo / "tests" / "b.py").write_text("def test_y():\n    pass\n", encoding="utf-8")
    cli.main(["approve", "tests/a.py::test_x"])
    cli.main(["approve", "tests/b.py::test_y"])
    content = (repo / ".verify" / "approved").read_text(encoding="utf-8")
    assert "tests/a.py::test_x" in content
    assert "tests/b.py::test_y" in content


def test_init_writes_scaffolding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    exit_code = cli.main(["init"])
    assert exit_code == 0
    assert (tmp_path / "AGENTS.md").is_file()
    assert (tmp_path / "CODEOWNERS").is_file()
    assert (tmp_path / ".github" / "workflows" / "actually-done.yml").is_file()


def _run_hook_compact(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]
) -> dict[str, object]:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    printed: list[str] = []
    monkeypatch.setattr(
        "builtins.print", lambda *a, **k: printed.append(" ".join(str(x) for x in a))
    )
    exit_code = cli.main(["hook", "compact"])
    assert exit_code == 0
    return json.loads(printed[0])  # type: ignore[no-any-return]


def test_hook_compact_reprints_agents_md(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "AGENTS.md").write_text("# Critical rules\n\n- never push\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    body = _run_hook_compact(monkeypatch, {"source": "compact"})

    assert body["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "never push" in body["hookSpecificOutput"]["additionalContext"]


def test_hook_compact_truncates_to_twenty_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "AGENTS.md").write_text("\n".join(f"line {i}" for i in range(50)), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    body = _run_hook_compact(monkeypatch, {"source": "compact"})

    block = body["hookSpecificOutput"]["additionalContext"]
    assert block.count("\n") == 19  # 20 lines
    assert "line 19" in block
    assert "line 20" not in block


def test_hook_compact_noop_when_agents_md_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert _run_hook_compact(monkeypatch, {"source": "compact"}) == {}


def test_hook_compact_noop_for_other_sessionstart_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _run_hook_compact(monkeypatch, {"source": "startup"}) == {}
