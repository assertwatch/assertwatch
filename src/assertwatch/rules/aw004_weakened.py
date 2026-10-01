"""AW004: an assertion was replaced by a weaker one on the same subject.

Each assertion that changed is classified by what it checks (equality, type, non-None,
truthiness, mock call arguments, expected exception, float tolerance) and paired with a
changed assertion on the same subject. A pair is flagged when the new check is strictly
weaker: `assert total == 10` -> `assert total is not None`, `pytest.raises(ValueError)` ->
`pytest.raises(Exception)`, a wider `approx` tolerance, `assert_called_once_with(...)` ->
`assert_called()`. An expected-exception check removed without the count dropping is
flagged too.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

from assertwatch.extract.python import dotted
from assertwatch.model import Assertion, Finding, Severity
from assertwatch.rules import Rule
from assertwatch.rules.matching import Matching, Pair

ID = "AW004"

BROAD_EXCEPTIONS = frozenset({"Exception", "BaseException", "Warning", "Throwable"})

_EQUAL_CALLS = frozenset(
    {
        "assertEqual",
        "assertEquals",
        "assertIs",
        "assertListEqual",
        "assertDictEqual",
        "assertTupleEqual",
        "assertSetEqual",
        "assertSequenceEqual",
        "assertMultiLineEqual",
        "assertCountEqual",
        "assert_equal",
        "assert_array_equal",
        "assert_frame_equal",
        "assert_series_equal",
    }
)
_ALMOST_CALLS = frozenset({"assertAlmostEqual", "assertAlmostEquals"})
_RAISES_CALLS = frozenset(
    {"raises", "warns", "assertRaises", "assertRaisesRegex", "assertWarns", "assertWarnsRegex"}
)
_CALLED_WITH = frozenset(
    {"assert_called_with", "assert_called_once_with", "assert_any_call", "assert_has_calls"}
)
_CALLED = frozenset({"assert_called", "assert_called_once"})

# Strictly weaker replacements: base kind -> head kinds that check less.
_WEAKER = {
    "equal": {"type", "notnone", "truthy"},
    "type": {"notnone", "truthy"},
    "called_with": {"called"},
}
_LABEL = {
    "equal": "an equality check",
    "type": "a type check",
    "notnone": "a not-None check",
    "truthy": "a truthiness check",
    "called_with": "a call-arguments check",
    "called": "a was-called check",
}

# pytest.approx defaults, and unittest's assertAlmostEqual default places.
_DEFAULT_REL = 1e-6
_DEFAULT_ABS = 1e-12
_DEFAULT_PLACES = 7


@dataclass(frozen=True)
class Check:
    """What one assertion verifies."""

    kind: str  # equal, type, notnone, truthy, called_with, called, raises
    subject: str  # the value under test, as source; "<raises>" for exception checks
    exception: str = ""  # raises: the expected exception, as source
    matches_message: bool = False  # raises: match= or a *Regex variant
    tolerance: tuple[float, float] | None = None  # equal: (rel, abs) for approx / almost-equal


def classify(assertion: Assertion) -> Check | None:
    """Turn an assertion into a Check, or None if it doesn't fit a known shape."""
    try:
        if assertion.kind == "assert":
            stmt = ast.parse(assertion.text).body[0]
            return _classify_expr(stmt.test) if isinstance(stmt, ast.Assert) else None
        node = ast.parse(assertion.text, mode="eval").body
    except (SyntaxError, IndexError):
        return None
    return _classify_call(node) if isinstance(node, ast.Call) else None


def _classify_expr(expr: ast.expr) -> Check | None:
    if isinstance(expr, ast.Compare) and len(expr.ops) == 1:
        left, op, right = expr.left, expr.ops[0], expr.comparators[0]
        if _is_none(right) or _is_none(left):
            subject = left if _is_none(right) else right
            if isinstance(op, ast.IsNot | ast.NotEq):
                return Check("notnone", ast.unparse(subject))
            return None
        if isinstance(op, ast.Eq | ast.Is):
            subject, expected = (right, left) if _is_literal(left) else (left, right)
            return Check("equal", ast.unparse(subject), tolerance=_approx_tolerance(expected))
        return None
    if isinstance(expr, ast.Call) and dotted(expr.func) == "isinstance" and expr.args:
        return Check("type", ast.unparse(expr.args[0]))
    if isinstance(expr, ast.Name | ast.Attribute | ast.Call | ast.Subscript):
        return Check("truthy", ast.unparse(expr))
    return None


def _classify_call(call: ast.Call) -> Check | None:
    name = dotted(call.func).rsplit(".", 1)[-1]
    args = call.args
    if name in _RAISES_CALLS:
        exception = ast.unparse(args[0]) if args else ""
        matches = name.endswith("Regex") or any(kw.arg == "match" for kw in call.keywords)
        return Check("raises", "<raises>", exception=exception, matches_message=matches)
    if name in _CALLED_WITH | _CALLED and isinstance(call.func, ast.Attribute):
        kind = "called_with" if name in _CALLED_WITH else "called"
        return Check(kind, ast.unparse(call.func.value))
    if not args:
        return None
    subject = ast.unparse(args[1] if _is_literal(args[0]) and len(args) > 1 else args[0])
    if name in _EQUAL_CALLS:
        return Check("equal", subject)
    if name in _ALMOST_CALLS:
        return Check("equal", subject, tolerance=_almost_tolerance(call))
    if name == "assertIsNotNone":
        return Check("notnone", subject)
    if name == "assertTrue":
        return Check("truthy", subject)
    if name == "assertIsInstance":
        return Check("type", subject)
    return None


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_literal(node: ast.expr) -> bool:
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    return True


