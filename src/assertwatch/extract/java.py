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


def split_args(tokens: list[Token]) -> list[list[Token]]:
    args: list[list[Token]] = []
    current: list[Token] = []
    depth = 0
    for tok in tokens:
        if tok.text in ("(", "[", "{"):
            depth += 1
        elif tok.text in (")", "]", "}"):
            depth -= 1
        elif (
            tok.text == "<"
            and current
            and current[-1].kind == "id"
            and current[-1].text[0].isupper()
        ):
            depth += 1  # a generic type argument: Map<String, Integer>
        elif tok.text == ">" and depth > 0 and any(t.text == "<" for t in current):
            depth -= 1
        if tok.text == "," and depth == 0:
            args.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        args.append(current)
    return args


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
        result.tests = list(
            _members(tokens, 0, len(tokens), path, (), frozenset(), False, _Helpers(tokens, {}))
        )
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
    helpers: _Helpers,
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
                scope = helpers.with_methods(_methods(tokens, body + 1, close))
                yield from _members(tokens, body + 1, close, path, inner, markers, is_junit3, scope)
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
                yield _test_case(tokens, name_at, params, body, where, annotations, helpers)
        annotations, modifiers, i = [], set(), member_end


@dataclass(frozen=True)
class _Method:
    start: int  # body range in the file's tokens
    end: int
    params: tuple[Token, ...]

    @property
    def arity(self) -> int | None:
        """Parameter count; None for varargs."""
        if any(t.text == "..." for t in self.params):
            return None
        return len(_param_names(list(self.params)))

    def accepts(self, types: tuple[str | None, ...]) -> bool:
        """Each argument whose type is known could be passed to its parameter."""
        declared = [_declared_type(p) for p in split_args(list(self.params))]
        return all(
            arg is None or param is None or param == "Object" or _same_type(arg, param)
            for arg, param in zip(types, declared, strict=False)
        )


def _methods(tokens: list[Token], start: int, end: int) -> dict[str, list[_Method]]:
    """The methods with a body declared directly in a class body, by name (overloads share
    one)."""
    found: dict[str, list[_Method]] = {}
    i = start
    while i < end:
        tok = tokens[i]
        if tok.text == "@" and i + 1 < end and tokens[i + 1].text != "interface":
            i = _annotation(tokens, i)[1]
        elif tok.text in _MODIFIERS or tok.text in (";", "}"):
            i += 1
        elif tok.text in _TYPE_KEYWORDS and tok.kind == "id" and i + 1 < end:
            body = next(k for k in range(i, end) if tokens[k].text == "{")
            if tok.text == "record":
                header = next(k for k in range(i, end) if tokens[k].text == "(")
                body = next(k for k in range(_close(tokens, header), end) if tokens[k].text == "{")
            i = _close(tokens, body) + 1
        elif tok.text == "{":
            i = _close(tokens, i) + 1
        else:
            i, method = _member(tokens, i, end)
            if method is not None and method[2] is not None:
                name_at, params, (body_start, body_end) = method
                found.setdefault(tokens[name_at].text, []).append(
                    _Method(body_start, body_end, tuple(params))
                )
    return found


_PRIMITIVES = frozenset({"int", "long", "short", "byte", "char", "boolean", "double", "float"})
_BOXED = {
    "Integer": "int", "Long": "long", "Short": "short", "Byte": "byte", "Character": "char",
    "Boolean": "boolean", "Double": "double", "Float": "float",
}  # fmt: skip


def _same_type(a: str, b: str) -> bool:
    return _BOXED.get(a, a) == _BOXED.get(b, b)


def _declared_type(param: list[Token]) -> str | None:
    """The simple type name of a parameter (`final Locale locale` -> "Locale"), None if it
    isn't a plain one (arrays, varargs)."""
    tokens = [t for t in param if t.text != "final"]
    while tokens and tokens[0].text == "@":
        tokens = tokens[2:]
    if len(tokens) < 2 or tokens[0].kind != "id" or any(t.text in ("[", "...") for t in tokens):
        return None
    return tokens[0].text


