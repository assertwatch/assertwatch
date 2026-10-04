"""Java test extractor: JUnit 5 (and JUnit 4) tests with JUnit, AssertJ and Hamcrest checks.

A small hand-written tokenizer, not a Java parser: test code needs only tokens and brace
structure (classes, annotated methods, call statements), and it keeps the core free of
dependencies. Anything it can't make sense of (an unterminated string, unbalanced braces)
makes the whole file a parse error, so its tests are unknown rather than removed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from fnmatch import fnmatchcase

from assertwatch.model import Assertion, ParamCase, TestCase, TestFile, TryBlock
from assertwatch.paths import JAVA_TEST_GLOBS

# Annotations that make a method a test.
_TEST_ANNOTATIONS = frozenset(
    {"Test", "ParameterizedTest", "RepeatedTest", "TestFactory", "TestTemplate"}
)
_NON_VOID_TESTS = frozenset({"TestFactory", "TestTemplate"})
_SKIP_ANNOTATIONS = frozenset({"Disabled", "Ignore"})
_CONDITION = re.compile(r"(Disabled|Enabled)(If|On|For)\w*")
# Statement-level calls that end a test early without failing it.
_SKIP_CALLS = frozenset({"assumeTrue", "assumeFalse", "assumeThat", "assumingThat", "abort"})
_SKIP_EXCEPTIONS = frozenset(
    {"TestAbortedException", "AssumptionViolatedException", "TestSkippedException"}
)
_RAISES_CALLS = frozenset({"assertThrows", "assertThrowsExactly", "assertThatThrownBy"})
_ASSERT_QUALIFIERS = frozenset(
    {"Assertions", "Assert", "Assumptions", "Assume", "MatcherAssert", "Mockito", "BDDAssertions"}
)
_KEYWORDS = frozenset(
    {
        "if", "else", "for", "while", "do", "switch", "case", "default", "try", "catch",
        "finally", "return", "throw", "new", "synchronized", "this", "super", "assert",
        "yield", "break", "continue",
    }
)  # fmt: skip
_TYPE_KEYWORDS = frozenset({"class", "interface", "enum", "record"})
_MODIFIERS = frozenset(
    {"public", "protected", "private", "static", "final", "abstract", "sealed", "strictfp",
     "default", "synchronized", "native", "transient", "volatile"}
)  # fmt: skip
_SOURCES_UNCOUNTABLE = frozenset(
    {"MethodSource", "CsvFileSource", "ArgumentsSource", "FieldSource", "EnumSource"}
)


@dataclass(frozen=True)
class Token:
    kind: str  # "id", "num", "str", "char", "op"
    text: str
    line: int


class JavaSyntaxError(Exception):
    pass


# --- tokens -----------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<line_comment>//[^\n]*)
  | (?P<block_comment>/\*.*?\*/)
  | (?P<text_block>\"\"\"[ \t\f]*\n(?:[^\\]|\\.)*?\"\"\")
  | (?P<str>"(?:[^"\\\n]|\\.)*")
  | (?P<char>'(?:[^'\\\n]|\\.)+')
  | (?P<num>0[xX][\da-fA-F_]+[lL]?|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?[fFdDlL]?)
  | (?P<id>[A-Za-z_$][\w$]*)
  | (?P<op>->|::|\.\.\.|==|!=|<=|>=|&&|\|\||\+\+|--|[+\-*/%&|^]=|<<=?
          |[{}()\[\];,.@=<>!~?:&|+\-*/^%])
    """,
    re.VERBOSE | re.DOTALL,
)


_HEX = frozenset("0123456789abcdefABCDEF")
_SURROGATES = re.compile("[\ud800-\udbff][\udc00-\udfff]|[\ud800-\udfff]")


