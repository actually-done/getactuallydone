"""CLI wiring for `actually-done hook pre-bash`, `hook stop`, and `verify --tier fast`."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from actually_done import cli


def _run_pre_bash(monkeypatch: pytest.MonkeyPatch, command: str) -> tuple[int, str]:
    payload = json.dumps({"tool_input": {"command": command}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
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


def test_pre_bash_allows_non_bash_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_input": {}})))
    assert cli.main(["hook", "pre-bash"]) == 0


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
