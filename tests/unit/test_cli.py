"""CLI wiring for `actually-done hook pre-bash`: stdin in, exit code and stderr out."""

from __future__ import annotations

import json

import pytest

from actually_done import cli


def _run_pre_bash(monkeypatch: pytest.MonkeyPatch, command: str) -> tuple[int, str]:
    payload = json.dumps({"tool_input": {"command": command}})
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))
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
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"tool_input": {}})))
    assert cli.main(["hook", "pre-bash"]) == 0
