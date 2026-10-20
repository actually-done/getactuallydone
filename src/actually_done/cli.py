"""Command-line entry point for actually-done."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from actually_done import changes, fast, full, guard, hookio, init, state
from actually_done import config as config_mod

_MAX_COMPACT_LINES = 20


def _cmd_hook_pre_bash(_args: argparse.Namespace) -> int:
    """Fails closed (blocks) on anything it can't make sense of, not just a detected
    push: this hook's matcher scopes it to Bash and PowerShell tool calls only (see
    hooks.json), so a payload with no parseable `tool_input.command` - or any other
    unexpected failure - means something is wrong, not that the command is safe. See
    docs/DECISIONS.md, D9/D11/the fail-open audit.

    `tool_name` picks the dialect: PowerShell's command syntax (the call operator,
    `cmd /c`, `Start-Process`) differs enough from POSIX shell that they need separate
    parsers - see guard.check_push_for_tool.
    """
    try:
        payload = hookio.read_payload(sys.stdin)
        command = guard.extract_command(payload)
        if command is None:
            print(
                "actually-done: could not read the command from the hook payload. "
                "Blocking out of caution.",
                file=sys.stderr,
            )
            return 2
        tool_name = payload.get("tool_name")
        blocked, reason = guard.check_push_for_tool(tool_name, command)
        if blocked:
            print(reason, file=sys.stderr)
            return 2
        return 0
    except Exception as exc:
        # A security gate must fail closed on any surprise, not crash its way open.
        print(
            f"actually-done: push guard crashed ({exc!r}). Blocking out of caution.",
            file=sys.stderr,
        )
        return 2


def _cmd_hook_stop(_args: argparse.Namespace) -> int:
    """Always exits 0 with valid JSON (D3): a crash here must surface as a halt,
    never as a non-zero exit that depends on the shell wrapper alone to catch it -
    this is the Python layer's own safety net, independent of plugin/scripts/stop.sh.
    """
    try:
        return _hook_stop_body()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "systemMessage": (
                        f"actually-done: the verifier crashed ({exc!r}). Please check "
                        "manually before trusting this turn."
                    )
                }
            )
        )
        return 0


def _hook_stop_body() -> int:
    payload = hookio.read_payload(sys.stdin)
    repo_root = Path(payload["cwd"]) if payload.get("cwd") else Path.cwd()

    if payload.get("stop_hook_active"):
        print(json.dumps({}))
        return 0

    config = config_mod.load_config(repo_root)
    touched_tests = changes.modified_or_removed_test_files(
        changes.changed_files(repo_root), config.unit_test_path
    )
    if touched_tests:
        print(
            json.dumps(
                {
                    "systemMessage": (
                        "actually-done: a test file was edited or removed ("
                        + ", ".join(touched_tests)
                        + "). This needs human review before the agent's own tests can be "
                        "trusted - the full-tier CI check still runs the base branch's "
                        "tests regardless."
                    )
                }
            )
        )
        return 0

    result = fast.run_fast_tier(repo_root, config)

    if result.status == "pass":
        state.reset_attempts(repo_root)
        print(json.dumps({}))
        return 0

    if result.status == "error":
        print(
            json.dumps(
                {
                    "systemMessage": (
                        f"actually-done: the verifier could not run cleanly "
                        f"({result.summary}). Please check manually before trusting this turn."
                    )
                }
            )
        )
        return 0

    try:
        attempt_count = state.record_attempt(repo_root, list(result.failing_tests))
    except state.StateUnwritable as exc:
        print(
            json.dumps(
                {
                    "systemMessage": (
                        f"actually-done: {exc}. Needs human help: the no-progress "
                        "counter can't be persisted, so this could block the same "
                        "failure forever otherwise."
                    )
                }
            )
        )
        return 0

    if attempt_count >= 3:
        print(
            json.dumps(
                {
                    "systemMessage": (
                        f"actually-done: the same tests have failed {attempt_count} times in "
                        f"a row with no progress. Needs human help. {result.summary}\n"
                        f"{result.details}"
                    )
                }
            )
        )
        return 0

    print(
        json.dumps(
            {
                "decision": "block",
                "reason": f"actually-done: {result.summary}. Fix these before finishing.\n"
                f"{result.details}",
            }
        )
    )
    return 0


def _cmd_hook_compact(_args: argparse.Namespace) -> int:
    """Not a gate - nothing here blocks or halts anything, so any failure (a crash,
    a payload we can't parse) degrades to a clean no-op {} rather than propagating.
    """
    try:
        return _hook_compact_body()
    except Exception:
        print(json.dumps({}))
        return 0


def _hook_compact_body() -> int:
    payload = hookio.read_payload(sys.stdin)
    if payload.get("source") not in (None, "compact"):
        print(json.dumps({}))
        return 0

    repo_root = Path(payload["cwd"]) if payload.get("cwd") else Path.cwd()
    agents_md = repo_root / "AGENTS.md"
    if not agents_md.is_file():
        print(json.dumps({}))
        return 0

    lines = agents_md.read_text(encoding="utf-8").splitlines()
    block = "\n".join(lines[:_MAX_COMPACT_LINES])
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": block,
                }
            }
        )
    )
    return 0


def _cmd_verify_fast(repo_root: Path, config: config_mod.Config) -> int:
    result = fast.run_fast_tier(repo_root, config)
    print(result.summary)
    if result.details:
        print(result.details)
    return 0 if result.status == "pass" else 1


def _cmd_verify_full(args: argparse.Namespace, repo_root: Path, config: config_mod.Config) -> int:
    if not args.base:
        print("error: --tier full requires --base <ref-or-path>", file=sys.stderr)
        return 2
    with full.materialize_base(repo_root, args.base) as base_root:
        result, evidence = full.run_full_tier(repo_root, base_root, config, base_ref=args.base)
    print(full.render_evidence_markdown(evidence, result))
    return 0 if result.status == "pass" else 1


def _cmd_verify(args: argparse.Namespace) -> int:
    repo_root = Path.cwd()
    config = config_mod.load_config(repo_root)
    if args.tier == "full":
        return _cmd_verify_full(args, repo_root, config)
    return _cmd_verify_fast(repo_root, config)


def _cmd_approve(args: argparse.Namespace) -> int:
    repo_root = Path.cwd()
    path = repo_root / ".verify" / "approved"
    existing: set[str] = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            entry = line.split("#", 1)[0].strip()
            if entry:
                existing.add(entry)
    existing.update(args.test_ids)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sorted(existing)) + "\n", encoding="utf-8")
    print(f"approved {len(args.test_ids)} test id(s); {len(existing)} total in {path}")
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    report = init.run_init(Path.cwd(), force=args.force)
    for path in report.written:
        print(f"wrote {path}")
    for path in report.appended:
        print(f"updated {path}")
    for path in report.skipped:
        print(f"skipped {path} (already present; pass --force to overwrite)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="actually-done")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hook_parser = subparsers.add_parser("hook", help="Run a Claude Code hook handler")
    hook_subparsers = hook_parser.add_subparsers(dest="hook_name", required=True)
    hook_subparsers.add_parser("pre-bash", help="PreToolUse hook: block git push")
    hook_subparsers.add_parser("stop", help="Stop hook: block on fixable test failures")
    hook_subparsers.add_parser(
        "compact", help="SessionStart(compact) hook: re-print AGENTS.md's rules block"
    )

    verify_parser = subparsers.add_parser("verify", help="Run the verifier directly")
    verify_parser.add_argument("--tier", choices=["fast", "full"], default="fast")
    verify_parser.add_argument(
        "--base", default=None, help="Base ref or checkout path (required for --tier full)"
    )

    approve_parser = subparsers.add_parser(
        "approve", help="Approve test ids for removal/skipping in the full tier"
    )
    approve_parser.add_argument("test_ids", nargs="+", metavar="TEST_ID")

    init_parser = subparsers.add_parser("init", help="Scaffold CI, CODEOWNERS, and rules files")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing files")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "hook" and args.hook_name == "pre-bash":
        return _cmd_hook_pre_bash(args)
    if args.command == "hook" and args.hook_name == "stop":
        return _cmd_hook_stop(args)
    if args.command == "hook" and args.hook_name == "compact":
        return _cmd_hook_compact(args)
    if args.command == "verify":
        return _cmd_verify(args)
    if args.command == "approve":
        return _cmd_approve(args)
    if args.command == "init":
        return _cmd_init(args)

    parser.error(f"unknown command: {args.command} {getattr(args, 'hook_name', '')}".strip())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
