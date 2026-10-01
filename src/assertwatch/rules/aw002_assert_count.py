"""AW002: a test has fewer assertions than before."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator

from assertwatch.model import Finding, Severity
from assertwatch.rules import Context, Rule
from assertwatch.rules.matching import Pair, blanked

ID = "AW002"


def check(context: Context) -> Iterator[Finding]:
    matching = context.matching
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
        if not lost or _parametrized_over(pair, lost):
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


def _parametrized_over(pair: Pair, lost: list[str]) -> bool:
    """The test became parametrized over the checks it used to spell out one by one.

    `assert "A" in out; assert "B" in out` -> one `assert expect in out` with cases "A" and
    "B": each lost assertion must match a head assertion once values are blanked, its values
    must be in that assertion or in the cases, and cases x assertions must not shrink.
    """
    base, head = pair.base, pair.head
    before_cases, after_cases = base.param_cases or 1, head.param_cases or 0
    if after_cases <= before_cases:
        return False
    if len(head.assertions) * after_cases < len(base.assertions) * before_cases:
        return False
    templates: dict[str, frozenset[str]] = {}  # head assertion template -> literals it keeps
    for a in head.assertions:
        template, literals = blanked(a.text, head.param_names)
        templates[template] = templates.get(template, frozenset()) | literals
    for text in lost:
        template, literals = blanked(text)
        if template not in templates or not literals - templates[template] <= head.param_literals:
            return False
    return True


RULE = Rule(
    id=ID,
    title="Assertion count dropped",
    severity=Severity.BLOCK,
    summary="A test kept its name but lost assertions that weren't moved elsewhere.",
    check=check,
)
