"""Python extractor: pytest and unittest test files -> TestFile.

Collection follows pytest's defaults: files named test_*.py or *_test.py; module-level
functions named test*; methods named test* in classes named Test* (without __init__) and in
unittest.TestCase subclasses, including nested classes and test methods inherited from classes
in the same file. `__test__ = False` disables collection.

Known limits:
- Test classes imported from other modules (or subclassed across files) are not followed; their
  tests are reported in the file that defines them.
- Assertions in helper functions are counted for the tests that call them only when the helper
  is defined in the same file (a module-level function, or a method of the test's class).
"""

from __future__ import annotations

import ast
import math
import operator
import re
from collections.abc import Iterator
from dataclasses import replace

from assertwatch.model import Assertion, ParamCase, TestCase, TestFile, TryBlock

_RAISES_CALLS = frozenset(
    {"pytest.raises", "pytest.warns", "pytest.deprecated_call", "raises", "warns"}
)
_SKIP_CALLS = frozenset({"pytest.skip", "pytest.xfail", "pytest.importorskip", "skip", "xfail"})
_UNITTEST_MARKERS = {
    "skip": "skip",
    "skipIf": "skipif",
    "skipUnless": "skipunless",
    "expectedFailure": "xfail",
}
_PATCH_ROOTS = frozenset({"patch", "mock", "unittest"})
_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_CAPTURE_CALLS = frozenset({"assertRaises", "assertRaisesRegex", "assertWarns", "assertWarnsRegex"})


def extract(source: str, path: str) -> TestFile:
    """Parse source and return the tests pytest would collect from it."""
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError) as exc:
        return TestFile(path=path, parse_error=f"{type(exc).__name__}: {exc}")

    result = TestFile(path=path, imports=_imports(tree))
    if _test_flag(tree.body) is False:
        return result
    module_markers = _pytestmark(tree.body)
    consts = _module_sequences(tree.body)
    local = _local_classes(tree.body)
    helpers = Helpers(_helper_defs(tree.body, ""))
    for node in tree.body:
        if isinstance(node, _FUNCTIONS) and node.name.startswith("test"):
            result.tests.append(_test_case(node, path, (), module_markers, consts, helpers))
        elif isinstance(node, ast.ClassDef):
            result.tests.extend(
                _collect_class(node, path, (), module_markers, local, consts, helpers)
            )
    return result


# --- collection -------------------------------------------------------------------------


def _collect_class(
    node: ast.ClassDef,
    path: str,
    outer: tuple[str, ...],
    inherited: frozenset[str],
    local: dict[str, ast.ClassDef],
    consts: dict[str, ast.expr],
    helpers: Helpers,
) -> list[TestCase]:
    lineage = [node, *_local_bases(node, local)]
    is_unittest = any(dotted(b).endswith("TestCase") for cls in lineage for b in cls.bases)
    has_init = any(_has_init(cls) for cls in lineage)
    collectable = is_unittest or (node.name.startswith("Test") and not has_init)
    flag = next((f for f in (_test_flag(cls.body) for cls in lineage) if f is not None), None)
    if not collectable or flag is False:
        return []
    classes = (*outer, node.name)
    markers = inherited.union(
        *(_decorator_markers(cls.decorator_list) | _pytestmark(cls.body) for cls in lineage)
    )
    methods: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for cls in reversed(lineage):  # an override replaces the inherited helper
        methods.update(_helper_defs(cls.body, "self."))
    helpers = helpers.with_methods(methods)
    tests: list[TestCase] = []
    seen: set[str] = set()
    for cls in lineage:  # own methods first, then inherited ones not overridden
        for child in cls.body:
            if isinstance(child, _FUNCTIONS) and child.name.startswith("test"):
                if child.name not in seen:
                    seen.add(child.name)
                    tests.append(_test_case(child, path, classes, markers, consts, helpers))
    if not is_unittest:  # pytest doesn't collect classes nested in a unittest.TestCase
        nested = _local_classes(node.body)
        for child in node.body:
            if isinstance(child, ast.ClassDef):
                tests.extend(_collect_class(child, path, classes, markers, nested, consts, helpers))
    return tests