def unicode_escapes(source: str) -> str:
    """Translate `\\uXXXX` escapes the way javac does before it reads anything else.

    Without this, `"x\\u005c"; check(); //"` is a closed string and a call to this reader but
    one long string to javac. A backslash starts an escape only after an even number of
    backslashes, and a translated backslash never starts another escape (JLS 3.3). One pass
    over the text: no regular expression to backtrack on long runs of backslashes.

    A `\\uD83D\\uDE00` pair becomes the one character javac reads; a lone surrogate stays as
    its escape text, so the result can be encoded and two different lone ones still differ.
    """
    if "\\u" not in source:
        return source
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        run = i
        while run < n and source[run] == "\\":
            run += 1
        slashes = run - i
        u = run
        while u < n and source[u] == "u":
            u += 1
        digits = source[u : u + 4]
        if slashes % 2 == 1 and u > run and len(digits) == 4 and set(digits) <= _HEX:
            out.append("\\" * (slashes - 1) + chr(int(digits, 16)))
            i = u + 4
        else:
            out.append("\\" * slashes)
            i = run
    return _SURROGATES.sub(_surrogate, "".join(out))


def _surrogate(match: re.Match[str]) -> str:
    text = match.group()
    if len(text) == 2:
        return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le")
    return f"\\u{ord(text):04X}"


def tokenize(source: str) -> list[Token]:
    source = unicode_escapes(source)
    tokens: list[Token] = []
    pos, line = 0, 1
    while pos < len(source):
        match = _TOKEN.match(source, pos)
        if match is None:
            raise JavaSyntaxError(f"unexpected {source[pos]!r} on line {line}")
        kind, text = match.lastgroup or "", match.group()
        if kind in ("text_block", "str"):
            tokens.append(Token("str", text, line))
        elif kind in ("char", "num", "id", "op"):
            tokens.append(Token(kind, text, line))
        line += text.count("\n")
        pos = match.end()
    return tokens


_OPEN = {"(": ")", "{": "}", "[": "]"}
_NO_SPACE_BEFORE = frozenset({")", "]", ",", ".", ";", "::"})
_NO_SPACE_AFTER = frozenset({"(", "[", ".", "@", "::", "!"})


def join(tokens: list[Token]) -> str:
    """Normalized source text: the same for any formatting of the same tokens."""
    out: list[str] = []
    for i, tok in enumerate(tokens):
        prev = tokens[i - 1] if i else None
        call = (
            tok.text in ("(", "[")
            and prev is not None
            and (prev.kind == "id" or prev.text in (")", "]", ">"))
        )
        if (
            prev
            and not call
            and tok.text not in _NO_SPACE_BEFORE
            and (prev.text not in _NO_SPACE_AFTER)
        ):
            out.append(" ")
        out.append(tok.text)
    return "".join(out)


def _close(tokens: list[Token], start: int) -> int:
    """Index of the bracket that closes tokens[start]."""
    depth: list[str] = []
    for i in range(start, len(tokens)):
        text = tokens[i].text
        if tokens[i].kind == "op" and text in _OPEN:
            depth.append(_OPEN[text])
        elif tokens[i].kind == "op" and text in (")", "}", "]"):
            if not depth or depth.pop() != text:
                raise JavaSyntaxError(f"unbalanced {text!r} on line {tokens[i].line}")
            if not depth:
                return i
    raise JavaSyntaxError(f"unclosed {tokens[start].text!r} on line {tokens[start].line}")


# --- structure --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Annotation:
    name: str  # last part: "Test" for @org.junit.jupiter.api.Test
    args: list[Token]  # inside the parentheses, if any


def _annotation(tokens: list[Token], i: int) -> tuple[_Annotation, int]:
    """Parse the annotation starting at tokens[i] == '@'; return it and the next index."""
    j = i + 1
    name = tokens[j].text
    while j + 2 < len(tokens) and tokens[j + 1].text == "." and tokens[j + 2].kind == "id":
        j += 2
        name = tokens[j].text
    j += 1
    args: list[Token] = []
    if j < len(tokens) and tokens[j].text == "(":
        end = _close(tokens, j)
        args = tokens[j + 1 : end]
        j = end + 1
    return _Annotation(name, args), j


