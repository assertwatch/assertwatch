"""Command-line entry point.

Exit codes: 0 = clean, 1 = findings at or above --fail-on, 2 = usage, git or parse error.
"""

from __future__ import annotations

import argparse
import sys

from assertwatch import __version__
from assertwatch.check import check_diff
from assertwatch.gitdiff import GitError, repo_root, resolve_base, resolve_commit
from assertwatch.report import FORMATS, Report, failing, render
from assertwatch.rules import all_rules

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
    check.add_argument("--format", choices=FORMATS, default="text")
    check.add_argument(
        "--fail-on",
        choices=["block", "warn"],
        default="block",
        help="exit 1 when a finding has at least this severity (default: block)",
    )

    sub.add_parser("rules", help="list rules")
    sub.add_parser("init", help="set up config, hook snippet and CODEOWNERS lines")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_USAGE
    if args.command == "check":
        return _check(args)
    if args.command == "rules":
        return _rules()
    print(f"assertwatch {args.command}: not implemented yet", file=sys.stderr)
    return EXIT_USAGE


def _check(args: argparse.Namespace) -> int:
    try:
        repo = repo_root()
        base = resolve_base(repo, args.base, args.head)
        head = None if args.worktree else resolve_commit(repo, args.head or "HEAD")
        result = check_diff(repo, base, head)
    except GitError as exc:
        print(f"assertwatch: {exc}", file=sys.stderr)
        return EXIT_USAGE
    report = Report(base, head, result.findings, result.errors, result.test_files)
    sys.stdout.write(render(report, args.format))
    if failing(report, args.fail_on):
        return EXIT_FINDINGS
    return EXIT_USAGE if report.errors else EXIT_OK


def _rules() -> int:
    for rule in all_rules():
        print(f"{rule.id}  {rule.severity:<5}  {rule.title}")
        print(f"       {rule.summary}")
    return EXIT_OK
