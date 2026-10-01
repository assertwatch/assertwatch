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
