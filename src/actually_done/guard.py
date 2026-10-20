"""Push-guard logic: detect a `git push` inside a Bash or PowerShell command, however
it's spelled.

A Bash permission deny rule like `Bash(git push *)` only matches the exact text Claude
writes; it doesn't catch `git -C . push`, `env X=1 git push`, or a push buried inside
`sh -c "..."` (see docs/DECISIONS.md, D8). This module walks the command the way a shell
would: split on shell operators, peel known wrappers (`env`, leading `VAR=val`, and
`sh -c` / `bash -lc`-style forms, recursing into the embedded string), then check whether
the resulting program is `git` and its first positional argument is `push`.

On Windows, Claude Code can run commands through its PowerShell tool instead of (or
alongside) Bash - matching only the Bash tool leaves a real gap (see docs/DECISIONS.md,
D9/D11). `check_push_for_tool` dispatches to a second, PowerShell-syntax-aware walk that
reuses the same dialect-agnostic `_git_subcommand_is_push` check once it's found the
actual `git` invocation, however it's wrapped (the call operator `&`, `cmd /c`,
`Start-Process`).
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


def extract_command(payload: dict[str, Any]) -> str | None:
    """Pull `tool_input.command` out of a PreToolUse payload, if present and a string.
    Bash and PowerShell share this exact field name and shape - see the tools
    reference - so this one function serves both tools' payloads.
    """
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


def check_push_for_tool(tool_name: str | None, command: str) -> tuple[bool, str]:
    """Return (blocked, reason), parsing `command` as PowerShell syntax when
    `tool_name` is exactly `"PowerShell"` (the tool's canonical name - see the
    tools reference) and as POSIX shell syntax otherwise, including when
    `tool_name` is missing or unrecognized: the hook's own matcher already scopes
    invocation to `Bash|PowerShell`, so an absent/odd `tool_name` here means a
    payload shape this wasn't built for, not a signal to pick a dialect blindly.
    """
    if tool_name == "PowerShell":
        if _contains_git_push_powershell(command):
            return True, _BLOCK_REASON
        return False, ""
    return check_push(command)


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


# ---------------------------------------------------------------------------
# PowerShell dialect
#
# PowerShell's lexical rules differ from POSIX shell enough that shlex doesn't
# apply: backtick is the escape character (not backslash), single quotes are
# always literal, double quotes interpolate but don't change tokenization for our
# purposes, and `&` is the call operator (invoke the following string/path as a
# command) rather than a background/control operator on its own. Statement
# separators are `;`, `|`, newline, and (PowerShell 7+) `&&`/`||`.
# ---------------------------------------------------------------------------

_PS_STATEMENT_SEPARATORS = {";", "|", "\n", "&&", "||"}
_PS_SHELL_WRAPPERS = {"cmd"}  # "cmd.exe" normalizes to this via _strip_exe first
_PS_CMD_FLAGS = {"/c", "/k", "-c", "-k"}


def _contains_git_push_powershell(command: str) -> bool:
    return any(_statement_is_git_push_ps(stmt) for stmt in _split_statements_ps(command))


def _split_statements_ps(command: str) -> list[list[str]]:
    statements: list[list[str]] = []
    current: list[str] = []
    for token in _tokenize_ps(command):
        if token in _PS_STATEMENT_SEPARATORS:
            if current:
                statements.append(current)
            current = []
        else:
            current.append(token)
    if current:
        statements.append(current)
    return statements


def _tokenize_ps(command: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    i = 0
    n = len(command)

    def flush() -> None:
        if current:
            tokens.append("".join(current))
            current.clear()

    while i < n:
        ch = command[i]
        if ch in " \t\r":
            flush()
            i += 1
        elif ch == "\n":
            flush()
            tokens.append("\n")
            i += 1
        elif ch == "`" and i + 1 < n:  # backtick escape: literal next char
            current.append(command[i + 1])
            i += 2
        elif ch == "'":
            j = i + 1
            while j < n:
                if command[j] == "'" and j + 1 < n and command[j + 1] == "'":
                    current.append("'")
                    j += 2
                    continue
                if command[j] == "'":
                    break
                current.append(command[j])
                j += 1
            i = j + 1
        elif ch == '"':
            j = i + 1
            while j < n and command[j] != '"':
                if command[j] == "`" and j + 1 < n:
                    current.append(command[j + 1])
                    j += 2
                    continue
                current.append(command[j])
                j += 1
            i = j + 1
        elif ch == "|":
            flush()
            if i + 1 < n and command[i + 1] == "|":
                tokens.append("||")
                i += 2
            else:
                tokens.append("|")
                i += 1
        elif ch == ";":
            flush()
            tokens.append(";")
            i += 1
        elif ch == "&":
            # Always its own token, whether it's the call operator ("&git.exe",
            # no space needed before the target) or half of "&&" - never let it
            # get absorbed into an adjacent word.
            flush()
            if i + 1 < n and command[i + 1] == "&":
                tokens.append("&&")
                i += 2
            else:
                tokens.append("&")
                i += 1
        else:
            current.append(ch)
            i += 1
    flush()
    return tokens


def _basename_ps(token: str) -> str:
    return re.split(r"[\\/]", token)[-1].lower()


def _strip_exe(name: str) -> str:
    return name[:-4] if name.endswith(".exe") else name


def _statement_is_git_push_ps(tokens: list[str]) -> bool:
    if not tokens:
        return False
    if tokens[0] == "&":  # the call operator: "& git push", "& 'C:\...\git.exe' push"
        tokens = tokens[1:]
        if not tokens:
            return False

    program = _strip_exe(_basename_ps(tokens[0]))

    if program in _PS_SHELL_WRAPPERS:
        return _check_cmd_wrapper_ps(tokens[1:])
    if program == "start-process":
        return _looks_like_start_process_push(tokens[1:])
    if program == "git":
        return _git_subcommand_is_push(tokens[1:])
    return False


def _check_cmd_wrapper_ps(rest: list[str]) -> bool:
    """`cmd /c git push` or `cmd.exe /c "git push"`: cmd.exe's own escaping rules
    differ from PowerShell's, but for a one-shot `/c <command>` invocation,
    re-tokenizing the remaining text with the same tokenizer is close enough to
    reliably catch the spellings this guards against.
    """
    cleaned: list[str] = []
    skipped_flag = False
    for token in rest:
        if not skipped_flag and token.lower() in _PS_CMD_FLAGS:
            skipped_flag = True
            continue
        cleaned.append(token)
    if not skipped_flag or not cleaned:
        return False
    if len(cleaned) == 1:
        return _contains_git_push_powershell(cleaned[0])
    return _statement_is_git_push_ps(cleaned)


def _looks_like_start_process_push(rest: list[str]) -> bool:
    """`Start-Process git -ArgumentList "push","origin"` or `-ArgumentList "push
    origin main"`: Start-Process takes the target and its arguments as separate
    named/positional parameters rather than one command string, so this checks for
    `git`/`git.exe` and an exact `push` word among the remaining tokens (splitting
    each on commas and whitespace, since -ArgumentList accepts either an array or a
    single space-separated string) instead of parsing positions precisely. A
    heuristic, like the rest of this module - documented as such in
    docs/DECISIONS.md.
    """
    words: list[str] = []
    for token in rest:
        for piece in re.split(r"[,\s]+", token):
            if piece:
                words.append(_strip_exe(piece.lower()))
    return "git" in words and "push" in words