def _local_classes(body: list[ast.stmt]) -> dict[str, ast.ClassDef]:
    return {n.name: n for n in body if isinstance(n, ast.ClassDef)}


def _local_bases(node: ast.ClassDef, local: dict[str, ast.ClassDef]) -> list[ast.ClassDef]:
    """Base classes defined in the same scope, depth-first, without repeats or cycles."""
    found: list[ast.ClassDef] = []
    pending = [node]
    while pending:
        cls = pending.pop(0)
        for base in cls.bases:
            base_def = local.get(dotted(base))
            if base_def is not None and base_def is not node and base_def not in found:
                found.append(base_def)
                pending.append(base_def)
    return found


def _has_init(node: ast.ClassDef) -> bool:
    return any(isinstance(c, _FUNCTIONS) and c.name == "__init__" for c in node.body)


def _test_flag(body: list[ast.stmt]) -> bool | None:
    """Value of a literal `__test__ = True/False` assignment in this scope, if any."""
    for stmt in body:
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
            if isinstance(stmt.value.value, bool) and any(
                isinstance(t, ast.Name) and t.id == "__test__" for t in stmt.targets
            ):
                return stmt.value.value
    return None


def _imports(tree: ast.Module) -> frozenset[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return frozenset(modules)


# --- markers ----------------------------------------------------------------------------


def _marker_name(expr: ast.expr) -> str | None:
    name = dotted(expr.func if isinstance(expr, ast.Call) else expr)
    parts = name.split(".")
    if "mark" in parts[:-1]:
        return parts[-1]
    if parts[-1] in _UNITTEST_MARKERS and (len(parts) == 1 or parts[0] == "unittest"):
        return _UNITTEST_MARKERS[parts[-1]]
    return None


def _decorator_markers(decorators: list[ast.expr]) -> frozenset[str]:
    markers: set[str] = set()
    for dec in decorators:
        name = _marker_name(dec)
        if name is None:
            continue
        markers.add(name)
        if name == "parametrize" and isinstance(dec, ast.Call):
            markers.update(_param_marks(dec))
        if _runs_only_on_py2(name, dec):
            markers.add("py2-only")
    return frozenset(markers)


def _runs_only_on_py2(marker: str, dec: ast.expr) -> bool:
    """`skipif(not PY2)` or `skipUnless(PY2)`: the test is skipped everywhere but Python 2."""
    if marker not in ("skipif", "skipunless") or not isinstance(dec, ast.Call) or not dec.args:
        return False
    condition = dec.args[0]
    if isinstance(condition, ast.Constant) and isinstance(condition.value, str):
        try:  # pytest evaluates string conditions: skipif("sys.version_info >= (3,)")
            condition = ast.parse(condition.value, mode="eval").body
        except SyntaxError:
            return False
    return py2_gate(condition) is (marker == "skipunless")


def _param_marks(call: ast.Call) -> set[str]:
    """Marks attached to single parametrize cases: pytest.param(..., marks=pytest.mark.skip)."""
    found: set[str] = set()
    for node in ast.walk(call):
        if isinstance(node, ast.Call) and dotted(node.func).endswith("param"):
            for kw in node.keywords:
                if kw.arg == "marks":
                    values = (
                        kw.value.elts if isinstance(kw.value, ast.List | ast.Tuple) else [kw.value]
                    )
                    for value in values:
                        name = _marker_name(value)
                        if name:
                            found.add(f"param-{name}")
    return found


def _module_sequences(body: list[ast.stmt]) -> dict[str, ast.expr]:
    """Module-level `NAME = [...]` / `(...)` assignments, for parametrize values by name."""
    found: dict[str, ast.expr] = {}
    for stmt in body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, value = stmt.target, stmt.value
        else:
            continue
        if isinstance(target, ast.Name) and isinstance(value, ast.List | ast.Tuple):
            found[target.id] = value
    return found


def _parametrize(
    decorators: list[ast.expr],
    consts: dict[str, ast.expr],
) -> tuple[frozenset[str], int | None, frozenset[str], tuple[ParamCase, ...]]:
    """Argnames, case count, literal values and cases of a test's parametrize decorators.

    Stacked decorators multiply the count. It is None when there are none, or when any
    argvalues isn't a literal list or tuple (or a module-level name bound to one).
    """
    names: set[str] = set()
    literals: set[str] = set()
    counts: list[int | None] = []
    rows: list[ParamCase] = []
    for dec in decorators:
        if not isinstance(dec, ast.Call) or _marker_name(dec) != "parametrize":
            continue
        argnames = _call_arg(dec, 0, "argnames")
        argvalues = _call_arg(dec, 1, "argvalues")
        if isinstance(argvalues, ast.Name):
            argvalues = consts.get(argvalues.id, argvalues)
        own: list[str] = []
        if isinstance(argnames, ast.Constant) and isinstance(argnames.value, str):
            own = [n.strip() for n in argnames.value.split(",") if n.strip()]
        elif isinstance(argnames, ast.List | ast.Tuple):
            own = [e.value for e in argnames.elts if isinstance(e, ast.Constant) and e.value]
        names.update(own)
        counts.append(len(argvalues.elts) if isinstance(argvalues, ast.List | ast.Tuple) else None)
        if argvalues is not None:
            literals.update(literal_reprs(argvalues))
        if own and isinstance(argvalues, ast.List | ast.Tuple):
            rows.extend(_param_cases(tuple(own), argvalues.elts))
    count = None if not counts or None in counts else math.prod(counts)
    return frozenset(names), count, frozenset(literals), tuple(rows)


def _param_cases(names: tuple[str, ...], elts: list[ast.expr]) -> Iterator[ParamCase]:
    """Cases whose values line up with the argnames; pytest.param ids and marks are left out."""
    for elt in elts:
        values: list[ast.expr] = [elt]
        if isinstance(elt, ast.Call) and dotted(elt.func).endswith("param"):
            values = list(elt.args)
        elif len(names) > 1 and isinstance(elt, ast.Tuple | ast.List):
            values = list(elt.elts)
        if len(values) == len(names):
            yield ParamCase(names, tuple(ast.unparse(v) for v in values), elt.lineno)


def literal_reprs(node: ast.AST) -> set[str]:
    """repr() of every literal constant under node; a signed number like -1 counts as one."""
    found: set[str] = set()
    signed: set[int] = set()
    for n in ast.walk(node):
        if (
            isinstance(n, ast.UnaryOp)
            and isinstance(n.op, ast.USub | ast.UAdd)
            and isinstance(n.operand, ast.Constant)
            and isinstance(n.operand.value, int | float | complex)
        ):
            found.add(repr(ast.literal_eval(n)))
            signed.add(id(n.operand))
        elif isinstance(n, ast.Constant) and id(n) not in signed:
            found.add(repr(n.value))
    return found


def _call_arg(call: ast.Call, index: int, keyword: str) -> ast.expr | None:
    if len(call.args) > index:
        return call.args[index]
    return next((kw.value for kw in call.keywords if kw.arg == keyword), None)


def _pytestmark(body: list[ast.stmt]) -> frozenset[str]:
    markers: set[str] = set()
    for stmt in body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets
        ):
            values = (
                stmt.value.elts if isinstance(stmt.value, ast.List | ast.Tuple) else [stmt.value]
            )
            markers |= _decorator_markers(values)
    return frozenset(markers)


