"""Every push spelling the push guard must catch, and every command it must let through."""

from __future__ import annotations

import pytest

from actually_done import guard

BLOCKED_COMMANDS = [
    "git push",
    "git push origin main",
    "git -C . push",
    'sh -c "git push"',
    "bash -lc 'git push origin main'",
    "env X=1 git push",
    "X=1 git push",
    "git -c push.default=current push origin main",
    "git 'push' origin main",
    "cd /tmp && git push",
    "echo hi; git push",
    "(git push)",
]

ALLOWED_COMMANDS = [
    "git status",
    "echo push",
    "git log --grep push",
    "git log --grep=push",
    "git commit -m 'push the button'",
    "npm run build",
    "git -C . status",
    "git push-is-not-a-real-subcommand-but-contains-push",
]


@pytest.mark.parametrize("command", BLOCKED_COMMANDS)
def test_blocks_every_push_spelling(command: str) -> None:
    blocked, reason = guard.check_push(command)
    assert blocked is True
    assert reason


@pytest.mark.parametrize("command", ALLOWED_COMMANDS)
def test_allows_non_push_commands(command: str) -> None:
    blocked, _reason = guard.check_push(command)
    assert blocked is False


def test_extract_bash_command_missing_tool_input() -> None:
    assert guard.extract_bash_command({}) is None


def test_extract_bash_command_non_bash_tool() -> None:
    payload = {"tool_input": {"file_path": "foo.py"}}
    assert guard.extract_bash_command(payload) is None
