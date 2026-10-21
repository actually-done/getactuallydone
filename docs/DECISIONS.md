# Decisions: where current docs differ from or refine solution.md

Researched against the live Claude Code docs (code.claude.com/docs/en/{hooks,hooks-guide,permissions,plugins,plugins/manifest-reference,memory}) on 2026-10-19. Where the docs disagree with solution.md or the build prompt's assumptions, the docs win; each entry below says what we build as a result.

## D1: Plugins cannot ship permission/deny rules

`plugin.json`'s `settings` field (and a `settings.json` at the plugin root) only applies its `agent` and `subagentStatusLine` keys at load time — "other keys are dropped." A `permissions.deny` key in a plugin's settings has no effect. The build prompt anticipated this ("If plugins can't ship permission rules, document the line users should add").

**Decision:** the push guard is entirely the PreToolUse hook (Step 1). `actually-done init` and SETUP.md document one line for the user to add to their own `.claude/settings.json` (`"deny": ["Bash(git push:*)"]`) as defense-in-depth, with an explicit note that it's bypassable (see D8) and the hook is the real guard.

## D2: `onFailure: "block"` does not cover Stop hooks

Confirmed: `onFailure` is supported on `command` and `http` hook types (Claude Code v2.1.295+), and the docs state explicitly it "has no effect on ... Stop hooks." It does apply to `PreToolUse`.

**Decision:** set `onFailure: "block"` on the PreToolUse (push-guard) hook entry in `hooks/hooks.json`. For the Stop hook, we can't lean on `onFailure` — `plugin/scripts/stop.sh` itself must trap every error and always emit a valid blocking-or-halting JSON response (per solution.md's "wrapper that rarely fails open"), since Claude Code will only treat a crashed Stop hook as non-blocking otherwise.

## D3: Exit-code and JSON output semantics

Exit 2 blocks on `PreToolUse` and `Stop` (stderr is the reason shown to Claude); exit 0 with a JSON body (`{"decision":"block","reason":...}` for Stop, `{"hookSpecificOutput":{"permissionDecision":"deny",...}}` for PreToolUse) is the structured equivalent. Any other exit code is non-blocking.

**Decision:** `actually-done hook pre-bash` and `actually-done hook stop` print structured JSON on stdout and exit 0 for every outcome (allow/deny/block/halt), matching solution.md's "small adapter maps results to each agent's format." The wrapper shell scripts additionally trap unexpected crashes and fall back to exit 2 with a stderr reason, so a wrapper bug fails closed rather than silently passing.

## D4: Stop hook anti-loop fields

Confirmed stdin fields on `Stop`: `stop_hook_active` (bool — true when already continuing from a prior Stop-hook block), `last_assistant_message`, `background_tasks`, `session_crons`. Claude Code also enforces its own 8-consecutive-block cap, resetting on any tool call, independent of our logic.

**Decision:** `actually-done hook stop` checks `stop_hook_active` first and allows the stop immediately if true (never re-blocks on the same continuation), on top of solution.md's own "halt after ~3 consecutive failures with no progress" rule tracked via `state.py`'s attempt counter. These are two independent safety nets, not one.

## D5: SessionStart `compact` matcher exists

Confirmed `SessionStart` matcher values: `startup`, `resume`, `clear`, `compact`, `fork`. The Step 4 compact hook uses matcher `"compact"` exactly as specified.

## D6: AGENTS.md import syntax

Confirmed: CLAUDE.md files use `@path/to/import` syntax, e.g. `@AGENTS.md` on its own line, loaded into context at launch. Docs also state CLAUDE.md/AGENTS.md are "context, not enforced configuration" — confirming solution.md's framing and the project's core thesis for why PreToolUse hooks + CI (not CLAUDE.md) must carry the enforcement weight.

**Decision:** `actually-done init` writes an `AGENTS.md` critical-rules block and appends `@AGENTS.md` to `CLAUDE.md` (creating it if absent) rather than duplicating the rules inline.

## D7: Plugin file layout

