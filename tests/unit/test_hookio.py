"""hookio.py: shared stdin JSON reading for every `actually-done hook <name>` handler."""

from __future__ import annotations

import io

from actually_done import hookio


def test_read_payload_parses_json() -> None:
    stream = io.StringIO('{"a": 1}')
    assert hookio.read_payload(stream) == {"a": 1}


def test_read_payload_handles_empty_stdin() -> None:
    assert hookio.read_payload(io.StringIO("")) == {}


def test_read_payload_rejects_non_object_json() -> None:
    assert hookio.read_payload(io.StringIO("[1, 2, 3]")) == {}
