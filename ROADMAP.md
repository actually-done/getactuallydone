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

## Known trade-offs in v0.1's full tier (`full.py`)

Not out of scope exactly, but simplified in ways worth knowing about:

- **Oracle scoping**: every test under `tests/oracle/` runs with `--runxfail` and must
  pass, on every PR. solution.md's "oracle tests not yet targeted are pending: neither
  passed nor skipped" (scoping a PR to only the issue's own `tests/oracle/<id>/`
  directory) needs a PR↔issue linking convention this build prompt didn't specify.
  Workaround for now: keep `tests/oracle/` scoped to whatever's currently being worked.
- **pyproject.toml's `[tool.pytest.ini_options]` isn't merged from base.** The sandbox
  clears `addopts` entirely via `--override-ini=addopts=` (closing the main cheat
  surface: a PR quietly adding a disabling flag), but doesn't lock `markers`,
  `filterwarnings`, or other pytest-config keys to base's values, because `tomllib` is
  read-only and merging would need a TOML-preserving writer (e.g. `tomlkit`). Use
  `pytest.ini`/`setup.cfg` instead of pyproject.toml for pytest config if this matters to
  you today - those files ARE locked to base wholesale.
- **No escape hatch for pytest plugins full tier needs autoloaded** (pytest-asyncio and
  similar). `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` is unconditional. Add a
  `[tool.actually-done] full_tier_extra_plugins = [...]` setting, explicitly `-p` loaded.
- **Dependency install ordering** in the generated workflow (base's dev deps, then the
  PR's runtime deps) can conflict on a shared package; not resolved.
- **Python/pytest only.** `changes.py`/`full.py`'s source-vs-test classification and the
  whole sandbox-building approach assume pytest. solution.md also names
  `jest --findRelatedTests`/`vitest related` for JS/TS - a real port, not a config knob.
- **A PowerShell-matched push guard.** On Windows, the PowerShell tool runs alongside
  Bash by default even with Git for Windows installed, and our PreToolUse guard is
  matched to `"Bash"` only - a push issued through PowerShell isn't caught locally (the
  full-tier CI check still catches it; see `docs/DECISIONS.md`, D9). A second PreToolUse
  entry matched to `"PowerShell"`, parsing PowerShell's `git`-invocation syntax the way
  `guard.py` parses POSIX shell syntax, would close this.
