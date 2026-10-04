"""What a Java assertion checks, for the rules that compare assertions (AW004, AW005, AW011).

An assertion's text is re-tokenized and read as a call chain: `assertEquals(a, b)`,
`assertThat(x).isNotNull().isEqualTo(5)`, `assertThat(x, is(5))` (Hamcrest),
`assertThatThrownBy(() -> f()).isInstanceOf(X.class)`, `verify(mock).save(order)`. The answers
have the same shape as the Python readers' answers, so the rules compare them the same way.
"""

from __future__ import annotations

from dataclasses import dataclass

from assertwatch.extract.java import JavaSyntaxError, Token, join, tokenize

ALWAYS = "always"
SWAPPED = "swapped"

_EQUAL = frozenset(
    {"assertEquals", "assertSame", "assertArrayEquals", "assertIterableEquals", "assertLinesMatch"}
)
_ASSERTJ_EQUAL = frozenset(
    {"isEqualTo", "isSameAs", "isEqualByComparingTo", "containsExactly", "isEqualToIgnoringCase"}
)
_ASSERTJ_TYPE = frozenset({"isInstanceOf", "isExactlyInstanceOf", "isInstanceOfAny"})
_ASSERTJ_TRUTHY = frozenset({"isTrue", "isPresent", "isNotEmpty", "isNotBlank"})
_ASSERTJ_TOLERANCE = frozenset({"isCloseTo"})
_ASSERTJ_MESSAGE = frozenset({"as", "describedAs", "withFailMessage", "overridingErrorMessage"})
_THROWN_MESSAGE = frozenset(
    {"hasMessage", "hasMessageContaining", "hasMessageStartingWith", "hasMessageMatching",
     "withMessage", "withMessageContaining"}
)  # fmt: skip
_HAMCREST_EQUAL = frozenset({"is", "equalTo", "sameInstance", "closeTo", "comparesEqualTo"})
_HAMCREST_TYPE = frozenset({"instanceOf", "isA", "any"})
_MOCK_ANY = frozenset(
    {"any", "anyInt", "anyLong", "anyString", "anyList", "anyMap", "anySet", "anyDouble",
     "anyBoolean", "anyCollection", "anyIterable", "notNull", "isNull", "isA", "anyChar",
     "anyFloat", "anyShort", "anyByte"}
)  # fmt: skip
_STRENGTH = {"equal": 4, "type": 3, "notnone": 2, "truthy": 1}


@dataclass(frozen=True)
class Call:
    name: str
    args: list[list[Token]]


def chain(text: str) -> list[Call]:
    """The call chain of an assertion statement; [] if it isn't one."""
    try:
        tokens = [t for t in tokenize(text) if t.text != ";"]
    except JavaSyntaxError:
        return []
    calls: list[Call] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.kind == "id" and i + 1 < len(tokens) and tokens[i + 1].text == "(":
            end = _close(tokens, i + 1)
            if end is None:
                return []
            calls.append(Call(tok.text, split_args(tokens[i + 2 : end])))
            i = end + 1
            continue
        if tok.text != "." and tok.kind != "id":
            return calls  # not a plain chain
        i += 1
    return calls


def _close(tokens: list[Token], start: int) -> int | None:
    depth = 0
    for i in range(start, len(tokens)):
        if tokens[i].text in ("(", "[", "{"):
            depth += 1
        elif tokens[i].text in (")", "]", "}"):
            depth -= 1
            if depth == 0:
                return i
    return None


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


def literal(arg: list[Token]) -> object | None:
    """The value of a literal argument (`5`, `-1.5`, `"a"`, `'c'`, `true`, `null`), or None.

    Returns the _NULL marker for `null`, so None always means "not a literal".
    """
    if len(arg) == 2 and arg[0].text == "-" and arg[1].kind == "num":
        value = _number(arg[1].text)
        return None if value is None else -value
    if len(arg) != 1:
        return None
    tok = arg[0]
    if tok.kind == "num":
        return _number(tok.text)
    if tok.kind == "str":
        return _string(tok.text)
    if tok.kind == "char":
        return ("char", tok.text[1:-1])
    if tok.text in ("true", "false"):
        return tok.text == "true"
    if tok.text == "null":
        return _NULL
    return None


