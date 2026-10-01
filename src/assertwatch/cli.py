"""Command-line entry point.

Exit codes: 0 = clean, 1 = findings at or above --fail-on, 2 = usage, git or parse error.
`assertwatch hook` follows Claude Code's convention instead: 2 blocks the agent's stop.

Imports are deferred to the command that needs them, to keep the hook's fast path fast.
"""

from __future__ import annotations

import sys

TYPE_CHECKING = False  # typing.TYPE_CHECKING without importing typing (4 ms)
if TYPE_CHECKING:
    import argparse

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2


def _build_parser() -> argparse.ArgumentParser:
    import argparse

    from assertwatch.report import FORMATS

    parser = argparse.ArgumentParser(
        prog="assertwatch",
        description="Catch AI coding agents weakening your tests.",
    )
    parser.add_argument("--version", action="store_true", help="show the version and exit")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    check = sub.add_parser("check", help="check a diff for test weakening")
    check.add_argument("--base", help="base ref (default: merge-base with main/master)")
    target = check.add_mutually_exclusive_group()
    target.add_argument("--head", help="head ref (default: HEAD)")
    target.add_argument("--worktree", action="store_true", help="check the working tree")
    check.add_argument("--format", choices=FORMATS, default="text")
    check.add_argument(
        "--fail-on",
        choices=["block", "warn", "never"],
        default="block",
        help="exit 1 when a finding has at least this severity (default: block)",
    )
    check.add_argument(
        "--github-actions",
        action="store_true",
        help="also emit annotations, the job summary and step outputs (in GitHub Actions)",
    )

    sub.add_parser("rules", help="list rules")
    init = sub.add_parser("init", help="set up config, hook snippet and CODEOWNERS lines")
    init.add_argument(
        "--owner", help="CODEOWNERS owner for the guard files, e.g. @org/team or @user"
    )
    sub.add_parser("hook", help="Claude Code hook (SessionStart and Stop); reads JSON on stdin")
    return parser


def main(argv: list[str] | None = None) -> int:
    if (sys.argv[1:] if argv is None else argv) == ["hook"]:
        return _hook()  # runs on every agent stop: skip building the parser
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.version:
        from assertwatch import __version__

        print(f"assertwatch {__version__}")
        parser.exit()
    if args.command is None:
        parser.print_help()
        return EXIT_USAGE
    if args.command == "check":
        return _check(args)
    if args.command == "rules":
        return _rules()
    if args.command == "init":
        return _init(args)
    return _hook()


def _check(args: argparse.Namespace) -> int:
    from assertwatch.check import check_diff
    from assertwatch.gitdiff import GitError, repo_root, resolve_base, resolve_commit
    from assertwatch.report import Report, failing, render

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
    if args.github_actions:
        import os

        from assertwatch.report.actions import write_actions

        write_actions(report, args.fail_on, sys.stdout, os.environ)
    if failing(report, args.fail_on):
        return EXIT_FINDINGS
    return EXIT_USAGE if report.errors else EXIT_OK


def _rules() -> int:
    from assertwatch.rules import all_rules

    for rule in all_rules():
        print(f"{rule.id}  {rule.severity:<5}  {rule.title}")
        print(f"       {rule.summary}")
    return EXIT_OK


def _init(args: argparse.Namespace) -> int:
    from assertwatch import init
    from assertwatch.gitdiff import GitError, repo_root

    try:
        repo = repo_root()
    except GitError as exc:
        print(f"assertwatch: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return init.run(repo, sys.stdout, owner=args.owner or init.DEFAULT_OWNER)


def _hook() -> int:
    from assertwatch import hook

    payload = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
    return hook.run(payload, sys.stdout, sys.stderr)
