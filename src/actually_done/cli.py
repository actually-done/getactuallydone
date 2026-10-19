"""Command-line entry point for actually-done."""

from __future__ import annotations

import argparse
import sys

from actually_done import guard


def _cmd_hook_pre_bash(_args: argparse.Namespace) -> int:
    payload = guard.read_hook_payload(sys.stdin)
    command = guard.extract_bash_command(payload)
    if command is None:
        return 0
    blocked, reason = guard.check_push(command)
    if blocked:
        print(reason, file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="actually-done")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hook_parser = subparsers.add_parser("hook", help="Run a Claude Code hook handler")
    hook_subparsers = hook_parser.add_subparsers(dest="hook_name", required=True)
    hook_subparsers.add_parser("pre-bash", help="PreToolUse hook: block git push")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "hook" and args.hook_name == "pre-bash":
        return _cmd_hook_pre_bash(args)

    parser.error(f"unknown command: {args.command} {getattr(args, 'hook_name', '')}".strip())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
