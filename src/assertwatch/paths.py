"""Which paths hold tests and which hold test or guard config.

Kept free of heavy imports: the Claude Code hook uses these to decide in a few milliseconds
whether a change needs checking at all.
"""

from __future__ import annotations

from fnmatch import fnmatch, fnmatchcase
from pathlib import PurePosixPath

CONFIG_PATH = "assertwatch.toml"
PYTHON_TEST_GLOBS = ("test_*.py", "*_test.py")
# Maven Surefire's and Failsafe's default includes.
JAVA_TEST_GLOBS = ("Test*.java", "*Test.java", "*Tests.java", "*TestCase.java", "*IT.java")
TEST_FILE_GLOBS = PYTHON_TEST_GLOBS + JAVA_TEST_GLOBS
SETTINGS = (".claude/settings.json", ".claude/settings.local.json")
WORKFLOWS = (".github/workflows/*.yml", ".github/workflows/*.yaml")
PYTEST_FILES = frozenset({"pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", ".coveragerc"})


def is_test_path(path: str) -> bool:
    name = PurePosixPath(path).name
    return any(fnmatch(name, pattern) for pattern in TEST_FILE_GLOBS)


def is_workflow_path(path: str) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in WORKFLOWS)


def is_guard_path(path: str) -> bool:
    """Guard and test config files, which AW009 reads in full."""
    name = PurePosixPath(path).name
    return (
        path in (CONFIG_PATH, *SETTINGS)
        or name in PYTEST_FILES
        or name == "conftest.py"
        or is_workflow_path(path)
    )
