# Solutions: 5 Reliability Problems in AI Coding Agents

For developers who let agents (Claude Code, Codex, Cursor, Copilot) do most of their coding. One principle runs through every fix: **the agent proposes, a check it can't edit decides.** Prompts and CLAUDE.md are advice, local hooks steer the agent, and a required CI check that runs the base branch's grader decides. The five briefings come first; the setup principles they rely on follow.

## 1. "Almost right" code

- **Why:** the agent writes the code and its tests from one understanding, so boundaries and error paths never get asserted.
- **Own the oracle:** before the agent starts, merge 2–3 boundary tests of your own (empty, one item, exact multiple, last partial page) to `tests/oracle/<issue>/` as expected failures. The agent never edits them. For the PR you link to that issue, CI runs that directory in its own pytest run with `--runxfail`, so the tests must really pass.
- **Revert test in CI:** a PR that changes production code must add at least one test that fails on the base branch by assertion. Human-added labels relax this: `new-feature` accepts any failure, and `no-behaviour-change` waives it for refactors as long as every base test still passes.
- **Diff-scoped mutation in CI** (Stryker, mutmut, PIT): each surviving mutant on a changed line goes back to the agent as a test to write, with production code frozen.
- **Property tests** (Hypothesis, fast-check) wherever an invariant exists: pagination, ranges, indexing, parsers, serializers, round-trips.
- **Limit:** killing a mutant proves the tests tell versions apart, not that the expected values are right, so your oracle matters. Equivalent mutants are reported, not blocked.
- **Evidence:** Google ran changed-line mutants in code review for 24k+ developers, and 82% of the mutants that got feedback were rated productive. Engineers accepted 73% of Meta's mutation-guided tests.

## 2. Can't understand or trust the output

- **Why:** the agent produces more change than a reviewer can absorb, and nothing tells the reviewer where to look.
- **Enforce size:** CI fails a PR over ~400 changed lines or ~15 files (excluding lockfiles and generated code) unless a human adds `large-change`. Plan first, one slice per PR, and review a stack of slices against that plan.
- **Evidence from CI, not the agent:** commit SHA, commands, exit codes, test counts and every new function, posted on the PR with grader-path changes listed first.
- **Change map:** public symbols changed and their callers, schema/config/dependency/route changes, and the tests that cover them. Few tools build this deterministically today; tree-sitter or your language server can.
- **Read structurally:** difftastic or SemanticDiff, so moved code and formatting don't hide real edits.
- **AI reviewers** (a fresh-context subagent, CodeRabbit, Greptile) find bugs, but are never the approval.
- **Evidence:** Microsoft found the share of useful review comments falls as changes touch more files. In Stack Overflow's 2026 survey, 48% trust easily verified AI output, but only ~7% trust it for important decisions.

## 3. False "done" claims and test gaming

- **Why:** the agent grades itself, and the grader sits in a workspace it can write to.
- **Done = the required CI check, run with the base branch's grader:** `verify.sh`, existing test files and test config all come from base. Editing an assertion changes nothing, and only a human-approved edit or removal counts.
- **Grader paths need Code Owner approval:** `conftest.py`, `pytest.ini`, plugins, `*.pth`/`sitecustomize.py`, jest/vitest setup and mock files, dependency manifests, lockfiles, build and install scripts, `.github/`, `.verify/`, and the agents' own rules and hooks (`AGENTS.md`, `CLAUDE.md`, `.claude/`, `.cursor/`, `.codex/`). Each can weaken the checks without touching a test.
- **Audit the whole diff:** new skip markers and falling test counts fail the check. Production-code heuristics (test-environment checks, test-framework imports, fixture literals, early exits) are flagged for a human to acknowledge.
- **Beat special-casing:** property tests with random inputs catch hashed or table-driven cheats. Held-out tests catch more: keep them in a private repo, fetch them only in a job the PR can't edit, and report only pass/fail counts. Some disguised special-casing will still pass.
- **Fair exits:** flaky tests go to quarantine with an owner and expiry, and the agent can dispute a test or ask for approval, which hands control to you instead of tempting it to cheat. On ImpossibleBench's conflicting-tests split, such a flag cut GPT-5's cheating from 54% to 9%.
- **Evidence:** in RewardHackingAgents (ML-engineering agents), agents tried to tamper with the evaluator in ~50% of runs. Locking it took that to 0, but each single defense blocked only its own attack, so these layers are combined.

## 4. Forgetting rules in long sessions

