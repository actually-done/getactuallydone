"""Stdin JSON plumbing shared by every `actually-done hook <name>` handler."""

from __future__ import annotations

import json
from typing import IO, Any


def read_payload(stream: IO[str]) -> dict[str, Any]:
    """Parse a Claude Code hook's JSON payload from stdin. Empty input is treated as {}."""
    raw = stream.read()
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    return payload if isinstance(payload, dict) else {}
