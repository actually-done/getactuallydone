#!/usr/bin/env bash
# PreToolUse(Bash) wrapper: fails closed if actually-done isn't installed, and fails
# closed (never relays a surprise exit code) if it crashes - the Python side already
# tries hard to only ever exit 0 or 2, but this is the gate's last line of defense.
set -uo pipefail

if ! command -v actually-done >/dev/null 2>&1; then
  echo "actually-done: the 'actually-done' CLI is not installed (uv tool install actually-done). Blocking this command until it is." >&2
  exit 2
fi

OUTPUT=$(actually-done hook pre-bash 2>&1)
RC=$?

if [ "$RC" -eq 0 ] || [ "$RC" -eq 2 ]; then
  if [ -n "$OUTPUT" ]; then
    echo "$OUTPUT" >&2
  fi
  exit "$RC"
fi

echo "actually-done: the push guard crashed unexpectedly (exit $RC). Blocking this command out of caution. Details: $OUTPUT" >&2
exit 2