def _local_types(tokens: list[Token]) -> dict[str, str]:
    """Variables declared in a parameter list or body with a plain type: name -> type."""
    found: dict[str, str] = {}
    for k in range(1, len(tokens) - 1):
        name, before, after = tokens[k], tokens[k - 1], tokens[k + 1]
        if not (
            name.kind == "id" and before.kind == "id" and after.text in ("=", ";", ",", ")", ":")
        ):
            continue
        if before.text in _PRIMITIVES or (
            before.text[0].isupper() and (k < 2 or tokens[k - 2].text != ".")
        ):
            found.setdefault(name.text, before.text)
    return found


def _argument_types(args: list[list[Token]], local: dict[str, str]) -> tuple[str | None, ...]:
    """The type of each argument where it is plain to see: a literal or a declared variable."""
    types: list[str | None] = []
    for arg in args:
        tok = arg[0] if len(arg) == 1 else None
        if tok is None:
            types.append(None)
        elif tok.kind == "str":
            types.append("String")
        elif tok.kind == "char":
            types.append("char")
        elif tok.text in ("true", "false"):
            types.append("boolean")
        elif tok.kind == "id":
            types.append(local.get(tok.text))
        else:
            types.append(None)
    return tuple(types)


class _Helpers:
    """Methods of the test's class (and of the classes around it) that a test can call.

    A test that calls one is credited with its assertions, so moving checks into
    `private void assertCart(Cart cart, double total) { assertEquals(total, cart.total()); }`
    keeps the count, as in the Python reader. Overloads are told apart by argument count, then
    by the argument types that are plain to see (literals, declared variables); when that
    still leaves more than one, the one with the fewest assertions counts.
    """

    def __init__(self, tokens: list[Token], defs: dict[str, list[_Method]]):
        self.tokens = tokens
        self.defs = defs
        self._cache: dict[tuple[str, tuple[str | None, ...]], list[Assertion]] = {}
        self._external: dict[tuple[str, tuple[str | None, ...]], frozenset[str]] = {}

    def with_methods(self, methods: dict[str, list[_Method]]) -> _Helpers:
        return _Helpers(self.tokens, {**self.defs, **methods}) if methods else self

    def call(
        self, name: str, types: tuple[str | None, ...], line: int
    ) -> tuple[list[Assertion], frozenset[str]]:
        """The assertions of the helper called with arguments of these types (None where
        unknown), placed at the calling line, and the outside functions it calls."""
        key = (name, types)
        if key not in self._cache:
            self._cache[key] = []  # a recursive helper stops here
            self._external[key] = frozenset()
            overloads = self.defs[name]
            fits = [m for m in overloads if m.arity == len(types)] or [
                m for m in overloads if m.arity is None
            ]
            fits = [m for m in fits if m.accepts(types)] or fits
            found = [self._collect(m) for m in fits or overloads]
            self._cache[key], self._external[key] = min(found, key=lambda f: len(f[0]))
        checks = [
            Assertion(a.kind, a.name, a.text, line, lang="java", via=a.via or name)
            for a in self._cache[key]
        ]
        return checks, self._external[key]

    def _collect(self, method: _Method) -> tuple[list[Assertion], frozenset[str]]:
        """The checks in a helper's body, and the outside functions it (or a helper it calls)
        calls."""
        scratch = TestCase(path="", name="", classes=(), line=0, end_line=0, markers=frozenset())
        _scan_body(self.tokens[method.start : method.end], scratch, self, list(method.params))
        return scratch.assertions, scratch.external_calls


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
    helpers: _Helpers,
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
    _scan_body(stmts, case, helpers, params)
    expected = _expected_exception(annotations)
    if expected is not None:
        case.assertions.insert(0, expected)
    return case


