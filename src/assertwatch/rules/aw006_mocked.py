"""AW006: the test now mocks the code it checks, so it checks the stand-in."""

from __future__ import annotations

from collections.abc import Iterator

from assertwatch.model import Finding, Severity
from assertwatch.rules import Context, Rule

ID = "AW006"


def check(context: Context) -> Iterator[Finding]:
    for pair in context.matching.pairs:
        base, head = pair.base, pair.head
        # Called for real at base, called on a stand-in now. A stand-in the base already
        # checked isn't this change's doing.
        new = sorted((head.mocked_calls - base.mocked_calls) & base.external_calls)
        if not new:
            continue
        names = ", ".join(f"`{n}`" for n in new)
        yield Finding(
            rule=ID,
            severity=RULE.severity,
            path=head.path,
            line=head.line,
            message=(
                f"`{head.name}` now mocks {names} and calls it itself: it checks the mock's "
                "return value, not the code."
            ),
            test_id=head.id,
            after="\n".join(m for m in head.mocks if m.rsplit(".", 1)[-1] in new),
            hint="Mock what the code under test depends on, not the code itself, and fix the code.",
        )


RULE = Rule(
    id=ID,
    title="Code under test mocked",
    severity=Severity.BLOCK,
    summary="A test now patches or stubs a function it calls directly and used to call for real.",
    check=check,
)
