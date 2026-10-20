# Setup

## 0. Requirements

- Python 3.11+
- Claude Code 2.1.295+, for the push guard's `onFailure: "block"` safety net
  (`claude --version`; `claude update` to upgrade). On an older version, a missing
  or crashing `actually-done` can fail *open* instead of blocking - see
  `docs/DECISIONS.md`, D2/D11. The plugin's session-start check warns about this
  automatically if it's not met.

## 1. Install

Use [`uv`](https://docs.astral.sh/uv/) or [`pipx`](https://pipx.pypa.io/), not
`pip install` into a project venv - the push guard and Stop hook run from Claude
Code's own process in whatever directory you're working in, so `actually-done` needs
to be on your global `PATH`, not just active inside one project's virtualenv.

```bash
uv tool install actually-done
# or
pipx install actually-done
```

## 2. Load the plugin

While developing, or to try it without a marketplace:

```bash
claude --plugin-dir ./plugin
```

To install it properly, add this repo as a marketplace and install from it:

```bash
claude plugin marketplace add actually-done/actually-done
claude plugin install actually-done
```

## 3. Scaffold your project

From your project's root (the repo you want protected, not this one):

```bash
actually-done init
```

This writes `.github/workflows/actually-done.yml`, `CODEOWNERS` entries for grader
paths, `tests/oracle/.gitkeep`, an `AGENTS.md` critical-rules block (imported into
`CLAUDE.md` via `@AGENTS.md`), and `[tool.actually-done]` defaults in
`pyproject.toml`. It's safe to re-run; it never overwrites a file's content unless you
pass `--force`.

Open `CODEOWNERS` and replace `@YOUR-GITHUB-USERNAME` with yourself or a team - without
this, Code Owner review isn't actually enforced on the paths that matter. Or let
`scripts/setup_github.sh` do it for you, along with the GitHub ruleset below.

## 4. GitHub ruleset

`scripts/setup_github.sh OWNER/REPO YOUR_GITHUB_USERNAME` (requires `gh`, authenticated
with admin rights on the repo) creates a ruleset on the default branch requiring a PR, 1
approval with stale-review dismissal and most-recent-push approval, Code Owner review,
the `actually-done` full-tier check (strict/up to date), and blocking force pushes and
deletions - with YOUR_GITHUB_USERNAME as the sole bypass actor, in pull-requests-only
mode (`bypass_mode: "pull_request"`), so you can still merge your own PR without a
second reviewer but can't push straight past the ruleset outside a PR. A real (non
`--dry-run`) run resolves your username to its numeric GitHub user id via
`gh api users/YOUR_GITHUB_USERNAME`; the agent's own identity is never covered by this
bypass entry, since it has a separate GitHub account per `solution.md`'s "Agent
identity" section. It also replaces CODEOWNERS' placeholder with your username, as a
normal file edit left for you to review and commit.

```bash
# Preview the ruleset JSON without calling the API:
scripts/setup_github.sh OWNER/REPO YOUR_GITHUB_USERNAME --dry-run

# Create it for real, and replace CODEOWNERS' placeholder:
scripts/setup_github.sh OWNER/REPO YOUR_GITHUB_USERNAME
```

The required status check's name is derived from the `actually-done.yml` workflow's
`verify` job's own `name:` field ("actually-done full tier"), on the assumption
(supported by GitHub's own "job names must be unique across all workflows" warning, but
not yet confirmed against a real check run by this project) that GitHub matches required
status checks against a job's display name, not its id or the workflow's name. After
your first PR runs, confirm with:

```bash
gh api "repos/OWNER/REPO/commits/<sha>/check-runs" --jq '.check_runs[].name'
```

and rerun with `--status-check "<actual name>"` if it doesn't match - the script prints
this same reminder every time it runs.

## 5. Optional: a manual permission deny rule

Plugins can't ship permission rules (`settings.json`'s `permissions` key has no effect
from a plugin - see `docs/DECISIONS.md`, D1), so the push guard's PreToolUse hook is the
real and only guard. As defense-in-depth, you can still add this to your own
`.claude/settings.json`:

```json
{
  "permissions": {
    "deny": ["Bash(git push:*)"]
  }
}
```

This catches the plain `git push ...` spelling one step earlier (before the hook even
runs), but **does not** catch `git -C . push`, `sh -c "git push"`, or similar - only the
hook does. Don't rely on this rule alone.

## 6. Windows

The push guard works with either the Bash tool or the PowerShell tool - it's matched to
`Bash|PowerShell` and invokes `actually-done` directly, with no shell wrapper of its
own. The Stop and compact hooks are still bash scripts, though, so they need Git for
Windows (which provides Git Bash) to run at all: without it, Claude Code uses the
PowerShell tool instead of Bash, and those two hooks don't fire - you still get the push
guard, just not the local fast-tier Stop hook.

To get all three hooks working locally on Windows, install
[Git for Windows](https://git-scm.com/downloads/win). It's optional either way: the
full-tier CI check is unaffected regardless, since it runs on a Linux GitHub Actions
runner no matter your local OS or tool choice, and remains the actual backstop. See
`docs/DECISIONS.md`, D9 and D11, for the full reasoning.

## 7. Adoption order

Follow `solution.md`'s "Setup principles" section for the fuller picture. The short
version, in order of effort:

- **Day 1**: `actually-done init`, and make the full-tier check required in your
  branch's protection rules/ruleset (`scripts/setup_github.sh`, step 4 above, does
  this). This is the actual gate - everything else is best effort until this is on.
- **Week 1**: give the agent its own bot identity (no Administration/Workflows access),
  turn on Code Owner review for the `CODEOWNERS` paths, require 1 approval with your own
  account as the only bypass actor, and narrow the push deny rule to protected branches.
  Without a separate agent identity, a determined agent running as you can still edit the
  workflow, the ruleset, or push directly - see `solution.md`'s "Agent identity" section.
- **Later**: mutation/property/held-out tests, the change map, and clone/complexity
  gates - see `ROADMAP.md` for what's not built yet and why.

## 8. Verify it's working

```bash
# Push guard: run this through the Bash tool in a Claude Code session and confirm it's blocked.
git -C . push

# Fast tier, run directly:
actually-done verify --tier fast

# Full tier, run directly against a real base ref:
actually-done verify --tier full --base main
```