def _expected_exception(annotations: list[_Annotation]) -> Assertion | None:
    """JUnit 4's `@Test(expected = X.class)`, an expected-exception check like assertThrows."""
    for a in annotations:
        if a.name != "Test":
            continue
        for arg in split_args(a.args):
            if len(arg) >= 3 and arg[0].text == "expected" and arg[1].text == "=":
                text = f"@Test({join(arg)})"
                return Assertion("raises", "expected", text, arg[0].line, lang="java")
    return None


def _scan_body(stmts: list[Token], case: TestCase, helpers: _Helpers, params: list[Token]) -> None:
    dead = _not_run(stmts)
    i = 0
    while i < len(stmts):
        tok = stmts[i]
        if dead[i]:
            i += 1
            continue
        if tok.text == "try" and i + 1 < len(stmts) and stmts[i + 1].text in ("{", "("):
            case.try_blocks.append(_try_block(stmts, i, helpers))
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
                if not (at == start and name in helpers.defs):  # the class's own helper
                    kind = "raises" if name in _RAISES_CALLS else "call"
                    case.assertions.append(_assertion(kind, name, stmts[start:stop], tok.line))
                i = stop
                continue
        i += 1
    external: set[str] = set()
    local = _local_types([*params, Token("op", ",", 0), *stmts])
    for k in _helper_calls(stmts, dead, helpers):
        types = _argument_types(split_args(stmts[k + 2 : _close(stmts, k + 1)]), local)
        checks, calls = helpers.call(stmts[k].text, types, stmts[k].line)
        case.assertions.extend(checks)
        external |= calls
    case.assertions.sort(key=lambda a: a.line)
    if not case.returns_first and case.assertions:
        # `if (...) return;` before the checks ends the test early, like a return first
        last = max(k for k, t in enumerate(stmts) if t.line <= case.assertions[-1].line)
        case.returns_first = any(k < last for k in _own_returns(stmts))
    case.external_calls = frozenset(external) | frozenset(
        stmts[k].text
        for k in range(len(stmts) - 1)
        if stmts[k].kind == "id"
        and stmts[k + 1].text == "("
        and stmts[k].text not in _KEYWORDS
        and stmts[k].text not in helpers.defs
    )
    case.call_count = sum(
        1
        for k in range(len(stmts) - 1)
        if stmts[k].kind == "id"
        and stmts[k + 1].text == "("
        and stmts[k].text not in _KEYWORDS
        and not _is_assert_call(stmts[k].text)
        and stmts[k].text not in _SKIP_CALLS
    )


def _helper_calls(stmts: list[Token], dead: list[bool], helpers: _Helpers) -> Iterator[int]:
    """Indexes of calls to a helper method of the class: `check(x)` or `this.check(x)`,
    anywhere in code that runs."""
    for k, tok in enumerate(stmts):
        if (
            not dead[k]
            and tok.kind == "id"
            and tok.text in helpers.defs
            and k + 1 < len(stmts)
            and stmts[k + 1].text == "("
        ):
            prev = stmts[k - 1].text if k else ""
            if prev == "new" or prev == "::":
                continue
            if prev == "." and not (k >= 2 and stmts[k - 2].text == "this"):
                continue
            yield k


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
    """JUnit/AssertJ/Hamcrest asserts, `fail`, Mockito `verify*`, and project helpers named
    the same way (`assertValidJson`, `verifyException`)."""
    return (
        name.startswith("assert")
        or name == "fail"
        or (name.startswith("verify") and (name == "verify" or name[6].isupper()))
    )


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
    elif _static_verify(stmts, j):
        j += 2
    if stmts[j].kind == "id" and j + 1 < len(stmts) and stmts[j + 1].text == "(":
        return stmts[j].text, j
    return None


def _static_verify(stmts: list[Token], j: int) -> bool:
    """`mocked.verify(() -> Util.call(x))`: a Mockito MockedStatic verification, whose
    argument is a lambda or method reference (a plain `x.verify(data)` is an ordinary call)."""
    if not (
        stmts[j].kind == "id"
        and j + 3 < len(stmts)
        and stmts[j + 1].text == "."
        and stmts[j + 2].text == "verify"
        and stmts[j + 3].text == "("
    ):
        return False
    args = split_args(stmts[j + 4 : _close(stmts, j + 3)])
    return bool(args) and any(t.text in ("->", "::") for t in args[0])


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


