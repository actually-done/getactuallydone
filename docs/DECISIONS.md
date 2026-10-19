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

Researched 2026-10-19 against code.claude.com/docs/en/setup. Git for Windows (which provides Git Bash) is **optional** on native Windows, not a prerequisite: "Without Git for Windows, Claude Code runs shell commands via the PowerShell tool" instead of the Bash tool. Our three hooks all shell out to `bash "${CLAUDE_PLUGIN_ROOT}/scripts/*.sh"`, and the PreToolUse push guard is additionally matched to `"Bash"` only.

Two distinct consequences, neither fixed by swapping bash for a Python wrapper script (a Python entry point has the identical bootstrap problem - if `python`/`python3`/`py` isn't predictably on `PATH`, which is less consistent on Windows than `bash` once Git for Windows is installed, we've traded one fragile dependency for another, with no gain):

1. **No Git for Windows at all**: none of the three hook scripts can even start (`bash`: command not found). The Stop hook's `onFailure` doesn't apply to Stop regardless (D2), so there is no guaranteed-present interpreter left to report "the hook couldn't run" - this fails *silently*, not closed. Claude Code uses the PowerShell tool instead, so our `"Bash"`-matched PreToolUse guard also just never fires for the commands actually being run.
2. **Git for Windows installed (so Bash tool and our hooks exist), but the PowerShell tool is also enabled** (docs: "on by default for claude.ai and Console accounts" whenever Git for Windows is present): a `git push` issued through the PowerShell tool instead of the Bash tool bypasses the push guard, since it's matched to `"Bash"` only. The Stop hook is unaffected (it isn't tool-matched), but the push guard specifically has this gap on Windows regardless of whether Git for Windows is installed.

**Decision:** document this precisely (README, SETUP.md) rather than claim a blanket OS boundary. Users who need the push guard's protection on Windows should set `CLAUDE_CODE_USE_POWERSHELL_TOOL=0` to disable the PowerShell tool, in addition to having Git for Windows installed. The full-tier CI check is unaffected either way, since it runs in GitHub Actions on a Linux runner regardless of the developer's local OS or tool choice - it remains the actual backstop. A PowerShell-native equivalent of `guard.py` (a second PreToolUse entry matched to `"PowerShell"`) would close gap 2 and is a reasonable v0.2 addition, but is new scope, not a release-readiness fix, so it's in `ROADMAP.md` instead.

## Not re-verified (out of scope for v0.1, noted for ROADMAP)

Mutation testing, size/complexity/duplication gates, held-out tests, and multi-agent adapters (Cursor/Codex/Copilot hook formats) are explicitly out of scope per the build prompt and were not researched against their respective docs. If a future version adds them, re-fetch Cursor's and Copilot's hook references rather than trusting solution.md's summaries, since only Claude Code's docs were verified here.
