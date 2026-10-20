#!/usr/bin/env bash
# One-time GitHub setup for a repo protected by actually-done:
#   1. Create a ruleset on the default branch requiring a PR, 1 approval with
#      stale-review dismissal and most-recent-push approval, Code Owner review,
#      the actually-done full-tier check (strict/up to date), and blocking force
#      pushes and deletions - with GITHUB_USERNAME as the sole bypass actor, in
#      pull-requests-only mode (solution.md's "Week 1" guidance: the human
#      maintainer, not the agent, can still merge without a second reviewer).
#   2. Replace CODEOWNERS' @YOUR-GITHUB-USERNAME placeholder with a real username,
#      as a normal file edit for you to review and commit yourself.
#   3. Print the created ruleset back via `gh api` so you can eyeball it.
#
# Usage:
#   scripts/setup_github.sh OWNER/REPO GITHUB_USERNAME [--dry-run] [--status-check NAME]
#
# --dry-run prints the ruleset JSON instead of calling the API (step 1 only -
# the CODEOWNERS edit in step 2 is a safe, uncommitted local change either way,
# left for you to `git diff`/commit, so it still happens in dry-run mode too).
#
# Requires: `gh`, authenticated (`gh auth status`) with admin rights on the repo.
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage: scripts/setup_github.sh OWNER/REPO GITHUB_USERNAME [options]

Options:
  --dry-run             Print the ruleset JSON payload instead of calling the
                         GitHub API. The CODEOWNERS edit still happens either
                         way - it's a local, uncommitted file change, not an
                         API call.
  --status-check NAME   Override the required status check's context. Default:
                         derived from the "verify" job's own `name:` field in
                         .github/workflows/actually-done.yml.
  -h, --help             Show this help.
USAGE
}

DRY_RUN=0
STATUS_CHECK_OVERRIDE=""
POSITIONAL=()

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --status-check)
      if [ $# -lt 2 ]; then
        echo "--status-check requires a value" >&2
        exit 2
      fi
      STATUS_CHECK_OVERRIDE="$2"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

if [ "${#POSITIONAL[@]}" -ne 2 ]; then
  usage
  exit 2
fi

REPO_SLUG="${POSITIONAL[0]}"
GITHUB_USERNAME="${POSITIONAL[1]#@}" # tolerate a leading @

case "$REPO_SLUG" in
  */*) : ;;
  *)
    echo "OWNER/REPO must be in owner/repo form, got: $REPO_SLUG" >&2
    exit 2
    ;;
esac

if [ -z "$GITHUB_USERNAME" ]; then
  echo "GITHUB_USERNAME must not be empty" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW_FILE="$REPO_ROOT/.github/workflows/actually-done.yml"
CODEOWNERS_FILE="$REPO_ROOT/CODEOWNERS"

# The "verify" job's own `name:` field becomes the check's display name in the
# GitHub UI/API, and - per GitHub's own "job names must be unique across all
# workflows, or results become ambiguous" warning (About protected branches) -
# that display name, not the job id or the workflow's own top-level name, is
# what required-status-check "context" strings match against. Derived from our
# own workflow file rather than hardcoded, so a future rename doesn't go stale
# silently; still worth confirming once real check runs exist (see the note
# this script prints below), since this project hasn't observed that directly.
_derive_status_check_name() {
  awk '
    /^  verify:/ { in_job = 1; next }
    in_job && /^  [a-zA-Z]/ { in_job = 0 }
    in_job && /^    name:/ {
      sub(/^    name:[[:space:]]*/, "")
      gsub(/^"|"$/, "")
      print
      exit
    }
  ' "$1"
}

if [ -n "$STATUS_CHECK_OVERRIDE" ]; then
  STATUS_CHECK_NAME="$STATUS_CHECK_OVERRIDE"
else
  if [ ! -f "$WORKFLOW_FILE" ]; then
    echo "Can't find $WORKFLOW_FILE to derive the status check name - pass --status-check NAME." >&2
    exit 1
  fi
  STATUS_CHECK_NAME="$(_derive_status_check_name "$WORKFLOW_FILE")"
  if [ -z "$STATUS_CHECK_NAME" ]; then
    echo "Could not derive the status check name from $WORKFLOW_FILE - pass --status-check NAME." >&2
    exit 1
  fi
fi

