"""AW009: test configuration or the guard itself was loosened.

Checked files, on both sides of the diff:
- assertwatch.toml: any change. It only takes effect after merge, so a human must see it.
- .claude/settings.json and settings.local.json: an AssertWatch hook removed, or
  `disableAllHooks` turned on.
- .github/workflows/*.yml: an AssertWatch step removed; `continue-on-error: true` or
  `|| true` added where tests run.
- pytest config (pytest.ini, pyproject.toml, setup.cfg, tox.ini): addopts gaining
  --deselect, -k, -m "not ...", --lf or --ignore; testpaths losing entries; coverage
  fail_under (also .coveragerc and --cov-fail-under) lowered or removed.
- conftest.py: collect_ignore entries or skip/xfail markers added.
"""

from __future__ import annotations

import ast
import configparser
import json
import re
import shlex
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from assertwatch.extract.python import dotted
from assertwatch.model import Finding, Severity
from assertwatch.paths import CONFIG_PATH, PYTEST_FILES, SETTINGS, is_workflow_path
from assertwatch.rules import Context, FileChange, Rule

ID = "AW009"


# addopts options that run fewer tests. Values: does the option take an argument?
_NARROWING = {
    "--deselect": True,
    "-k": True,
    "-m": True,
    "--lf": False,
    "--last-failed": False,
    "--ignore": True,
    "--ignore-glob": True,
}
_TEST_COMMAND = re.compile(r"\b(pytest|py\.test|tox|nox|unittest|assertwatch|test)\b", re.I)
_SWALLOW = re.compile(r"\|\|\s*(true|:|exit\s+0)\b")
_CONTINUE = re.compile(r"^\s*continue-on-error\s*:\s*(true|\$\{\{)", re.I | re.M)


def check(context: Context) -> Iterator[Finding]:
    for change in context.files:
        path = change.path
        name = PurePosixPath(path).name
        if path == CONFIG_PATH:
            yield from _assertwatch_config(change)
        elif path in SETTINGS or change.base_path in SETTINGS:
            yield from _claude_settings(change)
        elif is_workflow_path(path):
            yield from _workflow(change)
        elif name in PYTEST_FILES:
            yield from _pytest_config(change)
        elif name == "conftest.py":
            yield from _conftest(change)


def _finding(change: FileChange, what: str, needle: str = "") -> Finding:
    return Finding(
        rule=ID,
        severity=RULE.severity,
        path=change.path,
        line=_line_of(change.after or "", needle),
        message=f"{what}.",
        hint="Revert the change, or ask a human to make it: guard and test config "
        "changes need review.",
    )


def _line_of(text: str, needle: str) -> int:
    if needle:
        for number, line in enumerate(text.splitlines(), 1):
            if needle in line:
                return number
    return 1


# --- assertwatch.toml and Claude Code settings ------------------------------------------


def _assertwatch_config(change: FileChange) -> Iterator[Finding]:
    if change.before == change.after:
        return
    if change.before is None:
        what = "assertwatch.toml was added; it applies only once merged, after review"
    elif change.after is None:
        what = "assertwatch.toml was deleted"
    else:
        what = "assertwatch.toml was changed; the change applies only once merged, after review"
    yield _finding(change, what)


def _claude_settings(change: FileChange) -> Iterator[Finding]:
    before, after = _json(change.before), _json(change.after)
    hooks_before = _assertwatch_commands(before)
    if hooks_before and len(_assertwatch_commands(after)) < len(hooks_before):
        yield _finding(change, "The AssertWatch hook was removed from Claude Code settings")
    if _hooks_disabled(after) and not _hooks_disabled(before):
        yield _finding(change, "Claude Code hooks were disabled", "disableAllHooks")


def _json(text: str | None) -> object:
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None  # unreadable settings protect nothing


def _assertwatch_commands(data: object) -> list[str]:
    hooks = data.get("hooks") if isinstance(data, dict) else None
    return [s for s in _strings(hooks) if "assertwatch" in s.lower()]


def _strings(node: object) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _hooks_disabled(data: object) -> bool:
    return isinstance(data, dict) and data.get("disableAllHooks") is True


# --- CI workflows -----------------------------------------------------------------------


def _workflow(change: FileChange) -> Iterator[Finding]:
    before, after = change.before or "", change.after or ""
    if _guard_lines(after) < _guard_lines(before):
        yield _finding(change, "The AssertWatch step was removed from the CI workflow")
    if _TEST_COMMAND.search(after) and len(_CONTINUE.findall(after)) > len(
        _CONTINUE.findall(before)
    ):
        yield _finding(
            change,
            "`continue-on-error` was added to a CI workflow that runs tests",
            "continue-on-error",
        )
    if _swallowed(after) > _swallowed(before):
        yield _finding(change, "A test command in CI now ignores failures (`|| true`)", "||")


def _code_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def _guard_lines(text: str) -> int:
    return sum(1 for line in _code_lines(text) if "assertwatch" in line.lower())


def _swallowed(text: str) -> int:
    return sum(
        1 for line in _code_lines(text) if _TEST_COMMAND.search(line) and _SWALLOW.search(line)
    )


# --- pytest and coverage config ---------------------------------------------------------