class _Null:
    def __repr__(self) -> str:
        return "null"


_NULL = _Null()


def _number(text: str) -> int | float | None:
    clean = text.replace("_", "").rstrip("lLfFdD") if not text.lower().startswith("0x") else text
    try:
        if clean.lower().startswith("0x"):
            return int(clean.rstrip("lL").replace("_", ""), 16)
        if any(c in clean for c in ".eE"):
            return float(clean)
        return int(clean)
    except ValueError:
        return None


def _string(text: str) -> str:
    if text.startswith('"""'):
        return text[3:-3]
    return text[1:-1]


def _text(arg: list[Token]) -> str:
    return join(arg)


def _class_name(arg: list[Token]) -> str:
    text = _text(arg)
    return text.removesuffix(".class").rsplit(".", 1)[-1]


# --- AW004: what is checked -------------------------------------------------------------


def classify(text: str) -> dict | None:
    """The check an assertion makes, as Check fields: kind, subject, exception,
    matches_message, tolerance, literal. None if it doesn't fit a known shape."""
    calls = [] if _is_assert_statement(text) else chain(text)
    if not calls:
        return _assert_statement(text)
    first, rest = calls[0], [c for c in calls[1:] if c.name not in _ASSERTJ_MESSAGE]
    name, args = first.name, first.args
    if name in _EQUAL:
        expected, actual, delta = _junit_values(args)
        if actual is None:
            return None
        tolerance = None if delta is None else (0.0, delta)
        return dict(
            kind="equal", subject=_text(actual), tolerance=tolerance,
            literal=literal(expected) is not None,
        )  # fmt: skip
    if name == "assertNotNull" and args:
        subject = _last_value(args, 1)
        if literal(subject) is not None:
            return None  # assertNotNull("x") checks no value under test
        return dict(kind="notnone", subject=_text(subject))
    if name == "assertTrue" and args:
        subject = _last_value(args, 1)
        return None if literal(subject) is not None else dict(kind="truthy", subject=_text(subject))
    if name == "assertInstanceOf" and len(args) >= 2:
        return dict(kind="type", subject=_text(args[1]))
    if name in ("assertThrows", "assertThrowsExactly") and args:
        return dict(kind="raises", subject="<raises>", exception=_class_name(args[0]))
    if name == "assertThatThrownBy":
        return _thrown(rest)
    if name == "assertThatExceptionOfType" and args:
        matches = any(c.name in _THROWN_MESSAGE for c in rest)
        return dict(
            kind="raises", subject="<raises>", exception=_class_name(args[0]),
            matches_message=matches,
        )  # fmt: skip
    if name == "assertThat" and len(args) == 1:
        return _assertj(_text(args[0]), rest)
    if name == "assertThat" and len(args) in (2, 3):
        return _hamcrest(args[-2], args[-1])
    if name == "verify" and args and rest:
        call = rest[0]
        subject = f"{_text(args[0])}.{call.name}"
        loose = not call.args or all(_is_any(a) for a in call.args)
        return dict(kind="called" if loose and call.args else "called_with", subject=subject)
    return None


def _is_assert_statement(text: str) -> bool:
    return text.startswith("assert ") or text.startswith("assert(")


def _assert_statement(text: str) -> dict | None:
    """Java's `assert x == 5;` / `assert x != null;`."""
    try:
        tokens = [t for t in tokenize(text) if t.text != ";"]
    except JavaSyntaxError:
        return None
    if not tokens or tokens[0].text != "assert":
        return None
    body = tokens[1:]
    if ":" in [t.text for t in body]:  # `assert x : "message"`
        body = body[: [t.text for t in body].index(":")]
    for op, kind in (("!=", "notnone"), ("==", "equal")):
        parts = _split_op(body, op)
        if parts is None:
            continue
        left, right = parts
        if kind == "notnone":
            if literal(right) is _NULL:
                return dict(kind="notnone", subject=_text(left))
            return None
        if literal(left) is not None and literal(right) is None:
            left, right = right, left
        if literal(right) is _NULL:
            return None
        return dict(kind="equal", subject=_text(left), literal=literal(right) is not None)
    return None


