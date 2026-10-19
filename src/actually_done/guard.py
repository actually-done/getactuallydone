"""Push-guard logic: detect a `git push` inside a Bash command, however it's spelled.

A Bash permission deny rule like `Bash(git push *)` only matches the exact text Claude
writes; it doesn't catch `git -C . push`, `env X=1 git push`, or a push buried inside
`sh -c "..."` (see docs/DECISIONS.md, D8). This module walks the command the way a shell
would: split on shell operators, peel known wrappers (`env`, leading `VAR=val`, and
`sh -c` / `bash -lc`-style forms, recursing into the embedded string), then check whether
the resulting program is `git` and its first positional argument is `push`.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

_SHELL_WRAPPERS = {"sh", "bash", "zsh", "dash", "ksh", "ash", "busybox"}
_GIT_OPTS_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix"}
_PUNCTUATION = ";&|()<>\n"
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHORT_FLAG_RE = re.compile(r"^-[A-Za-z]+$")

_BLOCK_REASON = (
    "actually-done: blocked a `git push`, however it was spelled. Pushing is a human "
    "action, not an agent one (see solution.md, Agent identity) - ask the user to "
    "push themselves."
)


def extract_bash_command(payload: dict[str, Any]) -> str | None:
    """Pull `tool_input.command` out of a PreToolUse payload, if present and a string."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    return command if isinstance(command, str) else None


def check_push(command: str) -> tuple[bool, str]:
    """Return (blocked, reason) for a raw Bash command string."""
    if _contains_git_push(command):
        return True, _BLOCK_REASON
    return False, ""


def _contains_git_push(command: str) -> bool:
    return any(_subcommand_is_git_push(sub) for sub in _split_into_subcommands(command))


def _split_into_subcommands(command: str) -> list[list[str]]:
    subcommands: list[list[str]] = []
    current: list[str] = []
    for token in _tokenize(command):
        if token and all(ch in _PUNCTUATION for ch in token):
            if current:
                subcommands.append(current)
            current = []
        else:
            current.append(token)
    if current:
        subcommands.append(current)
    return subcommands


def _tokenize(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=_PUNCTUATION)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        # Unbalanced quotes or similar: fail closed rather than silently allowing.
        return ["git", "push"]


def _peel_assignments(atoms: list[str]) -> list[str]:
    while atoms and _ASSIGNMENT_RE.match(atoms[0]):
        atoms = atoms[1:]
    return atoms


def _peel_env_wrapper(atoms: list[str]) -> list[str]:
    atoms = atoms[1:]  # drop "env" itself
    while atoms and atoms[0].startswith("-") and atoms[0] != "--":
        if atoms[0] in ("-u", "--unset") and len(atoms) > 1:
            atoms = atoms[2:]
        else:
            atoms = atoms[1:]
    if atoms and atoms[0] == "--":
        atoms = atoms[1:]
    return _peel_assignments(atoms)


def _subcommand_is_git_push(atoms: list[str]) -> bool:
    atoms = _peel_assignments(atoms)
    while atoms:
        program = atoms[0].rsplit("/", 1)[-1]
        if program == "env":
            atoms = _peel_env_wrapper(atoms)
            continue
        if program in _SHELL_WRAPPERS:
            return _check_shell_wrapper(atoms)
        if program == "git":
            return _git_subcommand_is_push(atoms[1:])
        return False
    return False


def _check_shell_wrapper(atoms: list[str]) -> bool:
    for i, atom in enumerate(atoms[1:], start=1):
        if _SHORT_FLAG_RE.match(atom) and "c" in atom[1:] and i + 1 < len(atoms):
            return _contains_git_push(atoms[i + 1])
    return False


def _git_subcommand_is_push(rest: list[str]) -> bool:
    i = 0
    while i < len(rest):
        atom = rest[i]
        if atom in _GIT_OPTS_WITH_ARG:
            i += 2
            continue
        if atom.startswith("-"):
            i += 1
            continue
        return atom == "push"
    return False
