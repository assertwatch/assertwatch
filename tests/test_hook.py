import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from gitutil import commit_all, git, write

from assertwatch import hook

TESTS = """\
def test_total():
    assert total() == 10
    assert total() > 0


def test_tax():
    assert tax() == 2
"""


@pytest.fixture
def repo(tmp_path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    write(tmp_path, "tests/test_cart.py", TESTS)
    write(tmp_path, "src/cart.py", "def total():\n    return 10\n")
    commit_all(tmp_path, "base")
    return tmp_path


def run(repo: Path | str, event: str | None = "Stop", **fields) -> tuple[int, str, str]:
    payload = {"session_id": "s1", "cwd": str(repo), **fields}
    if event is not None:
        payload["hook_event_name"] = event
    out, err = io.StringIO(), io.StringIO()
    code = hook.run(json.dumps(payload), out, err)
    return code, out.getvalue(), err.getvalue()


def drop_assertion(repo: Path) -> None:
    write(repo, "tests/test_cart.py", TESTS.replace("    assert total() == 10\n", ""))


def state(repo: Path, session: str = "s1") -> dict:
    return json.loads((repo / ".git" / hook.STATE_DIR / f"{session}.json").read_text())


def test_outside_a_git_repo_does_nothing(tmp_path):
    assert run(tmp_path) == (0, "", "")
    assert run(tmp_path / "missing") == (0, "", "")


def test_session_start_records_head_silently(repo):
    head = git(repo, "rev-parse", "HEAD")
    assert run(repo, "SessionStart", source="startup") == (0, "", "")
    assert state(repo)["base"] == head
    write(repo, "src/x.py", "x = 1\n")
    commit_all(repo, "later")
    run(repo, "SessionStart", source="resume")  # a resumed session keeps its starting point
    assert state(repo)["base"] == head


def test_clean_session_passes_silently(repo):
    run(repo, "SessionStart")
    write(repo, "src/cart.py", "def total():\n    return 11\n")
    assert run(repo) == (0, "", "")


def test_dropping_a_check_the_exact_value_implies_passes(repo):
    # `total() > 0` is implied by the `total() == 10` that stays, so nothing was weakened
    run(repo, "SessionStart")
    write(repo, "tests/test_cart.py", TESTS.replace("    assert total() > 0\n", ""))
    assert run(repo) == (0, "", "")


def test_dropped_assertion_blocks_once_then_warns(repo):
    run(repo, "SessionStart")
    drop_assertion(repo)
    code, out, err = run(repo)
    assert (code, out) == (2, "")
    assert err.startswith("AssertWatch blocked the end of this turn")
    assert "AW002" in err and "- assert total() == 10" in err

    code, out, err = run(repo)  # the agent explained instead of restoring: don't loop
    assert (code, err) == (0, "")
    message = json.loads(out)["systemMessage"]
    assert "1 blocking finding in this session's changes is unresolved" in message
    assert "assertwatch check --worktree --base" in message


def test_restored_assertion_passes(repo):
    run(repo, "SessionStart")
    drop_assertion(repo)
    assert run(repo)[0] == 2
    write(repo, "tests/test_cart.py", TESTS)
    assert run(repo) == (0, "", "")


def test_new_finding_blocks_again_after_an_old_one_was_shown(repo):
    run(repo, "SessionStart")
    drop_assertion(repo)
    assert run(repo)[0] == 2
    source = TESTS.replace("    assert total() == 10\n", "").replace("def test_tax", "def _tax")
    write(repo, "tests/test_cart.py", source)
    code, _, err = run(repo)
    assert code == 2 and "AW001" in err


def test_unreadable_test_file_blocks_once(repo):
    # Code the parser can't read must not hide a change: it blocks (fail closed).
    run(repo, "SessionStart")
    write(repo, "tests/test_cart.py", TESTS + "\ndef broken(:\n")
    code, out, err = run(repo)
    assert code == hook.EXIT_BLOCK
    assert "couldn't read" in err and "tests/test_cart.py" in err
    code, out, err = run(repo)  # already shown: a warning, not another block
    assert code == hook.EXIT_OK and "unresolved" in out


def test_stop_hook_active_never_blocks(repo):
    run(repo, "SessionStart")
    drop_assertion(repo)
    code, out, err = run(repo, stop_hook_active=True)
    assert (code, err) == (0, "")
    assert "systemMessage" in json.loads(out)


def test_expected_value_rewritten_to_match_the_code_blocks(repo):
    run(repo, "SessionStart")
    write(repo, "tests/test_cart.py", TESTS.replace("total() == 10", "total() == 11"))
    code, _, err = run(repo)
    assert code == 2 and "AW005 [block]" in err
    assert "a checked value changed from `10` to `11`" in err


def test_expected_value_changed_with_the_code_only_warns(repo):
    run(repo, "SessionStart")
    write(repo, "src/cart.py", "def total():\n    return 11\n")
    write(repo, "tests/test_cart.py", TESTS.replace("total() == 10", "total() == 11"))
    assert run(repo) == (0, "", "")


def test_warn_findings_do_not_block(repo):
    write(repo, "assertwatch.toml", '[severity]\nAW004 = "warn"\n')
    commit_all(repo, "config")
    run(repo, "SessionStart")
    write(repo, "tests/test_cart.py", TESTS.replace("== 10", "is not None"))
    assert run(repo) == (0, "", "")


def test_commits_made_during_the_session_are_checked(repo):
    run(repo, "SessionStart")
    drop_assertion(repo)
    commit_all(repo, "simplify test")  # on main: the merge-base alone would hide this
    assert run(repo)[0] == 2


def test_without_session_state_the_merge_base_is_used(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    drop_assertion(repo)
    commit_all(repo, "simplify test")
    assert run(repo, session_id=None)[0] == 2
    assert run(repo, session_id=None)[0] == 2  # nothing remembered without a session


def test_lost_session_base_falls_back_to_the_merge_base(repo):
    path = repo / ".git" / hook.STATE_DIR / "s1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"base": "0" * 40, "shown": []}))
    drop_assertion(repo)
    assert run(repo)[0] == 2


def test_manual_run_without_payload(repo, monkeypatch):
    drop_assertion(repo)
    monkeypatch.chdir(repo / "src")
    for payload in ("", "not json", "[1, 2]"):
        assert hook.run(payload, io.StringIO(), io.StringIO()) == 2


def test_other_events_are_ignored(repo):
    drop_assertion(repo)
    assert run(repo, "PreToolUse") == (0, "", "")


def test_runs_from_a_subdirectory(repo):
    run(repo, "SessionStart")
    drop_assertion(repo)
    assert run(repo / "src")[0] == 2


def test_repo_without_commits(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    write(tmp_path, "tests/test_a.py", "def test_a():\n    assert 1\n")
    assert run(tmp_path, "SessionStart") == (0, "", "")
    assert run(tmp_path) == (0, "", "")


def test_session_id_cannot_escape_the_state_dir(repo):
    run(repo, "SessionStart", session_id="../../../evil")
    files = list((repo / ".git" / hook.STATE_DIR).iterdir())
    assert [f.name for f in files] == ["_.._.._evil.json"]
    assert not (repo / "evil.json").exists()


def test_prune_forgets_old_sessions(repo):
    run(repo, "SessionStart", session_id="old")
    run(repo, "SessionStart", session_id="new")
    old = repo / ".git" / hook.STATE_DIR / "old.json"
    stale = old.stat().st_mtime - hook.STATE_MAX_AGE - 10
    os.utime(old, (stale, stale))
    run(repo, "SessionStart", session_id="newest")
    assert sorted(p.name for p in old.parent.iterdir()) == ["new.json", "newest.json"]


def test_corrupt_state_is_ignored(repo):
    path = repo / ".git" / hook.STATE_DIR / "s1.json"
    path.parent.mkdir(parents=True)
    path.write_text("{oops")
    drop_assertion(repo)
    assert run(repo)[0] == 2
    assert state(repo)["shown"]


def test_fast_path_skips_the_rules(repo):
    run(repo, "SessionStart")
    write(repo, "src/cart.py", "def total():\n    return 11\n")
    payload = json.dumps({"hook_event_name": "Stop", "cwd": str(repo), "session_id": "s1"})
    script = (
        "import io, sys\n"
        "sys.argv = ['assertwatch', 'hook']\n"
        f"sys.stdin = io.StringIO({payload!r})\n"
        "from assertwatch.cli import main\n"
        "code = main()\n"
        "ours = sorted(m for m in sys.modules if m.startswith('assertwatch'))\n"
        "print(code, 'argparse' in sys.modules, *ours)\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    # Stdlib internals vary by patch release; what we import is ours to keep small.
    expected = ["0", "False", "assertwatch", "assertwatch.cli", "assertwatch.hook"]
    assert result.stdout.split() == [*expected, "assertwatch.paths"], result.stderr


def test_guard_file_change_alone_is_checked(repo):
    settings = {"hooks": {"Stop": [{"hooks": [{"command": "assertwatch hook"}]}]}}
    write(repo, ".claude/settings.json", json.dumps(settings))
    commit_all(repo, "hook")
    run(repo, "SessionStart")
    write(repo, ".claude/settings.json", "{}")
    code, _, err = run(repo)
    assert code == 2 and "AW009" in err


def test_untracked_guard_file_is_checked(repo):
    run(repo, "SessionStart")
    write(repo, "tests/conftest.py", 'collect_ignore = ["test_cart.py"]\n')
    code, _, err = run(repo)
    assert code == 2 and "AW009" in err


def test_cli_end_to_end(repo):
    drop_assertion(repo)
    payload = json.dumps({"hook_event_name": "Stop", "cwd": str(repo), "session_id": "e2e"})
    result = subprocess.run(
        [sys.executable, "-m", "assertwatch", "hook"],
        input=payload,
        capture_output=True,
        text=True,
        cwd=repo,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "AW002" in result.stderr


def assert_head_matches_git(path: Path) -> None:
    repo = hook.Repo.find(str(path))
    assert repo is not None
    assert repo._read_head() == git(path, "rev-parse", "HEAD")
    assert repo.top == Path(git(path, "rev-parse", "--show-toplevel")).resolve()


def test_head_from_loose_and_packed_refs(repo):
    assert_head_matches_git(repo)
    git(repo, "pack-refs", "--all")
    assert not (repo / ".git/refs/heads/main").exists()
    assert_head_matches_git(repo)
    write(repo, "src/x.py", "x = 1\n")
    commit_all(repo, "after packing")  # the loose ref now wins over the stale packed one
    assert_head_matches_git(repo)


def test_head_when_detached(repo):
    git(repo, "checkout", "-q", "--detach")
    assert_head_matches_git(repo)


def test_linked_worktree(repo, tmp_path_factory):
    other = tmp_path_factory.mktemp("wt") / "feature"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(other))
    write(other, "src/x.py", "x = 1\n")
    commit_all(other, "feature work")
    assert_head_matches_git(other)
    assert hook.Repo.find(str(other)).git_dir != repo / ".git"

    run(other, "SessionStart", session_id="wt")
    write(other, "tests/test_cart.py", TESTS.replace("    assert total() == 10\n", ""))
    assert run(other, session_id="wt")[0] == 2
    assert run(repo, session_id="wt") == (0, "", "")  # state is per worktree


def test_git_dir_env_defers_to_git(repo, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    found = hook.Repo.find(str(repo))
    assert found is not None and found.top == repo.resolve()


def test_unknown_head_format_asks_git(repo):
    found = hook.Repo.find(str(repo))
    (repo / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (repo / ".git/refs/heads/main").write_text("garbage\n")
    assert found._read_head() is None
