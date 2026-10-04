"""AW002: a test has fewer assertions than before."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator

from assertwatch.model import Assertion, Finding, Severity
from assertwatch.rules import Context, Rule
from assertwatch.rules.implied import implied
from assertwatch.rules.matching import Pair, blanked

ID = "AW002"


def check(context: Context) -> Iterator[Finding]:
    matching = context.matching
    head_tests = {(t.path, t.name) for t in [*(p.head for p in matching.pairs), *matching.added]}
    for pair in matching.pairs:
        before, after, moved = _counted(pair, context.python2_dropped)
        before = [a for a in before if not _out_of_view(a, pair, head_tests)]
        hidden = Counter() if context.python2_dropped else moved  # moved copies still counted
        count = len(after) - sum(hidden.values())
        if count >= len(before):
            continue
        # Assertions moved to another test (a split) aren't lost: only count the ones whose
        # copies went down across the whole diff, or that moved into a Python 2 branch here.
        local = Counter(a.text for a in before) - Counter(a.text for a in after)
        lost = [
            text
            for text, missing in (local + hidden).items()
            for _ in range(min(missing, matching.assertion_deficit(text) + moved[text]))
        ]
        if lost:
            covered = implied(lost, pair.base, pair.head)
            lost = [text for text in lost if text not in covered]
        if not lost or _parametrized_over(pair, lost):
            continue
        yield Finding(
            rule=ID,
            severity=RULE.severity,
            path=pair.head.path,
            line=pair.head.line,
            message=(f"`{pair.head.name}` has {count} assertion(s), down from {len(before)}."),
            test_id=pair.head.id,
            before="\n".join(lost),
            hint="Restore the removed assertion(s) and fix the code so they pass.",
        )


def _out_of_view(assertion: Assertion, pair: Pair, head_tests: set[tuple[str, str]]) -> bool:
    """A base check made by a helper the head can no longer show: the test still calls the
    helper but it now lives in another file (a base class, a utils module), or the helper
    became a test of its own, so its checks still run. Either way, counting them without
    the helper would make the test look weaker than it is."""
    helper = assertion.via
    if not helper:
        return False
    head = pair.head
    if helper in head.external_calls:
        return True
    return (head.path, helper) in head_tests


def _counted(pair: Pair, python2_dropped: bool) -> tuple[list, list, Counter[str]]:
    """The assertions that count on each side, and those moved into a Python 2 branch.

    An assertion under `if PY2:` only runs on Python 2. When the project no longer supports
    it, that code is dead: it doesn't count on either side, so removing it loses nothing.
    Either way, moving a live assertion into such a branch stops it running and is a loss.
    """
    base, head = pair.base.assertions, pair.head.assertions
    if python2_dropped:
        before = [a for a in base if not a.py2_only]
        after = [a for a in head if not a.py2_only]
    else:
        before, after = list(base), list(head)
    base_live = Counter(a.text for a in base if not a.py2_only)
    head_live = Counter(a.text for a in head if not a.py2_only)
    deadened = Counter(a.text for a in head if a.py2_only) - Counter(
        a.text for a in base if a.py2_only
    )
    # +: keep only texts whose live copies went down
    moved = +Counter({t: min(n, base_live[t] - head_live[t]) for t, n in deadened.items()})
    return before, after, moved


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