- **Why:** rules typed in chat are summarized away at compaction. Anthropic's docs call CLAUDE.md "context, not enforced configuration".
- **Enforce, don't remind:** must-hold rules become Claude Code deny rules and PreToolUse hooks that exit 2 (in Cursor, `beforeShellExecution` hooks). They are checked on every tool call, however full the context is.
- **Back it server-side:** deny rules miss `git -C . push` and `sh -c`. A ruleset with no bypass for the agent rejects the push however it's spelled. Classic branch protection exempts admins unless "Do not allow bypassing" is set.
- **Fail closed where you can:** Claude Code `onFailure: "block"` on PreToolUse command/http hooks (v2.1.295+; no effect on Stop hooks), and Cursor `failClosed: true` on its `before*` hooks. Elsewhere, CI covers the gap.
- **One rulebook, one hook config per agent:** rules live in AGENTS.md, imported into CLAUDE.md, and each agent's native hook config calls the same `verify.sh` through a small adapter.
- **Expect double hooks:** Cursor (via a per-user toggle, on by default) and Copilot CLI also load `.claude/settings.json`. The verifier caches its result by working-tree hash, so a second copy reuses it instead of re-running.
- **Re-inject what can't be enforced:** keep a short critical-rules list in the root CLAUDE.md, which is re-read from disk after compaction, and re-print it with a SessionStart `compact` hook.

## 5. Duplication and complexity creep

- **Why:** the agent writes before it searches, and nothing blocks new clones or complexity.
- **Search before writing:** a subagent lists existing helpers by signature and behaviour, not just name. An LSP plugin lets the agent navigate symbols precisely, and CI lists every new function, private ones included.
- **Gate duplication in CI:** jscpd for any language (lower `--min-tokens` so small helpers count), or PMD CPD with `--ignore-identifiers` for renamed Java/C++ copies. SonarQube's gate fails above 3% new duplication.
- **Gate complexity in CI:** measure the delta with lizard, radon or `wily diff`. SonarQube has no complexity-delta condition, but its "no new issues" rule flags new functions over its cognitive-complexity limit.
- **Reimplementations (custom today):** nothing off the shelf catches the same logic rewritten, especially in Python or JavaScript. Embed each new function's signature and docstring, list the nearest existing ones, and have a human accept "new helper".
- **Avoid false alarms:** exclude tests, generated code and schemas. Some duplication is legitimate, and forced de-duplication can over-abstract.
- **Evidence (weakest of the five):** CMU's MSR 2026 study of 806 Cursor-adopting repos found persistent rises in complexity (~41%) and static-analysis warnings (~30%). No study yet shows clone gates fix this, so treat them as guardrails.

## Setup principles

### Adoption order

- **Hackathon minimum (~1 h):**
  - your oracle tests and a Stop hook that runs your affected tests;
  - deny rules for `git push`, plus a PreToolUse hook that blocks any command containing both `git` and `push` (catching `-C` and `sh -c` forms); on a free private repo this is your only push guard;
  - on a public repo, a ruleset on main that requires a PR, with an empty bypass list.
  - Between milestone CI runs, only the local hook stands guard. That's the trade-off.
- **Day 1 (1–2 days without a starter script):** `verify.sh` as the agent's Stop hook (fast tier) and as a required CI check (full tier, pinned to GitHub Actions, run with the base grader). This stops accidental cheating. It doesn't stop a determined agent that still acts as you, because the agent could change the workflow or the ruleset.
- **Week 1:**
  - a bot identity for the agent;
  - Code Owner review on grader paths;
  - 1 required approval, with approval of the most recent push turned on, and your human account as the only bypass actor, in pull-requests-only mode;
  - the size gate, CI evidence, the revert test and the `compact` hook;
  - narrow the push deny rule to protected branches.
- **Later:** mutation, property and held-out tests, the change map, and clone and complexity gates.

### How the verifier behaves

- **Fast tier (Stop hook, under ~60 s):**
  - It runs the affected tests (pytest-testmon, `jest --findRelatedTests`, `vitest related`), or the unit suite if none are found for a changed source file.
  - It passes immediately if no source file changed.
- **Full tier (CI):**
  - Every base test and every new test must pass, and the count can't drop.
  - Human-approved removals are excluded.
  - Oracle tests not yet targeted are pending: neither passed nor skipped.
- **Keep working or hand back:**
  - On a failure the agent can fix, the hook blocks the stop.
  - When a human is needed (a test edit or removal awaiting approval, one disputed test, or a broken or too-slow verifier), it halts and says why.
  - Halts only pause the agent; CI still has to pass.
  - Halt after ~3 consecutive failures that make no progress. Claude Code's own cap of 8 blocks resets whenever the agent runs a tool.
