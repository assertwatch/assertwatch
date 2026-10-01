"""Pair each base test with its head counterpart, so rules compare like with like.

A test that was renamed, moved to another class or file, split, or folded into a
parametrized test must not look removed. Matching runs in stages, and each stage only sees
the tests the earlier stages left unmatched:

1. Same id, after following file renames.
2. Identical body, then body similarity >= RENAME_THRESHOLD (or SAME_NAME_THRESHOLD when
   the qualified name is unchanged and only the file changed). These become renamed pairs.
3. Template match against parametrized tests, with literals and parametrize argnames blanked
   out, for `test_a`/`test_b` -> one parametrized test and the reverse. The base test's
   literal values must still appear in the head test (in its body or parametrize values),
   so a hard case can't be folded into an easier one. Or, when the shape changed too
   (unittest -> pytest), every meaningful literal of the base test appears in a
   parametrized head test's values and both call a common function. These are folded.
4. Every assertion of the base test still exists in some head test, and the head has no
   fewer copies of it than the base had (split, or merged into another test). Folded too.

What remains on the base side is removed; what remains on the head side is added.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from assertwatch.extract.python import dotted, literal_reprs
from assertwatch.model import TestCase, TestFile

RENAME_THRESHOLD = 0.8
SAME_NAME_THRESHOLD = 0.6

_TOKEN = re.compile(r"\w+|[^\w\s]")


@dataclass(frozen=True)
class Pair:
    """A base test and its head counterpart. how is "same" or "renamed"."""

    base: TestCase
    head: TestCase
    how: str


@dataclass(frozen=True)
class Folded:
    """A base test whose checks now live in other head tests (split, merged, parametrized)."""

    base: TestCase
    heads: tuple[TestCase, ...]


@dataclass
class Matching:
    pairs: list[Pair] = field(default_factory=list)
    folded: list[Folded] = field(default_factory=list)
    removed: list[TestCase] = field(default_factory=list)
    added: list[TestCase] = field(default_factory=list)
    base_assertions: Counter[str] = field(default_factory=Counter)
    head_assertions: Counter[str] = field(default_factory=Counter)

    def assertion_deficit(self, text: str) -> int:
        """How many fewer copies of this assertion exist in the head than in the base."""
        return max(0, self.base_assertions[text] - self.head_assertions[text])


def match_tests(
    base_files: Iterable[TestFile],
    head_files: Iterable[TestFile],
    path_map: dict[str, str] | None = None,
) -> Matching:
    """Match tests across the two sides. path_map maps renamed base paths to head paths.

    Files that failed to parse on either side should be left out by the caller: their tests
    are unknown, not removed.
    """
    path_map = path_map or {}
    base = [t for f in base_files for t in f.tests]
    head = [t for f in head_files for t in f.tests]
    result = Matching(
        base_assertions=Counter(a.text for t in base for a in t.assertions),
        head_assertions=Counter(a.text for t in head for a in t.assertions),
    )

    head_by_id = {t.id: t for t in head}
    unmatched_base: list[TestCase] = []
    for test in base:
        counterpart = head_by_id.pop(_mapped_id(test, path_map), None)
        if counterpart is None:
            unmatched_base.append(test)
        else:
            result.pairs.append(Pair(test, counterpart, "same"))
    unmatched_head = list(head_by_id.values())

    renamed = _match_renames(unmatched_base, unmatched_head)
    result.pairs.extend(renamed)
    paired_base = {id(p.base) for p in renamed}
    paired_head = {id(p.head) for p in renamed}
    unmatched_base = [t for t in unmatched_base if id(t) not in paired_base]
    unmatched_head = [t for t in unmatched_head if id(t) not in paired_head]
    result.added = unmatched_head

    shapes = _Shapes()
    for test in unmatched_base:
        heads = _parametrized_matches(test, head, shapes) or _split_into(test, head, result)
        if heads:
            result.folded.append(Folded(test, heads))
        else:
            result.removed.append(test)
    return result


def _mapped_id(test: TestCase, path_map: dict[str, str]) -> str:
    return "::".join((path_map.get(test.path, test.path), *test.classes, test.name))


def _qualname(test: TestCase) -> tuple[str, ...]:
    return (*test.classes, test.name)


def _match_renames(base: list[TestCase], head: list[TestCase]) -> list[Pair]:
    """Greedy one-to-one matching by body similarity, best scores first."""
    if not base or not head:
        return []
    head_tokens = [_tokens(t.body_text) for t in head]
    candidates: list[tuple[float, int, int]] = []
    for i, b in enumerate(base):
        b_tokens = _tokens(b.body_text)
        for j, h in enumerate(head):
            threshold = SAME_NAME_THRESHOLD if _qualname(b) == _qualname(h) else RENAME_THRESHOLD
            score = _similarity(b.body_text, h.body_text, b_tokens, head_tokens[j], threshold)
            if score >= threshold:
                candidates.append((score, i, j))
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    used_base: set[int] = set()
    used_head: set[int] = set()
    pairs: list[Pair] = []
    for _, i, j in candidates:
        if i not in used_base and j not in used_head:
            used_base.add(i)
            used_head.add(j)
            pairs.append(Pair(base[i], head[j], "renamed"))
    return pairs


def _similarity(a: str, b: str, a_tokens: list[str], b_tokens: list[str], floor: float) -> float:
    if a == b:
        return 1.0
    matcher = SequenceMatcher(None, a_tokens, b_tokens, autojunk=False)
    if matcher.real_quick_ratio() < floor or matcher.quick_ratio() < floor:
        return 0.0
    return matcher.ratio()


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


# Literals too common to show that a case survived: 0, 1, True, None, "" ...
_TRIVIAL = frozenset({"0", "1", "-1", "None", "True", "False", "''", "0.0", "1.0"})


@dataclass(frozen=True)
class _Shape:
    template: str  # body with literals and parametrize argnames blanked
    tokens: list[str]
    literals: frozenset[str]  # literals in the body (repr)
    calls: frozenset[str]  # names of functions called, excluding assertions


class _Shapes:
    """Shapes of tests, computed once each."""

    def __init__(self) -> None:
        self._cache: dict[int, _Shape] = {}

    def __call__(self, test: TestCase) -> _Shape:
        shape = self._cache.get(id(test))
        if shape is None:
            shape = self._cache[id(test)] = _shape(test)
        return shape


def _shape(test: TestCase) -> _Shape:
    try:
        tree = ast.parse(test.body_text)
    except SyntaxError:
        return _Shape(test.body_text, _tokens(test.body_text), frozenset(), frozenset())
    literals = frozenset(literal_reprs(tree))
    calls = frozenset(
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (name := dotted(node.func).rsplit(".", 1)[-1])
        and not name.lower().startswith("assert")
    )
    template = ast.unparse(_Blank(test.param_names).visit(tree))
    return _Shape(template, _tokens(template), literals, calls)


def _parametrized_matches(
    test: TestCase, head: list[TestCase], shapes: _Shapes
) -> tuple[TestCase, ...]:
    """Parametrized head tests this test was folded into, or plain ones it was split into."""
    if not test.body_text:
        return ()
    mine = shapes(test)
    meaningful = (mine.literals | test.param_literals) - _TRIVIAL
    found = []
    for other in head:
        if not (test.param_names or other.param_names):
            continue
        theirs = shapes(other)
        if test.param_names and not other.param_names:
            # One parametrized test split into plain ones: each must use the base's values.
            keeps_values = theirs.literals <= mine.literals | test.param_literals
        else:
            keeps_values = mine.literals | test.param_literals <= (
                theirs.literals | other.param_literals
            )
        same_shape = keeps_values and RENAME_THRESHOLD <= _similarity(
            mine.template, theirs.template, mine.tokens, theirs.tokens, RENAME_THRESHOLD
        )
        values_kept = (
            other.param_names
            and meaningful
            and meaningful <= theirs.literals | other.param_literals
            and mine.calls & theirs.calls
        )
        if same_shape or values_kept:
            found.append(other)
    return tuple(found)


class _Blank(ast.NodeTransformer):
    """Replace literals and parametrize argnames with a placeholder name."""

    def __init__(self, names: frozenset[str]):
        self.names = names

    def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.AST:
        return node  # an f-string's text parts must stay string constants

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        return ast.copy_location(ast.Name("_"), node)

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in self.names:
            return ast.copy_location(ast.Name("_"), node)
        return node


def _split_into(test: TestCase, head: list[TestCase], result: Matching) -> tuple[TestCase, ...]:
    """Head tests that took over this test's assertions, if none of them were lost."""
    if not test.assertions:
        return ()
    if any(result.assertion_deficit(a.text) for a in test.assertions):
        return ()
    texts = {a.text for a in test.assertions}
    return tuple(t for t in head if texts & {a.text for a in t.assertions})
