# Setup

## 1. Install

```bash
uv tool install actually-done
```

## 2. Load the plugin

While developing, or to try it without a marketplace:

```bash
claude --plugin-dir ./plugin
```

To install it properly, add this repo as a marketplace and install from it:

```bash
claude plugin marketplace add <this-repo>
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
this, Code Owner review isn't actually enforced on the paths that matter.

## 4. Optional: a manual permission deny rule

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

## 5. Adoption order

Follow `solution.md`'s "Setup principles" section for the fuller picture. The short
version, in order of effort:

- **Day 1**: `actually-done init`, and make the full-tier check required in your
  branch's protection rules/ruleset. This is the actual gate - everything else is best
  effort until this is on.
- **Week 1**: give the agent its own bot identity (no Administration/Workflows access),
  turn on Code Owner review for the `CODEOWNERS` paths, require 1 approval with your own
  account as the only bypass actor, and narrow the push deny rule to protected branches.
  Without a separate agent identity, a determined agent running as you can still edit the
  workflow, the ruleset, or push directly - see `solution.md`'s "Agent identity" section.
- **Later**: mutation/property/held-out tests, the change map, and clone/complexity
  gates - see `ROADMAP.md` for what's not built yet and why.

## 6. Verify it's working

```bash
# Push guard: run this through the Bash tool in a Claude Code session and confirm it's blocked.
git -C . push

# Fast tier, run directly:
actually-done verify --tier fast

# Full tier, run directly against a real base ref:
actually-done verify --tier full --base main
```
