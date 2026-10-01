"""AW008: a test body was reduced to nothing that can fail."""

from __future__ import annotations

from collections.abc import Iterator

from assertwatch.model import Finding, Severity, TestCase
from assertwatch.rules import Rule
from assertwatch.rules.matching import Matching

ID = "AW008"


def _does_nothing(test: TestCase) -> bool:
    return test.is_empty or (not test.assertions and test.call_count == 0)


def check(matching: Matching) -> Iterator[Finding]:
    for pair in matching.pairs:
        if _does_nothing(pair.head) and not _does_nothing(pair.base):
            yield Finding(
                rule=ID,
                severity=RULE.severity,
                path=pair.head.path,
                line=pair.head.line,
                message=f"`{pair.head.name}` was emptied: it no longer checks or calls anything.",
                test_id=pair.head.id,
                before=pair.base.body_text,
                after=pair.head.body_text or None,
                hint="Restore the test body and fix the code under test.",
            )


RULE = Rule(
    id=ID,
    title="Test body emptied",
    severity=Severity.BLOCK,
    summary="A test body became pass, ..., or has no assertions and no calls.",
    check=check,
    supersedes=frozenset({"AW002"}),
)