def _split_op(tokens: list[Token], op: str) -> tuple[list[Token], list[Token]] | None:
    """Split `a == b` at a top-level operator."""
    depth = 0
    for i, tok in enumerate(tokens):
        if tok.text in ("(", "[", "{"):
            depth += 1
        elif tok.text in (")", "]", "}"):
            depth -= 1
        elif depth == 0 and tok.text == op:
            return tokens[:i], tokens[i + 1 :]
    return None


def _junit_values(
    args: list[list[Token]],
) -> tuple[list[Token], list[Token] | None, float | None]:
    """(expected, actual, delta) of assertEquals in JUnit 5 or JUnit 4 argument order."""
    if not args:
        return [], None, None
    if len(args) >= 3 and isinstance(literal(args[0]), str) and not _is_message(args[2]):
        args = args[1:]  # JUnit 4: assertEquals("message", expected, actual[, delta])
    if len(args) < 2:
        return args[0], None, None
    delta = None
    if len(args) >= 3:
        value = literal(args[2])
        delta = (
            float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
        )
    return args[0], args[1], delta


def _is_message(arg: list[Token]) -> bool:
    return isinstance(literal(arg), str) or any(t.text == "->" for t in arg)


def _last_value(args: list[list[Token]], count: int) -> list[Token]:
    """The checked value of assertTrue / assertNotNull, with a message on either side."""
    if len(args) > count and isinstance(literal(args[0]), str):
        return args[1]  # JUnit 4: assertTrue("message", condition)
    return args[0]


def _assertj(subject: str, calls: list[Call]) -> dict | None:
    best: dict | None = None
    for call in calls:
        found: dict | None = None
        if call.name in _ASSERTJ_EQUAL and call.args:
            found = dict(kind="equal", subject=subject, literal=literal(call.args[0]) is not None)
        elif call.name == "hasSize" and call.args:
            found = dict(kind="equal", subject=f"{subject}.size()", literal=True)
        elif call.name in _ASSERTJ_TOLERANCE and len(call.args) == 2:
            found = dict(
                kind="equal", subject=subject, tolerance=_offset(call.args[1]),
                literal=literal(call.args[0]) is not None,
            )  # fmt: skip
        elif call.name in _ASSERTJ_TYPE:
            found = dict(kind="type", subject=subject)
        elif call.name == "isNotNull":
            found = dict(kind="notnone", subject=subject)
        elif call.name in _ASSERTJ_TRUTHY:
            found = dict(kind="truthy", subject=subject)
        if found and (best is None or _STRENGTH[found["kind"]] > _STRENGTH[best["kind"]]):
            best = found
    return best


def _offset(arg: list[Token]) -> tuple[float, float] | None:
    calls = chain(_text(arg))
    if calls and calls[0].name in ("within", "offset", "byLessThan") and calls[0].args:
        value = literal(calls[0].args[0])
        if isinstance(value, int | float) and not isinstance(value, bool):
            return (0.0, float(value))
    return None


def _thrown(calls: list[Call]) -> dict | None:
    exception = next(
        (_class_name(c.args[0]) for c in calls if c.name in _ASSERTJ_TYPE and c.args), ""
    )
    matches = any(c.name in _THROWN_MESSAGE for c in calls)
    return dict(kind="raises", subject="<raises>", exception=exception, matches_message=matches)