@dataclass
class _PytestSettings:
    narrowing: set[tuple[str, str]] = field(default_factory=set)  # (option, value)
    testpaths: list[str] = field(default_factory=list)
    fail_under: float | None = None


def _pytest_config(change: FileChange) -> Iterator[Finding]:
    name = PurePosixPath(change.path).name
    before, after = _settings(name, change.before), _settings(name, change.after)
    for option, value in sorted(after.narrowing - before.narrowing):
        if option == "-m" and "not" not in value.split():
            continue  # selecting markers isn't loosening unless it excludes some
        shown = f"{option} {value}".strip()
        yield _finding(change, f"pytest addopts now runs fewer tests: `{shown}`", option)
    lost = [p for p in before.testpaths if not any(_covers(new, p) for new in after.testpaths)]
    if after.testpaths and lost:
        yield _finding(change, f"pytest testpaths no longer include {', '.join(lost)}", "testpaths")
    if before.fail_under is not None and (
        after.fail_under is None or after.fail_under < before.fail_under
    ):
        now = "removed" if after.fail_under is None else f"lowered to {after.fail_under:g}"
        yield _finding(
            change, f"Coverage fail_under was {now} (was {before.fail_under:g})", "fail_under"
        )


def _covers(new: str, old: str) -> bool:
    """new collects at least what old did: the same path, a parent, or old minus its suffix."""
    new_path, old_path = PurePosixPath(new), PurePosixPath(old)
    return new_path in (old_path, old_path.with_suffix("")) or new_path in old_path.parents


def _settings(name: str, text: str | None) -> _PytestSettings:
    settings = _PytestSettings()
    if not text:
        return settings
    addopts: list[str] = []
    fail_under: str | float | None = None
    if name == "pyproject.toml":
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return settings
        tool = data.get("tool", {})
        pytest = tool.get("pytest", {})
        for section in (pytest.get("ini_options", {}), pytest):
            addopts += _as_list(section.get("addopts"))
            settings.testpaths += _as_list(section.get("testpaths"))
        fail_under = tool.get("coverage", {}).get("report", {}).get("fail_under")
    else:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read_string(text)
        except configparser.Error:
            return settings
        pytest_section = {"setup.cfg": "tool:pytest"}.get(name, "pytest")
        if parser.has_section(pytest_section):
            addopts += _as_list(parser.get(pytest_section, "addopts", fallback=None))
            settings.testpaths += _as_list(parser.get(pytest_section, "testpaths", fallback=None))
        report = "report" if name == ".coveragerc" else "coverage:report"
        if parser.has_section(report):
            fail_under = parser.get(report, "fail_under", fallback=None)
    settings.narrowing, cov_fail_under = _narrowing(addopts)
    settings.fail_under = _float(fail_under if fail_under is not None else cov_fail_under)
    return settings


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        try:
            return shlex.split(value)
        except ValueError:
            return value.split()
    return []


def _narrowing(tokens: list[str]) -> tuple[set[tuple[str, str]], str | None]:
    found: set[tuple[str, str]] = set()
    cov_fail_under = None
    i = 0
    while i < len(tokens):
        token = tokens[i]
        option, _, value = token.partition("=")
        if option == "--cov-fail-under":
            cov_fail_under = value or (tokens[i + 1] if i + 1 < len(tokens) else None)
            i += 1 if value else 2
            continue
        short = option[:2] if option.startswith("-") and not option.startswith("--") else None
        if short in ("-k", "-m") and len(option) > 2:
            found.add((short, option[2:]))
        elif option in _NARROWING:
            if _NARROWING[option] and not value:
                value = tokens[i + 1] if i + 1 < len(tokens) else ""
                i += 1
            found.add((option, value))
        i += 1
    return found, cov_fail_under


def _float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# --- conftest.py ------------------------------------------------------------------------

_SKIP_NAMES = ("mark.skip", "mark.skipif", "mark.xfail", "pytest.skip", "pytest.xfail")


def _conftest(change: FileChange) -> Iterator[Finding]:
    before, after = _conftest_facts(change.before), _conftest_facts(change.after)
    if after is None:
        return
    before = before or (set(), 0)
    added = sorted(after[0] - before[0])
    if added:
        yield _finding(
            change, f"conftest.py now ignores test paths: {', '.join(added)}", "collect_ignore"
        )
    if after[1] > before[1]:
        yield _finding(change, "conftest.py adds skip or xfail markers to collected tests")


def _conftest_facts(text: str | None) -> tuple[set[str], int] | None:
    """Ignored paths (collect_ignore*) and the number of skip/xfail references."""
    if text is None:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    ignored: set[str] = set()
    skips = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(dotted(t).startswith("collect_ignore") for t in targets) and node.value:
                ignored |= _string_literals(node.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = dotted(node.func.value)
            if owner.startswith("collect_ignore") and node.func.attr in ("append", "extend"):
                for arg in node.args:
                    ignored |= _string_literals(arg)
        if isinstance(node, ast.Attribute | ast.Name) and dotted(node).endswith(_SKIP_NAMES):
            skips += 1
    return ignored, skips


def _string_literals(node: ast.AST) -> set[str]:
    return {
        n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


RULE = Rule(
    id=ID,
    title="Test or guard config loosened",
    severity=Severity.BLOCK,
    summary="Test selection, coverage thresholds, CI test steps or the guard were loosened.",
    check=check,
)