# --- test bodies ------------------------------------------------------------------------


def _test_case(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
    classes: tuple[str, ...],
    inherited: frozenset[str],
    consts: dict[str, ast.expr],
    helpers: Helpers,
) -> TestCase:
    stmts = _strip_docstring(node.body)
    case = TestCase(
        path=path,
        name=node.name,
        classes=classes,
        line=node.lineno,
        end_line=node.end_lineno or node.lineno,
        markers=inherited | _decorator_markers(node.decorator_list),
        is_empty=all(_is_noop(s) for s in stmts),
        returns_first=bool(stmts) and isinstance(stmts[0], ast.Return),
        body_text="\n".join(ast.unparse(s) for s in stmts),
    )
    case.param_names, case.param_cases, case.param_literals, case.param_rows = _parametrize(
        node.decorator_list, consts
    )
    case.cannot_fail = _cannot_fail(stmts, case)
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call):
            target = _mock_target(dec)
            if target is not None:
                case.mocks.append(target)

    captures = {"recwarn"} & {a.arg for a in node.args.args}
    py2 = _py2_only_nodes(stmts)
    calls: set[str] = set()
    for sub in _walk_body(stmts):
        dead = id(sub) in py2
        if isinstance(sub, ast.Assert):
            case.assertions.append(Assertion("assert", "", ast.unparse(sub), sub.lineno, dead))
        elif isinstance(sub, ast.Try | ast.TryStar):
            case.try_blocks.append(_try_block(sub, helpers, stmts))
        elif isinstance(sub, ast.With | ast.AsyncWith) and (block := _suppress(sub, helpers)):
            case.try_blocks.append(block)
        elif isinstance(sub, ast.withitem):
            captures.update(_capture(sub))
        elif isinstance(sub, ast.Call):
            inlined = helpers.assertions(dotted(sub.func), sub.lineno)
            if inlined is not None:
                case.assertions.extend(replace(a, py2_only=dead) for a in inlined)
                case.call_count += 1
            else:
                calls.add(dotted(sub.func).rsplit(".", 1)[-1])
                _classify_call(sub, case, dead)
    case.captures = frozenset(captures)
    case.external_calls = frozenset(calls)
    case.mocked_calls = frozenset(m.rsplit(".", 1)[-1] for m in case.mocks) & case.external_calls
    return case


