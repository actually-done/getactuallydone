"""Stdin JSON plumbing shared by every `actually-done hook <name>` handler."""

from __future__ import annotations

import json
from typing import IO, Any


def read_payload(stream: IO[str]) -> dict[str, Any]:
    """Parse a Claude Code hook's JSON payload from stdin. Empty or malformed input is
    treated as {} rather than raised - a hook handler crashing on bad stdin is a
    fail-open risk (an uncaught exception exits non-zero-but-not-2, which PreToolUse
    treats as "allow"), so every caller gets a safe, parseable default instead.
    """
    raw = stream.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
