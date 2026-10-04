"""Lost assertions that the test still guarantees in another form.

A test can lose assertions without checking less. AW002 asks this module before reporting a
drop, and each case here has to be provable from the two versions of the test alone.
(Assertions in dead Python 2 branches are left out before AW002 counts at all.)

- **Captured warnings or exceptions.** `with warnings.catch_warnings(record=True) as w`
  followed by `assert issubclass(w[0].category, DeprecationWarning)` and
  `assert "old" in str(w[0].message)` becomes `pytest.warns(DeprecationWarning, match="old")`.
  The new context must name every class the lost check named and match every string it
  looked for; checks on anything else (`__cause__`, `filename`) stay lost.
- **Implied by an exact value.** The head asserts `x == 5`; a lost `assert x is not None`,
  `assert x > 0` or `assert len(x) > 0` is true for that value. The lost check is evaluated
  with the value put in, using only literals, comparisons and a few pure builtins.
- **The same equality, unwrapped.** `assert Path(a) == Path(b)` is implied by `a == b`.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from assertwatch.extract.python import dotted
from assertwatch.model import Assertion, TestCase
from assertwatch.rules.matching import as_assert

# Attributes of a caught warning or exception that pytest.warns/raises can check for you.
_CAPTURE_ATTRS = frozenset(
    {"category", "message", "value", "type", "typename", "args", "exception", "warnings"}
)
_CAPTURE_HELPERS = frozenset({"len", "str", "repr", "isinstance", "issubclass", "type"})
_DEPRECATION = frozenset({"DeprecationWarning", "PendingDeprecationWarning"})

_SAFE_CALLS = {
    "len": len,
    "bool": bool,
    "str": str,
    "int": int,
    "float": float,
    "abs": abs,
    "sorted": sorted,
    "set": set,
    "frozenset": frozenset,
    "list": list,
    "tuple": tuple,
    "isinstance": isinstance,
    "type": type,
}
_SAFE_NAMES = {
    "None": None,
    "True": True,
    "False": False,
    "int": int,
    "float": float,
    "str": str,
    "bytes": bytes,
    "bool": bool,
    "list": list,
    "tuple": tuple,
    "dict": dict,
    "set": set,
    "frozenset": frozenset,
}


def implied(lost: list[str], base: TestCase, head: TestCase) -> set[str]:
    """The lost assertion texts that the head version of the test still guarantees."""
    if any(a.lang != "python" for a in (*base.assertions, *head.assertions)):
        return set()  # the evaluator reads Python; nothing is implied in other languages
    base_texts = {a.text for a in base.assertions}
    live = [a for a in head.assertions if not a.py2_only]  # dead code guarantees nothing
    contexts = [c for a in live if a.text not in base_texts and (c := _context(a)) is not None]
    head_exprs = [e for a in live if (e := _expr(a)) is not None]
    found: set[str] = set()
    for text in lost:
        copies = [a for a in base.assertions if a.text == text]
        if not copies:
            continue
        lost_context = _context(copies[0])
        if lost_context is not None:
            if any(lost_context.covered_by(c) for c in contexts):
                found.add(text)
            continue
        expr = _expr(copies[0])
        if expr is None:
            continue
        if (
            _captured(expr, base.captures, contexts)
            or _implied_by_value(expr, head_exprs)
            or _unwrapped(expr, head_exprs)
        ):
            found.add(text)
    return found


def _expr(assertion: Assertion) -> ast.expr | None:
    """What the assertion checks, as an expression; unittest calls are spelled as `assert`."""
    try:
        if assertion.kind == "assert":
            stmt = ast.parse(assertion.text).body[0]
            return stmt.test if isinstance(stmt, ast.Assert) else None
        if assertion.kind == "call":
            return as_assert(ast.parse(assertion.text, mode="eval").body)
    except (SyntaxError, IndexError):
        return None
    return None


# --- captured warnings and exceptions ---------------------------------------------------


@dataclass(frozen=True)
class _Context:
    """An expected-exception or expected-warning check: what it expects and the match."""

    kind: str  # "raises" or "warns"
    classes: frozenset[str]
    pattern: str | None  # match= / the *Regex pattern, when it is a literal string
    has_pattern: bool

    def covered_by(self, other: _Context) -> bool:
        """other expects the same thing and matches at least as much of the message."""
        if other.kind != self.kind or other.classes != self.classes:
            return False
        return not self.has_pattern or (self.pattern is not None and self.pattern == other.pattern)


_CONTEXT_KINDS = {
    "raises": "raises",
    "assertRaises": "raises",
    "assertRaisesRegex": "raises",
    "warns": "warns",
    "deprecated_call": "warns",
    "assertWarns": "warns",
    "assertWarnsRegex": "warns",
}


def _context(assertion: Assertion) -> _Context | None:
    try:
        call = ast.parse(assertion.text, mode="eval").body
    except SyntaxError:
        return None
    if not isinstance(call, ast.Call):
        return None
    name = dotted(call.func).rsplit(".", 1)[-1]
    kind = _CONTEXT_KINDS.get(name)
    if kind is None:
        return None
    if name == "deprecated_call":
        classes = set(_DEPRECATION)
    elif call.args:
        first = call.args[0]
        items = first.elts if isinstance(first, ast.Tuple) else [first]
        classes = {dotted(i).rsplit(".", 1)[-1] for i in items}
    else:
        classes = set()
    keyword = "expected_regex" if name.endswith("Regex") else "match"
    pattern = next((kw.value for kw in call.keywords if kw.arg == keyword), None)
    if pattern is None and name.endswith("Regex") and len(call.args) > 1:
        pattern = call.args[1]
    literal = pattern.value if isinstance(pattern, ast.Constant) else None
    return _Context(
        kind,
        frozenset(classes),
        literal if isinstance(literal, str) else None,
        pattern is not None,
    )


def _captured(expr: ast.expr, captures: frozenset[str], contexts: list[_Context]) -> bool:
    """A check on a caught warning/exception that a new pytest.warns/raises makes instead."""
    if not captures or not contexts:
        return False
    names = {n.id for n in ast.walk(expr) if isinstance(n, ast.Name)}
    if not names & captures:
        return False
    if any(isinstance(n, ast.Attribute) and n.attr not in _CAPTURE_ATTRS for n in ast.walk(expr)):
        return False
    if any(
        isinstance(n, ast.Constant) and type(n.value) is int and n.value > 1 for n in ast.walk(expr)
    ):
        return False  # `len(w) == 2`: pytest.warns only proves at least one
    classes = names - captures - _CAPTURE_HELPERS
    strings = [
        n.value for n in ast.walk(expr) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    return any(
        classes <= context.classes and all(_matches(context, s) for s in strings)
        for context in contexts
    )


def _matches(context: _Context, text: str) -> bool:
    """The context's pattern looks for text (written plainly or regex-escaped)."""
    pattern = context.pattern
    return pattern is not None and (text in pattern or re.escape(text) in pattern)


