"""AW003: a test that ran before is now skipped, xfailed or returns before checking."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator

from assertwatch.model import Finding, Severity
from assertwatch.rules import Context, Rule

ID = "AW003"

SKIP_MARKERS = frozenset({"skip", "skipif", "skipunless", "xfail"})


def _skip_markers(markers: frozenset[str]) -> set[str]:
    return {m for m in markers if m.removeprefix("param-") in SKIP_MARKERS}


def check(context: Context) -> Iterator[Finding]:
    matching = context.matching
    for pair in matching.pairs:
        base, head = pair.base, pair.head
        if "skip" in base.markers:
            continue  # it never ran: a new skipif or xfail narrows the skip, it can't disable it
        added = [f"@{m}" for m in sorted(_skip_markers(head.markers) - _skip_markers(base.markers))]
        added.extend(f"{c}()" for c in sorted(Counter(head.skip_calls) - Counter(base.skip_calls)))
        if head.returns_first and not base.returns_first:
            added.append("return as the first statement")
        if not added:
            continue
        yield Finding(
            rule=ID,
            severity=RULE.severity,
            path=head.path,
            line=head.line,
            message=f"`{head.name}` is now skipped or disabled: {', '.join(added)}.",
            test_id=head.id,
            hint="Remove the skip and fix the failure, or ask a human to approve the skip.",
        )


RULE = Rule(
    id=ID,
    title="Test skipped or disabled",
    severity=Severity.BLOCK,
    summary="A skip, skipif, xfail marker, a skip call, or an early return was added.",
    check=check,
)