def _hamcrest(actual: list[Token], matcher: list[Token]) -> dict | None:
    calls = chain(_text(matcher))
    while calls and calls[0].name in ("is", "not") and calls[0].args:
        if calls[0].name == "not":
            return None
        inner = chain(_text(calls[0].args[0]))
        if not inner:
            break
        calls = inner
    if not calls:
        return None
    name, args = calls[0].name, calls[0].args
    subject = _text(actual)
    if name in _HAMCREST_EQUAL and args:
        tolerance = None
        if name == "closeTo" and len(args) == 2:
            value = literal(args[1])
            tolerance = (0.0, float(value)) if isinstance(value, int | float) else None
        return dict(
            kind="equal", subject=subject, tolerance=tolerance,
            literal=literal(args[0]) is not None,
        )  # fmt: skip
    if name == "notNullValue":
        return dict(kind="notnone", subject=subject)
    if name in _HAMCREST_TYPE:
        return dict(kind="type", subject=subject)
    return None


def _is_any(arg: list[Token]) -> bool:
    calls = chain(_text(arg))
    return bool(calls) and calls[0].name in _MOCK_ANY


# --- AW005: expected values -------------------------------------------------------------


def shape(text: str) -> tuple[str, list[object]] | None:
    """The assertion with its literals blanked, and the literals in order; None if none.

    Failure messages and tolerances are left out, like in the Python reader.
    """
    calls = [] if _is_assert_statement(text) else chain(text)
    if not calls:
        return _statement_shape(text)
    kept: list[tuple[str, list[list[Token]]]] = []
    for i, call in enumerate(calls):
        if call.name in _ASSERTJ_MESSAGE:
            continue
        args = call.args
        if i == 0:
            args = _without_message(call.name, args)
        kept.append((call.name, args))
    values: list[object] = []
    parts = []
    for name, args in kept:
        parts.append(f"{name}({', '.join(_blank(a, values) for a in args)})")
    return (".".join(parts), values) if values else None


def _without_message(name: str, args: list[list[Token]]) -> list[list[Token]]:
    if name in _EQUAL:
        if len(args) >= 3 and isinstance(literal(args[0]), str) and not _is_message(args[2]):
            args = args[1:]
        return args[:2]  # the rest is a message or a tolerance
    if name in ("assertTrue", "assertFalse", "assertNotNull", "assertNull"):
        if len(args) == 2:
            return [args[1]] if isinstance(literal(args[0]), str) else [args[0]]
    if name == "assertThat" and len(args) == 3 and isinstance(literal(args[0]), str):
        return args[1:]  # Hamcrest: assertThat("message", actual, matcher)
    if name in ("assertThrows", "assertThrowsExactly", "assertInstanceOf") and len(args) == 3:
        return args[:2]
    return args


def _statement_shape(text: str) -> tuple[str, list[object]] | None:
    try:
        tokens = [t for t in tokenize(text) if t.text != ";"]
    except JavaSyntaxError:
        return None
    texts = [t.text for t in tokens]
    if ":" in texts:
        tokens = tokens[: texts.index(":")]
    values: list[object] = []
    template = _blank(tokens, values)
    return (template, values) if values else None


def _blank(tokens: list[Token], values: list[object]) -> str:
    """Source text with each literal replaced by `_`, its value appended to values."""
    out: list[Token] = []
    for tok in tokens:
        negative = (
            tok.kind == "num"
            and out
            and out[-1].text == "-"
            and (len(out) == 1 or out[-2].text in ("(", ",", "=", "==", "{"))
        )
        value = literal([tok]) if tok.kind in ("num", "str", "char") or tok.text in (
            "true", "false", "null"
        ) else None  # fmt: skip
        if value is None:
            out.append(tok)
            continue
        if negative and isinstance(value, int | float):
            out.pop()
            value = -value
        values.append(value)
        out.append(Token("id", "_", tok.line))
    return join(out)


# --- AW011: can never fail --------------------------------------------------------------


