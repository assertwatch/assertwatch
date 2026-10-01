"""AW001: a test, or a parametrized case of one, no longer runs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator

from assertwatch.model import Finding, Severity, TestCase
from assertwatch.rules import Context, Rule
from assertwatch.rules.matching import case_siblings

ID = "AW001"
HINT = (
    "Restore the test and fix the code under test instead. If the test is obsolete, "
    "say so and let a human approve the removal."
)


def check(context: Context) -> Iterator[Finding]:
    matching = context.matching
    for test in matching.removed:
        if test.is_empty:
            continue  # a placeholder that checked nothing; removing it weakens nothing
        yield Finding(
            rule=ID,
            severity=RULE.severity,
            path=test.path,
            line=test.line,
            message=f"Test `{test.name}` was removed and no renamed or moved copy was found.",
            test_id=test.id,
            before=test.body_text or None,
            hint=HINT,
            side="base",
        )

    flagged: set[str] = set()
    for pair in matching.pairs:
        before, after = pair.base.param_cases, pair.head.param_cases
        if before is not None and after is not None and after < before:
            if case_siblings(pair.base, pair.head, matching.added):
                continue  # the cases were split across new tests of the same shape
            flagged.add(pair.head.id)
            yield _fewer_cases(pair.head, before, after)

    # Plain tests merged into one parametrized test need at least one case each.
    merged: dict[str, list[TestCase]] = defaultdict(list)
    targets: dict[str, TestCase] = {}
    for fold in matching.folded:
        param_heads = [h for h in fold.heads if h.param_names]
        if not fold.base.param_names and len(param_heads) == 1:
            merged[param_heads[0].id].append(fold.base)
            targets[param_heads[0].id] = param_heads[0]
    own_cases = {p.head.id: p.base.param_cases or 1 for p in matching.pairs}
    for head_id, bases in merged.items():
        head = targets[head_id]
        expected = len(bases) + own_cases.get(head_id, 0)
        if head_id not in flagged and head.param_cases is not None and head.param_cases < expected:
            yield _fewer_cases(head, expected, head.param_cases)


def _fewer_cases(test: TestCase, before: int, after: int) -> Finding:
    return Finding(
        rule=ID,
        severity=RULE.severity,
        path=test.path,
        line=test.line,
        message=f"`{test.name}` runs {after} parametrized case(s), down from {before}.",
        test_id=test.id,
        hint=HINT,
    )


RULE = Rule(
    id=ID,
    title="Test removed",
    severity=Severity.BLOCK,
    summary="A test or parametrized case disappeared without a renamed or moved copy.",
    check=check,
)
