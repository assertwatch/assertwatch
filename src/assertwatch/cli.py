"""Command-line entry point.

Exit codes: 0 = clean, 1 = findings at or above --fail-on, 2 = usage or internal error.
"""

from __future__ import annotations

import argparse
import sys

from assertwatch import __version__

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assertwatch",
        description="Catch AI coding agents weakening your tests.",
    )
    parser.add_argument("--version", action="version", version=f"assertwatch {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    check = sub.add_parser("check", help="check a diff for test weakening")
    check.add_argument("--base", help="base ref (default: merge-base with main/master)")
    target = check.add_mutually_exclusive_group()
    target.add_argument("--head", help="head ref (default: HEAD)")
    target.add_argument("--worktree", action="store_true", help="check the working tree")
    check.add_argument("--format", choices=["text", "json", "sarif", "github"], default="text")
    check.add_argument("--fail-on", choices=["block", "warn"], default="block")

    sub.add_parser("rules", help="list rules")
    sub.add_parser("init", help="set up config, hook snippet and CODEOWNERS lines")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_USAGE
    print(f"assertwatch {args.command}: not implemented yet", file=sys.stderr)
    return EXIT_USAGE