def _capture(item: ast.withitem) -> set[str]:
    """The name a `with` binds to caught warnings or an expected exception, if it does."""
    call, target = item.context_expr, item.optional_vars
    if not isinstance(call, ast.Call) or not isinstance(target, ast.Name):
        return set()
    name = dotted(call.func)
    if name in _RAISES_CALLS or name.rsplit(".", 1)[-1] in _CAPTURE_CALLS:
        return {target.id}
    if name.endswith("catch_warnings") and any(
        kw.arg == "record" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in call.keywords
    ):
        return {target.id}
    return set()


# --- Python 2 branches ------------------------------------------------------------------

_PY2_NAME = re.compile(r"(IS_)?PY(THON)?_?2\d?")
_PY3_NAME = re.compile(r"(IS_)?PY(THON)?_?3")
_COMPARE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}
# Interpreter versions a Python 3 test suite can run on, for evaluating version checks.
_PY3_VERSIONS = ((3, 0, 0), (3, 5, 0), (3, 13, 0))


def _py2_only_nodes(stmts: list[ast.stmt]) -> set[int]:
    """ids of the nodes in branches that only run on Python 2."""
    dead: set[int] = set()
    for node in _walk_body(stmts):
        if isinstance(node, ast.If) and id(node) not in dead:
            gate = py2_gate(node.test)
            branch = node.body if gate is True else node.orelse if gate is False else []
            dead.update(id(n) for n in _walk_body(branch))
    return dead


def py2_gate(expr: ast.expr) -> bool | None:
    """True if expr can only be true on Python 2, False if it can only be false there.

    `PY2`, `six.PY2`, `sys.version_info[0] == 2`, `sys.version_info < (3,)` -> True;
    `PY3`, `not PY2`, `sys.version_info >= (3,)` -> False; anything else -> None. A check
    on a Python 3 minor version (`< (3, 8)`) is None: that branch may still run.
    """
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        inner = py2_gate(expr.operand)
        return None if inner is None else not inner
    if isinstance(expr, ast.BoolOp):
        gates = [py2_gate(v) for v in expr.values]
        # `PY2 and x` is true only on Python 2; `PY3 or x` is false only there
        wanted = isinstance(expr.op, ast.And)
        return wanted if wanted in gates else None
    if isinstance(expr, ast.Name | ast.Attribute):
        last = dotted(expr).rsplit(".", 1)[-1]
        if _PY2_NAME.fullmatch(last):
            return True
        if _PY3_NAME.fullmatch(last):
            return False
        return None
    if isinstance(expr, ast.Compare) and len(expr.ops) == 1:
        return _version_gate(expr.left, expr.ops[0], expr.comparators[0])
    return None


