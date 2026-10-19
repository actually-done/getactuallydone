# Roadmap

v0.1 builds only what's in the build prompt's scope: push guard, fast-tier Stop hook,
`init` + CI full tier, the compact hook, and the attack suite. Everything below is
explicitly out of scope for v0.1 (per the build prompt) and un-researched against any
tool's current docs — re-verify before building, the way docs/DECISIONS.md did for
Claude Code's own hooks/permissions/plugins docs.

- **Mutation testing** (diff-scoped, e.g. mutmut/Stryker/PIT) — solution.md §1.
- **Size/complexity/duplication gates** (lizard, radon, jscpd, PMD CPD) — solution.md §2, §5.
- **Revert test in CI** (a changed-production-code PR must add a test that fails on base
  by assertion, with `new-feature`/`no-behaviour-change` label overrides) — solution.md §1.
- **Label checks** (human-only `large-change`, `new-feature`, `no-behaviour-change`,
  `tests-approved` labels, verified against the PR timeline's actor) — solution.md §2, §3.
- **Held-out tests** in a private repo, fetched only in a job the PR can't edit —
  solution.md §3.
- **Property-based tests** (Hypothesis / fast-check) wired into the fast or full tier —
  solution.md §1.
- **Change map** (public symbols changed, their callers, schema/route/dependency diffs) —
  solution.md §2.
- **Multi-agent adapters** (Cursor `beforeShellExecution`/`failClosed`, Codex hooks,
  Copilot CLI hooks) — solution.md's "one rulebook, one hook config per agent". Needs each
  agent's current hook docs fetched fresh, the way this repo fetched Claude Code's.
- **Bot identity / GitHub App setup, Code Owner review wiring, last-pusher-approval
  ruleset config** — solution.md's "Agent identity" and "Week 1" adoption step. This is
  mostly GitHub-side configuration a user does, not code `actually-done` ships, but a
  future version could add an `actually-done init --github` helper.
- **Quarantine for flaky tests** (owner + expiry, a scheduled job flagging expired ones).