def tautology(text: str) -> str | None:
    """ALWAYS or SWAPPED if the assertion can never fail, else None."""
    calls = [] if _is_assert_statement(text) else chain(text)
    if not calls:
        return _statement_tautology(text)
    first, rest = calls[0], [c for c in calls[1:] if c.name not in _ASSERTJ_MESSAGE]
    name, args = first.name, first.args
    if name == "assertTrue" and args and _always(_last_value(args, 1), True):
        return ALWAYS
    if name == "assertFalse" and args and _always(_last_value(args, 1), False):
        return ALWAYS
    if name == "assertThat" and len(args) == 1 and not rest:
        return ALWAYS  # AssertJ: assertThat(x) alone checks nothing
    if name == "verify" and len(args) == 2 and _never_fails(args[1]):
        return ALWAYS
    if name == "assertNotNull" and args:
        value = literal(_last_value(args, 1))
        if value is not None and value is not _NULL:
            return ALWAYS
    if name == "assertInstanceOf" and args and _class_name(args[0]) == "Object":
        return ALWAYS
    if name in _EQUAL:
        expected, actual, _ = _junit_values(args)
        if actual is not None:
            return _pair_tautology(expected, actual)
    if name == "assertThat" and len(args) == 1:
        subject = args[0]
        for call in rest:
            if call.name in _ASSERTJ_EQUAL and call.args:
                return _pair_tautology(call.args[0], subject)
            if call.name == "isTrue" and literal(subject) is True:
                return ALWAYS
            if call.name == "isFalse" and literal(subject) is False:
                return ALWAYS
            if call.name == "isNotNull" and literal(subject) not in (None, _NULL):
                return ALWAYS
            if call.name in _ASSERTJ_TYPE and call.args and _class_name(call.args[0]) == "Object":
                return ALWAYS
    if name == "assertThat" and len(args) in (2, 3):
        matcher = chain(_text(args[-1]))
        if matcher and matcher[0].name == "anything":
            return ALWAYS
        if matcher and matcher[0].name in _HAMCREST_EQUAL and matcher[0].args:
            inner = chain(_text(matcher[0].args[0]))
            if not (inner and inner[0].name in ("not", "is", "equalTo")):
                return _pair_tautology(matcher[0].args[0], args[-2])
    return None


def _pair_tautology(expected: list[Token], actual: list[Token]) -> str | None:
    left, right = literal(expected), literal(actual)
    if left is not None and right is not None:
        return ALWAYS if left == right and type(left) is type(right) else None
    if _text(expected) == _text(actual):
        return SWAPPED
    return None


def _statement_tautology(text: str) -> str | None:
    try:
        tokens = [t for t in tokenize(text) if t.text != ";"]
    except JavaSyntaxError:
        return None
    if not tokens or tokens[0].text != "assert":
        return None
    body = tokens[1:]
    texts = [t.text for t in body]
    if ":" in texts:
        body = body[: texts.index(":")]
    if _always(body, True):
        return ALWAYS
    parts = _split_op(body, "==")
    if parts is not None:
        return _pair_tautology(*parts)
    return None


def _always(expr: list[Token], value: bool) -> bool:
    """expr is the literal value, or (for true) has a top-level `|| true` operand, or
    (for false) a top-level `&& false` operand: `x || true` can never be false."""
    if literal(expr) is value:
        return True
    op = "||" if value else "&&"
    parts, depth, current = [], 0, []
    for tok in expr:
        if tok.text in ("(", "[", "{"):
            depth += 1
        elif tok.text in (")", "]", "}"):
            depth -= 1
        if depth == 0 and tok.text == op:
            parts.append(current)
            current = []
        else:
            current.append(tok)
    parts.append(current)
    return len(parts) > 1 and any(literal(_unparen(p)) is value for p in parts)


def _unparen(tokens: list[Token]) -> list[Token]:
    while len(tokens) >= 2 and tokens[0].text == "(" and tokens[-1].text == ")":
        tokens = tokens[1:-1]
    return tokens


def _never_fails(mode: list[Token]) -> bool:
    """A Mockito verification mode that any number of calls satisfies: atLeast(0)."""
    calls = chain(_text(mode))
    return (
        bool(calls)
        and calls[0].name == "atLeast"
        and calls[0].args != []
        and (literal(calls[0].args[0]) == 0)
    )
