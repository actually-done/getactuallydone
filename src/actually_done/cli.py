"""Command-line entry point for actually-done."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from actually_done import changes, fast, guard, hookio, state
from actually_done import config as config_mod


def _cmd_hook_pre_bash(_args: argparse.Namespace) -> int:
    payload = hookio.read_payload(sys.stdin)
    command = guard.extract_bash_command(payload)
    if command is None:
        return 0
    blocked, reason = guard.check_push(command)
    if blocked:
        print(reason, file=sys.stderr)
        return 2
    return 0


def _cmd_hook_stop(_args: argparse.Namespace) -> int:
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

    attempt_count = state.record_attempt(repo_root, list(result.failing_tests))
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


def _cmd_verify(args: argparse.Namespace) -> int:
    repo_root = Path.cwd()
    config = config_mod.load_config(repo_root)
    result = fast.run_fast_tier(repo_root, config)
    print(result.summary)
    if result.details:
        print(result.details)
    return 0 if result.status == "pass" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="actually-done")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hook_parser = subparsers.add_parser("hook", help="Run a Claude Code hook handler")
    hook_subparsers = hook_parser.add_subparsers(dest="hook_name", required=True)
    hook_subparsers.add_parser("pre-bash", help="PreToolUse hook: block git push")
    hook_subparsers.add_parser("stop", help="Stop hook: block on fixable test failures")

    verify_parser = subparsers.add_parser("verify", help="Run the verifier directly")
    verify_parser.add_argument("--tier", choices=["fast"], default="fast")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "hook" and args.hook_name == "pre-bash":
        return _cmd_hook_pre_bash(args)
    if args.command == "hook" and args.hook_name == "stop":
        return _cmd_hook_stop(args)
    if args.command == "verify":
        return _cmd_verify(args)

    parser.error(f"unknown command: {args.command} {getattr(args, 'hook_name', '')}".strip())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
