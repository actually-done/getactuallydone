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


def test_extract_command_missing_tool_input() -> None:
    assert guard.extract_command({}) is None


def test_extract_command_non_bash_tool() -> None:
    payload = {"tool_input": {"file_path": "foo.py"}}
    assert guard.extract_command(payload) is None


# ---------------------------------------------------------------------------
# PowerShell dialect - Claude Code can run commands through its PowerShell tool
# instead of (or alongside) Bash; see docs/DECISIONS.md, D9/D11.
# ---------------------------------------------------------------------------

BLOCKED_COMMANDS_POWERSHELL = [
    "git push",
    "git push origin main",
    "git.exe push origin main",
    "git -C . push",
    "& git push",
    '& "git" push',
    r'& "C:\Program Files\Git\bin\git.exe" push',
    "&git.exe push",  # no space after the call operator - also valid PowerShell
    'Start-Process git -ArgumentList "push","origin","main"',
    'Start-Process -FilePath git.exe -ArgumentList "push origin main"',
    "cmd /c git push",
    'cmd /c "git push"',
    "cmd.exe /c git push",
    "git status; git push",
    "git push | Out-Null",
    "git push && echo done",
]

ALLOWED_COMMANDS_POWERSHELL = [
    "git status",
    "Get-ChildItem",
    "git log --grep push",
    "git log --grep=push",
    "echo push",
    'Start-Process notepad -ArgumentList "push.txt"',
    "git push-is-not-a-real-subcommand-but-contains-push",
    "cmd /c git status",
]


@pytest.mark.parametrize("command", BLOCKED_COMMANDS_POWERSHELL)
def test_blocks_every_push_spelling_powershell(command: str) -> None:
    blocked, reason = guard.check_push_for_tool("PowerShell", command)
    assert blocked is True
    assert reason


@pytest.mark.parametrize("command", ALLOWED_COMMANDS_POWERSHELL)
def test_allows_non_push_commands_powershell(command: str) -> None:
    blocked, _reason = guard.check_push_for_tool("PowerShell", command)
    assert blocked is False


def test_check_push_for_tool_dispatches_to_posix_by_default() -> None:
    # tool_name of None/"Bash"/anything else uses the POSIX parser - same behavior
    # as check_push, not the PowerShell one.
    assert guard.check_push_for_tool(None, "git push")[0] is True
    assert guard.check_push_for_tool("Bash", "git push")[0] is True
    assert guard.check_push_for_tool("SomeOtherTool", "git push")[0] is True


def test_dialect_dispatch_actually_matters() -> None:
    # Start-Process is a PowerShell cmdlet with no POSIX equivalent: the POSIX
    # parser sees "Start-Process" as the program name (not git/env/sh/bash) and
    # stops there, so it misses this entirely - proving check_push_for_tool isn't
    # just ignoring tool_name and running the same parser either way.
    powershell_only = 'Start-Process git -ArgumentList "push","origin"'
    assert guard.check_push_for_tool("PowerShell", powershell_only)[0] is True
    assert guard.check_push(powershell_only)[0] is False
