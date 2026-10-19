# actually-done

Make your coding agent prove it's done. A Claude Code plugin and CI check built on one
principle: **the agent proposes, a check it can't edit decides.** Local hooks steer the
agent (a push guard, and a Stop hook that blocks on its own fixable test failures); a
required CI check that runs the base branch's own test files, config, and oracle tests
against the PR's code is what actually decides. See [`solution.md`](solution.md) for the
full design and [`docs/DECISIONS.md`](docs/DECISIONS.md) for where the current Claude
Code docs confirmed or refined it.

## Install

```bash
uv tool install actually-done
```

Then, from your project root, load the plugin (while developing or testing without a
marketplace):

```bash
claude --plugin-dir ./plugin
```

Or add this repo as a marketplace and install from it:

```bash
claude plugin marketplace add <this-repo>
claude plugin install actually-done
```

Finally, scaffold the CI workflow, CODEOWNERS entries, the oracle directory, and the
AGENTS.md rules block into your project:

```bash
actually-done init
```

`init` never overwrites an existing file's content unless you pass `--force`; it's safe
to re-run.

## What it does

- **Push guard** (`PreToolUse` hook): blocks `git push` in a `Bash` command however it's
  spelled - `git -C . push`, `env X=1 git push`, a push buried inside `sh -c "..."` - by
  tokenizing the command like a shell would, not pattern-matching the literal text.
- **Fast tier** (`Stop` hook): when a source file changed, runs the affected tests
  (`pytest-testmon` if installed, else the configured unit suite) under a timeout, and
  blocks the agent from finishing if they fail - unless the same failure has already
  blocked three times in a row with no progress, or the agent edited/removed a test file,
  in which case it halts for a human instead of fighting a problem it can't solve itself.
- **Full tier** (required CI check, `actually-done verify --tier full --base <ref>`):
  the decisive check. Every test file that exists in both the base branch and the PR
  runs with **base's content**, not the PR's - editing an assertion or adding a
  skip/xfail marker to dodge a failure changes nothing. Every `conftest.py`/
  `pytest.ini`/`setup.cfg` is locked the same way, and plugin autoload is disabled, so a
  cheat shipped as a test-config hook or an installed plugin doesn't work either. A
  deleted test isn't silently restored - it fails the check through a test-count drop,
  unless a human approved it (`actually-done approve <test-id>`). Tests under
  `tests/oracle/` (yours, the agent never edits them) run separately with `--runxfail`,
  so a still-unimplemented boundary case can't hide behind its own expected-failure
  marker.
- **Compact hook** (`SessionStart`, `compact` matcher): re-prints `AGENTS.md`'s
  critical-rules block into context after compaction, since CLAUDE.md/AGENTS.md are
  context the model can forget, not enforcement.

## What it doesn't guarantee

- **Local hooks steer; CI decides.** Between CI runs, only the push guard and Stop hook
  stand watch, and both run as the agent's own process - a sufficiently determined agent
  that can edit `.claude/settings.json`, the plugin itself, or the GitHub Actions
  workflow could weaken or remove them. The full-tier CI check is what actually can't be
  talked around, and only once `.github/` and the grader paths in `CODEOWNERS` require
  human review to change (see `solution.md`, "Agent identity").
- **A determined agent with your credentials can still bypass this.** CLI and IDE agents
  inherit your `gh` login and SSH keys. If the agent runs as you, it can push, merge its
  own PR, or edit the CI workflow as you. Give the agent its own bot identity with no
  Administration/Workflows access, and keep your own account as the only required
  approver, if you need this to hold against a motivated agent rather than an
  accidental one.
- **A single oracle test with one fixed input can be defeated by hardcoding that exact
  value.** `tests/oracle/` only helps if it's genuinely boundary-shaped (2-3 varied
  cases, per `solution.md` §1) - property tests and held-out tests close this gap
  further, and are out of scope for v0.1 (see `ROADMAP.md`).
- **v0.1 is Python/pytest-only, and doesn't merge `pyproject.toml`'s
  `[tool.pytest.ini_options]`** (it clears `addopts` but doesn't lock `markers`/
  `filterwarnings` to base). See `ROADMAP.md` for the full list of known trade-offs and
  what's deliberately out of scope.
- **On Windows, local hook coverage depends on which tool runs the command, not the OS.**
  Without Git for Windows, Claude Code uses the PowerShell tool instead of Bash, and none
  of the three hooks (all shell scripts) can even start. With Git for Windows installed,
  the PowerShell tool is *also* enabled by default, so a `git push` run through it instead
  of Bash still bypasses the push guard. Set `CLAUDE_CODE_USE_POWERSHELL_TOOL=0` if you
  need the push guard's local protection on Windows; the full-tier CI check is unaffected
  either way, since it runs on a Linux GitHub Actions runner regardless. See
  `docs/DECISIONS.md`, D9.

## The attack suite

`tests/attacks/` builds a small throwaway git repo per attack, applies it, and asserts
the right layer catches it. These are the tests that matter most - everything else is
infrastructure to make these true.

| Attack | Caught by | Test |
| --- | --- | --- |
| Agent edits an assertion to match its own buggy output | Full tier (base's test content always wins) | [`test_attack_01_edited_assertion.py`](tests/attacks/test_attack_01_edited_assertion.py) |
| Agent marks the failing test `@pytest.mark.skip` instead of fixing it | Full tier (same base-content-wins mechanism) | [`test_attack_02_skip_marker.py`](tests/attacks/test_attack_02_skip_marker.py) |
| Agent deletes the test that catches its bug | Full tier (test-count drop, unapproved) | [`test_attack_03_deleted_test.py`](tests/attacks/test_attack_03_deleted_test.py) |
| Agent adds a `conftest.py` hook that marks every result as passed | Full tier (base config locked, plugin autoload disabled) | [`test_attack_04_cheat_conftest.py`](tests/attacks/test_attack_04_cheat_conftest.py) |
| Agent special-cases one oracle test's exact input instead of generalizing | A second oracle test covering the same boundary class, run for real via `--runxfail` | [`test_attack_05_special_cased_production_code.py`](tests/attacks/test_attack_05_special_cased_production_code.py) |
| Agent pushes via `git -C . push` or `sh -c "git push"` | Push guard (PreToolUse hook) | [`test_attack_06_push_guard_bypass_attempts.py`](tests/attacks/test_attack_06_push_guard_bypass_attempts.py) |
| The verifier itself crashes | Stop-hook wrapper halts (never a silent pass) | [`test_attack_07_verifier_crash.py`](tests/attacks/test_attack_07_verifier_crash.py) |

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
mypy src
```

See [`docs/SETUP.md`](docs/SETUP.md) for the full setup and adoption-order walkthrough,
[`ROADMAP.md`](ROADMAP.md) for what's deliberately out of scope for v0.1, and
[`docs/DECISIONS.md`](docs/DECISIONS.md) for the research behind the hook/permission
design choices.
