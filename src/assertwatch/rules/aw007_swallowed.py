"""AW007: a failing check is caught and ignored, so the test passes whatever happens."""

from __future__ import annotations

from collections.abc import Iterator

from assertwatch.model import Finding, Severity, TestCase, TryBlock
from assertwatch.rules import Context, Rule

ID = "AW007"


def _swallowing(test: TestCase) -> list[TryBlock]:
    return [t for t in test.try_blocks if t.swallows]


def check(context: Context) -> Iterator[Finding]:
    for pair in context.matching.pairs:
        before, after = _swallowing(pair.base), _swallowing(pair.head)
        if len(after) <= len(before):
            continue  # already there at base: not this change's doing
        caught = ", ".join(sorted({h or "bare except" for t in after for h in t.handlers}))
        yield Finding(
            rule=ID,
            severity=RULE.severity,
            path=pair.head.path,
            line=after[0].line,
            message=(
                f"`{pair.head.name}` now catches and ignores its own failing checks ({caught}): "
                "it passes whether or not they hold."
            ),
            test_id=pair.head.id,
            hint="Remove the try/except or suppress around the check and fix the code instead.",
        )


RULE = Rule(
    id=ID,
    title="Failing check swallowed",
    severity=Severity.BLOCK,
    summary="A check was wrapped in try/except (catch) that ignores AssertionError.",
    check=check,
)