def _version_gate(left: ast.expr, op: ast.cmpop, right: ast.expr) -> bool | None:
    compare = _COMPARE_OPS.get(type(op))
    try:
        bound = ast.literal_eval(right)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    if compare is None:
        return None
    if dotted(left).endswith("version_info") and isinstance(bound, tuple):
        values = list(_PY3_VERSIONS)
    elif _is_major(left) and isinstance(bound, int):
        values = [v[0] for v in _PY3_VERSIONS]
    else:
        return None
    try:
        results = [compare(v, bound) for v in values]
    except TypeError:
        return None
    if not any(results):
        return True
    if all(results):
        return False
    return None


def _is_major(node: ast.expr) -> bool:
    """`sys.version_info[0]` or `sys.version_info.major`."""
    if isinstance(node, ast.Subscript):
        index = node.slice
        return (
            dotted(node.value).endswith("version_info")
            and isinstance(index, ast.Constant)
            and index.value == 0
        )
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "major"
        and (dotted(node.value).endswith("version_info"))
    )


_Function = ast.FunctionDef | ast.AsyncFunctionDef


def _helper_defs(body: list[ast.stmt], prefix: str) -> dict[str, _Function]:
    """Functions in this scope, keyed by how a test calls them (`name` or `self.name`)."""
    return {prefix + n.name: n for n in body if isinstance(n, _FUNCTIONS)}


class Helpers:
    """Helper functions defined in the test file: module-level ones, and `self.` methods.

    A test that calls a helper is credited with the helper's assertions, so moving checks into
    `def assert_cart(cart, total): assert cart.total() == total` keeps the count. A helper
    without assertions is an ordinary call, whatever its name.
    """

    def __init__(self, defs: dict[str, _Function]):
        self.defs = defs
        self._cache: dict[str, list[Assertion]] = {}

    def with_methods(self, methods: dict[str, _Function]) -> Helpers:
        return Helpers({**self.defs, **methods}) if methods else self

    def assertions(self, name: str, line: int) -> list[Assertion] | None:
        """The helper's assertions, placed at the calling line; None if name isn't a helper."""
        if name not in self.defs:
            return None
        if name not in self._cache:
            self._cache[name] = []  # a recursive helper stops here
            self._cache[name] = self._collect(self.defs[name])
        via = name.rsplit(".", 1)[-1]
        return [Assertion(a.kind, a.name, a.text, line, via=via) for a in self._cache[name]]

    def _collect(self, node: _Function) -> list[Assertion]:
        found: list[Assertion] = []
        for sub in _walk_body(_strip_docstring(node.body)):
            if isinstance(sub, ast.Assert):
                found.append(Assertion("assert", "", ast.unparse(sub), sub.lineno))
            elif isinstance(sub, ast.Call):
                inner = self.assertions(dotted(sub.func), sub.lineno)
                if inner is not None:
                    found.extend(inner)
                    continue
                assertion = _call_assertion(sub)
                if assertion is not None:
                    found.append(assertion)
        return found


def _call_assertion(call: ast.Call) -> Assertion | None:
    name = dotted(call.func)
    last = name.rsplit(".", 1)[-1]
    if last.lower().startswith("assert"):
        return Assertion("call", last, ast.unparse(call), call.lineno)
    if name in _RAISES_CALLS:
        return Assertion("raises", last, ast.unparse(call), call.lineno)
    return None


def _classify_call(call: ast.Call, case: TestCase, py2_only: bool = False) -> None:
    name = dotted(call.func)
    last = name.rsplit(".", 1)[-1]
    assertion = _call_assertion(call)
    if assertion is not None:
        case.assertions.append(replace(assertion, py2_only=py2_only))
    elif name in _SKIP_CALLS or last == "skipTest":
        case.skip_calls.append(name)
    else:
        target = _mock_target(call)
        if target is not None:
            case.mocks.append(target)
        else:
            case.call_count += 1


