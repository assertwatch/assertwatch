"""Reporter output is snapshot-tested. Run with UPDATE_SNAPSHOTS=1 to rewrite the snapshots."""

import json
import os
from pathlib import Path

import pytest
from gitutil import commit_all, git, write

from assertwatch.cli import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, main
from assertwatch.model import Finding, Severity
from assertwatch.report import Report, failing, render
from assertwatch.report.actions import annotations, outputs

SNAPSHOTS = Path(__file__).parent / "snapshots"
BASE = "0123456789abcdef0123456789abcdef01234567"
HEAD = "fedcba9876543210fedcba9876543210fedcba98"

FINDINGS = [
    Finding(
        rule="AW002",
        severity=Severity.BLOCK,
        path="tests/test_refund.py",
        line=12,
        message="`test_refund` has 2 assertion(s), down from 3.",
        test_id="tests/test_refund.py::test_refund",
        before="assert refund.currency == 'USD'",
        hint="Restore the removed assertion(s) and fix the code so they pass.",
    ),
    Finding(
        rule="AW004",
        severity=Severity.WARN,
        path="tests/test_pay.py",
        line=40,
        message="Assertion weakened in `test_total`: an equality check became a not-None check.",
        test_id="tests/test_pay.py::TestPay::test_total",
        before="assert total() == 10 | 2",
        after="assert total() is not None",
        hint="Restore the stricter assertion and fix the code so it passes.",
    ),
    Finding(
        rule="AW000",
        severity=Severity.BLOCK,
        path="tests/test_pay.py",
        line=7,
        message='Waiver proposed in this change (inline allow AW003); reason given: "flaky". '
        "It needs human approval.",
    ),
    Finding(
        rule="AW001",
        severity=Severity.BLOCK,
        path="tests/legacy/test_old.py",
        line=3,
        message="Test `test_old` was removed and no renamed or moved copy was found.",
        test_id="tests/legacy/test_old.py::test_old",
        before="assert old() == 1",
        waiver="legacy suite retired",
        side="base",
    ),
]
FULL = Report(BASE, HEAD, FINDINGS, ["tests/test_broken.py: SyntaxError: bad"], test_files=4)
CLEAN = Report(BASE, None, [], [], test_files=1)


def check_snapshot(name: str, actual: str) -> None:
    path = SNAPSHOTS / name
    if os.environ.get("UPDATE_SNAPSHOTS"):
        path.parent.mkdir(exist_ok=True)
        path.write_text(actual)
    assert actual == path.read_text(), f"{name} changed; rerun with UPDATE_SNAPSHOTS=1 if intended"


@pytest.mark.parametrize("fmt, ext", [("text", "txt"), ("json", "json"), ("github", "md")])
@pytest.mark.parametrize("name, report", [("full", FULL), ("clean", CLEAN)])
def test_snapshots(fmt, ext, name, report):
    check_snapshot(f"{name}.{ext}", render(report, fmt))


def test_json_summary_counts_and_round_trip():
    data = json.loads(render(FULL, "json"))
    assert data["version"] == 1
    assert data["summary"] == {"findings": 3, "block": 2, "warn": 1, "info": 0, "waived": 1}
    assert data["findings"][3]["waiver"] == "legacy suite retired"
    assert data["head"] == HEAD and data["errors"] == ["tests/test_broken.py: SyntaxError: bad"]


def test_failing_ignores_waived_and_respects_threshold():
    assert [f.rule for f in failing(FULL, "block")] == ["AW002", "AW000"]
    assert [f.rule for f in failing(FULL, "warn")] == ["AW002", "AW004", "AW000"]
    assert failing(FULL, "never") == []
    only_waived = Report(BASE, HEAD, [FINDINGS[3]])
    assert failing(only_waived, "warn") == []


def test_github_escapes_table_cells():
    md = render(FULL, "github")
    assert "| warn | AW004 | `tests/test_pay.py:40` |" in md
    assert "assert total() == 10 \\| 2" not in md  # snippets go in diff blocks, unescaped
    assert "-assert total() == 10 | 2" in md
    piped = Report(BASE, HEAD, [Finding("AW001", Severity.BLOCK, "t.py", 1, "a | b")])
    assert "| a \\| b |" in render(piped, "github")


def test_actions_annotations_snapshot():
    check_snapshot("full.actions.txt", "\n".join(annotations(FULL, "block")) + "\n")


def test_actions_annotation_levels_follow_fail_on():
    levels = [line.split(" ", 1)[0] for line in annotations(FULL, "never")[:3]]
    assert levels == ["::warning", "::warning", "::warning"]  # report-only: nothing is an error
    levels = [line.split(" ", 1)[0] for line in annotations(FULL, "warn")[:3]]
    assert levels == ["::error", "::error", "::error"]
    info = Report(BASE, HEAD, [Finding("AW009", Severity.INFO, "pytest.ini", 2, "x")])
    assert annotations(info, "warn") == ["::notice file=pytest.ini,line=2,title=AW009 (info)::x"]