- **Approvals:** a human-only label added after the last push that touched tests, a `.verify/approved` file read from base, or, locally, a `verify approve` command you run outside the agent's sandbox. Quarantine entries carry an owner and expiry, and a scheduled job flags expired ones.
- **A wrapper that rarely fails open:**
  - The hook is a tiny shell wrapper that always prints valid output and traps any error into a halt.
  - It runs under its own timeout, set below the host's. Set the host's explicitly: Copilot CLI defaults to 30 s, Claude Code to 600 s.
  - A small adapter maps results to each agent's format: Claude Code uses `decision`/`continue`, Cursor uses `followup_message`.
  - Only a missing wrapper still fails open, and CI covers that.
- **CI isolation:**
  - Run base `verify.sh` and scripts from a separate base checkout.
  - Call test runners directly, not `npm test`, whose pre/post scripts come from the PR.
  - Disable pytest plugin autoload and load the needed plugins with `-p`.
  - Install without lifecycle scripts (`npm ci --ignore-scripts`).
  - Check the JUnit report against the runner's exit code.
  - Give the test job read-only permissions and a time limit, and post evidence from a separate job.
  - The workflow YAML itself runs from the PR, so this holds only once the bot lacks Workflows permission and `.github/` is Code Owner–reviewed.

### Agent identity

- **A separate identity:** a bot account or GitHub App with no Administration or Workflows access. It opens PRs; you approve and merge them. Never make the last push to its branch yourself, because approval must come from someone other than the last pusher.
- **Solo developers:** as the only pull-requests-only bypass actor, you can merge your own PRs, while the bot's PRs still need your approval. Confirm with a test PR that Code Owner review blocks as expected.
- **Your credentials out of reach:** CLI and IDE agents alike inherit your `gh` login and SSH keys. Run them in a devcontainer holding only the bot's token, or every "human-only" step, your bypass included, can be done as you.
- **Human-only, checked:** any token with pull-request access can add labels, so CI accepts `large-change`, `new-feature`, `no-behaviour-change` or `tests-approved` only if a listed human added it, read from the PR timeline.
- **Free private repos** have no branch protection or rulesets: go public, upgrade, or keep push rights away from the agent and push yourself.

## Sources

- Stack Overflow 2026 survey, trust figures (The Register): https://www.theregister.com/software/2026/10/10/stack-overflow-survey-finds-devs-hooked-on-ai-but-not-totally-sold-on-its-judgment/5301662
- Google, diff-based mutation testing in code review: https://arxiv.org/abs/2102.11378
- Meta, mutation-guided LLM test generation (ACH): https://arxiv.org/abs/2501.12862
- Anthropic, agentic property-based testing: https://www.anthropic.com/research/property-based-testing
- Microsoft, characteristics of useful code reviews: https://www.microsoft.com/en-us/research/?p=168510
- Google engineering practices, small CLs: https://google.github.io/eng-practices/review/developer/small-cls.html
- RewardHackingAgents: https://arxiv.org/abs/2603.11337
- ImpossibleBench: https://arxiv.org/abs/2510.20270
- Claude Code hooks reference: https://code.claude.com/docs/en/hooks
- Claude Code hooks guide (Stop hook block cap): https://code.claude.com/docs/en/hooks-guide
- Claude Code permissions: https://code.claude.com/docs/en/permissions
- Claude Code memory (CLAUDE.md is context, AGENTS.md import): https://code.claude.com/docs/en/memory
- Claude Code context window and compaction: https://code.claude.com/docs/en/context-window
- Cursor hooks: https://cursor.com/docs/agent/hooks
- Cursor third-party (Claude Code) hooks: https://cursor.com/docs/reference/third-party-hooks
- Codex hooks: https://learn.chatgpt.com/docs/hooks
- GitHub Copilot CLI hooks reference: https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-hooks-reference
- GitHub protected branches: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
- GitHub rulesets (bypass modes, plan availability): https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository
- GitHub ruleset rules (Code Owner review, last-pusher approval): https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets
- GitHub fine-grained token permissions: https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens
- pytest plugins (autoload, `-p`): https://docs.pytest.org/en/stable/how-to/plugins.html
- PMD CPD: https://docs.pmd-code.org/latest/pmd_userdocs_cpd.html
- SonarQube quality gates: https://docs.sonarsource.com/sonarqube-server/2026.4/quality-standards-administration/managing-quality-gates/introduction-to-quality-gates
- CMU, Cursor adoption and code quality (MSR 2026): https://arxiv.org/abs/2511.04427
