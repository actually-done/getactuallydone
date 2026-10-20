"""pyproject.toml's version and plugin/.claude-plugin/plugin.json's version are two
separate files with no way to share one value at the TOML/JSON level - this test is
what actually keeps them in sync, catching a bump to one without the other.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _pyproject_version() -> str:
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _plugin_json_version() -> str:
    data = json.loads(
        (_REPO_ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    return str(data["version"])


def test_pyproject_and_plugin_json_versions_match() -> None:
    pyproject_version = _pyproject_version()
    plugin_version = _plugin_json_version()
    assert pyproject_version == plugin_version, (
        f"pyproject.toml says {pyproject_version!r} but plugin.json says "
        f"{plugin_version!r} - bump both together."
    )


def test_version_looks_like_semver() -> None:
    parts = _pyproject_version().split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)