def test_actions_annotations_escape_and_place_removals():
    removed = Finding(
        "AW001", Severity.BLOCK, "t,1:x.py", 9, "50% gone\nnow", hint="Restore.", side="base"
    )
    [line] = annotations(Report(BASE, HEAD, [removed]), "block")
    # A removed test's line is on the base side, which GitHub can't place: file-level only.
    assert line == (
        "::error file=t%2C1%3Ax.py,title=AW001 (block)::"
        "50%25 gone%0Anow (line 9 before the change)%0ARestore."
    )


def test_actions_outputs():
    assert outputs(FULL, "block") == {
        "findings": "3",
        "block": "2",
        "warn": "1",
        "info": "0",
        "failed": "true",
    }
    assert outputs(FULL, "never")["failed"] == "false"
    assert outputs(CLEAN, "warn")["failed"] == "false"


# --- CLI end to end ---------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    write(tmp_path, "tests/test_a.py", "def test_a():\n    assert a() == 1\n    assert b() == 2\n")
    commit_all(tmp_path, "base")
    git(tmp_path, "checkout", "-q", "-b", "feature")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_cli_clean(repo, capsys):
    assert main(["check"]) == EXIT_OK
    assert "no test weakening found" in capsys.readouterr().out


def test_cli_findings_in_commit_and_worktree(repo, capsys):
    write(repo, "tests/test_a.py", "def test_a():\n    assert a() == 1\n")
    assert main(["check"]) == EXIT_OK  # HEAD is still the base
    assert main(["check", "--worktree"]) == EXIT_FINDINGS
    out = capsys.readouterr().out
    assert "AW002 [block]" in out and "fix: Restore" in out
    commit_all(repo, "drop")
    assert main(["check", "--format", "json"]) == EXIT_FINDINGS
    assert json.loads(capsys.readouterr().out)["summary"]["block"] == 1


def test_cli_fail_on_warn_and_severity_config(repo, capsys):
    write(repo, "assertwatch.toml", '[severity]\nAW002 = "warn"\n')
    commit_all(repo, "config")
    git(repo, "branch", "-f", "main")
    write(repo, "tests/test_a.py", "def test_a():\n    assert a() == 1\n")
    assert main(["check", "--worktree"]) == EXIT_OK  # warn is below the default block
    assert main(["check", "--worktree", "--fail-on", "warn"]) == EXIT_FINDINGS


def test_cli_github_actions(repo, capsys, monkeypatch):
    summary, output = repo.parent / "summary.md", repo.parent / "output.txt"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    output.write_text("earlier=step\n")  # shared with the job's other steps
    write(repo, "tests/test_a.py", "def test_a():\n    assert a() == 1\n")
    commit_all(repo, "drop")
    assert main(["check", "--github-actions", "--fail-on", "never"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("AssertWatch: 1 finding (1 block)")  # the text report stays first
    assert "::warning file=tests/test_a.py,line=1,title=AW002 (block)::" in out
    assert summary.read_text().startswith("## AssertWatch\n\n**1 finding** (1 block)")
    assert output.read_text() == "earlier=step\nfindings=1\nblock=1\nwarn=0\ninfo=0\nfailed=false\n"
    assert main(["check", "--github-actions"]) == EXIT_FINDINGS
    assert "::error file=tests/test_a.py,line=1" in capsys.readouterr().out
    assert summary.read_text().count("## AssertWatch") == 2  # appended, never truncated


def test_cli_github_actions_without_actions_env(repo, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert main(["check", "--github-actions"]) == EXIT_OK
    assert "::" not in capsys.readouterr().out


def test_cli_expected_value_change_blocks_unless_code_changed(repo, capsys):
    write(repo, "tests/test_a.py", "def test_a():\n    assert a() == 1\n    assert b() == 3\n")
    assert main(["check", "--worktree"]) == EXIT_FINDINGS
    assert "AW005 [block]" in capsys.readouterr().out
    write(repo, "src/app.py", "def b():\n    return 3\n")  # untracked files count too
    assert main(["check", "--worktree"]) == EXIT_OK
    assert "AW005 [warn]" in capsys.readouterr().out


def test_cli_unparsable_test_file_exits_usage(repo, capsys):
    write(repo, "tests/test_a.py", "def test_a(:\n")
    assert main(["check", "--worktree"]) == EXIT_USAGE
    assert "Not checked:" in capsys.readouterr().out


def test_cli_git_errors_exit_usage(repo, capsys):
    assert main(["check", "--base", "no-such-ref"]) == EXIT_USAGE
    assert "unknown revision: no-such-ref" in capsys.readouterr().err


def test_cli_rules_lists_every_rule(capsys):
    assert main(["rules"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.count("block") == 9 and "AW011  block  Assertion no longer checks anything" in out