def _mock_target(call: ast.Call) -> str | None:
    """Return what a patch/monkeypatch call replaces, or None if it isn't one."""
    parts = dotted(call.func).split(".")
    args = call.args
    # Only mock libraries: `client.patch("/items/1")` is an HTTP PATCH, not a mock.
    if "patch" in parts and (parts[0] in _PATCH_ROOTS or parts[0].endswith("mocker")):
        if parts[-1] == "object" and len(args) >= 2:
            return f"{_target_text(args[0])}.{_target_text(args[1])}"
        if args:
            return _target_text(args[0])
        return None
    if len(parts) >= 2 and parts[-2] == "monkeypatch" and parts[-1] in ("setattr", "delattr"):
        if len(args) >= 2 and not (isinstance(args[0], ast.Constant) and parts[-1] == "setattr"):
            return f"{_target_text(args[0])}.{_target_text(args[1])}"
        if args:
            return _target_text(args[0])
    return None


def _target_text(node: ast.expr) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ast.unparse(node)


def _try_block(node: ast.Try | ast.TryStar, helpers: Helpers, test: list[ast.stmt]) -> TryBlock:
    wraps = _wraps_assertion(node.body, helpers)
    checked = _checked_names(test, node)
    return TryBlock(
        line=node.lineno,
        handlers=tuple(ast.unparse(h.type) if h.type else "" for h in node.handlers),
        wraps_assertion=wraps,
        handler_is_noop=bool(node.handlers)
        and all(all(_is_noop(s) for s in _strip_docstring(h.body)) for h in node.handlers),
        swallows=wraps
        and not _fails(node.orelse)  # `else: raise AssertionError(...)`: a failure is expected
        and any(
            _catches_failure(h.type) and not _fails(h.body) and not _flags(h.body, checked)
            for h in node.handlers
        ),
    )


def _suppress(node: ast.With | ast.AsyncWith, helpers: Helpers) -> TryBlock | None:
    """`with contextlib.suppress(AssertionError):` around a check, as a try block."""
    caught = [
        arg
        for item in node.items
        if isinstance(call := item.context_expr, ast.Call)
        and dotted(call.func).rsplit(".", 1)[-1] == "suppress"
        for arg in call.args
    ]
    if not any(_catches_failure(arg) for arg in caught):
        return None
    wraps = _wraps_assertion(node.body, helpers)
    names = tuple(ast.unparse(arg) for arg in caught)
    return TryBlock(node.lineno, names, wraps, handler_is_noop=True, swallows=wraps)


# Exceptions whose handler also catches a failed assert or self.assertX (AssertionError).
_FAILURE_CATCHERS = frozenset({"AssertionError", "Exception", "BaseException", "failureException"})


def _catches_failure(node: ast.expr | None) -> bool:
    if node is None:
        return True  # bare except
    if isinstance(node, ast.Tuple):
        return any(_catches_failure(e) for e in node.elts)
    return dotted(node).rsplit(".", 1)[-1] in _FAILURE_CATCHERS


def _wraps_assertion(stmts: list[ast.stmt], helpers: Helpers) -> bool:
    return any(
        _is_assertion(n)
        or (isinstance(n, ast.Call) and bool(helpers.assertions(dotted(n.func), 0)))
        for n in _walk_body(stmts)
    )


def _checked_names(test: list[ast.stmt], skip: ast.AST) -> set[str]:
    """Names read by the test's assertions outside `skip`."""
    inside = {id(n) for n in ast.walk(skip)}
    return {
        name.id
        for n in _walk_body(test)
        if id(n) not in inside and _is_assertion(n)
        for name in ast.walk(n)
        if isinstance(name, ast.Name)
    }


def _flags(stmts: list[ast.stmt], checked: set[str]) -> bool:
    """`except AssertionError: failed = True` with `assert failed` later: the failure is the
    expected outcome and is checked."""
    return any(
        isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id in checked
        for s in stmts
        for n in ast.walk(s)
    )


