"""Detection rules. Each rule reads the Context of one diff and yields findings."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace

from assertwatch.config import Config
from assertwatch.gitdiff import DiffLine
from assertwatch.model import Finding, Severity, TestFile
from assertwatch.rules.matching import Matching, match_copies, match_tests


@dataclass(frozen=True)
class FileChange:
    """A changed non-test file the rules read in full, on both sides (None = absent)."""

    base_path: str | None
    head_path: str | None
    before: str | None
    after: str | None

    @property
    def path(self) -> str:
        return self.head_path or self.base_path or ""


@dataclass
class Context:
    """Everything the rules see about one diff."""

    matching: Matching
    files: list[FileChange] = field(default_factory=list)  # guard and test-config files
    lines: list[DiffLine] = field(default_factory=list)  # added and removed lines, all files
    messages: list[str] = field(default_factory=list)  # commit messages in base..head
    new_test_files: frozenset[str] = frozenset()  # test files that didn't exist at base
    python2_dropped: bool = False  # the base ref declares no Python 2 support


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    severity: Severity
    summary: str
    check: Callable[[Context], Iterator[Finding]]
    supersedes: frozenset[str] = frozenset()  # rules it replaces for the same test


def all_rules() -> list[Rule]:
    from assertwatch.rules import (
        aw000_waiver,
        aw001_removed,
        aw002_assert_count,
        aw003_skipped,
        aw004_weakened,
        aw005_expected,
        aw007_swallowed,
        aw008_emptied,
        aw009_config,
        aw011_oracle,
    )

    modules = (
        aw000_waiver,
        aw001_removed,
        aw002_assert_count,
        aw003_skipped,
        aw004_weakened,
        aw005_expected,
        aw007_swallowed,
        aw008_emptied,
        aw009_config,
        aw011_oracle,
    )
    return [m.RULE for m in modules]


def run_rules(
    base_files: Iterable[TestFile],
    head_files: Iterable[TestFile],
    path_map: dict[str, str] | None = None,
    rules: Iterable[Rule] | None = None,
    *,
    files: Iterable[FileChange] = (),
    lines: Iterable[DiffLine] = (),
    messages: Iterable[str] = (),
    config: Config | None = None,
    python2_dropped: bool = False,
    find_copies: Callable[[list[str]], Iterable[TestFile]] | None = None,
) -> list[Finding]:
    """Match tests across base and head, run every rule, and return sorted findings.

    When one rule supersedes another (an emptied test also lost its assertions), only the
    more specific finding is kept for that test. Then the config applies: severities are
    overridden, "off" rules dropped, and findings under a standing waiver marked as waived.

    find_copies, given the names of tests that look removed, returns test files outside
    the diff that may hold an identical copy of them.
    """
    base_files, head_files = list(base_files), list(head_files)
    path_map = path_map or {}
    old_paths = {path_map.get(f.path, f.path) for f in base_files}
    matching = match_tests(base_files, head_files, path_map)
    if find_copies is not None and matching.removed:
        match_copies(matching, find_copies(sorted({t.name for t in matching.removed})))
    context = Context(
        matching,
        list(files),
        list(lines),
        list(messages),
        frozenset(f.path for f in head_files if f.path not in old_paths),
        python2_dropped,
    )
    selected = list(rules) if rules is not None else all_rules()
    findings = [f for rule in selected for f in rule.check(context)]
    by_rule = {rule.id: rule for rule in selected}
    superseded = {
        (f.test_id, dropped)
        for f in findings
        if f.test_id is not None
        for dropped in by_rule[f.rule].supersedes
    }
    kept = [f for f in findings if (f.test_id, f.rule) not in superseded]
    if config is not None:
        kept = apply_config(kept, config)
    return sorted(kept, key=lambda f: (f.path, f.line, f.rule))


def apply_config(findings: list[Finding], config: Config) -> list[Finding]:
    result = []
    for finding in findings:
        level = config.severity.get(finding.rule)
        if level == "off":
            continue
        if level is not None:
            finding = replace(finding, severity=Severity(level))
        waiver = next((a for a in config.allow if a.covers(finding.rule, finding.path)), None)
        if waiver is not None:
            finding = replace(finding, waiver=waiver.reason)
        result.append(finding)
    return result