# --- implied by an exact value ----------------------------------------------------------


class _Unknown(Exception):
    """The expression uses something this evaluator won't guess about."""


def _implied_by_value(expr: ast.expr, head_exprs: list[ast.expr]) -> bool:
    for subject, value in _exact_values(head_exprs):
        try:
            if _evaluate(expr, subject, value):
                return True
        except (_Unknown, TypeError, ValueError, AttributeError, IndexError, KeyError):
            continue
    return False


def _exact_values(exprs: list[ast.expr]) -> list[tuple[str, object]]:
    """(subject source, value) for each `subject == literal` the head asserts."""
    found = []
    for e in exprs:
        if not (isinstance(e, ast.Compare) and len(e.ops) == 1 and isinstance(e.ops[0], ast.Eq)):
            continue
        left, right = e.left, e.comparators[0]
        for subject, literal in ((left, right), (right, left)):
            try:
                value = ast.literal_eval(literal)
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                continue
            if not _is_literal_node(subject):
                found.append((ast.unparse(subject), value))
            break
    return found


def _is_literal_node(node: ast.expr) -> bool:
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    return True


def _evaluate(node: ast.expr, subject: str, value: object) -> object:
    """Evaluate node with `subject` bound to value; raise _Unknown on anything else."""
    if ast.unparse(node) == subject:
        return value
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in _SAFE_NAMES:
            return _SAFE_NAMES[node.id]
        raise _Unknown(node.id)
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        items = [_evaluate(e, subject, value) for e in node.elts]
        return {ast.Tuple: tuple, ast.List: list, ast.Set: set}[type(node)](items)
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate(node.operand, subject, value)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand  # type: ignore[operator]
        raise _Unknown(ast.unparse(node))
    if isinstance(node, ast.BoolOp):
        results = [bool(_evaluate(v, subject, value)) for v in node.values]
        return all(results) if isinstance(node.op, ast.And) else any(results)
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, subject, value)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _evaluate(comparator, subject, value)
            if not _compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        func = _SAFE_CALLS.get(node.func.id)
        if func is None:
            raise _Unknown(node.func.id)
        return func(*(_evaluate(a, subject, value) for a in node.args))
    raise _Unknown(ast.unparse(node))


def _compare(op: ast.cmpop, left: object, right: object) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Is):
        return left is right
    if isinstance(op, ast.IsNot):
        return left is not right
    if isinstance(op, ast.Lt):
        return left < right  # type: ignore[operator]
    if isinstance(op, ast.LtE):
        return left <= right  # type: ignore[operator]
    if isinstance(op, ast.Gt):
        return left > right  # type: ignore[operator]
    if isinstance(op, ast.GtE):
        return left >= right  # type: ignore[operator]
    if isinstance(op, ast.In):
        return left in right  # type: ignore[operator]
    if isinstance(op, ast.NotIn):
        return left not in right  # type: ignore[operator]
    raise _Unknown(type(op).__name__)


# --- the same equality, unwrapped -------------------------------------------------------


def _unwrapped(expr: ast.expr, head_exprs: list[ast.expr]) -> bool:
    """`f(a) == f(b)` is implied when the head asserts `a == b`."""
    if not (isinstance(expr, ast.Compare) and len(expr.ops) == 1):
        return False
    left, right = expr.left, expr.comparators[0]
    if not (
        isinstance(expr.ops[0], ast.Eq)
        and isinstance(left, ast.Call)
        and isinstance(right, ast.Call)
        and ast.unparse(left.func) == ast.unparse(right.func)
        and len(left.args) == len(right.args) == 1
        and not left.keywords
        and not right.keywords
    ):
        return False
    pair = {ast.unparse(left.args[0]), ast.unparse(right.args[0])}
    return any(
        isinstance(e, ast.Compare)
        and len(e.ops) == 1
        and isinstance(e.ops[0], ast.Eq)
        and {ast.unparse(e.left), ast.unparse(e.comparators[0])} == pair
        for e in head_exprs
    )
