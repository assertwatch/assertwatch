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
    """

    kind: str
    name: str
    text: str
    line: int


@dataclass(frozen=True)
class TryBlock:
    """A try statement inside a test, for detecting swallowed failures."""

    line: int
    handlers: tuple[str, ...]  # exception types caught; "" for a bare except
    wraps_assertion: bool  # the try body contains an assertion
    handler_is_noop: bool  # every handler body is only pass / ... / continue


@dataclass
class TestCase:
    """One test function or method, as the test runner would collect it."""

    __test__ = False  # stop pytest from collecting this dataclass

    path: str
    name: str
    classes: tuple[str, ...]  # enclosing class chain, outermost first
    line: int
    end_line: int
    markers: frozenset[str]  # "skip", "skipif", "xfail", "parametrize", ... (incl. inherited)
    assertions: list[Assertion] = field(default_factory=list)
    mocks: list[str] = field(default_factory=list)  # patch targets, e.g. "app.refund.calc"
    skip_calls: list[str] = field(default_factory=list)  # "pytest.skip", "self.skipTest", ...
    try_blocks: list[TryBlock] = field(default_factory=list)
    call_count: int = 0  # function calls in the body, excluding assertions
    is_empty: bool = False  # body is only a docstring / pass / ...
    returns_first: bool = False  # the first statement is `return`
    body_text: str = ""  # normalized body without docstring, for rename matching
    param_names: frozenset[str] = frozenset()  # argnames from parametrize decorators
    param_cases: int | None = None  # parametrized case count; None if absent or not countable
    param_literals: frozenset[str] = frozenset()  # repr() of literals in the parametrize values

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
