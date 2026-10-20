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

## Not re-verified (out of scope for v0.1, noted for ROADMAP)

Mutation testing, size/complexity/duplication gates, held-out tests, and multi-agent adapters (Cursor/Codex/Copilot hook formats) are explicitly out of scope per the build prompt and were not researched against their respective docs. If a future version adds them, re-fetch Cursor's and Copilot's hook references rather than trusting solution.md's summaries, since only Claude Code's docs were verified here.