def _number(node: ast.expr | None) -> float | None:
    if node is None or not _is_literal(node):
        return None
    value = ast.literal_eval(node)
    return float(value) if isinstance(value, int | float) else None


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _approx_tolerance(node: ast.expr) -> tuple[float, float] | None:
    if not (isinstance(node, ast.Call) and dotted(node.func).rsplit(".", 1)[-1] == "approx"):
        return None
    rel = _number(_keyword(node, "rel") or (node.args[1] if len(node.args) > 1 else None))
    abs_ = _number(_keyword(node, "abs") or (node.args[2] if len(node.args) > 2 else None))
    return (_DEFAULT_REL if rel is None else rel, _DEFAULT_ABS if abs_ is None else abs_)


def _almost_tolerance(call: ast.Call) -> tuple[float, float]:
    delta = _number(_keyword(call, "delta"))
    if delta is not None:
        return (0.0, delta)
    places = _number(_keyword(call, "places") or (call.args[2] if len(call.args) > 2 else None))
    places = _DEFAULT_PLACES if places is None else places
    return (0.0, 0.5 * 10.0**-places)


def weakening(before: Check, after: Check) -> str | None:
    """Describe how after checks less than before, or None if it doesn't."""
    if before.kind == "raises" and after.kind == "raises":
        if after.exception in BROAD_EXCEPTIONS and before.exception not in BROAD_EXCEPTIONS:
            return f"expects any `{after.exception}` instead of `{before.exception}`"
        if before.matches_message and not after.matches_message:
            return "no longer checks the exception message"
        return None
    if before.subject != after.subject:
        return None
    if after.kind in _WEAKER.get(before.kind, ()):
        return f"{_LABEL[before.kind]} became {_LABEL[after.kind]}"
    if before.kind == after.kind == "equal" and before.tolerance and after.tolerance:
        (rel_b, abs_b), (rel_a, abs_a) = before.tolerance, after.tolerance
        if rel_a > rel_b or abs_a > abs_b:
            return "the tolerance was widened"
    return None


def check(matching: Matching) -> Iterator[Finding]:
    for pair in matching.pairs:
        yield from _check_pair(pair)


def _check_pair(pair: Pair) -> Iterator[Finding]:
    base, head = pair.base.assertions, pair.head.assertions
    lost = _changed(base, head)
    gained = _changed(head, base)
    lost_checks = [(a, classify(a)) for a in lost]
    gained_checks = [(a, classify(a)) for a in gained]
    used: set[int] = set()
    for before, before_check in lost_checks:
        if before_check is None:
            continue
        candidates = [
            i
            for i, (_, after_check) in enumerate(gained_checks)
            if i not in used
            and after_check is not None
            and (after_check.subject == before_check.subject)
        ]
        if not candidates and len(lost) == len(gained) == 1 and gained_checks[0][1]:
            candidates = [0]  # a one-for-one swap; subjects may be spelled differently
        for i in candidates:
            after, after_check = gained_checks[i]
            reason = weakening(before_check, after_check)
            if reason:
                used.add(i)
                yield _finding(pair, after.line, reason, before.text, after.text)
                break

    base_raises = sum(1 for a in base if a.kind == "raises" or "Raises" in a.name)
    head_raises = sum(1 for a in head if a.kind == "raises" or "Raises" in a.name)
    if head_raises < base_raises and len(head) >= len(base):
        removed = [a.text for a in lost if a.kind == "raises" or "Raises" in a.name]
        yield _finding(
            pair,
            pair.head.line,
            "an expected-exception check was removed",
            "\n".join(removed) or None,
            None,
        )


def _changed(side: list[Assertion], other: list[Assertion]) -> list[Assertion]:
    """Assertions on this side whose text doesn't appear (as often) on the other side."""
    remaining = Counter(a.text for a in other)
    changed = []
    for a in side:
        if remaining[a.text]:
            remaining[a.text] -= 1
        else:
            changed.append(a)
    return changed


def _finding(pair: Pair, line: int, reason: str, before: str | None, after: str | None):
    return Finding(
        rule=ID,
        severity=RULE.severity,
        path=pair.head.path,
        line=line,
        message=f"Assertion weakened in `{pair.head.name}`: {reason}.",
        test_id=pair.head.id,
        before=before,
        after=after,
        hint="Restore the stricter assertion and fix the code so it passes.",
    )


RULE = Rule(
    id=ID,
    title="Assertion weakened",
    severity=Severity.BLOCK,
    summary="An assertion was replaced by a weaker check on the same value.",
    check=check,
)
