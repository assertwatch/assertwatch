from pathlib import Path

import pytest
from gitutil import commit_all, git, write

from assertwatch.check import check_diff

TESTS = """\
import pytest


def test_total():
    assert total() == 10


def test_tax():
    assert tax() == 2
"""


@pytest.fixture
def repo(tmp_path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    write(tmp_path, "tests/test_cart.py", TESTS)
    write(tmp_path, "src/cart.py", "def total():\n    return 10\n")
    commit_all(tmp_path, "base")
    git(tmp_path, "tag", "base")
    return tmp_path


def test_worktree_changes_are_checked(repo):
    write(repo, "tests/test_cart.py", TESTS.replace("== 10", "is not None"))
    result = check_diff(repo, "main")
    assert [(f.rule, f.path) for f in result.findings] == [("AW004", "tests/test_cart.py")]
    assert result.errors == []


def test_committed_head_and_file_rename(repo):
    git(repo, "mv", "tests/test_cart.py", "tests/test_basket.py")
    write(
        repo,
        "tests/test_basket.py",
        TESTS.replace("def test_tax", "@pytest.mark.skip\ndef test_tax"),
    )
    head = commit_all(repo, "rename and skip")
    result = check_diff(repo, "base", head)
    assert result.test_files == 1  # the renamed file counts once
    found = result.findings
    assert [(f.rule, f.test_id) for f in found] == [("AW003", "tests/test_basket.py::test_tax")]


def test_non_test_files_and_deleted_test_files(repo):
    write(repo, "src/cart.py", "def total():\n    return 11\n")
    assert check_diff(repo, "main").findings == []
    assert check_diff(repo, "main").test_files == 0
    (repo / "tests/test_cart.py").unlink()
    assert [f.rule for f in check_diff(repo, "main").findings] == ["AW001", "AW001"]


def test_unparsable_file_is_reported_not_treated_as_removed(repo):
    write(repo, "tests/test_cart.py", "def test_total(:\n")
    result = check_diff(repo, "main")
    assert result.findings == []
    assert len(result.errors) == 1 and result.errors[0].startswith("tests/test_cart.py: Syntax")


PY2_BRANCH = (
    "def test_total():\n    if PY2:\n        assert total() == 9\n    assert total() == 10\n"
)
PY3_ONLY = "def test_total():\n    assert total() == 10\n"


@pytest.mark.parametrize(
    ("base_requires", "head_requires", "rules"),
    [
        (">=3.8", ">=3.8", []),  # Python 2 already dropped: the branch was dead
        (">=2.7", ">=2.7", ["AW002"]),  # still supported: the branch ran on Python 2
        (">=2.7", ">=3.8", ["AW002"]),  # the change can't drop support for its own check
        (None, None, ["AW002"]),  # unknown support
    ],
)
def test_python2_branches_follow_the_base_declaration(repo, base_requires, head_requires, rules):
    def project(spec):
        if spec is not None:
            write(repo, "pyproject.toml", f'[project]\nname = "x"\nrequires-python = "{spec}"\n')

    project(base_requires)
    write(repo, "tests/test_cart.py", PY2_BRANCH)
    base = commit_all(repo, "with a Python 2 branch")
    project(head_requires)
    write(repo, "tests/test_cart.py", PY3_ONLY)
    head = commit_all(repo, "drop it")
    assert [f.rule for f in check_diff(repo, base, head).findings] == rules


def test_removed_test_with_a_copy_in_an_untouched_file(repo):
    tax = "\n\ndef test_tax():\n    assert tax() == 2\n"
    write(repo, "tests/test_tax.py", tax)
    commit_all(repo, "copy test_tax")
    write(repo, "tests/test_cart.py", TESTS.split("\n\ndef test_tax")[0] + "\n")
    copied = [("AW001", "warn")]  # reported for a human, not blocking
    assert levels(check_diff(repo, "HEAD")) == copied  # working tree
    head = commit_all(repo, "drop the leftover")
    assert levels(check_diff(repo, "HEAD~1", head)) == copied
    (repo / "tests/test_tax.py").unlink()  # the copy is read at head, not the working tree
    assert levels(check_diff(repo, "HEAD~1", head)) == copied
    git(repo, "checkout", "--", "tests/test_tax.py")
    # A copy outside the configured test paths doesn't count.
    write(repo, "assertwatch.toml", 'test_paths = ["tests/test_cart.py"]\n')
    commit_all(repo, "config")
    write(repo, "tests/test_cart.py", TESTS)
    commit_all(repo, "restore")
    write(repo, "tests/test_cart.py", TESTS.split("\n\ndef test_tax")[0] + "\n")
    assert levels(check_diff(repo, "HEAD")) == [("AW001", "block")]


def levels(result):
    return [(f.rule, f.severity) for f in result.findings]


def test_copy_in_a_file_the_change_also_touches_is_not_reused(repo):
    write(repo, "tests/test_tax.py", "\n\ndef test_tax():\n    assert tax() == 2\n")
    commit_all(repo, "copy test_tax")
    write(repo, "tests/test_cart.py", TESTS.split("\n\ndef test_tax")[0] + "\n")
    write(repo, "tests/test_tax.py", "\n\ndef test_tax():\n    assert tax() >= 0\n")
    found = check_diff(repo, "HEAD").findings
    assert ("AW001", "tests/test_cart.py::test_tax") in [(f.rule, f.test_id) for f in found]
