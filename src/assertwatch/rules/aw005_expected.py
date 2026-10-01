"""AW005: an expected value in a test was rewritten.

An assertion that changed only in its literals (`assert total == 10.05` -> `assert total ==
10`, `assertEqual(x, "a")` -> `assertEqual(x, "b")`, `match="old"` -> `match="new"`) is paired
with the one it replaced. So is a parametrized case that kept all its values but one.

When no production code changed in the same diff, the test was changed to agree with the
code, not the other way round: the classic way to make a failing test pass, so it blocks.
With production code changed too, it is often an intended behavior change: a warning that
asks for confirmation.

Assertion messages and tolerances are left out: a new failure message checks nothing
different, and a wider tolerance is AW004's.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterator
from dataclasses import replace
from pathlib import PurePosixPath

from assertwatch.extract.python import dotted
from assertwatch.model import Assertion, Finding, ParamCase, Severity
from assertwatch.paths import is_guard_path, is_test_path
from assertwatch.rules import Context, Rule
from assertwatch.rules.aw004_weakened import changed
from assertwatch.rules.matching import Pair

ID = "AW005"

_IGNORED_KEYWORDS = frozenset({"msg", "rel", "abs", "delta", "places"})
# unittest calls whose third positional argument is a message or a tolerance, not a value
_TWO_VALUE_CALLS = frozenset(
    {
        "assertEqual",
        "assertEquals",
        "assertNotEqual",
        "assertIs",
        "assertIsNot",
        "assertIn",
        "assertNotIn",
        "assertListEqual",
        "assertDictEqual",
        "assertTupleEqual",
        "assertSetEqual",
        "assertSequenceEqual",
        "assertMultiLineEqual",
        "assertCountEqual",
        "assertAlmostEqual",
        "assertAlmostEquals",
        "assertGreater",
        "assertGreaterEqual",
        "assertLess",
        "assertLessEqual",
    }
)
_NOT_CODE = frozenset({".md", ".rst", ".txt", ".adoc", ".cfg", ".ini", ".toml", ".lock"})
_TEST_DIRS = frozenset({"test", "tests", "testing"})

HINT_TESTS_ONLY = (
    "The code wasn't changed, the test was changed to agree with it. If the old value was "
    "right, restore it and fix the code. If the new value is right, tell the user why."
)
HINT_WITH_CODE = (
    "Confirm the behavior change is intended. If the old value was right, restore it and fix "
    "the code instead."
)


def check(context: Context) -> Iterator[Finding]:
    code_changed = any(_is_code(line.path) for line in context.lines)
    for pair in context.matching.pairs:
        for finding in _check_pair(pair):
            if code_changed:
                yield replace(
                    finding,
                    severity=Severity.WARN,
                    message=finding.message + " Production code changed in the same diff.",
                    hint=HINT_WITH_CODE,
                )
            else:
                yield finding


def _check_pair(pair: Pair) -> Iterator[Finding]:
    gained = [(a, _shape(a)) for a in changed(pair.head.assertions, pair.base.assertions)]
    used: set[int] = set()
    for before in changed(pair.base.assertions, pair.head.assertions):
        shape = _shape(before)
        if shape is None:
            continue
        template, old_values = shape
        for i, (after, other) in enumerate(gained):
            if i in used or other is None or other[0] != template:
                continue
            diffs = [(a, b) for a, b in zip(old_values, other[1], strict=True) if not _same(a, b)]
            if diffs:
                used.add(i)
                if not any(_dynamic(a) != _dynamic(b) for a, b in diffs):
                    yield _finding(pair, after.line, _describe(diffs), before.text, after.text)
                break  # a literal replaced by an interpolated parameter is a refactor
    yield from _check_cases(pair)


def _check_cases(pair: Pair) -> Iterator[Finding]:
    """Pair each case that's gone with a new one that kept every value but one literal."""
    shapes = _ValueShapes()
    lost = _unmatched(pair.base.param_rows, pair.head.param_rows, shapes)
    gained = _unmatched(pair.head.param_rows, pair.base.param_rows, shapes)
    used: set[int] = set()
    for before in lost:
        if len(before.names) < 2:
            continue  # a single value has nothing kept to pair it by
        for i, after in enumerate(gained):
            if i in used or after.names != before.names:
                continue
            diffs = [
                (name, a, b)
                for name, a, b in zip(before.names, before.values, after.values, strict=True)
                if shapes.key(a) != shapes.key(b)
            ]
            if len(diffs) == 1 and _literal_change(shapes(diffs[0][1]), shapes(diffs[0][2])):
                used.add(i)
                name, old, new = diffs[0]
                yield _finding(
                    pair,
                    after.line,
                    f"`{name}` in a parametrized case changed from `{old}` to `{new}`",
                    _row_text(before),
                    _row_text(after),
                )
                break


def _unmatched(
    side: tuple[ParamCase, ...], other: tuple[ParamCase, ...], shapes: _ValueShapes
) -> list[ParamCase]:
    """Cases on this side with no equal case (as often) on the other side."""
    remaining = Counter(shapes.row_key(r) for r in other)
    found = []
    for row in side:
        key = shapes.row_key(row)
        if remaining[key]:
            remaining[key] -= 1
        else:
            found.append(row)
    return found