{
  echo "Required status check context: \"$STATUS_CHECK_NAME\""
  echo "(derived from the verify job's \"name:\" field - GitHub matches required"
  echo " status checks against a GitHub Actions job's display name, not its id or"
  echo " the workflow's own name; this project hasn't observed that against a real"
  echo " check run yet. After your first PR runs, confirm with:"
  echo "   gh api \"repos/$REPO_SLUG/commits/<sha>/check-runs\" --jq '.check_runs[].name'"
  echo " and rerun with --status-check \"<actual name>\" if it doesn't match.)"
  echo
} >&2

# The bypass actor is GITHUB_USERNAME, by resolved numeric GitHub user id, in
# "pull_request" mode - they can still merge their own PR without a second
# approval, but can't push straight past the ruleset outside a PR. GitHub's
# rulesets API requires actor_id for actor_type "User" (an *Integration*
# actor_id, by contrast, is the GitHub App's own id - irrelevant here since
# this bypasses a human, not the agent). Resolving the real id needs a `gh api`
# call, which --dry-run must never make (see test_dry_run_never_invokes_gh_at_all),
# so dry-run substitutes a placeholder and explains it below instead.
if [ "$DRY_RUN" -eq 1 ]; then
  BYPASS_ACTOR_ID=0
  {
    echo "Bypass actor: @$GITHUB_USERNAME (actor_id shown as 0 - --dry-run never calls"
    echo " \`gh\`, so the real numeric id isn't resolved; a real run looks it up via"
    echo " \`gh api users/$GITHUB_USERNAME --jq .id\`)."
    echo
  } >&2
else
  echo "Resolving GitHub user id for @$GITHUB_USERNAME..." >&2
  if ! BYPASS_ACTOR_ID=$(gh api "users/$GITHUB_USERNAME" --jq '.id'); then
    echo "Could not resolve a GitHub user id for '$GITHUB_USERNAME' - check the username and your \`gh auth status\`." >&2
    exit 1
  fi
fi

RULESET_PAYLOAD=$(cat <<JSON
{
  "name": "actually-done: require PR, full-tier CI, and Code Owner review",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [
    { "actor_type": "User", "actor_id": $BYPASS_ACTOR_ID, "bypass_mode": "pull_request" }
  ],
  "conditions": {
    "ref_name": {
      "include": ["~DEFAULT_BRANCH"],
      "exclude": []
    }
  },
  "rules": [
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": true,
        "require_last_push_approval": true,
        "required_review_thread_resolution": false
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "required_status_checks": [
          { "context": "$STATUS_CHECK_NAME" }
        ],
        "strict_required_status_checks_policy": true
      }
    },
    { "type": "non_fast_forward" },
    { "type": "deletion" }
  ]
}
JSON
)

if [ "$DRY_RUN" -eq 1 ]; then
  echo "$RULESET_PAYLOAD"
else
  echo "Creating ruleset on $REPO_SLUG..." >&2
  RULESET_ID=$(printf '%s' "$RULESET_PAYLOAD" | gh api \
    --method POST \
    -H "Accept: application/vnd.github+json" \
    "repos/$REPO_SLUG/rulesets" \
    --input - \
    --jq '.id')
  echo "Created ruleset id $RULESET_ID. Fetching it back for you to eyeball:" >&2
  gh api "repos/$REPO_SLUG/rulesets/$RULESET_ID"
fi

echo >&2
if [ ! -f "$CODEOWNERS_FILE" ]; then
  echo "No CODEOWNERS file found at $CODEOWNERS_FILE - run 'actually-done init' first, then rerun this script." >&2
  exit 1
fi

if ! grep -q "@YOUR-GITHUB-USERNAME" "$CODEOWNERS_FILE"; then
  echo "No @YOUR-GITHUB-USERNAME placeholder found in $CODEOWNERS_FILE - already replaced, nothing to do." >&2
else
  sed -i "s/@YOUR-GITHUB-USERNAME/@$GITHUB_USERNAME/g" "$CODEOWNERS_FILE"
  echo "Updated $CODEOWNERS_FILE: @YOUR-GITHUB-USERNAME -> @$GITHUB_USERNAME." >&2
  echo "This is a normal file edit, not committed for you - review and commit it yourself:" >&2
  echo "  git diff CODEOWNERS" >&2
  echo "  git add CODEOWNERS && git commit -m \"CODEOWNERS: set grader-path owner to @$GITHUB_USERNAME\"" >&2
fi
