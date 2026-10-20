#!/usr/bin/env bash
# SessionStart(startup/resume) check: not a gate (SessionStart can't block anything)
# - this only warns, loudly, about two things that would otherwise fail silently:
#
#   1. actually-done isn't on PATH at all, so the push guard's PreToolUse hook
#      command can't even start - every Bash/PowerShell call gets blocked by
#      onFailure (see hooks.json, docs/DECISIONS.md D11), which is safe but
#      confusing without this warning.
#   2. The installed Claude Code is older than 2.1.295, the version floor
#      onFailure: "block" needs (D2, D11). Below that floor, a missing or
#      crashing actually-done can fail OPEN instead of blocking.
#
# Must never crash the session: always print valid JSON, and fall back to a
# generic warning on any error this script can't make sense of, rather than
# silently passing or producing invalid output.
set -uo pipefail

_ONFAILURE_FLOOR="2.1.295"

warn() {
  local escaped
  escaped=$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr '\n' ' ')
  printf '{"systemMessage":"%s"}\n' "$escaped"
}

if ! command -v actually-done >/dev/null 2>&1; then
  warn "actually-done: the 'actually-done' CLI is not on PATH. Every Bash/PowerShell command will be blocked by the push guard until it's installed - run: uv tool install actually-done (or: pipx install actually-done)."
  exit 0
fi

if ! command -v claude >/dev/null 2>&1; then
  warn "actually-done: could not find 'claude' on PATH to check its version. If it's older than $_ONFAILURE_FLOOR, the push guard's onFailure safety net isn't available and it can fail open - please check manually (claude --version)."
  exit 0
fi

if command -v timeout >/dev/null 2>&1; then
  CLAUDE_VERSION_RAW=$(timeout 5 claude --version 2>/dev/null) || {
    warn "actually-done: 'claude --version' failed or timed out. If Claude Code is older than $_ONFAILURE_FLOOR, the push guard's onFailure safety net isn't available and it can fail open - please check manually."
    exit 0
  }
else
  CLAUDE_VERSION_RAW=$(claude --version 2>/dev/null) || {
    warn "actually-done: 'claude --version' failed to run. If Claude Code is older than $_ONFAILURE_FLOOR, the push guard's onFailure safety net isn't available and it can fail open - please check manually."
    exit 0
  }
fi

VERSION=$(printf '%s' "$CLAUDE_VERSION_RAW" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n 1)

if [ -z "${VERSION:-}" ]; then
  warn "actually-done: could not parse a version number from 'claude --version' output ('$CLAUDE_VERSION_RAW'). If Claude Code is older than $_ONFAILURE_FLOOR, the push guard's onFailure safety net isn't available and it can fail open - please check manually."
  exit 0
fi

MAJOR=$(printf '%s' "$VERSION" | cut -d. -f1)
MINOR=$(printf '%s' "$VERSION" | cut -d. -f2)
PATCH=$(printf '%s' "$VERSION" | cut -d. -f3)
FLOOR_MAJOR=$(printf '%s' "$_ONFAILURE_FLOOR" | cut -d. -f1)
FLOOR_MINOR=$(printf '%s' "$_ONFAILURE_FLOOR" | cut -d. -f2)
FLOOR_PATCH=$(printf '%s' "$_ONFAILURE_FLOOR" | cut -d. -f3)

BELOW_FLOOR=0
if [ "$MAJOR" -lt "$FLOOR_MAJOR" ] 2>/dev/null; then
  BELOW_FLOOR=1
elif [ "$MAJOR" -eq "$FLOOR_MAJOR" ] 2>/dev/null && [ "$MINOR" -lt "$FLOOR_MINOR" ] 2>/dev/null; then
  BELOW_FLOOR=1
elif [ "$MAJOR" -eq "$FLOOR_MAJOR" ] 2>/dev/null && [ "$MINOR" -eq "$FLOOR_MINOR" ] 2>/dev/null \
  && [ "$PATCH" -lt "$FLOOR_PATCH" ] 2>/dev/null; then
  BELOW_FLOOR=1
fi

if [ "$BELOW_FLOOR" -eq 1 ]; then
  warn "actually-done: Claude Code $VERSION is older than $_ONFAILURE_FLOOR. The push guard's onFailure safety net needs $_ONFAILURE_FLOOR+ - on this version, a missing or crashing actually-done can fail OPEN instead of blocking. Please upgrade Claude Code (run: claude update)."
  exit 0
fi

echo '{}'
exit 0
