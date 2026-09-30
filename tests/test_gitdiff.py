import subprocess
from pathlib import Path

import pytest

from assertwatch.gitdiff import (
    ChangedFile,
    GitError,
    changed_files,
    read_text,
    repo_root,
    resolve_base,
    resolve_commit,
)


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch, tmp_path_factory):
    """Keep the user's git config (signing, hooks, identity) out of the tests."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{var}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{var}_EMAIL", "test@example.invalid")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def write(repo: Path, path: str, content: str | bytes) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content)


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


TEST_BODY = "def test_one():\n    assert 1 + 1 == 2\n\n\ndef test_two():\n    assert 'a' in 'abc'\n"


@pytest.fixture
def repo(tmp_path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    write(tmp_path, "src/app.py", "def add(a, b):\n    return a + b\n")
    write(tmp_path, "tests/test_app.py", TEST_BODY)
    write(tmp_path, "tests/test_old.py", "def test_old():\n    assert True\n")
    write(tmp_path, "tests/test_rename_me.py", TEST_BODY.replace("one", "alpha"))
    commit_all(tmp_path, "base")
    return tmp_path


def test_repo_root_from_subdirectory(repo):
    assert repo_root(repo / "tests") == repo.resolve()


def test_not_a_repo_raises(tmp_path):
    with pytest.raises(GitError):
        repo_root(tmp_path)


def test_committed_changes_add_modify_delete_rename_and_skip_binary(repo):
    base = resolve_commit(repo, "HEAD")
    git(repo, "checkout", "-q", "-b", "feature")
    write(repo, "tests/test_app.py", TEST_BODY + "\n\ndef test_three():\n    assert 3\n")
    (repo / "tests/test_old.py").unlink()
    git(repo, "mv", "tests/test_rename_me.py", "tests/test_renamed.py")
    write(repo, "tests/test_new file.py", "def test_new():\n    assert 1\n")
    write(repo, "tests/data/blob.bin", b"\x00\x01\x02binary")
    commit_all(repo, "feature work")

    changes = changed_files(repo, resolve_base(repo, head="HEAD"), "HEAD")

    assert changes == [
        ChangedFile("M", "tests/test_app.py"),
        ChangedFile("A", "tests/test_new file.py"),
        ChangedFile("D", "tests/test_old.py"),
        ChangedFile("R", "tests/test_renamed.py", "tests/test_rename_me.py"),
    ]
    assert resolve_base(repo) == base


def test_binary_included_on_request(repo):
    write(repo, "tests/data/blob.bin", b"\x00\x01\x02binary")
    head = commit_all(repo, "binary")
    changes = changed_files(repo, f"{head}~1", head, include_binary=True)
    assert changes == [ChangedFile("A", "tests/data/blob.bin")]


def test_worktree_includes_staged_unstaged_and_untracked(repo):
    base = resolve_base(repo)
    write(repo, "src/app.py", "def add(a, b):\n    return a - b\n")  # unstaged
    write(repo, "tests/test_app.py", "def test_one():\n    pass\n")
    git(repo, "add", "tests/test_app.py")  # staged
    write(repo, "tests/test_untracked.py", "def test_u():\n    assert 1\n")  # untracked
    write(repo, ".gitignore", "*.log\n")
    write(repo, "debug.log", "ignored")

    changes = changed_files(repo, base)

    assert changes == [
        ChangedFile("A", ".gitignore"),
        ChangedFile("M", "src/app.py"),
        ChangedFile("M", "tests/test_app.py"),
        ChangedFile("A", "tests/test_untracked.py"),
    ]


def test_read_text_at_base_head_and_worktree(repo):
    base = resolve_commit(repo, "HEAD")
    write(repo, "src/app.py", "changed in head\n")
    head = commit_all(repo, "head")
    write(repo, "src/app.py", "changed in worktree\n")

    assert read_text(repo, base, "src/app.py") == "def add(a, b):\n    return a + b\n"
    assert read_text(repo, head, "src/app.py") == "changed in head\n"
    assert read_text(repo, None, "src/app.py") == "changed in worktree\n"


def test_read_text_missing_and_binary_return_none(repo):
    write(repo, "img.bin", b"\x89PNG\x00\x00")
    head = commit_all(repo, "binary")
    assert read_text(repo, head, "does/not/exist.py") is None
    assert read_text(repo, None, "does/not/exist.py") is None
    assert read_text(repo, head, "img.bin") is None


def test_base_is_merge_base_not_branch_tip(repo):
    fork_point = resolve_commit(repo, "HEAD")
    git(repo, "checkout", "-q", "-b", "feature")
    write(repo, "tests/test_app.py", TEST_BODY + "# feature\n")
    commit_all(repo, "feature")
    git(repo, "checkout", "-q", "main")
    write(repo, "src/app.py", "# main moved on\n")
    commit_all(repo, "main moves")
    git(repo, "checkout", "-q", "feature")

    base = resolve_base(repo, "main")
    assert base == fork_point
    # The change made on main after the fork is not reported as a change on feature.
    assert changed_files(repo, base, "HEAD") == [ChangedFile("M", "tests/test_app.py")]


def test_default_base_falls_back_to_master(tmp_path):
    git(tmp_path, "init", "-q", "-b", "master")
    write(tmp_path, "a.py", "x = 1\n")
    sha = commit_all(tmp_path, "init")
    assert resolve_base(tmp_path) == sha


def test_no_default_branch_raises(tmp_path):
    git(tmp_path, "init", "-q", "-b", "trunk")
    write(tmp_path, "a.py", "x = 1\n")
    commit_all(tmp_path, "init")
    with pytest.raises(GitError, match="pass --base"):
        resolve_base(tmp_path)


def test_unknown_base_raises(repo):
    with pytest.raises(GitError, match="unknown revision"):
        resolve_base(repo, "no-such-branch")


def test_changed_file_side_paths():
    assert ChangedFile("A", "n.py").base_path is None
    assert ChangedFile("D", "o.py").head_path is None
    renamed = ChangedFile("R", "new.py", "old.py")
    assert (renamed.base_path, renamed.head_path) == ("old.py", "new.py")