def extract(source: str, path: str) -> TestFile:
    """Tokenize source and return the tests JUnit would run from it."""
    try:
        tokens = tokenize(source)
        result = TestFile(path=path, imports=_imports(tokens))
        result.tests = list(_members(tokens, 0, len(tokens), path, (), frozenset(), False))
    except (JavaSyntaxError, IndexError) as exc:
        return TestFile(path=path, parse_error=f"SyntaxError: {exc}")
    return result


def _imports(tokens: list[Token]) -> frozenset[str]:
    found = set()
    for i, tok in enumerate(tokens):
        if tok.text == "import" and (i == 0 or tokens[i - 1].text in (";", "}")):
            j = i + 1
            if tokens[j].text == "static":
                j += 1
            end = next(k for k in range(j, len(tokens)) if tokens[k].text == ";")
            found.add("".join(t.text for t in tokens[j:end]).removesuffix(".*"))
    return frozenset(found)


def _members(
    tokens: list[Token],
    start: int,
    end: int,
    path: str,
    classes: tuple[str, ...],
    inherited: frozenset[str],
    junit3: bool,
) -> Iterator[TestCase]:
    """Tests declared between start and end: a file's top level or a class body.

    Only what JUnit runs counts. An inner class runs only when it is a non-static `@Nested`
    class (Surefire skips other nested classes); a test method only when it is neither
    private nor static and returns void (`@TestFactory` and `@TestTemplate` excepted).
    A test that stops qualifying looks removed, which is what it is.
    """
    annotations: list[_Annotation] = []
    modifiers: set[str] = set()
    i = start
    while i < end:
        tok = tokens[i]
        if tok.text == "@" and i + 1 < end and tokens[i + 1].text != "interface":
            annotation, i = _annotation(tokens, i)
            annotations.append(annotation)
            continue
        if tok.text in _MODIFIERS:  # keep the annotations for the class or method that follows
            modifiers.add(tok.text)
            i += 1
            continue
        if tok.text in _TYPE_KEYWORDS and tok.kind == "id" and i + 1 < end:
            name = tokens[i + 1].text
            body = next(k for k in range(i, end) if tokens[k].text == "{")
            if tok.text == "record":  # skip the record header's parentheses
                header = next(k for k in range(i, end) if tokens[k].text == "(")
                body = next(k for k in range(_close(tokens, header), end) if tokens[k].text == "{")
            close = _close(tokens, body)
            header_text = {t.text for t in tokens[i:body]}
            markers = inherited | _markers(annotations, None)
            is_junit3 = "TestCase" in header_text and "extends" in header_text
            nested_ok = (
                any(fnmatchcase(f"{name}.java", glob) for glob in JAVA_TEST_GLOBS)
                if not classes  # Surefire picks top-level classes by name, not by file
                else any(a.name == "Nested" for a in annotations) and "static" not in modifiers
            )
            if tok.text == "class" and "abstract" not in modifiers and nested_ok:
                inner = (*classes, name)
                yield from _members(tokens, body + 1, close, path, inner, markers, is_junit3)
            annotations, modifiers, i = [], set(), close + 1
            continue
        if tok.text in (";", "}"):
            annotations, modifiers, i = [], set(), i + 1
            continue
        if tok.text == "{":  # an initializer block
            annotations, modifiers, i = [], set(), _close(tokens, i) + 1
            continue
        member_end, method = _member(tokens, i, end)
        if method is not None and classes:
            name_at, params, body = method
            if body is not None and _runs_as_test(
                tokens, name_at, params, annotations, modifiers, junit3
            ):
                where = (path, classes, inherited)
                yield _test_case(tokens, name_at, params, body, where, annotations)
        annotations, modifiers, i = [], set(), member_end


