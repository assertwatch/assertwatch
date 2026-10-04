"""Whether a project has dropped Python 2, from what its packaging metadata declares.

Rules treat code that only runs on Python 2 (`if PY2:` branches, `skipif(not PY2)` tests) as
dead only when the project says it no longer supports Python 2. The metadata is read from the
base ref, like the config, so a change can't declare its own way out of a finding.

Sources, at the repository root: `requires-python` and classifiers in pyproject.toml (and
Poetry's `python` dependency), `python_requires` and classifiers in setup.cfg and setup.py.
Any sign of Python 2 support wins; with no signal either way, support is unknown and the
answer is False.
"""

from __future__ import annotations

import configparser
import re
import tomllib
from collections.abc import Callable

_CLAUSE = re.compile(r"(===|==|~=|>=|<=|!=|>|<|\^|~)?\s*v?(\d+)(?:\.(?:\d+|\*))*")
_PY2_CLASSIFIER = re.compile(r"Programming Language :: Python :: 2(\.\d+)?\s*$")
_PY3_ONLY = "Programming Language :: Python :: 3 :: Only"
_SETUP_REQUIRES = re.compile(r"python_requires\s*=\s*[\"']([^\"']+)[\"']")
_SETUP_CLASSIFIER = re.compile(r"[\"'](Programming Language :: Python[^\"']*)[\"']")


def python2_dropped(read: Callable[[str], str | None]) -> bool:
    """True if the metadata read() returns rules out Python 2; read(path) -> text or None."""
    specs: list[str] = []
    classifiers: list[str] = []
    _pyproject(read("pyproject.toml"), specs, classifiers)
    _setup_cfg(read("setup.cfg"), specs, classifiers)
    setup_py = read("setup.py")
    if setup_py:
        specs.extend(_SETUP_REQUIRES.findall(setup_py))
        classifiers.extend(_SETUP_CLASSIFIER.findall(setup_py))
    if any(_PY2_CLASSIFIER.search(c) for c in classifiers):
        return False
    verdicts = [excludes_python2(s) for s in specs]
    if False in verdicts:
        return False
    return True in verdicts or any(c.strip() == _PY3_ONLY for c in classifiers)


def excludes_python2(spec: str) -> bool:
    """`>=3.8`, `~=3.9`, `^3.10`, `>=3, <4` -> True; `>=2.7, !=3.0.*`, `<4`, `*` -> False."""
    for clause in spec.split(","):
        match = _CLAUSE.fullmatch(clause.strip())
        if match is None:
            continue
        op, major = match.group(1) or "==", int(match.group(2))
        if major >= 3 and op in ("===", "==", "~=", ">=", ">", "^", "~"):
            return True
    return False


def _pyproject(text: str | None, specs: list[str], classifiers: list[str]) -> None:
    if not text:
        return
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return
    project = data.get("project", {})
    if isinstance(project, dict):
        if isinstance(project.get("requires-python"), str):
            specs.append(project["requires-python"])
        classifiers.extend(c for c in project.get("classifiers", []) if isinstance(c, str))
    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    if isinstance(poetry, dict):
        dependencies = poetry.get("dependencies")
        python = dependencies.get("python") if isinstance(dependencies, dict) else None
        if isinstance(python, str):
            specs.append(python)
        classifiers.extend(c for c in poetry.get("classifiers", []) if isinstance(c, str))


def _setup_cfg(text: str | None, specs: list[str], classifiers: list[str]) -> None:
    if not text:
        return
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return
    spec = parser.get("options", "python_requires", fallback=None)
    if spec:
        specs.append(spec)
    listed = parser.get("metadata", "classifiers", fallback="")
    classifiers.extend(line.strip() for line in listed.splitlines() if line.strip())
