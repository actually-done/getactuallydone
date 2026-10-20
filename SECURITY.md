# Security Policy

## Supported versions

actually-done is pre-1.0. Only the latest released version receives security fixes.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for a security vulnerability.

Instead, report it privately via [GitHub's private vulnerability reporting](../../security/advisories/new)
on this repository, or by opening a draft security advisory. Include:

- What you found and why it's a security issue (not just a bug).
- Steps to reproduce, ideally a minimal fixture repo or diff.
- The version of `actually-done` and the Claude Code version you tested against.

You should get an initial response within a few days. We'll credit you in the fix's
changelog entry unless you'd rather stay anonymous.

## What counts as a security issue here

actually-done's whole premise is "the agent proposes, a check it can't edit decides" -
so the most relevant class of vulnerability is **a way for an agent-controlled diff to
make the full-tier check pass without the underlying problem actually being fixed**,
beyond the trade-offs already documented in [`README.md`](README.md#what-it-doesnt-guarantee)
and [`ROADMAP.md`](ROADMAP.md). Examples worth reporting:

- A way to make `actually-done verify --tier full` report `pass` while a base test it
  should have run actually failed or was silently skipped.
- A way for a PR to smuggle a change into `conftest.py`, `pytest.ini`, `setup.cfg`, or
  `tests/oracle/` that `full.py`'s sandbox doesn't lock to base's content.
- A push-guard bypass not already covered by `tests/attacks/test_attack_06_*` or noted
  in `docs/DECISIONS.md`.
- Any of the fail-open scenarios in `docs/DECISIONS.md` (D10) that we got wrong - a case
  we claim blocks/halts but that actually allows.

Already-known, already-documented limitations (a single oracle test defeated by exact
hardcoding, the Windows PowerShell-tool gap, a determined agent with the user's own
credentials) aren't new reports - see `README.md`'s "What it doesn't guarantee" section
first.
