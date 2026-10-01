"""Detection rules. Each rule compares matched base/head tests and yields findings."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from assertwatch.model import Finding, Severity, TestFile
from assertwatch.rules.matching import Matching, match_tests


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    severity: Severity
    summary: str
    check: Callable[[Matching], Iterator[Finding]]
    supersedes: frozenset[str] = frozenset()  # rules it replaces for the same test


def all_rules() -> list[Rule]:
    from assertwatch.rules import (
        aw001_removed,
        aw002_assert_count,
        aw003_skipped,
        aw004_weakened,
        aw008_emptied,
    )

    modules = (aw001_removed, aw002_assert_count, aw003_skipped, aw004_weakened, aw008_emptied)
    return [m.RULE for m in modules]


def run_rules(
    base_files: Iterable[TestFile],
    head_files: Iterable[TestFile],
    path_map: dict[str, str] | None = None,
    rules: Iterable[Rule] | None = None,
) -> list[Finding]:
    """Match tests across base and head, run every rule, and return sorted findings.

    When one rule supersedes another (an emptied test also lost its assertions), only the
    more specific finding is kept for that test.
    """
    matching = match_tests(base_files, head_files, path_map)
    selected = list(rules) if rules is not None else all_rules()
    findings = [f for rule in selected for f in rule.check(matching)]
    by_rule = {rule.id: rule for rule in selected}
    superseded = {
        (f.test_id, dropped)
        for f in findings
        if f.test_id is not None
        for dropped in by_rule[f.rule].supersedes
    }
    kept = [f for f in findings if (f.test_id, f.rule) not in superseded]
    return sorted(kept, key=lambda f: (f.path, f.line, f.rule))
