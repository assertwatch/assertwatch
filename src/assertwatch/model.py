"""Language-neutral model of test files, tests, assertions and findings.

Extractors (one per language) turn source code into these objects; rules compare the base
and head versions of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    BLOCK = "block"
    WARN = "warn"
    INFO = "info"


@dataclass(frozen=True)
class Assertion:
    """One check inside a test.

    kind:
      "assert"   - a bare `assert` statement
      "call"     - an assert-style call: self.assertEqual, assert_frame_equal,
                   mock.assert_called_once_with, ...
      "raises"   - an expected-exception/warning context: pytest.raises, pytest.warns,
                   self.assertRaises used as a context manager is kind "call"
    name is the called name for "call"/"raises" ("assertEqual", "raises"), "" for "assert".
    text is the normalized source (ast.unparse), stable across formatting changes.
    py2_only is set when it sits in a branch that only runs on Python 2 (`if PY2:`, the
    `else` of `if sys.version_info >= (3,):`), which is dead code today.
    lang is the source language, "python" or "java"; rules read text in that language.
    via is the helper function the test called to make this check (`check_cart`), "" when the
    test makes it itself.
    In Java, "call" is a statement starting with assertEquals, assertThat(...).isEqualTo(...),
    verify(...) or fail, and "raises" is assertThrows or assertThatThrownBy.
    """

    kind: str
    name: str
    text: str
    line: int
    py2_only: bool = False
    lang: str = "python"
    via: str = ""


@dataclass(frozen=True)
class TryBlock:
    """A try statement inside a test, for detecting swallowed failures."""

    line: int
    handlers: tuple[str, ...]  # exception types caught; "" for a bare except
    wraps_assertion: bool  # the try body contains an assertion
    handler_is_noop: bool  # every handler body is only pass / ... / continue
    # a handler catches assertion failures (AssertionError, Exception, Throwable, a bare
    # except, contextlib.suppress) and neither re-raises nor fails, while nothing after the
    # protected code fails the test in its place: a failing check in the body passes
    swallows: bool = False


@dataclass(frozen=True)
class ParamCase:
    """One case of a parametrize decorator: its argnames and values, as normalized source."""

    names: tuple[str, ...]
    values: tuple[str, ...]
    line: int


@dataclass
class TestCase:
    """One test function or method, as the test runner would collect it."""

    __test__ = False  # stop pytest from collecting this dataclass

    path: str
    name: str
    classes: tuple[str, ...]  # enclosing class chain, outermost first
    line: int
    end_line: int
    # "skip", "skipif", "xfail", "parametrize", ... (incl. inherited); "py2-only" when a
    # skipif/skipUnless lets it run only on Python 2
    markers: frozenset[str]
    assertions: list[Assertion] = field(default_factory=list)
    mocks: list[str] = field(default_factory=list)  # patch targets, e.g. "app.refund.calc"
    # names (last part) of mocked or stubbed functions that the test also calls itself, so
    # it checks the stand-in rather than the code: `patch("shop.total", return_value=4)`
    # then `assert total() == 4`; Java `when(cart.total()).thenReturn(4.0)` then `cart.total()`
    mocked_calls: frozenset[str] = frozenset()
    skip_calls: list[str] = field(default_factory=list)  # "pytest.skip", "self.skipTest", ...
    try_blocks: list[TryBlock] = field(default_factory=list)
    call_count: int = 0  # function calls in the body, excluding assertions
    # names (last part) of the functions it calls that aren't defined in the file
    external_calls: frozenset[str] = frozenset()
    is_empty: bool = False  # body is only a docstring / pass / ...
    # every statement is a no-op or binds names to a value built from literals only, so the
    # body can't fail or check anything (`line = "-" * 20`); implied by is_empty
    cannot_fail: bool = False
    returns_first: bool = False  # the first statement is `return`
    body_text: str = ""  # normalized body without docstring, for rename matching
    param_names: frozenset[str] = frozenset()  # argnames from parametrize decorators
    param_cases: int | None = None  # parametrized case count; None if absent or not countable
    param_literals: frozenset[str] = frozenset()  # repr() of literals in the parametrize values
    param_rows: tuple[ParamCase, ...] = ()  # cases of argvalues written as a list
    # names bound to caught warnings or exceptions: `catch_warnings(record=True) as w`,
    # `pytest.raises(...) as exc`, `self.assertRaises(...) as cm`, the `recwarn` fixture
    captures: frozenset[str] = frozenset()

    @property
    def id(self) -> str:
        return "::".join((self.path, *self.classes, self.name))


@dataclass
class TestFile:
    """All tests found in one file. parse_error is set when the file could not be parsed."""

    __test__ = False

    path: str
    tests: list[TestCase] = field(default_factory=list)
    imports: frozenset[str] = frozenset()  # imported module paths, e.g. "app.refund"
    parse_error: str | None = None

    def by_id(self) -> dict[str, TestCase]:
        return {t.id: t for t in self.tests}


@dataclass(frozen=True)
class Finding:
    """One problem reported by a rule."""

    rule: str  # "AW001"
    severity: Severity
    path: str
    line: int
    message: str
    test_id: str | None = None
    before: str | None = None
    after: str | None = None
    hint: str | None = None
    waiver: str | None = None  # reason of the standing waiver that covers it, if any
    side: str = "head"  # which side of the diff `line` is on: "head", or "base" for removals
