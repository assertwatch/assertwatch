"""Python extractor: pytest and unittest test files -> TestFile.

Collection follows pytest's defaults: files named test_*.py or *_test.py; module-level
functions named test*; methods named test* in classes named Test* (without __init__) and in
unittest.TestCase subclasses, including nested classes and test methods inherited from classes
in the same file. `__test__ = False` disables collection.

Known limits:
- Test classes imported from other modules (or subclassed across files) are not followed; their
  tests are reported in the file that defines them.
- Assertions inside helper functions or methods called by a test are not counted for that test.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Iterator

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
    for node in tree.body:
        if isinstance(node, _FUNCTIONS) and node.name.startswith("test"):
            result.tests.append(_test_case(node, path, (), module_markers, consts))
        elif isinstance(node, ast.ClassDef):
            result.tests.extend(_collect_class(node, path, (), module_markers, local, consts))
    return result


# --- collection -------------------------------------------------------------------------


def _collect_class(
    node: ast.ClassDef,
    path: str,
    outer: tuple[str, ...],
    inherited: frozenset[str],
    local: dict[str, ast.ClassDef],
    consts: dict[str, ast.expr],
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
    tests: list[TestCase] = []
    seen: set[str] = set()
    for cls in lineage:  # own methods first, then inherited ones not overridden
        for child in cls.body:
            if isinstance(child, _FUNCTIONS) and child.name.startswith("test"):
                if child.name not in seen:
                    seen.add(child.name)
                    tests.append(_test_case(child, path, classes, markers, consts))
    if not is_unittest:  # pytest doesn't collect classes nested in a unittest.TestCase
        nested = _local_classes(node.body)
        for child in node.body:
            if isinstance(child, ast.ClassDef):
                tests.extend(_collect_class(child, path, classes, markers, nested, consts))
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
    return frozenset(markers)


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
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call):
            target = _mock_target(dec)
            if target is not None:
                case.mocks.append(target)

    for sub in _walk_body(stmts):
        if isinstance(sub, ast.Assert):
            case.assertions.append(Assertion("assert", "", ast.unparse(sub), sub.lineno))
        elif isinstance(sub, ast.Try | ast.TryStar):
            case.try_blocks.append(_try_block(sub))
        elif isinstance(sub, ast.Call):
            _classify_call(sub, case)
    return case


def _classify_call(call: ast.Call, case: TestCase) -> None:
    name = dotted(call.func)
    last = name.rsplit(".", 1)[-1]
    if last.lower().startswith("assert"):
        case.assertions.append(Assertion("call", last, ast.unparse(call), call.lineno))
    elif name in _RAISES_CALLS:
        case.assertions.append(Assertion("raises", last, ast.unparse(call), call.lineno))
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


def _try_block(node: ast.Try | ast.TryStar) -> TryBlock:
    return TryBlock(
        line=node.lineno,
        handlers=tuple(ast.unparse(h.type) if h.type else "" for h in node.handlers),
        wraps_assertion=any(_is_assertion(n) for n in _walk_body(node.body)),
        handler_is_noop=bool(node.handlers)
        and all(all(_is_noop(s) for s in _strip_docstring(h.body)) for h in node.handlers),
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