def _runs_as_test(
    tokens: list[Token],
    name_at: int,
    params: list[Token],
    annotations: list[_Annotation],
    modifiers: set[str],
    junit3: bool,
) -> bool:
    if "private" in modifiers or "static" in modifiers:
        return False
    returns_void = tokens[name_at - 1].text == "void"
    names = {a.name for a in annotations}
    if names & _TEST_ANNOTATIONS:
        return returns_void or bool(names & _NON_VOID_TESTS)
    name = tokens[name_at].text
    return (
        junit3 and name.startswith("test") and not params and "public" in modifiers and returns_void
    )


def _member(
    tokens: list[Token], i: int, end: int
) -> tuple[int, tuple[int, list[Token], tuple[int, int] | None] | None]:
    """Skip one field or method starting at i. Return the next index and, for a method,
    (name index, parameter tokens, body range or None)."""
    j = i
    while j < end:
        text = tokens[j].text
        if text == "<":  # type parameters or generic types
            depth = 0
            while j < end:
                depth += {"<": 1, ">": -1}.get(tokens[j].text, 0)
                j += 1
                if depth == 0:
                    break
            continue
        if text == "(" and tokens[j - 1].kind == "id":
            name_at = j - 1
            close = _close(tokens, j)
            params = tokens[j + 1 : close]
            k = close + 1
            while k < end and tokens[k].text not in ("{", ";"):
                k += 1  # throws clause, default value
            if k < end and tokens[k].text == "{":
                body_end = _close(tokens, k)
                return body_end + 1, (name_at, params, (k + 1, body_end))
            return k + 1, (name_at, params, None)
        if text in ("=", ";"):
            k = j
            while k < end and tokens[k].text != ";":
                k = _close(tokens, k) + 1 if tokens[k].text in _OPEN else k + 1
            return k + 1, None
        if text in _OPEN:
            j = _close(tokens, j) + 1
            continue
        j += 1
    return end, None


# --- markers and parametrized cases -----------------------------------------------------


def _markers(annotations: list[_Annotation], params: list[str] | None) -> frozenset[str]:
    markers = set()
    for a in annotations:
        if a.name in _SKIP_ANNOTATIONS:
            markers.add("skip")
        elif _CONDITION.fullmatch(a.name):
            markers.add("skipif")
        elif a.name == "ParameterizedTest":
            markers.add("parametrize")
        elif a.name == "Tag" and a.args:
            markers.add(f"tag:{join(a.args)}")
    return frozenset(markers)


def _param_names(params: list[Token]) -> tuple[str, ...]:
    names, depth, last = [], 0, None
    for tok in params:
        if tok.text in ("(", "<", "["):
            depth += 1
        elif tok.text in (")", ">", "]"):
            depth -= 1
        elif depth == 0 and tok.text == ",":
            if last:
                names.append(last)
            last = None
        elif depth == 0 and tok.kind == "id":
            last = tok.text
    if last:
        names.append(last)
    return tuple(names)


def _parametrized(
    annotations: list[_Annotation], names: tuple[str, ...]
) -> tuple[int | None, frozenset[str], tuple[ParamCase, ...]]:
    """Case count (None if not countable), literal values, and the listed cases."""
    count: int | None = 0
    literals: set[str] = set()
    rows: list[ParamCase] = []
    sources = 0
    for a in annotations:
        line = a.args[0].line if a.args else 0
        if a.name == "ValueSource":
            sources += 1
            values = [t.text for t in a.args if t.kind in ("num", "str", "char")]
            values += [t.text for t in a.args if t.text in ("true", "false")]
            values += [join([t]) for t in a.args if t.text.endswith(".class")]
            literals.update(values)
            rows.extend(ParamCase(names[:1], (v,), line) for v in values)
            count = None if count is None else count + len(values)
        elif a.name == "CsvSource":
            sources += 1
            parsed = _csv_rows(a.args)
            if parsed is None:
                count = None
                continue
            for row in parsed:
                literals.update(row)
                rows.append(ParamCase(names[: len(row)], tuple(row), line))
            count = None if count is None else count + len(parsed)
        elif a.name in ("NullSource", "EmptySource"):
            sources += 1
            count = None if count is None else count + 1
        elif a.name == "NullAndEmptySource":
            sources += 1
            count = None if count is None else count + 2
        elif a.name in _SOURCES_UNCOUNTABLE:
            sources += 1
            count = None
    if not sources:
        return None, frozenset(), ()
    return count, frozenset(literals), tuple(rows) if count is not None else ()


