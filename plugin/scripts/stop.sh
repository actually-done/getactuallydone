#!/usr/bin/env bash
# Stop hook wrapper: always exits 0 with valid JSON. onFailure has no effect on Stop
# hooks (see docs/DECISIONS.md, D2), so this script traps every failure itself and turns
# it into a visible halt (systemMessage) instead of a silent pass.
set -uo pipefail

if ! command -v actually-done >/dev/null 2>&1; then
  echo '{"systemMessage":"actually-done: the actually-done CLI is not installed (uv tool install actually-done). Verification was skipped for this turn -- please check manually."}'
  exit 0
fi

OUTPUT=$(actually-done hook stop 2>&1)
RC=$?

if [ "$RC" -eq 0 ] && [ -n "$OUTPUT" ]; then
  echo "$OUTPUT"
  exit 0
fi

TAIL=$(printf '%s' "$OUTPUT" | tail -c 500 | tr -d '\r' | sed 's/\\/\\\\/g; s/"/\\"/g' | tr '\n' ' ')
echo "{\"systemMessage\":\"actually-done: the verifier crashed (exit $RC). Please check manually before trusting this turn. Details: $TAIL\"}"
exit 0