def _fails(stmts: list[ast.stmt]) -> bool:
    """The statements re-raise, check something or fail the test on some path."""
    return any(
        isinstance(n, ast.Raise | ast.Assert)
        or (
            isinstance(n, ast.Call)
            and (_is_assertion(n) or dotted(n.func).rsplit(".", 1)[-1] == "fail")
        )
        for n in _walk_body(stmts)
    )


def _is_assertion(node: ast.AST) -> bool:
    if isinstance(node, ast.Assert):
        return True
    if isinstance(node, ast.Call):
        name = dotted(node.func)
        return name.rsplit(".", 1)[-1].lower().startswith("assert") or name in _RAISES_CALLS
    return False


def _walk_body(stmts: list[ast.stmt]) -> Iterator[ast.AST]:
    """Walk statements depth-first, not descending into nested functions, classes or lambdas."""
    stack: list[ast.AST] = list(reversed(stmts))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _NESTED_SCOPES):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            return body[1:]
    return body


def _cannot_fail(stmts: list[ast.stmt], case: TestCase) -> bool:
    """Every statement is a no-op or binds a name to a value built from literals only.

    Parametrize argnames count as literals when every case is listed with literal values;
    the body is then evaluated once per case.
    """
    if not case.param_names:
        return _binds_only_literals(stmts, {})
    if case.param_cases != len(case.param_rows):
        return False  # stacked decorators, or cases that aren't listed
    for row in case.param_rows:
        try:
            env = {n: ast.literal_eval(v) for n, v in zip(row.names, row.values, strict=True)}
        except (ValueError, SyntaxError, TypeError):
            return False  # pytest.param(...), a call, a name
        if not _binds_only_literals(stmts, env):
            return False
    return True


def _binds_only_literals(stmts: list[ast.stmt], env: dict[str, object]) -> bool:
    env = dict(env)
    for stmt in stmts:
        if _is_noop(stmt):
            continue
        if isinstance(stmt, ast.Assign):
            targets, value = stmt.targets, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets, value = [stmt.target], stmt.value
        else:
            return False
        if not all(isinstance(t, ast.Name) for t in targets):
            return False
        try:
            result = _literal_value(value, env)
        except Exception:  # unknown node, or the expression itself raises (1 / 0, "a" - 1)
            return False
        env.update((t.id, result) for t in targets if isinstance(t, ast.Name))
    return True


_LITERAL_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul}
# Bounds on literal arithmetic: "-" * 20 is fine; "x" * 10**9 is not worth building.
_MAX_LITERAL_FACTOR = 10_000


def _literal_value(node: ast.expr, env: dict[str, object]) -> object:
    """Evaluate an expression made only of literals, names in env and + - *; raise on
    anything else."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env[node.id]  # KeyError: a fixture, a global, a builtin
    if isinstance(node, ast.Tuple | ast.List):
        items = [_literal_value(e, env) for e in node.elts]
        return tuple(items) if isinstance(node, ast.Tuple) else items
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        value = _literal_value(node.operand, env)
        if not isinstance(value, int | float | complex):
            raise TypeError(type(value).__name__)
        return value
    if isinstance(node, ast.BinOp) and type(node.op) in _LITERAL_OPS:
        left, right = _literal_value(node.left, env), _literal_value(node.right, env)
        if isinstance(node.op, ast.Mult) and any(
            isinstance(v, int) and abs(v) > _MAX_LITERAL_FACTOR for v in (left, right)
        ):
            raise ValueError("too large")
        value = _LITERAL_OPS[type(node.op)](left, right)
        if isinstance(value, str | bytes | list | tuple) and len(value) > _MAX_LITERAL_FACTOR:
            raise ValueError("too large")  # "x" * 9999 * 9999
        return value
    raise ValueError(ast.unparse(node))


def _is_noop(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Pass | ast.Continue):
        return True
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    )


def dotted(node: ast.AST) -> str:
    """'self.assertEqual', 'pytest.mark.skip', 'mocker.patch.object'; '' if not a name chain."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return dotted(node.func)
    return ""