Confirmed against the manifest reference and plugin overview: manifest at `plugin/.claude-plugin/plugin.json`; hooks at `plugin/hooks/hooks.json` (hooks.json lives at the plugin root's `hooks/` dir, *not* under `.claude-plugin/`); `${CLAUDE_PLUGIN_ROOT}` resolves to the plugin's installed directory and is available in `command`/`args` of hook entries; a repo can ship its own marketplace via `.claude-plugin/marketplace.json` at the repo root, letting users `claude plugin marketplace add <repo>`; `--plugin-dir ./plugin` loads a plugin straight from its folder with no marketplace, for local dev/testing. This matches the build prompt's planned layout exactly — no change needed.

## D8: Confirmed — Bash deny rules cannot catch respelled pushes

The permissions docs explicitly list `Bash(git push *)` as a deny rule that stops `git push origin main` but **not** `git -C . push origin main`, `git -c push.default=current push origin main`, or a quoted `git 'push' origin main` — and separately, wrapper-stripping rules don't cover `sh -c '...'` either, since that's a distinct program invocation, not a stripped wrapper. This directly validates the build prompt's Step 1 design: a PreToolUse hook that tokenizes the command and blocks on the *presence* of both `git` and `push` tokens anywhere (including inside `-c`/`sh -c` payloads), rather than a prefix-matching permission rule.

## D9: Windows coverage is narrower than "macOS/Linux only" or "Windows supported" - it's per-tool

Researched 2026-10-19 against code.claude.com/docs/en/setup. Git for Windows (which provides Git Bash) is **optional** on native Windows, not a prerequisite: "Without Git for Windows, Claude Code runs shell commands via the PowerShell tool" instead of the Bash tool. Our Stop and compact hooks still shell out to `bash "${CLAUDE_PLUGIN_ROOT}/scripts/*.sh"`; the push guard no longer does (D11).

**No Git for Windows at all**: the Stop and compact hook scripts can't even start (`bash`: command not found). The Stop hook's `onFailure` doesn't apply to Stop regardless (D2), so there is no guaranteed-present interpreter left to report "the hook couldn't run" for *those two* - this fails *silently*, not closed, for Stop/compact specifically. This is why D11 drops the bash dependency for the push guard rather than extending it: on exactly this configuration (no Git for Windows), Claude Code also auto-enables the PowerShell tool, which is precisely where push-guard coverage matters most.

**The push-guard gap this entry originally described is closed** (D11): the guard now parses PowerShell syntax too, matched via `"Bash|PowerShell"`, and no longer depends on `bash` being present to run at all. What remains open on Windows: if neither Git for Windows nor the PowerShell tool's own prerequisites are met in some unusual configuration, or on a Claude Code version older than the `onFailure` floor (D11), the push guard's fail-closed guarantee weakens to whatever Claude Code's own default is for an unreachable hook - still no *worse* than not having the plugin installed at all, but not a guarantee either. The Stop and compact hooks still need Git for Windows unconditionally; the full-tier CI check is unaffected either way, since it runs on a Linux GitHub Actions runner regardless of the developer's local OS or tool choice - it remains the actual backstop.

## D10: Fail-open audit - what's fixed, what's N/A, and the one case that can't be closed

Audited every hook handler (`pre-bash`, `stop`, `compact`) against: not installed,
python missing from `PATH`, malformed/empty stdin, verifier timeout, unwritable state
dir, and an unexpected crash. Tests: `tests/unit/test_fail_open_audit.py`.

**Two real bugs found and fixed, not just documented:**

- `hookio.read_payload` let `json.JSONDecodeError` propagate on malformed (not just
  empty) stdin. For `pre-bash`, an uncaught exception exits 1, and PreToolUse only
  blocks on exit 2 - exit 1 is "non-blocking error, the action proceeds" per the hooks
  reference. **Malformed stdin was silently letting the Bash command through.** Fixed
  by catching the decode error in `read_payload` and treating it as `{}`, and
  separately making `_cmd_hook_pre_bash` block (not allow) whenever it can't determine
  the command at all, since the hook's own matcher already guarantees it's only ever
  invoked for an actual Bash call.
- `state._save` let `OSError` (an unwritable `.actually-done/` dir) propagate up
  through `fast.run_fast_tier` and crash `_cmd_hook_stop`. First fix: made `_save`
  best-effort, swallowing the error everywhere - a real test failure was still
  reported and blocked even when nothing could be persisted. **That first fix was
  itself a fail-open bug**, caught on review: if `record_attempt`'s write silently
  never succeeds, the no-progress counter can never increment past 1, so it can
  never reach its 3-strikes threshold - the Stop hook would block the exact same
  failure forever. Claude Code's own 8-consecutive-block cap doesn't rescue this: it
  resets on every tool call, and the agent keeps calling tools while trying (and
  failing) to fix the problem. Corrected: `state.StateUnwritable` is raised by
  `_save` and now deliberately *not* swallowed by `record_attempt` - only by
  `set_cached_result` and `reset_attempts`, where a lost write is a pure
  optimization with no liveness consequence. `_cmd_hook_stop` catches it specifically
  and halts immediately (`"state dir not writable: <path>"`), never blocks, on the
  very first call - see `test_stop_halts_immediately_with_unwritable_state_dir`.

**Defense in depth added beyond the two real bugs**, since a security gate should fail
closed on *any* surprise, not just the ones found today: `_cmd_hook_pre_bash` and
`_cmd_hook_stop` each wrap their entire body in `try/except Exception`, converting any
crash into a block/halt respectively rather than relying solely on the shell wrapper to
catch it. `pre-bash.sh` no longer blindly relays `actually-done`'s exit code - only 0
and 2 pass through; anything else is treated as a crash and forced to exit 2.

**N/A, with reasons** (not fixed because there's nothing there to fix): "python missing
from `PATH`" and "verifier exceeds timeout" don't apply to `pre-bash`/`compact` - neither
spawns a subprocess at all (`guard.py` is pure string parsing; `compact` only reads a
file). "Unwritable state dir" doesn't apply to `pre-bash`/`compact` either - neither
touches `state.py`. For `stop`, "python missing from `PATH`" is a non-issue by
construction, not luck: `fast.py` spawns pytest via `sys.executable`, the absolute path
of the already-running interpreter, never a bare `"python"` resolved through `PATH` -
tested explicitly (`test_fast_tier_does_not_depend_on_python_being_on_path`) so a future
change can't regress it unnoticed.

**One case that genuinely can't be made to fail closed**: `compact` degrades to a clean
no-op (`{}`) on a crash or bad input, rather than halting. This is intentional, not a
gap - `SessionStart` doesn't gate anything, so there's no "stop" to force; a user just
doesn't get the critical-rules block re-injected that one time. Documented here per the
audit's own ask, not left as a silent assumption.

**The one failure mode no amount of code can close**: if `bash` itself doesn't exist at
all (no Git for Windows - D9), none of the three hook scripts can start, and there is no
cross-platform, dependency-free interpreter left to report that failure with. This is
why D9 settles for documenting the requirement rather than chasing a wrapper language
that would have the identical problem under a different name.

**Addendum - a transient lock isn't the same as "unwritable"**: on Windows specifically,
a sync client (OneDrive, Dropbox) or an antivirus scanner can hold a brief lock on a file
under `.actually-done/`, and once `record_attempt`'s write failure became a halt rather
than a silent degrade, a momentary lock risked being misreported as the same thing as a
genuinely unwritable directory - a false halt over nothing. `state._save` now retries a
few times (`_SAVE_RETRY_ATTEMPTS`, `_SAVE_RETRY_DELAY_SECONDS`) before raising
`StateUnwritable`, confirmed to both recover from a one-shot transient failure
(`test_record_attempt_survives_a_transient_lock`) and still correctly raise once the
directory is genuinely blocked (the existing `test_record_attempt_raises_state_unwritable`,
which creates a file where the directory needs to go - that doesn't clear on retry).
Found by testing this repo's own checkout, which happens to sit under OneDrive at a path
containing a space (`C:\Users\...\OneDrive\...`) - exactly the two conditions (OneDrive
sync, a space in the path) worth testing on Windows before trusting either claim.

## D11: Push guard extended to the PowerShell tool

Researched 2026-10-20 against code.claude.com/docs/en/tools-reference and hooks. Confirmed: `PowerShell` is the tool's exact canonical name (for matchers and permission rules), its `tool_input` carries `command` in the same shape as Bash's (`command`, `description`, `timeout`, `run_in_background`), and the docs explicitly recommend matching `"Bash|PowerShell"` in hooks that inspect shell commands, "since matching Bash alone is not enough." This is reliable, documented, first-class surface - not a case to decline on.

**What changed:**

- `guard.py` gained a second, PowerShell-syntax-aware walk (`check_push_for_tool`, dispatching on `tool_name`): a hand-written tokenizer for PowerShell's lexical rules (backtick escapes, single-quote-is-always-literal, `&` as the call operator, `;`/`|`/`&&`/`||` as statement separators - shlex doesn't apply, it's POSIX-specific), then the same dialect-agnostic `_git_subcommand_is_push` check once it's identified the actual `git` invocation. Handles the call operator (`& git push`, `&"git.exe" push` with no space), `cmd /c`/`cmd.exe /c` (recursing into the embedded string, mirroring `sh -c` on the POSIX side), and `Start-Process` (whose target and arguments are separate parameters rather than one command string - matched heuristically, by checking for `git`/`git.exe` and an exact `push` word among the remaining tokens, split on both commas and whitespace since `-ArgumentList` accepts either an array or a single string). Tests: `tests/unit/test_guard.py`.
- `hooks.json`'s PreToolUse matcher is now `"Bash|PowerShell"`.
- **The push guard's shell wrapper is gone.** `plugin/scripts/pre-bash.sh` is deleted; `hooks.json` now invokes `actually-done hook pre-bash` directly (exec form: `"command": "actually-done", "args": ["hook", "pre-bash"]`), relying on `onFailure: "block"` for "not installed" and "crashed with an unexpected exit code" - both already listed as `onFailure` failure conditions (D2). This is a deliberate trade-off, not an oversight: wiring the PowerShell matcher onto the *existing* `bash pre-bash.sh` wrapper would have meant the guard still couldn't run at all on exactly the configuration where PowerShell coverage matters most - native Windows without Git for Windows, where there is no `bash` to invoke the wrapper with in the first place (D9). A Python-script wrapper wouldn't have fixed this either: it has the identical bootstrapping problem (an interpreter that might not be on `PATH`), for no gain over `onFailure` doing the same job natively.
- This means the push guard's fail-closed guarantee for "not installed"/"crashed" now requires Claude Code v2.1.295+ (the `onFailure` floor). On an older version, those two failure modes default to whatever Claude Code does for a hook it can't run - likely non-blocking, same as not having the plugin installed at all. This is not a new regression (the full-tier CI check was never conditioned on the local hook anyway), but it is a real, version-dependent boundary worth stating plainly rather than implying the old wrapper's guarantee still holds.
- `extract_bash_command` is renamed `extract_command` (it always read a tool-agnostic field; the name was just stale once a second tool used it).

**Known remaining limitation**: `Start-Process`'s heuristic match (co-occurrence of `git`/`git.exe` and an exact `push` word, not precise named-parameter parsing) could in principle be evaded by splitting the argument string into pieces that only concatenate to "push" at runtime (e.g. two separate `-ArgumentList` values joined later) - an absurd, deliberately-evasive case not chased here, in the same spirit as the POSIX side's own documented gaps (D8). `cmd /c`'s recursive re-tokenization uses the same tokenizer for the embedded string as the outer PowerShell statement, which is close enough for the spellings this guards against but isn't a true cmd.exe parser (cmd.exe has its own distinct escaping rules, e.g. `^` as its escape character, not modeled here).

## D12: Session-start check for the onFailure floor and a missing install

D11's push guard dropped its bash wrapper in favor of invoking `actually-done`
directly and relying on `onFailure: "block"` (Claude Code 2.1.295+) for "not
installed"/"crashed". That trade-off is invisible to a user on an older Claude Code,
or one who hasn't installed `actually-done` yet at all - the push guard would simply
never fire, with nothing to say why. A `SessionStart` hook matched to
`startup|resume` (not `compact`, which is the existing rules-reinjection hook) now
checks both conditions and warns via `systemMessage` when either holds.

**Why a separate bash script, not an `actually-done hook` subcommand**: the whole
point is to detect "`actually-done` isn't on `PATH`" - if the check itself were
`actually-done hook session-check`, it would have the identical bootstrap problem
it exists to report (the hook command couldn't start either). `plugin/scripts/
session-start-check.sh` is dependency-free: `command -v` for both presence checks,
and `claude --version` output parsed with `grep -oE` and compared numerically
component-by-component against `2.1.295` - plain string comparison would wrongly
order e.g. `2.1.9` above `2.1.10`. This is consistent with the Stop/compact hooks'
existing bash dependency (D9), not a new category of gap.

**Correction - `timeout` isn't portable enough to trust by name alone**: the first
version wrapped `claude --version` in `timeout 5` whenever `command -v timeout`
succeeded. Two real problems with that, caught on review before release:

- Stock macOS has no `timeout` command at all - only `gtimeout`, from
  `brew install coreutils`. The original code would just run `claude --version`
  unbounded there, silently losing the time limit.
- On Windows, Git Bash's `PATH` typically carries *two* programs named `timeout`:
  Git's own GNU coreutils one (`.../Git/usr/bin/timeout.exe`) and Windows' native
  `C:\Windows\System32\timeout.exe` - a completely different tool that waits N
  seconds or for a keypress and does not run-and-kill a command at all. Which one
  `command -v timeout` finds depends on `PATH` order, which varies by machine -
  confirmed on this project's own dev machine: `where timeout` lists both, with
  Git's version first in that particular `PATH`, but nothing guarantees that order
  for every user. Running `timeout 5 claude --version` against the native one would
  not behave as a 5-second limiter at all.

Fixed by detecting a real limiter *behaviorally*, not by name: try `timeout` then
`gtimeout`, and accept a candidate only if `<candidate> --version </dev/null` prints
"GNU coreutils" - confirmed safe to probe this way, since the native Windows
`timeout.exe` rejects `--version` immediately (`ERROR: Invalid value for timeout (/T)
specified`, exit 1) rather than hanging or waiting on stdin. When neither candidate
qualifies, a pure-bash fallback (`_run_with_timeout_fallback`) backgrounds the
command, polls once a second, and kills it after 5 seconds, returning the same exit
code (124) GNU `timeout` would. All three paths - real GNU `timeout`, `gtimeout`-only,
and no limiter at all - are tested against a genuinely slow `claude` stub and checked
against actual elapsed time, not just output shape: matching output alone doesn't
prove which code path enforced the cutoff, only that one of them eventually did
(`tests/unit/test_session_start_check.py`). Also checked for an orphaned background
process after a kill on this machine; found none.

**Never crashes the session**: every branch - `actually-done` missing, `claude`
missing, `claude --version` failing or timing out, or its output not containing a
parseable version number - prints a valid `{"systemMessage": "..."}` and exits 0,
never letting an unexpected shell error (e.g. `[: integer expression expected` from a
malformed comparison) propagate past stderr. Tested directly via stubbed
`actually-done`/`claude` executables on a controlled `PATH`, including the exact
semver boundary (`2.1.294` warns, `2.1.295` and `2.2.0` and `3.0.0` don't) -
`tests/unit/test_session_start_check.py`.

**Config regression test**: `tests/unit/test_hooks_json.py` parses `hooks.json`
directly and asserts the PreToolUse matcher covers exactly `{Bash, PowerShell}`, every
PreToolUse hook entry has `onFailure: "block"`, and it still invokes `actually-done`
directly rather than a wrapper - so a future edit that silently drops any of D11's
three properties fails CI instead of waiting to be noticed in a diff.

## D13: `scripts/setup_github.sh` - ruleset payload, status-check naming, and empty bypass

Researched 2026-10-20 against docs.github.com's REST API reference for repository rulesets. Confirmed field shapes directly from the docs rather than guessed: top-level `name`/`target`/`enforcement`/`bypass_actors`/`conditions`/`rules`; `conditions.ref_name.include` accepting the literal string `~DEFAULT_BRANCH`; the `pull_request` rule's `required_approving_review_count`/`dismiss_stale_reviews_on_push`/`require_code_owner_review`/`require_last_push_approval` as required booleans/integer; the `required_status_checks` rule's array of `{context, integration_id?}` plus `strict_required_status_checks_policy`; and `non_fast_forward`/`deletion` as parameter-less rule types.

**What's confirmed vs. inferred**: the payload shape above is directly from GitHub's docs. The required status check's `context` string - the name GitHub matches against a GitHub Actions job - is **not** directly confirmed by this project against a real check run (the repo has no PR history yet). It's inferred from GitHub's "About protected branches" warning that "job names must be unique across all workflows" and that duplicates "can cause ambiguous status check results" - which only makes sense if the job's display `name:` field (here, `"actually-done full tier"`, from the `verify` job), not its id or the workflow's own name, is the matching key. `setup_github.sh` derives this from the workflow file itself rather than hardcoding it, prints an explicit note every run pointing at `gh api ".../check-runs" --jq '.check_runs[].name'` to confirm after the first real PR, and accepts `--status-check NAME` to override if the assumption turns out wrong.

**Empty `bypass_actors`, as asked, departs from solution.md's own "Week 1" and "Solo developers" guidance**: solution.md recommends the human's account as the *sole* bypass actor specifically so a solo maintainer can still merge their own PRs without the agent's PR process; an empty bypass list blocks that too - the repo owner gets no self-merge shortcut, even on their own change. This is what was asked for here (maximum strictness, no exceptions), not an oversight, but worth knowing before enabling it: a human-authored, same-day fix would also need to go through a PR.

**Correction - empty bypass actually makes the repo unmergeable, not just stricter**: the combination asked for above (empty bypass + `required_approving_review_count: 1` + `require_last_push_approval: true`) doesn't just remove a convenience shortcut - it locks out a solo maintainer entirely. `require_last_push_approval` means the *most recent* pusher's own approval never counts, so a lone maintainer's PR can never reach 1 qualifying approval from anyone else, and with no bypass actor there's no second person who can supply it. Fixed per solution.md's actual guidance: `bypass_actors` now holds exactly one entry, `{"actor_type": "User", "actor_id": <GITHUB_USERNAME's numeric id>, "bypass_mode": "pull_request"}`.

Re-checked against docs.github.com's rulesets reference for the allowed `actor_type`/`bypass_mode` values rather than guessed:

- `actor_type` is one of `Integration`, `OrganizationAdmin`, `RepositoryRole`, `Team`, `DeployKey`, `User`. `User` - a single named individual - is valid, so there's no need to fall back to a role-based actor like `RepositoryRole: admin` (which would be coarser: it covers *any* admin, not just one named person).
- `bypass_mode` is one of `always` (default), `pull_request` ("the actor can only bypass rules on pull requests" - not available to `DeployKey`), or `exempt` (the rule doesn't run for that actor at all, with no audit trail). `pull_request` is solution.md's "pull-requests-only mode" and is what's used here: GITHUB_USERNAME can still merge their own PR without a second reviewer, but can't push straight past the ruleset outside a PR.
- `actor_id` is required for `Integration`, `RepositoryRole`, `Team`, and `User`. For `User` it's the account's numeric GitHub user id (`gh api users/<username> --jq .id`), not the login string.

**Exactly who this bypass entry covers**: the one GitHub account named by `GITHUB_USERNAME`, resolved to its numeric id - nobody else, and no role (so a future admin added to the repo isn't automatically covered). The agent/bot identity is never covered by this entry as long as it uses a separate GitHub account from the human maintainer's, per solution.md's "Agent identity" section and `SETUP.md`'s "Week 1" step - this script doesn't and can't enforce that separation itself, since it only ever resolves and bypasses the one username it's given.

Resolving the real numeric id needs a `gh api users/<username>` call, which conflicts with the existing, tested guarantee that `--dry-run` never calls `gh` at all (`test_dry_run_never_invokes_gh_at_all`). Resolved by only doing the lookup in real (non-`--dry-run`) runs; `--dry-run` prints a placeholder `actor_id` of `0` with an explicit stderr note that it's a placeholder, not a real id, and shows the `gh api` command a real run would use. Tests: `test_dry_run_bypass_actor_placeholder_is_explained`, `test_real_run_resolves_bypass_actor_id_via_gh` (stubs `gh` entirely to verify the real id ends up in the payload), `test_real_run_errors_when_user_lookup_fails`.

## D14: `actually-done.yml` - handling a bootstrap base with no project yet

Found on PR #1 (`v0.1-build` -> `main`), the PR that introduces this project to its own repo: `origin/main` was still GitHub's bare initial commit (`LICENSE` + a 2-line `README.md`, no `pyproject.toml`), so "Install the grader from base" failed immediately — `pip install -e ".[dev]"` against a directory with no `setup.py`/`pyproject.toml` errors out, not a bug in the PR's own code. The full-tier check is designed to verify a PR against an *existing* project on base; a bootstrap PR has no such base to defend yet, so there's nothing for the check to meaningfully run.

**Decision:** added a `Check whether base has a project yet` step that tests `base/pyproject.toml` for existence and skips grader install, PR-dependency install, and verification when it's absent, instead posting an `evidence.md` explaining the skip. This can't be gamed by a PR's own content: the condition reads `base`'s checkout, fixed to `github.event.pull_request.base.sha`, which a PR's changes never touch. Once this PR merges, `main` has a real `pyproject.toml` and every subsequent PR runs the full check normally — this bootstrap path only ever fires once, for the PR that creates the project.

**Correction - that "skip" reported success, and `pyproject.toml` was the wrong signal to key it on**: caught on the real PR #1 run. Two separate problems, both real:

1. A skipped `verify` job exits 0 — GitHub shows a green check. Nothing distinguished "skipped because this is a real bootstrap" from "skipped (or ran and found nothing) for any other reason" in the check's own status; only the evidence comment said anything, and a human scanning PR checks reads the green mark first. Any repo that for whatever reason lacked `pyproject.toml` on base — not just this project's own literal first commit — would get a permanent, silent, always-green check that verified nothing, forever.
2. `base/pyproject.toml` is also the *wrong* bootstrap signal on its own terms: a base that already runs this workflow (so it's genuinely not a bootstrap case) but happens to lack a `pyproject.toml` — or a PR that deletes its own — would also hit the skip path and get waved through unverified, when it should instead fall back to defaults and actually check base's tests for real. `full.py`'s `run_full_tier` never reads `pyproject.toml` itself (it takes an already-resolved `Config`), and `config.load_config` already defaults cleanly when the file or the `[tool.actually-done]` section is missing (`Config()`) — the only reason base lacking a manifest broke anything was the workflow step that hard-installs from it.

**Decision, replacing the one above:**

- **Bootstrap redefined** as base lacking `.github/workflows/actually-done.yml` itself (read from base's checkout, same as before — a PR's own changes can't touch it), not `pyproject.toml`. This only ever fires for the literal PR that first introduces actually-done's CI to a repo; every PR after that has the workflow file on base. The skip path still reports success (there's genuinely nothing on base yet to defend), but the posted evidence now says **"BOOTSTRAP: not verified"** up front, in bold, so a human can't mistake the green check for a real result without reading past the first line.
- **A non-bootstrap base missing `pyproject.toml` is no longer a skip.** `Install the grader from base` now only runs when base has a manifest to pin from; when it doesn't (but isn't a bootstrap base), a new `Install a default grader` step installs unpinned `pytest` instead, and verification still runs against base's real tests via whatever `config.load_config` defaults to.
- **Zero tests collected is now a hard fail, not a vacuous pass** (`src/actually_done/full.py`, `run_full_tier`): previously, if the real pytest run collected zero tests *and* base's own baseline collected zero too, the function fell through to `FullResult(status="pass", summary="0 test(s) passed")` — a report that reads exactly like a real, clean pass. Added a check for `report.tests == 0 and baseline_count == 0` that fails instead, with a summary naming the configured `unit_test_path`. Gated on `baseline_count == 0` specifically (not just `report.tests == 0`) so an *approved* removal that legitimately brings the count to zero — where `baseline_count` was positive before the approval — still passes, per `test_approved_removal_is_excluded_from_the_count`. As a side effect, this also closes a sharper version of the same hole: since `config` is loaded from the PR's own `pyproject.toml` (`cli.py`'s `_cmd_verify`), a PR could in principle repoint its own `unit_test_path` to somewhere empty to blind the check against base too — now caught the same way.
- Both the shipped template (`src/actually_done/templates/workflow.yml`) and this repo's own dogfooded copy (`.github/workflows/actually-done.yml`) are fixed and kept byte-identical, as `actually-done init` always writes them — the previous fix only touched the dogfooded copy, leaving the template (what every other adopter actually gets from `init`) on the old, unconditional, hard-fail-on-missing-manifest behavior.

Tests: `tests/unit/test_full.py::test_zero_tests_collected_fails_instead_of_passing_vacuously`, `::test_runs_against_base_tests_even_when_base_has_no_pyproject_toml`; `tests/unit/test_cli.py::test_verify_full_tier_still_uses_base_tests_when_pr_deletes_pyproject_toml`. The bootstrap-detection YAML change itself isn't unit-testable locally (no local GitHub Actions runtime) — validated by re-running real CI on PR #1, which is still the one case currently able to exercise the true-bootstrap path for real.

## D15: `fast.py` inheriting a disabled plugin autoload broke its own `--testmon`

Found on PR #2's real CI run, once the full-tier check was actually running for real (D14's correction) for the first time: `tests/unit/test_fast.py`, `test_cli.py`'s hook-stop tests, and one `test_fail_open_audit.py` test all failed with `pytest exited with code 4` / `KeyError: 'decision'` - but only inside CI's sandboxed verification, never locally.

Root cause, confirmed by reproducing it directly: `fast.py`'s `_run_pytest` spawns `python -m pytest ... --testmon` (appended whenever `importlib.util.find_spec("testmon")` succeeds) via `subprocess.run` with no explicit `env=`, so it inherits whatever environment called it. `full.py`'s own `_pytest_env()` sets `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` on the sandboxed pytest run it spawns (deliberately, so a PR's own conftest.py/plugins can't influence the full-tier result) - and once this project's own CI started actually running that sandboxed run for real, every test inside it that itself calls `fast.run_fast_tier` inherited that same env var. `find_spec("testmon")` only checks the package is importable, not that pytest will actually register it as a plugin - with autoload disabled, pytest instead rejects `--testmon` as an unrecognized argument (exit code 4, a usage error), which `_run_pytest` reports as `status="error"` rather than a real pass/fail.

**Decision:** `fast.py` now has its own `_pytest_env()` that pops `PYTEST_DISABLE_PLUGIN_AUTOLOAD` before spawning its subprocess, regardless of what it inherited - this subprocess only ever runs this project's own trusted test suite, so it has no reason to inherit a lockdown meant for grading an untrusted PR's sandbox. Reproduced locally (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest ... --testmon` → the exact same "unrecognized arguments: --testmon", exit 4) and confirmed the fix resolves it, both directly and via a new regression test, `test_fast.py::test_not_broken_by_an_inherited_plugin_autoload_lockdown` (which first reproduced the failure against the unfixed code, then passed against the fix).

**A second, related production bug surfaced right after fixing the first one**: with `fast.py`'s env fix in place, `test_result_is_cached_by_working_tree_hash` started failing for a *different* reason - its first assertion (`first.status == "fail"`) had always masked a second bug further down the same test, never reached before. First hypothesis, fixed but **not actually the cause of this specific CI failure**: `pytest-testmon`'s `db.py` opens its SQLite database with `PRAGMA journal_mode = WAL`, which can leave `-wal`/`-shm` sidecar files next to `.testmondata`, and `changes.py`'s `_is_tool_owned` only exact-matched the literal name `.testmondata`. Real and worth fixing (confirmed against testmon's own source, not guessed), but pushing it didn't clear the CI failure.

**Actual root cause**, found by adding a diagnostic test (in its own *new* file - a test added inside `test_fast.py` is invisible to the real full-tier run, since that file already exists on base and the sandbox always substitutes base's content for any file on both sides; see below) that printed the raw `git status --porcelain` on a cache miss: `__pycache__/` directories, created as a side effect of Python importing `pkg.py`/`test_pkg.py` during the pytest run. `_is_tool_owned` never excluded these at all, at any depth. They never surfaced locally because a local/global `.gitignore` already hides `__pycache__` on most dev machines - and git never reports an *already-ignored* path as untracked, so `changed_files` (which relies on `git status` to notice anything in the first place) simply never saw it there. A clean CI checkout, with no such gitignore, saw it as genuinely untracked, feeding it into `working_tree_hash` on every run and permanently busting the fast-tier cache on any repo lacking that convenience gitignore.

Fixed by matching `.testmondata`'s prefix family (`-wal`/`-shm`/`-journal`) *and* `__pycache__`/`.actually-done` as path components at any depth, not just a root-level prefix. New tests: `test_changes.py::test_changed_files_hides_testmon_wal_sidecar_files`, `::test_working_tree_hash_ignores_testmon_wal_sidecar_files`, `::test_changed_files_hides_pycache_at_any_depth`, `::test_working_tree_hash_ignores_pycache_at_any_depth` - each verified to fail against the unfixed code first.

**A methodological note worth keeping**: a test added to an *existing* test file is invisible to this project's own full-tier CI check, exactly like an edit would be - `full.py` always substitutes base's content for any file that exists on both sides, new test functions included. A diagnostic meant to actually run as part of a real CI verification has to live in a brand-new file path; `tests/unit/test_fast_cache_ci_diagnostics.py` is that diagnostic (temporary, meant to be deleted once this is fully settled in production).

**Three further test failures from the same CI run were left alone, not fixed**: `test_repo_slug_consistency.py`'s `git ls-files` call errors inside `full.py`'s own sandbox (which deliberately excludes `.git` from what it copies); `test_setup_github_sh.py::test_dry_run_never_invokes_gh_at_all` and `test_fail_open_audit.py::test_fast_tier_does_not_depend_on_python_being_on_path` both strip PATH directories by name-collision (`gh`, `python`/`python3`) that, on GitHub's hosted Ubuntu runners, also hold `dirname`/`git`/coreutils in the same directory (`/usr/bin`) - stripping the whole directory breaks far more than the one program the test meant to remove. All three are bugs in the *tests themselves* (pre-existing, already merged via PR #1), not in the production code they exercise - AGENTS.md is explicit that an existing test can't be edited to make it pass, only a human can fix or `actually-done approve` these. Left for the human to triage.

## D16: Evidence truncation hid failure detail (tests 1 and 3 of D15's triage)

While triaging D15's three pre-existing test-only failures, two of them (`test_repo_slug_consistency.py`, `test_fail_open_audit.py::test_fast_tier_does_not_depend_on_python_being_on_path`) showed only a one-line pytest summary in the posted evidence, with no traceback at all - `render_evidence_markdown` only ever includes `_tail(output)`, the raw pytest stdout/stderr's last 80 lines, and later failures in the same run pushed these two out of that window entirely.

**Decision:** `_parse_junit` now extracts every `<testcase>` with a `<failure>`/`<error>` child directly from the JUnit report (`_junit_failures`) - each test's id (`file::name` when pytest's `--junitxml` provides a `file` attribute, else `classname::name`, since this project's pytest version doesn't emit one) and the first non-blank line of its message. `Evidence.failing_tests` carries this, and `render_evidence_markdown` renders it unconditionally, before the existing tail - so every failing test and a real summary of what broke survives regardless of how much other output precedes it, with the raw tail still available underneath for full context. New test: `test_full.py::test_failing_test_evidence_survives_output_truncation`.

## D17: `setup_github.sh`'s `sed -i` wasn't portable to macOS

Caught immediately by the new `ci.yml` matrix (D15/D16's own motivation): 7 of `test_setup_github_sh.py`'s tests failed on `macos-latest` only, all with `sed: 1: "...": invalid command code v`. Root cause: `sed -i "s/.../.../" "$file"` is a classic GNU-vs-BSD portability trap - GNU sed (Linux, what this project had only ever run on) accepts a bare `-i` with no backup suffix; BSD sed (macOS) requires `-i ''` as a *separate* argument and otherwise treats the next argument (here, the sed script itself) as the backup suffix, then tries to parse the file path as the actual script - hence "invalid command code v" (from a `v` somewhere in the runner's temp path).

**Decision:** replaced `sed -i ... "$file"` with `sed ... "$file" > "$file.tmp" && mv "$file.tmp" "$file"`, sidestepping the `-i` flag's incompatible spellings entirely rather than trying to detect which sed flavor is running. Works identically on GNU, BSD, and Windows Git Bash. No new test needed - the existing `test_setup_github_sh.py` suite already exercises this path; it just needed to actually run on macOS once, which `ci.yml` now ensures happens on every PR.

## D18: `.verify/approved` bound a bare id, not the content it was approved for

Flagged by the human before ever running `actually-done approve` on this repo: `read_approved`/`_cmd_approve` stored and matched on the bare test-id string alone - no hash, no file reference, no PR/SHA. `common_tests` already locks any EXISTING test file's content to base's own, so a PR editing an approved test's assertions couldn't actually take effect in the sandbox either way - but the approval was never checked against *what it was approved for* at all, only whether the id string matched.

**Confirmed as a real, exploitable gap, not a theoretical one** - reproduced directly: approved `tests/test_pkg.py::test_add` under the old bare-id scheme, then let base move on past that approval (the same file later edited to be genuinely broken, as an unrelated merge would), and ran `actually-done verify --tier full` against that later base state. Result: **`exit_code 0`, "0 test(s) passed"** - the broken test was silently deselected and waved through as a clean pass, forever, because nothing ever checked whether the approval still described the test it was granted for. Two live paths this opens: (1) a stale approval for a test that's since been edited or whose file no longer exists still discounts `expected_minimum = baseline_count - len(approved)` by one, permanently, letting an unrelated later PR delete a different, currently-passing test and ride on that stale credit; (2) a file deleted and later recreated at the same path is a genuinely *new* file (not locked to base's content at all), so a new, unverified test deliberately or coincidentally sharing an old approved id gets silently deselected forever.

**Decision:** each `.verify/approved` entry is now `<test id> <sha256 of the named file's content>`, recorded at approval time (`cli.py`'s `_cmd_approve`, which now refuses to approve a test whose file doesn't exist) and re-verified against base's *current* content every time (`full.py`'s `read_approved`, via the new `file_hash` helper both sides share). A file that no longer matches its recorded hash - edited, or deleted-and-recreated - voids that one entry outright: not "approved with a stale binding," simply not approved, so the test runs for real again. A malformed or legacy bare-id line (one field, no hash) is likewise never honored - fails closed, same as every other ambiguous case in this codebase.

Scoped to the whole file's content, not just the one approved test function's own source - simpler than AST-extracting a single function, and more conservative: any change anywhere in that file invalidates every approval recorded against it, forcing an explicit human re-approval rather than silently narrowing what's actually protected.

**Existing tests updated to match the new contract** (not edited to dodge a failure - the old assertions describe behavior this change deliberately replaces, at the human's explicit direction): `test_cli.py::test_approve_is_additive` now approves real files (the CLI now requires one to hash); `test_full.py::test_approved_removal_is_excluded_from_the_count` and `::test_approve_cli_round_trip` now write hash-bound entries. New tests: `test_full.py::test_read_approved_ignores_a_bare_id_with_no_hash`, `::test_read_approved_voids_a_stale_hash_when_file_content_changes`, and the full end-to-end attack, `tests/attacks/test_attack_08_stale_test_approval.py` - verified to fail against the unfixed code first (the reproduction above), then pass against the fix.

**Addendum - a legacy bare-id entry now fails the check loudly, not silently**: the human asked, before approving anything on this repo, whether a pre-D18 bare-id leftover would be silently dropped or rejected outright. It was silently dropped (treated as simply not-approved) - safe in isolation, but a human could believe an approval is in effect when it quietly never took hold. `full.py` now has `read_approval_errors(base_root)` (and the shared `_parse_approved` both it and `read_approved` call), and `run_full_tier` checks it first, before anything else: any legacy-format line makes the whole check return `status="error"` immediately, naming every offending entry, instead of proceeding as if nothing were approved. New tests: `test_full.py::test_legacy_bare_id_approval_fails_the_check_loudly`, and `::test_read_approved_ignores_a_bare_id_with_no_hash` now also asserts `read_approval_errors` surfaces the entry.

## D19: In this repo's own dogfood CI, the PR's own code runs the check - not base's

Surfaced answering the human's question "whose `actually-done` code actually reads `.verify/approved` during the full-tier job - base's or the PR's?" Confirmed empirically (two packages of the same name, installed editable in sequence, second one wins - reproduced directly, not just reasoned about) and by reading `.github/workflows/actually-done.yml` line by line: "Install the grader from base" (`working-directory: base`, `pip install -e ".[dev]"`) runs *before* "Install PR runtime dependencies" (`working-directory: pr`, `pip install -e .`) - and since this repo's own package is itself named `actually-done`, the second editable install overwrites the first's link. By the time "Run full-tier verification" executes, the installed `actually_done` package - including `full.py`, `cli.py`, every module this check's own logic lives in - resolves to the **PR's** source tree, not base's.

**This is not a bug introduced here - it's a structural consequence of dogfooding a tool against its own source.** For any normal adopting project, "the grader" (`actually-done`) is a separate, globally pip-installed package, entirely outside that project's own repo and PR diffs - a PR there cannot touch the grader's source no matter what it does. It only becomes possible here because this repo's target project *is* actually-done, so "install the grader from base" and "install the PR's own code" both happen to install the exact same package name, and whichever happens last wins. It is also, in a real sense, intentional: every fix built in this entire session - including everything in D14 through D18 - needed the PR's *own* version of `full.py`/`fast.py`/`changes.py` to run, specifically so a change to the verifier's own logic could be checked against its own new behavior, live, on real CI. Pinning the grader to base's `full.py` permanently would mean no PR could ever evolve the verifier itself and have that evolution actually exercised.

**What this means in practice**: in this repo specifically, the full-tier check's own logic - not just the target code it's grading - is within a PR's reach. `base_root`'s *files* are still fixed to the PR's base sha regardless (the "Checkout base" step's `ref:` doesn't depend on what gets installed), so `.verify/approved`'s binding still checks real, base-pinned file content either way - but the *code doing that check* could, in principle, be rewritten by a sufficiently motivated PR to skip the check entirely. The actual protection here is not automated: it's the human reviewing every PR's diff - including any changes under `src/actually_done/` - before merging, which is exactly what's happened throughout this entire build. Worth considering for `CODEOWNERS`: `src/actually_done/` and `scripts/` aren't currently in `init.py`'s `_CODEOWNERS_PATTERNS` list (reasonably so for a normal adopting project, where that path doesn't exist in their repo at all) - for this repo's own dogfood use specifically, adding them would make a change to the verifier's own logic require Code Owner review the same way `.github/`/`pyproject.toml` already do. Not implemented here - flagged for the human to decide, not a change made unprompted.

**Correction - this is not a dogfooding-only quirk, and it was closed, not just flagged**: D19 framed the hole narrowly, as a package-name collision specific to this repo being actually-done's own source. That's too narrow. `pip install -e pr/.` puts the PR's own install into the SAME Python environment the grader runs in - and anything sharing an environment with the grader can replace `import actually_done` outright via a `.pth` file's `import`-prefixed line (a documented CPython `site` module feature, not a bug being exploited) or a `sitecustomize.py`, with **no package-name collision required at all**. This applies to any project using actually-done, not just this repo.

Reproduced directly, both ways, before and after the fix (`tests/attacks/test_attack_09_shadow_grader_package.py`): a `.pth` file planted in a single shared venv (the design D19 described) successfully overrides `import actually_done` for that whole environment; the same `.pth` file, planted in a venv kept separate from the grader's own, has no effect on the grader's import at all.

**Decision:** both `.github/workflows/actually-done.yml` and the shipped template (`src/actually_done/templates/workflow.yml`, kept byte-identical as always) now create two separate venvs instead of one shared one:

- `$RUNNER_TEMP/grader-venv` - `actually-done` (+ dev deps) installed from **base only**, never touched by the PR's own install. Invoked afterward by its own absolute path (`$RUNNER_TEMP/grader-venv/bin/actually-done`), not a bare `actually-done` resolved off `$PATH`.
- `$RUNNER_TEMP/pr-venv` - the PR's own runtime dependencies (`pip install -e ./pr`), plus `pytest` pinned to the *exact* version the grader's own venv resolved (extracted via `pip freeze`, so a PR can't supply a different pytest either). pytest actually **runs in** this venv, not the grader's.

`full.py`'s `run_full_tier`/`_collect_count`/`_run_pytest` gained a `python: str | None` parameter (default `None` → `sys.executable`, unchanged for local/single-environment use) threaded through from a new `actually-done verify --tier full --python <path>` CLI flag, so the orchestration logic (grader venv) and the actual test execution (PR venv, with the PR's own dependencies importable) can run under two different interpreters. Verified end to end with real venvs (not just unit-level mocking) before writing this up, including the two-venv `actually-done verify ... --python` invocation producing a correct pass/fail result exactly as the real workflow will call it.

New test proving the interpreter is genuinely used, not silently defaulted: `test_full.py::test_run_full_tier_actually_uses_the_given_python_not_sys_executable` (a real venv deliberately missing pytest; a silent fallback to `sys.executable`, which does have pytest, would make this wrongly pass).

## D21: Approval binding, extended to cover the PR's own new content too

D18 bound `.verify/approved` to base's content at approval time, closing "this id now means whatever a later PR wants it to mean." It didn't cover a different, related gap: an approval only ever meant "don't run this" (deselected) - there was no way to approve a *specific fix* to an existing test and have the check actually verify that fix for real, since `common_tests`' lock (base's content always wins for a file existing on both sides) has no exception for an approved edit. The only way to land a genuine test-content fix was to merge it unverified and let it become the new base - exactly the position this project was in with the three Linux-only test bugs found earlier in this session (D15).

**Decision:** `.verify/approved` entries now take an optional third field: `<test id> <base sha256> <approved new sha256>`, written by `actually-done approve --to <ref> <id>` (`cli.py`'s `_cmd_approve`). `full.py`'s `_parse_approved` (replacing the old two-value `read_approved`/`read_approval_errors` pair with a single `Approvals` dataclass: `deselected`, `accepted_edit_files`, `mismatched`, `legacy`) now checks, per three-field entry:

- base's current content still matches the recorded base hash (same staleness rule as D18 - otherwise void, silent, ordinary locking applies);
- if the PR's current content matches the approved NEW hash exactly, that file is pulled out of `common_tests` before `_build_sandbox` runs - the PR's version is used instead of base's, and it runs for real (not deselected);
- if the PR's content matches *neither* the base hash (untouched) *nor* the approved new hash (the approved edit) - a PR that changed an approved-edit test to something nobody signed off on - `run_full_tier` fails immediately (`status="fail"`, naming every mismatched id), rather than silently falling back to base's content as if the approval entry weren't there.

A subtlety caught by testing, not guessed: `--to <ref>`'s hash is taken from an actual checkout of that ref (`full.materialize_base`, the same worktree-based mechanism `verify --tier full` itself uses for base) rather than `git show <ref>:<path>`'s raw blob bytes. Those can differ - line-ending normalization on checkout (e.g. LF stored in the repo, CRLF checked out under `core.autocrlf` on Windows) means a hash computed from `git show`'s output didn't match what `_parse_approved` actually reads from the PR's own working-tree checkout at verify time. Found via `tests/unit/test_cli.py::test_approve_to_ref_binds_both_base_and_new_content_hash` failing against the initial `git show`-based implementation, not anticipated in advance.

New tests: `test_full.py::test_approved_edit_lets_the_prs_fixed_version_run_for_real` (base's OWN test starts genuinely broken - checks the wrong value - so the result unambiguously shows which content ran: base's would fail, the approved fix passes), `::test_approved_edit_fails_if_the_pr_changes_it_again` (the requested attack: approve a fix, then the PR substitutes a different, unapproved edit - `assert True` - instead of applying it), and `test_cli.py::test_approve_to_ref_binds_both_base_and_new_content_hash`.

## D22: PR #2 is a bootstrap merge - it can't pass its own dogfood check

Confirmed directly on real CI, not theoretically: pushing D20's `--python` flag made `actually-done full tier` fail immediately with `actually-done: error: unrecognized arguments: --python` ([run 39634534116](https://github.com/actually-done/getactuallydone/actions/runs/39634534116)) - and because nothing ever got far enough to produce `evidence.md`, "Post evidence to the PR" failed too, on `GraphQL: Body cannot be blank`.

This is D20 working correctly, not a regression it introduced. Before D20's isolation fix, D19's finding meant the PR's own `pip install -e pr/.` silently overwrote the grader venv's `actually-done` install - so even though "Install the grader from base" nominally ran first, the CLI that actually executed was always the PR's own, `--python` and all. D20 closes exactly that: the grader venv now installs **only** from base (`$RUNNER_TEMP/grader-venv`, `pip install -e "./base[dev]"`), never touched by the PR's install. Base's `actually-done` CLI is still the version from before this PR - it has never heard of `--python`, because that flag is part of this very PR's own changes to the grader's contract. The properly-isolated grader genuinely cannot run a workflow that assumes a CLI surface only this PR introduces.

**This PR changes the grader's own approval contract** - D18 through D21, the `--python` flag, the `Approvals` dataclass, `--to <ref>`, the three-field entry format, the isolated-venv workflow design itself. Base's grader, correctly isolated, evaluates PR #2 using rules that predate every one of those changes - there is no version of the dogfood check, run with base's own grader, that could meaningfully pass judgment on a PR whose entire content is "change what the grader's rules are." This is the same shape of problem D14 solved for the *first* PR (base had no grader at all yet) - here base has a grader, just not one that understands what this PR is.

**What actually gates this merge, per the human's own instruction: human review, with `ci.yml` green and the dogfood failures explicitly listed, not silently accepted.** `ci.yml` (D15's addition, unaffected by any of this - it runs this PR's own code directly, with no base/grader distinction at all) passed on ubuntu-latest (1m5s) and windows-latest (3m17s); macos-latest was still queued by GitHub (not started, not failed) as of this entry - [run 39634534039](https://github.com/actually-done/getactuallydone/actions/runs/39634534039) has the current state. The dogfood check's failures, as of this entry, are all structurally expected, not signs of a new problem:

- `actually-done full tier`: fails outright (`--python` unrecognized by base's pre-D20 grader) - the bootstrap condition this entry describes.
- `Post evidence to the PR`: fails as a direct, mechanical consequence of the above (no evidence ever got produced to post).
- The six pre-existing test-file failures from D15/D18 (three Linux-only test bugs already fixed on this branch but locked to base's content until merge; three more from the approval-format change itself, same reason) remain exactly as documented there - this PR's fixes for them exist in this branch's content, not yet in effect against this PR's own check, for the same base-locking reason as everything else in this list.

Once this PR merges, base has D20's isolated-venv workflow *and* a grader that understands `--python`, `--to`, and the three-field approval format together, atomically - every PR after this one gets evaluated by a grader whose rules actually match the workflow invoking it. **This PR is not merged as part of writing this entry or anything in this session - that decision is the human's.**

## Not re-verified (out of scope for v0.1, noted for ROADMAP)

Mutation testing, size/complexity/duplication gates, held-out tests, and multi-agent adapters (Cursor/Codex/Copilot hook formats) are explicitly out of scope per the build prompt and were not researched against their respective docs. If a future version adds them, re-fetch Cursor's and Copilot's hook references rather than trusting solution.md's summaries, since only Claude Code's docs were verified here.
