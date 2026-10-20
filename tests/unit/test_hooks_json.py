"""plugin/hooks/hooks.json config regression test: a future edit that silently
drops PowerShell coverage or the onFailure safety net from the push guard should
fail CI, not just be caught by someone reading the diff.
"""

from __future__ import annotations

import json
from pathlib import Path

_HOOKS_JSON = (
    Path(__file__).resolve().parents[2] / "plugin" / "hooks" / "hooks.json"
)


def _load_hooks() -> dict[str, object]:
    data = json.loads(_HOOKS_JSON.read_text(encoding="utf-8"))
    return data["hooks"]  # type: ignore[no-any-return]


def _pretooluse_entries() -> list[dict[str, object]]:
    hooks = _load_hooks()
    return hooks["PreToolUse"]  # type: ignore[index,return-value]


def test_push_guard_matches_both_bash_and_powershell() -> None:
    entries = _pretooluse_entries()
    matchers = {entry["matcher"] for entry in entries}  # type: ignore[index]
    matched_tools: set[str] = set()
    for matcher in matchers:
        matched_tools.update(str(matcher).split("|"))
    assert matched_tools == {"Bash", "PowerShell"}, (
        f"PreToolUse matcher(s) {matchers!r} don't cover exactly Bash and PowerShell - "
        "the push guard would silently stop covering one of them."
    )


def test_push_guard_has_onfailure_block() -> None:
    entries = _pretooluse_entries()
    for entry in entries:
        for hook in entry["hooks"]:  # type: ignore[index]
            assert hook.get("onFailure") == "block", (  # type: ignore[union-attr]
                f"PreToolUse hook entry {hook!r} is missing onFailure: \"block\" - "
                "without it, a missing/crashing actually-done can fail open "
                "(docs/DECISIONS.md, D2/D11)."
            )


def test_push_guard_invokes_actually_done_directly() -> None:
    """No shell wrapper (docs/DECISIONS.md, D11): invoking bash here again would
    reintroduce the Windows-without-Git-Bash gap this design deliberately avoids.
    """
    entries = _pretooluse_entries()
    for entry in entries:
        for hook in entry["hooks"]:  # type: ignore[index]
            assert hook.get("command") == "actually-done"


def test_stop_and_session_start_hooks_still_present() -> None:
    hooks = _load_hooks()
    assert hooks.get("Stop")
    session_start_matchers = {entry["matcher"] for entry in hooks.get("SessionStart", [])}
    assert "compact" in session_start_matchers
    assert any("startup" in m and "resume" in m for m in session_start_matchers)