Shape = tuple[str, list[object]]


class _ValueShapes:
    """Parametrize values with their literals blanked, cached per source text."""

    def __init__(self) -> None:
        self._cache: dict[str, Shape] = {}

    def __call__(self, text: str) -> Shape:
        if text not in self._cache:
            try:
                node = ast.parse(text, mode="eval").body
            except SyntaxError:
                self._cache[text] = (text, [])
            else:
                values: list[object] = []
                self._cache[text] = (ast.unparse(_Blank(values).visit(node)), values)
        return self._cache[text]

    def key(self, text: str) -> tuple:
        template, values = self(text)
        return template, tuple((type(v).__name__, repr(v)) for v in values)

    def row_key(self, row: ParamCase) -> tuple:
        return row.names, tuple(self.key(v) for v in row.values)


def _literal_change(before: Shape, after: Shape) -> bool:
    """Same expression around different literals, none swapped for an interpolation."""
    if before[0] != after[0]:
        return False
    diffs = [(a, b) for a, b in zip(before[1], after[1], strict=True) if not _same(a, b)]
    return bool(diffs) and not any(_dynamic(a) != _dynamic(b) for a, b in diffs)


def _shape(assertion: Assertion) -> tuple[str, list[object]] | None:
    """The assertion with its literals blanked, and the literals in order; None if none."""
    try:
        if assertion.kind == "assert":
            stmt = ast.parse(assertion.text).body[0]
            if not isinstance(stmt, ast.Assert):
                return None
            node: ast.AST = stmt.test  # the failure message is left out
        else:
            node = ast.parse(assertion.text, mode="eval").body
    except (SyntaxError, IndexError):
        return None
    values: list[object] = []
    template = ast.unparse(_Blank(values).visit(node))
    return (template, values) if values else None


class _Blank(ast.NodeTransformer):
    def __init__(self, values: list[object]):
        self.values = values

    def visit_Call(self, node: ast.Call) -> ast.AST:
        node.keywords = [kw for kw in node.keywords if kw.arg not in _IGNORED_KEYWORDS]
        if dotted(node.func).rsplit(".", 1)[-1] in _TWO_VALUE_CALLS:
            node.args = node.args[:2]
        if dotted(node.func).rsplit(".", 1)[-1] == "approx":
            node.args = node.args[:1]
        self.generic_visit(node)
        return node

    def visit_Dict(self, node: ast.Dict) -> ast.AST:
        if all(isinstance(k, ast.Constant) for k in node.keys):  # key order doesn't matter
            pairs = sorted(
                zip(node.keys, node.values, strict=True), key=lambda kv: repr(kv[0].value)
            )
            node.keys = [k for k, _ in pairs]
            node.values = [v for _, v in pairs]
        self.generic_visit(node)
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        if isinstance(node.op, ast.USub | ast.UAdd) and isinstance(node.operand, ast.Constant):
            if isinstance(node.operand.value, int | float | complex):
                self.values.append(ast.literal_eval(node))
                return ast.Name("_")
        self.generic_visit(node)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        self.values.append(node.value)
        return ast.Name("_")

    def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.AST:
        text = _FString(ast.unparse(node))
        text.dynamic = any(isinstance(v, ast.FormattedValue) for v in node.values)
        self.values.append(text)
        return ast.Name("_")


class _FString(str):
    """An f-string, kept as its source. dynamic: it interpolates something."""

    dynamic = False


def _dynamic(value: object) -> bool:
    return isinstance(value, _FString) and value.dynamic


def _same(a: object, b: object) -> bool:
    """Equal as written values: 10 and 10.0 are the same, 1 and True are not."""
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        return a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return a == b
    return type(a) is type(b) and a == b


def _describe(diffs: list[tuple[object, object]]) -> str:
    shown = ", ".join(f"`{_lit(a)}` to `{_lit(b)}`" for a, b in diffs[:2])
    more = f" (and {len(diffs) - 2} more)" if len(diffs) > 2 else ""
    return f"a checked value changed from {shown}{more}"


def _lit(value: object) -> str:
    text = str(value) if isinstance(value, _FString) else repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _row_text(row: ParamCase) -> str:
    return ", ".join(f"{n}={v}" for n, v in zip(row.names, row.values, strict=True))


def _is_code(path: str) -> bool:
    """Production code: not a test, test helper, guard or config file, and not docs."""
    pure = PurePosixPath(path)
    return not (
        is_test_path(path)
        or is_guard_path(path)
        or pure.suffix.lower() in _NOT_CODE
        or _TEST_DIRS & set(pure.parts[:-1])
    )


def _finding(pair: Pair, line: int, change: str, before: str | None, after: str | None):
    return Finding(
        rule=ID,
        severity=RULE.severity,
        path=pair.head.path,
        line=line,
        message=f"In `{pair.head.name}`, {change}.",
        test_id=pair.head.id,
        before=before,
        after=after,
        hint=HINT_TESTS_ONLY,
    )


RULE = Rule(
    id=ID,
    title="Expected value rewritten",
    severity=Severity.BLOCK,
    summary=(
        "An expected value in an assertion or a parametrized case changed. Blocks when no "
        "production code changed; a warning when it did."
    ),
    check=check,
)