def _csv_rows(args: list[Token]) -> list[list[str]] | None:
    """Rows of a @CsvSource, from `{"a, 1", ...}`, a single string, or `textBlock = \"\"\"`."""
    if any(t.kind == "id" and t.text not in ("value", "textBlock") for t in args):
        return None  # delimiter = ..., nullValues = ...: not worth guessing
    rows = []
    for tok in (t for t in args if t.kind == "str"):
        if tok.text.startswith('"""'):
            body = tok.text[3:-3]
            lines = [ln.strip() for ln in body.splitlines()]
            rows += [_csv_split(ln) for ln in lines if ln and not ln.startswith("#")]
        else:
            rows.append(_csv_split(tok.text[1:-1]))
    return rows


def _csv_split(line: str) -> list[str]:
    values, current, quoted = [], [], False
    for ch in line:
        if ch == "'":
            quoted = not quoted
            current.append(ch)
        elif ch == "," and not quoted:
            values.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    values.append("".join(current).strip())
    return [_csv_value(v) for v in values]


_CSV_NUMBER = re.compile(r"[+-]?(\d[\d_]*(?:\.[\d_]*)?|\.\d+)([eE][+-]?\d+)?")


def _csv_value(value: str) -> str:
    """A CSV cell as a literal: numbers and booleans as written, anything else quoted."""
    if _CSV_NUMBER.fullmatch(value) or value in ("true", "false"):
        return value
    if len(value) >= 2 and value[0] == value[-1] == "'":
        value = value[1:-1]
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --- test bodies ------------------------------------------------------------------------


def _test_case(
    tokens: list[Token],
    name_at: int,
    params: list[Token],
    body: tuple[int, int],
    where: tuple[str, tuple[str, ...], frozenset[str]],
    annotations: list[_Annotation],
) -> TestCase:
    path, classes, inherited = where
    start, end = body
    stmts = tokens[start:end]
    names = _param_names(params)
    case = TestCase(
        path=path,
        name=tokens[name_at].text,
        classes=classes,
        line=tokens[name_at].line,
        end_line=tokens[end].line,
        markers=inherited | _markers(annotations, list(names)),
        is_empty=all(t.text == ";" for t in stmts),
        returns_first=bool(stmts) and stmts[0].text == "return",
        body_text=join(stmts),
    )
    case.cannot_fail = case.is_empty
    if "parametrize" in case.markers:
        case.param_names = frozenset(names)
        case.param_cases, case.param_literals, case.param_rows = _parametrized(annotations, names)
    _scan_body(stmts, case)
    return case


def _scan_body(stmts: list[Token], case: TestCase) -> None:
    dead = _not_run(stmts)
    i = 0
    while i < len(stmts):
        tok = stmts[i]
        if dead[i]:
            i += 1
            continue
        if tok.text == "try" and i + 1 < len(stmts) and stmts[i + 1].text in ("{", "("):
            case.try_blocks.append(_try_block(stmts, i))
        if (
            tok.text == "throw"
            and i + 2 < len(stmts)
            and stmts[i + 1].text == "new"
            and stmts[i + 2].text in _SKIP_EXCEPTIONS
        ):
            case.skip_calls.append(f"throw new {stmts[i + 2].text}")
        if not _statement_start(stmts, i):
            i += 1
            continue
        if tok.text == "assert" and tok.kind == "id":
            stop = _statement_end(stmts, i)
            case.assertions.append(_assertion("assert", "", stmts[i:stop], tok.line))
            i = stop
            continue
        start = i
        call = _call_name(stmts, i)
        if call is None and (value := _assigned_value(stmts, i)) is not None:
            start, call = value, _call_name(stmts, value)  # `X e = assertThrows(...)`
        if call is not None:
            name, at = call
            stop = _statement_end(stmts, start)
            if name in _SKIP_CALLS:
                case.skip_calls.append(join(stmts[start : at + 1]))
            elif _is_assert_call(name):
                kind = "raises" if name in _RAISES_CALLS else "call"
                case.assertions.append(_assertion(kind, name, stmts[start:stop], tok.line))
                i = stop
                continue
        i += 1
    if not case.returns_first and case.assertions:
        # `if (...) return;` before the checks ends the test early, like a return first
        last = max(k for k, t in enumerate(stmts) if t.line <= case.assertions[-1].line)
        case.returns_first = any(k < last for k in _own_returns(stmts))
    case.call_count = sum(
        1
        for k in range(len(stmts) - 1)
        if stmts[k].kind == "id"
        and stmts[k + 1].text == "("
        and stmts[k].text not in _KEYWORDS
        and not _is_assert_call(stmts[k].text)
        and stmts[k].text not in _SKIP_CALLS
    )


