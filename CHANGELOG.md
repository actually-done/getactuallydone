# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

### Added

- **Push guard** (`PreToolUse` hook): blocks `git push` in a Bash command however it's
  spelled, by tokenizing the command like a shell would rather than pattern-matching
  the literal text.
- **Fast tier** (`Stop` hook): runs affected tests (via `pytest-testmon` when
  installed) under a timeout when a source file changed, caches the result by
  working-tree hash, and blocks a fixable failure or halts when a human is needed
  (an edited/removed test file, three consecutive identical failures, or a broken
  verifier).
- **Full tier** (`actually-done verify --tier full --base <ref>`, intended as a
  required CI check): the decisive check. Every test file that exists in both the
  base branch and the PR runs with base's content; `conftest.py`/`pytest.ini`/
  `setup.cfg` are locked the same way; plugin autoload is disabled; a test-count
  drop against a base-collected baseline catches an unapproved deletion; oracle
  tests under `tests/oracle/` run separately under `--runxfail`.
- `actually-done approve <test-id>...` to record a human-approved test removal in
  `.verify/approved`.
- `actually-done init` to scaffold `.github/workflows/actually-done.yml`,
  `CODEOWNERS` entries for grader paths, `tests/oracle/`, an `AGENTS.md`
  critical-rules block imported into `CLAUDE.md`, and `[tool.actually-done]`
  defaults - idempotently, never overwriting existing content without `--force`.
- **Compact hook** (`SessionStart`, `compact` matcher): re-prints `AGENTS.md`'s
  critical-rules block into context after compaction.
- An attack suite (`tests/attacks/`) demonstrating the 7 attacks each piece exists to
  catch, with a table in `README.md` mapping each to its test.
- `scripts/clean_install_check.sh`: builds the wheel, installs it into a fresh venv,
  and smoke-tests the push guard and Stop hook against a clean, non-editable install.

### Known limitations

See `README.md`'s "What it doesn't guarantee" section and `ROADMAP.md` for the full
list - notably: local hooks steer but only the full-tier CI check actually decides; a
determined agent with the user's own credentials can still bypass this; a single
oracle test with one fixed input can be defeated by hardcoding that exact value; v0.1
is Python/pytest-only; and Windows coverage depends on which tool (Bash vs PowerShell)
runs a given command, not just the OS (`docs/DECISIONS.md`, D9).
