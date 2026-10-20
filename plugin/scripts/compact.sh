#!/usr/bin/env bash
# SessionStart(compact) wrapper: re-prints AGENTS.md's critical-rules block into
# context after compaction. Not a gate - CLAUDE.md/AGENTS.md are context, not
# enforcement (docs/DECISIONS.md, D6), so a failure here just means the block isn't
# re-injected this time. Always succeed; never disrupt the session over this.
set -uo pipefail

if command -v actually-done >/dev/null 2>&1; then
  actually-done hook compact 2>/dev/null && exit 0
fi

echo '{}'
exit 0
