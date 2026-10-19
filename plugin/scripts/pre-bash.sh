#!/usr/bin/env bash
# PreToolUse(Bash) wrapper: fails closed if actually-done isn't installed.
set -uo pipefail

if command -v actually-done >/dev/null 2>&1; then
  actually-done hook pre-bash
  exit $?
fi

echo "actually-done: the 'actually-done' CLI is not installed (uv tool install actually-done). Blocking this command until it is." >&2
exit 2
