#!/usr/bin/env bash
# End-to-end "would a stranger's fresh install actually work" check:
#   1. build the wheel from this checkout
#   2. install it into a brand-new venv - no dev extras, no editable install
#   3. clone this checkout to a throwaway directory, as if it were a real clone
#   4. validate the plugin manifest and install it via the marketplace path
#      (claude plugin marketplace add / claude plugin install), if `claude` is
#      on PATH - otherwise this step is skipped with a clear message, since
#      `claude` isn't available in every environment this script might run in
#   5. run the push-guard + Stop-hook smoke test against a throwaway git repo,
#      and confirm `actually-done init`'s templates survive a real (non-editable)
#      wheel install
#
# Exits non-zero on the first failure. Everything happens under a throwaway temp
# directory that's removed on exit, so it's safe to run repeatedly.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

log() { printf '\n==> %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python
command -v "$PYTHON" >/dev/null 2>&1 || fail "no python3/python found on PATH"

log "Building the wheel from $REPO_ROOT"
"$PYTHON" -m pip install --quiet --upgrade build
( cd "$REPO_ROOT" && "$PYTHON" -m build --wheel --outdir "$WORK_DIR/dist" >/dev/null )
WHEEL="$(find "$WORK_DIR/dist" -name '*.whl' | head -n 1)"
[ -n "$WHEEL" ] || fail "no wheel produced"
echo "Built: $WHEEL"

log "Installing into a fresh venv (no dev extras, no editable install)"
VENV_DIR="$WORK_DIR/venv"
"$PYTHON" -m venv "$VENV_DIR"
if [ -f "$VENV_DIR/Scripts/activate" ]; then
  # shellcheck disable=SC1091
  source "$VENV_DIR/Scripts/activate"
else
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
fi
pip install --quiet "$WHEEL"
command -v actually-done >/dev/null 2>&1 || fail "actually-done not on PATH after a clean install"
actually-done --help >/dev/null 2>&1 || fail "actually-done --help failed after a clean install"
echo "OK: actually-done is installed and runs"

log "Confirming the clean install has no runtime dependencies (stdlib only)"
REQUIRES="$(pip show actually-done | sed -n 's/^Requires: *//p' | tr -d '[:space:]')"
if [ -n "$REQUIRES" ]; then
  fail "actually-done declares runtime dependencies it shouldn't: $REQUIRES"
fi
echo "OK: no runtime dependencies"

log "Cloning this repo to a throwaway directory"
CLONE_DIR="$WORK_DIR/clone"
# --no-hardlinks: a same-filesystem local clone otherwise tries to hardlink objects,
# which some Windows filesystem configurations refuse ("hardlink cannot be checked").
git clone --quiet --no-hardlinks "$REPO_ROOT" "$CLONE_DIR"

log "Confirming actually-done init's templates survive a real wheel install"
INIT_DIR="$WORK_DIR/init-check"
mkdir -p "$INIT_DIR"
( cd "$INIT_DIR" && git init -q && actually-done init >/dev/null )
[ -f "$INIT_DIR/.github/workflows/actually-done.yml" ] || fail "init did not write the workflow template from the installed wheel"
[ -f "$INIT_DIR/AGENTS.md" ] || fail "init did not write AGENTS.md from the installed wheel"
echo "OK: init's packaged templates are present in a clean, non-editable install"

if command -v claude >/dev/null 2>&1; then
  log "Validating the plugin manifest"
  claude plugin validate "$CLONE_DIR/plugin"

  log "Installing the plugin via the marketplace path"
  claude plugin marketplace add "$CLONE_DIR" --scope local
  claude plugin install actually-done@actually-done --scope local -y
  echo "OK: plugin installed from a local marketplace source"
else
  echo
  echo "SKIP: 'claude' not found on PATH - cannot run 'claude plugin validate',"
  echo "      'claude plugin marketplace add', or 'claude plugin install'."
  echo "      The remaining checks below exercise the actually-done CLI and the"
  echo "      hook scripts directly instead, bypassing Claude Code's own plugin"
  echo "      loading - run this script where 'claude' is installed for full coverage."
fi

log "Push-guard smoke test (the PreToolUse hook's own CLI command)"
PUSH_OUTPUT="$(printf '%s' '{"tool_input":{"command":"git -C . push origin main"}}' \
  | actually-done hook pre-bash 2>&1)" && PUSH_RC=0 || PUSH_RC=$?
[ "$PUSH_RC" -eq 2 ] || fail "push guard did not block 'git -C . push' (exit $PUSH_RC): $PUSH_OUTPUT"
echo "OK: push guard blocked git -C . push (exit 2)"

log "Stop-hook smoke test against a throwaway git repo"
TEST_REPO="$WORK_DIR/testrepo"
mkdir -p "$TEST_REPO/tests"
(
  cd "$TEST_REPO"
  git init -q
  git config user.email test@example.com
  git config user.name Test
  printf 'def add(a, b):\n    return a + b\n' > pkg.py
  printf '[tool.actually-done]\nunit_test_path = "tests"\n' > pyproject.toml
  printf "import sys\nsys.path.insert(0, '.')\nfrom pkg import add\n\ndef test_add():\n    assert add(1, 2) == 3\n" > tests/test_pkg.py
  git add -A
  git commit -q -m base
)
printf 'def add(a, b):\n    return a - b\n' > "$TEST_REPO/pkg.py"

STOP_OUTPUT="$(cd "$TEST_REPO" && echo '{}' | bash "$REPO_ROOT/plugin/scripts/stop.sh")"
echo "$STOP_OUTPUT" | "$PYTHON" -c "
import json, sys
body = json.load(sys.stdin)
assert body.get('decision') == 'block', f'expected a block, got: {body}'
print('OK: stop hook blocked the failing test')
"

log "All checks passed"