def _not_run(stmts: list[Token]) -> list[bool]:
    """Per token: is it in code the test declares but may never run? Checks there count
    for nothing, or a failing check could be parked where it never executes.

    - a lambda that isn't an argument of a call (`Runnable r = () -> check();`); one passed
      straight to a call (`forEach(x -> check(x))`, `assertAll(() -> ...)`) runs
    - the body of an anonymous class (`new Runnable() { ... }`) or a local class
    - the branch of `if (false)`
    """
    dead = [False] * len(stmts)

    def mark(start: int, stop: int) -> None:
        for k in range(start, min(stop, len(stmts))):
            dead[k] = True

    for k, tok in enumerate(stmts):
        if tok.text == "->" and not _argument_lambda(stmts, k):
            mark(k + 1, _lambda_end(stmts, k + 1))
        elif tok.text == "{" and k and stmts[k - 1].text == ")" and _anonymous_class(stmts, k - 1):
            mark(k, _close(stmts, k) + 1)
        elif tok.text == "class" and tok.kind == "id" and (k == 0 or stmts[k - 1].text != "."):
            body = next((j for j in range(k, len(stmts)) if stmts[j].text == "{"), None)
            if body is not None:
                mark(k, _close(stmts, body) + 1)
        elif (
            tok.text == "if"
            and k + 3 < len(stmts)
            and [t.text for t in stmts[k + 1 : k + 4]] == ["(", "false", ")"]
        ):
            mark(k + 4, _lambda_end(stmts, k + 4))
    return dead


def _argument_lambda(stmts: list[Token], arrow: int) -> bool:
    """The lambda ending its parameters at `arrow` is passed straight to a call."""
    head = arrow - 1
    if head >= 0 and stmts[head].text == ")":
        depth = 0
        for k in range(head, -1, -1):
            if stmts[k].text == ")":
                depth += 1
            elif stmts[k].text == "(":
                depth -= 1
                if depth == 0:
                    head = k
                    break
    return head > 0 and stmts[head - 1].text in ("(", ",")


def _lambda_end(stmts: list[Token], start: int) -> int:
    """Index past a lambda body or a single statement starting at start."""
    if start < len(stmts) and stmts[start].text == "{":
        return _close(stmts, start) + 1
    depth = 0
    for k in range(start, len(stmts)):
        text = stmts[k].text
        if text in _OPEN:
            depth += 1
        elif text in (")", "}", "]"):
            depth -= 1
            if depth < 0:
                return k
        elif depth == 0 and text in (";", ","):
            return k + 1
    return len(stmts)


def _own_returns(stmts: list[Token]) -> Iterator[int]:
    """Indexes of `return` statements of the test itself, not of a lambda or an anonymous
    class inside it."""
    nested: list[bool] = []  # per open brace: does it belong to a lambda or anonymous class?
    for k, tok in enumerate(stmts):
        if tok.text == "{":
            prev = stmts[k - 1].text if k else ""
            nested.append(prev == "->" or (prev == ")" and _anonymous_class(stmts, k - 1)))
        elif tok.text == "}":
            if nested:
                nested.pop()
        elif tok.text == "return" and not any(nested):
            yield k


