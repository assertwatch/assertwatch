"""AW002: a test has fewer assertions than before."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator

from assertwatch.model import Finding, Severity
from assertwatch.rules import Rule
from assertwatch.rules.matching import Matching

ID = "AW002"


def check(matching: Matching) -> Iterator[Finding]:
    for pair in matching.pairs:
        before, after = pair.base.assertions, pair.head.assertions
        if len(after) >= len(before):
            continue
        # Assertions moved to another test (a split) aren't lost: only count the ones whose
        # copies went down across the whole diff.
        local = Counter(a.text for a in before) - Counter(a.text for a in after)
        lost = [
            text
            for text, missing in local.items()
            for _ in range(min(missing, matching.assertion_deficit(text)))
        ]
        if not lost:
            continue
        yield Finding(
            rule=ID,
            severity=RULE.severity,
            path=pair.head.path,
            line=pair.head.line,
            message=(f"`{pair.head.name}` has {len(after)} assertion(s), down from {len(before)}."),
            test_id=pair.head.id,
            before="\n".join(lost),
            hint="Restore the removed assertion(s) and fix the code so they pass.",
        )


RULE = Rule(
    id=ID,
    title="Assertion count dropped",
    severity=Severity.BLOCK,
    summary="A test kept its name but lost assertions that weren't moved elsewhere.",
    check=check,
)