# Catching one of these also catches a failed assertEquals, assertThat or fail()
# (AssertionError and its subclasses); `Exception` doesn't.
_FAILURE_CATCHERS = frozenset(
    {
        "AssertionError", "AssertionFailedError", "ComparisonFailure", "MultipleFailuresError",
        "Error", "Throwable",
    }
)  # fmt: skip


def _try_block(stmts: list[Token], i: int, helpers: _Helpers) -> TryBlock:
    j = i + 1
    if stmts[j].text == "(":  # try-with-resources
        j = _close(stmts, j) + 1
    body_end = _close(stmts, j)
    wraps = any(
        _statement_start(stmts, k)
        and _call_name(stmts, k) is not None
        and _is_assert_call(_call_name(stmts, k)[0])  # type: ignore[index]
        or stmts[k].text == "assert"
        or _checking_helper(stmts, k, helpers)
        for k in range(j + 1, body_end)
    )
    handlers: list[str] = []
    noop = True
    swallows = False
    k = body_end + 1
    while k < len(stmts) and stmts[k].text == "catch":
        close = _close(stmts, k + 1)
        types = [t.text for t in stmts[k + 2 : close - 1] if t.kind == "id"]
        handlers.append(" | ".join(types))
        handler_end = _close(stmts, close + 1)
        handler = stmts[close + 2 : handler_end]
        if any(t.text != ";" for t in handler):
            noop = False
        if (
            any(t in _FAILURE_CATCHERS for t in types)
            and not _fails(handler)
            and not _flags(handler, stmts[handler_end + 1 :])
        ):
            swallows = True
        k = handler_end + 1
    if k < len(stmts) and stmts[k].text == "finally":
        k = _close(stmts, k + 1) + 1
    # `try { assertX(bad); } catch (AssertionError e) { return; } fail(...)`: a test of the
    # check itself, which expects it to fail
    after = _call_name(stmts, k) if k < len(stmts) else None
    expects_failure = (k < len(stmts) and stmts[k].text == "throw") or (
        after is not None and after[0] == "fail"
    )
    swallows = swallows and wraps and not expects_failure
    return TryBlock(stmts[i].line, tuple(handlers), wraps, noop and bool(handlers), swallows)


def _checking_helper(stmts: list[Token], k: int, helpers: _Helpers) -> bool:
    """A call at k to a helper of the class that makes checks."""
    tok = stmts[k]
    if tok.kind != "id" or tok.text not in helpers.defs or k + 1 >= len(stmts):
        return False
    if stmts[k + 1].text != "(" or (k and stmts[k - 1].text in ("new", "::")):
        return False
    types = tuple(None for _ in split_args(stmts[k + 2 : _close(stmts, k + 1)]))
    return bool(helpers.call(tok.text, types, tok.line)[0])


def _flags(handler: list[Token], rest: list[Token]) -> bool:
    """`catch (AssertionError e) { failed = true; }` with `assertTrue(failed)` later: the
    failure is the expected outcome and is checked."""
    assigned = {
        handler[k - 1].text
        for k in range(1, len(handler))
        if handler[k].text == "=" and handler[k - 1].kind == "id"
    }
    for k, tok in enumerate(rest):
        if tok.kind == "id" and _is_assert_call(tok.text) and k + 1 < len(rest):
            if rest[k + 1].text == "(":
                args = rest[k + 2 : _close(rest, k + 1)]
                if any(t.text in assigned for t in args):
                    return True
    return False


def _fails(handler: list[Token]) -> bool:
    """The catch block re-throws, checks something or fails the test on some path."""
    return any(
        t.text == "throw"
        or (
            t.kind == "id"
            and _is_assert_call(t.text)
            and k + 1 < len(handler)
            and handler[k + 1].text == "("
        )
        for k, t in enumerate(handler)
    )