def _anonymous_class(stmts: list[Token], close: int) -> bool:
    """The `)` at close ends `new Name(...)` or `new Name<T>(...)`."""
    depth = 0
    for k in range(close, -1, -1):
        if stmts[k].text == ")":
            depth += 1
        elif stmts[k].text == "(":
            depth -= 1
            if depth == 0:
                j = k - 1
                while j >= 0 and (stmts[j].kind == "id" or stmts[j].text in (".", "<", ">", ",")):
                    if stmts[j].text == "new":
                        return True
                    j -= 1
                return False
    return False


def _assertion(kind: str, name: str, tokens: list[Token], line: int) -> Assertion:
    return Assertion(kind, name, join(tokens), line, lang="java")


def _is_assert_call(name: str) -> bool:
    return name.startswith("assert") or name in ("fail", "verify", "verifyNoInteractions")


def _statement_start(stmts: list[Token], i: int) -> bool:
    return i == 0 or stmts[i - 1].text in (";", "{", "}", ")", "->", "else", ":")


def _assigned_value(stmts: list[Token], i: int) -> int | None:
    """Where the value starts in a declaration or assignment at i (`final Ex e = `,
    `Map<K, V> m = `, `this.e = `); None for any other statement."""
    for k in range(i, len(stmts)):
        text = stmts[k].text
        if text == "=":
            return k + 1 if k > i and stmts[k - 1].kind == "id" else None
        if not (
            stmts[k].kind == "id" or text in (".", "<", ">", ">>", ">>>", ",", "?", "[", "]", "&")
        ):
            return None
    return None


def _call_name(stmts: list[Token], i: int) -> tuple[str, int] | None:
    """`assertEquals(` or `Assertions.assertEquals(` at i: the called name and its index."""
    j = i
    if (
        stmts[j].text in _ASSERT_QUALIFIERS
        and j + 2 < len(stmts)
        and stmts[j + 1].text == "."
        and stmts[j + 2].kind == "id"
    ):
        j += 2
    if stmts[j].kind == "id" and j + 1 < len(stmts) and stmts[j + 1].text == "(":
        return stmts[j].text, j
    return None


def _statement_end(stmts: list[Token], i: int) -> int:
    """Index just past the statement starting at i: its `;`, or the end of an expression
    lambda's body (a `)` or `,` that closes past where it started)."""
    depth = 0
    for k in range(i, len(stmts)):
        text = stmts[k].text
        if text in _OPEN:
            depth += 1
        elif text in (")", "}", "]"):
            depth -= 1
            if depth < 0:
                return k
        elif depth == 0 and text == ";":
            return k + 1
        elif depth == 0 and text == ",":
            return k
    return len(stmts)


def _try_block(stmts: list[Token], i: int) -> TryBlock:
    j = i + 1
    if stmts[j].text == "(":  # try-with-resources
        j = _close(stmts, j) + 1
    body_end = _close(stmts, j)
    wraps = any(
        _statement_start(stmts, k)
        and _call_name(stmts, k) is not None
        and _is_assert_call(_call_name(stmts, k)[0])  # type: ignore[index]
        or stmts[k].text == "assert"
        for k in range(j + 1, body_end)
    )
    handlers: list[str] = []
    noop = True
    k = body_end + 1
    while k < len(stmts) and stmts[k].text == "catch":
        close = _close(stmts, k + 1)
        types = [t.text for t in stmts[k + 2 : close - 1] if t.kind == "id"]
        handlers.append(" | ".join(types))
        handler_end = _close(stmts, close + 1)
        if any(t.text != ";" for t in stmts[close + 2 : handler_end]):
            noop = False
        k = handler_end + 1
    return TryBlock(stmts[i].line, tuple(handlers), wraps, noop and bool(handlers))
