"""AW011: an assertion no longer checks anything, even though the count and name are unchanged.

Two cases that count-based checks miss:

- A new assertion that can never fail: `assert True`, a truthy literal, `assert (x, "msg")`
  (a non-empty tuple), `x or True`, two literals that compare true, `isinstance(x, object)`,
  `assertTrue(True)`. Self-comparisons (`x == x`, `assertEqual(a, a)`) and comparisons with
  `mock.ANY` count only when they replace a removed assertion that wasn't one of them,
  since equality tests use them on purpose.
- A test that compared against a literal value now only checks not-None, truthiness or type,
  on a subject spelled differently enough that AW004 couldn't pair the two.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Iterator
from typing import Any

from assertwatch.extract.python import dotted
from assertwatch.model import Assertion, Finding, Severity
from assertwatch.rules import Context, Rule, java_checks
from assertwatch.rules.aw004_weakened import changed, check_pair, classify, is_literal
from assertwatch.rules.matching import Pair

ID = "AW011"

_COMPARE: dict[type, Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
    ast.In: lambda a, b: operator.contains(b, a),
    ast.NotIn: lambda a, b: not operator.contains(b, a),
}
# Comparing anything with itself is true for these operators.
_REFLEXIVE = (ast.Eq, ast.Is, ast.LtE, ast.GtE)
_EQUAL_CALLS = frozenset(
    {"assertEqual", "assertEquals", "assertIs", "assertCountEqual", "assertSequenceEqual"}
    | {"assertListEqual", "assertDictEqual", "assertTupleEqual", "assertSetEqual"}
    | {"assertMultiLineEqual", "assertAlmostEqual", "assertAlmostEquals"}
)
_WEAK_KINDS = frozenset({"notnone", "truthy", "type"})

# How sure we are that an assertion can't fail.
ALWAYS = "always"  # can't fail, whatever the code does
SWAPPED = "swapped"  # can't fail, but has legitimate uses (x == x, == ANY)


def tautology(assertion: Assertion) -> str | None:
    """ALWAYS or SWAPPED if the assertion can never fail, else None."""
    if assertion.lang == "java":
        return java_checks.tautology(assertion.text)
    try:
        if assertion.kind == "assert":
            stmt = ast.parse(assertion.text).body[0]
            return _expr_tautology(stmt.test) if isinstance(stmt, ast.Assert) else None
        node = ast.parse(assertion.text, mode="eval").body
    except (SyntaxError, IndexError):
        return None
    return _call_tautology(node) if isinstance(node, ast.Call) else None


def _expr_tautology(expr: ast.expr) -> str | None:
    if _always_truthy(expr):
        return ALWAYS
    if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.Or):
        if any(_always_truthy(v) for v in expr.values):
            return ALWAYS
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        if is_literal(expr.operand) and not ast.literal_eval(expr.operand):
            return ALWAYS
    if isinstance(expr, ast.Call) and _isinstance_object(expr):
        return ALWAYS
    if isinstance(expr, ast.Compare) and len(expr.ops) == 1:
        left, op, right = expr.left, expr.ops[0], expr.comparators[0]
        if _literal_compare_true(left, op, right):
            return ALWAYS
        if isinstance(op, _REFLEXIVE) and ast.dump(left) == ast.dump(right):
            return SWAPPED
        if isinstance(op, ast.Eq) and (_is_any(left) or _is_any(right)):
            return SWAPPED
    return None


def _call_tautology(call: ast.Call) -> str | None:
    name = dotted(call.func).rsplit(".", 1)[-1]
    args = call.args
    if not args:
        return None
    first = args[0]
    if name == "assertTrue" and _always_truthy(first):
        return ALWAYS
    if name == "assertFalse" and is_literal(first) and not ast.literal_eval(first):
        return ALWAYS
    if name == "assertIsNotNone" and is_literal(first) and ast.literal_eval(first) is not None:
        return ALWAYS
    if name == "assertIsInstance" and len(args) > 1 and dotted(args[1]) == "object":
        return ALWAYS
    if name in _EQUAL_CALLS and len(args) > 1:
        second = args[1]
        if _literal_compare_true(first, ast.Eq(), second):
            return ALWAYS
        if ast.dump(first) == ast.dump(second) or _is_any(first) or _is_any(second):
            return SWAPPED
    return None


def _always_truthy(node: ast.expr) -> bool:
    if isinstance(node, ast.Tuple | ast.List | ast.Set | ast.Dict):
        # Non-empty containers are truthy whatever they hold: `assert (x == 1, "msg")`.
        return bool(node.keys if isinstance(node, ast.Dict) else node.elts)
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.Constant) and v.value for v in node.values)
    return is_literal(node) and bool(ast.literal_eval(node))


def _literal_compare_true(left: ast.expr, op: ast.cmpop, right: ast.expr) -> bool:
    if not (is_literal(left) and is_literal(right)) or type(op) not in _COMPARE:
        return False
    try:
        return bool(_COMPARE[type(op)](ast.literal_eval(left), ast.literal_eval(right)))
    except TypeError:
        return False


def _isinstance_object(call: ast.Call) -> bool:
    return (
        dotted(call.func) == "isinstance"
        and len(call.args) == 2
        and dotted(call.args[1]) == "object"
    )


def _is_any(node: ast.expr) -> bool:
    return dotted(node) in ("ANY", "mock.ANY", "unittest.mock.ANY")


def check(context: Context) -> Iterator[Finding]:
    matching = context.matching
    for pair in matching.pairs:
        lost = changed(pair.base.assertions, pair.head.assertions)
        gained = changed(pair.head.assertions, pair.base.assertions)
        # A reflexive check rewritten as another (`e is e` -> `e1 is e1`) isn't a swap.
        rewritten = sum(1 for a in lost if tautology(a) == SWAPPED)
        replaced = len(lost) - rewritten
        for assertion in gained:
            certainty = tautology(assertion)
            if certainty == SWAPPED and rewritten:
                rewritten -= 1
                continue
            if certainty == ALWAYS or (certainty == SWAPPED and replaced):
                yield _finding(
                    pair,
                    assertion.line,
                    f"`{assertion.text}` can never fail",
                    "\n".join(a.text for a in lost) or None,
                    assertion.text,
                )
        yield from _lost_exact_value(pair, lost, gained)


def _lost_exact_value(
    pair: Pair, lost: list[Assertion], gained: list[Assertion]
) -> Iterator[Finding]:
    """Literal-value checks replaced only by weak ones that AW004 couldn't pair up."""
    if len(pair.head.assertions) < len(pair.base.assertions):
        return  # AW002 reports the drop
    exact = [a for a in lost if (c := classify(a)) and c.kind == "equal" and c.literal]
    weak = [a for a in gained if (c := classify(a)) and c.kind in _WEAK_KINDS]
    strong_gained = [a for a in gained if (c := classify(a)) and c.kind == "equal"]
    if not exact or not weak or strong_gained:
        return
    if next(check_pair(pair), None) is not None:
        return  # AW004 already explains this change
    yield _finding(
        pair,
        weak[0].line,
        "exact-value checks were replaced by checks that only test for a value",
        "\n".join(a.text for a in exact),
        "\n".join(a.text for a in weak),
    )


def _finding(pair: Pair, line: int, reason: str, before: str | None, after: str | None):
    return Finding(
        rule=ID,
        severity=RULE.severity,
        path=pair.head.path,
        line=line,
        message=f"Assertion in `{pair.head.name}` no longer checks anything: {reason}.",
        test_id=pair.head.id,
        before=before,
        after=after,
        hint="Restore an assertion that compares against the expected value.",
    )


RULE = Rule(
    id=ID,
    title="Assertion no longer checks anything",
    severity=Severity.BLOCK,
    summary="A new assertion can never fail, or exact-value checks became weak ones.",
    check=check,
    supersedes=frozenset({"AW004"}),  # "can never fail" is the sharper description
)
