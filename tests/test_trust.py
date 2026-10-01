"""Config from the base ref, standing waivers, proposed waivers (AW000), loosened config (AW009)."""

import json
from pathlib import Path
from textwrap import dedent

import pytest
from gitutil import commit_all, git, write

from assertwatch.check import check_diff
from assertwatch.config import Allow, parse_config
from assertwatch.gitdiff import DiffLine, _parse_unified, commit_messages, diff_lines
from assertwatch.rules import FileChange, run_rules
from assertwatch.rules.aw009_config import is_guard_path


def file_rules(path, before, after, rule=None):
    found = run_rules([], [], files=[FileChange(path, path, before, after)])
    return [f for f in found if rule is None or f.rule == rule]


# --- config -----------------------------------------------------------------------------


def test_config_defaults_and_parsing():
    assert parse_config(None).test_paths == ()
    config = parse_config(
        dedent(
            """
            test_paths = ["tests/**"]
            [severity]
            AW011 = "warn"
            AW003 = "off"
            [[allow]]
            rule = "AW001"
            path = "tests/legacy/**"
            reason = "legacy suite is being retired"
            """
        )
    )
    assert config.test_paths == ("tests/**",)
    assert config.severity == {"AW011": "warn", "AW003": "off"}
    assert config.allow == (Allow("AW001", "tests/legacy/**", "legacy suite is being retired"),)
    assert config.errors == []
    assert config.allow[0].covers("AW001", "tests/legacy/sub/test_a.py")
    assert not config.allow[0].covers("AW002", "tests/legacy/test_a.py")


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("test_paths = [", "using defaults"),
        ('[severity]\nAW001 = "loud"', "is not one of"),
        ('[[allow]]\nrule = "AW001"\npath = "x"', "a waiver needs a reason"),
        ('[[allow]]\nrule = "AW001"\npath = "x"\nreason = "  "', "a waiver needs a reason"),
        ('[[allow]]\nrule = "AW000"\npath = "x"\nreason = "y"', "always need a human"),
        ('[[allow]]\nrule = "AW001"\nreason = "y"', "missing path"),
        ('test_paths = "tests"', "list of strings"),
    ],
)
def test_config_errors_are_reported_and_ignored(text, error):
    config = parse_config(text)
    assert config.allow == ()
    assert any(error in e for e in config.errors)


REMOVED = ("def test_a():\n    assert a() == 1\n\ndef test_b():\n    assert b() == 2\n", "")


def test_severity_override_off_and_waivers():
    from assertwatch.extract.python import extract

    base = [extract(REMOVED[0], "tests/legacy/test_x.py")]
    head = [extract("", "tests/legacy/test_x.py")]
    plain = run_rules(base, head)
    assert [(f.rule, f.severity, f.waiver) for f in plain] == [("AW001", "block", None)] * 2

    warn = parse_config('[severity]\nAW001 = "warn"')
    assert {f.severity for f in run_rules(base, head, config=warn)} == {"warn"}
    off = parse_config('[severity]\nAW001 = "off"')
    assert run_rules(base, head, config=off) == []

    waived = parse_config(
        '[[allow]]\nrule = "AW001"\npath = "tests/legacy/**"\nreason = "retiring"'
    )
    found = run_rules(base, head, config=waived)
    assert [f.waiver for f in found] == ["retiring", "retiring"]  # still reported


# --- AW000 ------------------------------------------------------------------------------


def lines(*items):
    return [DiffLine(path, n, text, added) for path, n, text, added in items]


def test_aw000_inline_waiver_added():
    found = run_rules(
        [],
        [],
        lines=lines(("tests/test_a.py", 7, '    # assertwatch: allow AW003 reason="flaky"', True)),
    )
    assert [(f.rule, f.path, f.line) for f in found] == [("AW000", "tests/test_a.py", 7)]
    assert 'reason given: "flaky"' in found[0].message
    no_reason = run_rules(
        [], [], lines=lines(("t.py", 1, "x = 1  # AssertWatch: allow AW001", True))
    )
    assert "no reason given" in no_reason[0].message


def test_aw000_waiver_syntax_in_docs_is_not_a_waiver():
    prose = "Add `assertwatch: allow AW003` only through review."
    assert run_rules([], [], lines=lines(("README.md", 4, prose, True))) == []
    java = '    @Disabled // assertwatch: allow AW003 reason="flaky on CI"'
    assert [f.rule for f in run_rules([], [], lines=lines(("T.java", 9, java, True)))] == ["AW000"]


def test_aw000_moved_or_existing_inline_waiver_is_not_new():
    comment = '# assertwatch: allow AW003 reason="flaky"'
    moved = lines(("t.py", 3, comment, False), ("t.py", 9, comment, True))
    assert run_rules([], [], lines=moved) == []
    removed = lines(("t.py", 3, comment, False))
    assert run_rules([], [], lines=removed) == []


def test_aw000_config_allow_added():
    before = '[[allow]]\nrule = "AW010"\npath = "golden/**"\nreason = "regenerated"\n'
    after = before + '\n[[allow]]\nrule = "AW001"\npath = "tests/**"\nreason = "cleanup"\n'
    found = file_rules("assertwatch.toml", before, after, "AW000")
    assert [(f.path, f.line) for f in found] == [("assertwatch.toml", 9)]
    assert "[[allow]] AW001 for tests/**" in found[0].message
    assert file_rules("assertwatch.toml", before, before, "AW000") == []


def test_aw000_commit_trailer():
    message = 'Fix refund\n\nAssertWatch-Allow: AW002 reason="test was wrong"\n'
    found = run_rules([], [], messages=[message, "Unrelated commit"])
    assert [(f.rule, f.path) for f in found] == [("AW000", "(commit message)")]
    assert "trailer for AW002" in found[0].message
    assert 'reason given: "test was wrong"' in found[0].message


# --- AW009 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("assertwatch.toml", True),
        (".claude/settings.json", True),
        (".github/workflows/ci.yml", True),
        ("pyproject.toml", True),
        ("sub/pkg/setup.cfg", True),
        ("tests/conftest.py", True),
        ("src/app.py", False),
        ("docs/ci.yml", False),
    ],
)
def test_guard_paths(path, expected):
    assert is_guard_path(path) is expected


def test_aw009_assertwatch_config_any_change():
    assert file_rules("assertwatch.toml", "a = 1\n", "a = 2\n", "AW009")
    assert "added" in file_rules("assertwatch.toml", None, "a = 1\n", "AW009")[0].message
    assert "deleted" in file_rules("assertwatch.toml", "a = 1\n", None, "AW009")[0].message
    assert file_rules("assertwatch.toml", "a = 1\n", "a = 1\n") == []


HOOKED = {
    "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "assertwatch check --worktree"}]}]}
}


@pytest.mark.parametrize(
    "after",
    [
        {"hooks": {}},
        {},
        {**HOOKED, "disableAllHooks": True},
        None,  # file deleted
        "{not json",
    ],
    ids=["hook-removed", "hooks-key-removed", "all-disabled", "deleted", "broken-json"],
)
def test_aw009_claude_hook_removed(after):
    text = after if isinstance(after, str) or after is None else json.dumps(after)
    found = file_rules(".claude/settings.json", json.dumps(HOOKED), text, "AW009")
    assert len(found) == 1


def test_aw009_claude_settings_other_edits_are_fine():
    after = {**HOOKED, "permissions": {"allow": ["Bash(ls)"]}}
    assert file_rules(".claude/settings.json", json.dumps(HOOKED), json.dumps(after)) == []
    assert file_rules(".claude/settings.json", None, json.dumps(after)) == []


WORKFLOW = """\
---
name: ci
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pytest -q
      - uses: assertwatch/assertwatch@v0
"""


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("      - uses: assertwatch/assertwatch@v0\n", ""), "AssertWatch step was removed"),
        (
            ("      - uses: assertwatch/assertwatch@v0\n", "      # - uses: assertwatch@v0\n"),
            "AssertWatch step was removed",
        ),
        (
            (
                "    runs-on: ubuntu-latest\n",
                "    runs-on: ubuntu-latest\n    continue-on-error: true\n",
            ),
            "continue-on-error",
        ),
        (("pytest -q", "pytest -q || true"), "ignores failures"),
    ],
)
def test_aw009_workflow_loosened(change, message):
    found = file_rules(".github/workflows/ci.yml", WORKFLOW, WORKFLOW.replace(*change), "AW009")
    assert [message in f.message for f in found] == [True]


def test_aw009_workflow_unrelated_edits_are_fine():
    after = WORKFLOW.replace("ubuntu-latest", "ubuntu-24.04") + "      - run: make docs || true\n"
    assert file_rules(".github/workflows/ci.yml", WORKFLOW, after) == []
    docs = "name: docs\njobs:\n  build:\n    steps:\n      - run: mkdocs build\n"
    lenient = docs.replace("  build:\n", "  build:\n    continue-on-error: true\n")
    assert file_rules(".github/workflows/docs.yml", docs, lenient) == []


PYPROJECT = """\
[tool.pytest.ini_options]
addopts = "-ra --strict-markers"
testpaths = ["tests", "integration"]

[tool.coverage.report]
fail_under = 90
"""


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            ('"-ra --strict-markers"', '"-ra --deselect tests/test_a.py::test_x"'),
            "`--deselect tests/test_a.py::test_x`",
        ),
        (('"-ra --strict-markers"', "\"-ra -k 'not slow_refund'\""), "`-k not slow_refund`"),
        (('"-ra --strict-markers"', '"-ra -kfoo"'), "`-k foo`"),
        (('"-ra --strict-markers"', "\"-ra -m 'not integration'\""), "`-m not integration`"),
        (('"-ra --strict-markers"', '"-ra --lf"'), "`--lf`"),
        (('"-ra --strict-markers"', '["-ra", "--ignore=tests/slow"]'), "`--ignore tests/slow`"),
        (('["tests", "integration"]', '["tests"]'), "no longer include integration"),
        (
            ('["tests", "integration"]', '["tests", "integration/fast"]'),
            "no longer include integration",
        ),
        (("fail_under = 90", "fail_under = 60"), "lowered to 60 (was 90)"),
        (("fail_under = 90\n", ""), "fail_under was removed"),
    ],
)
def test_aw009_pyproject_loosened(change, message):
    found = file_rules("pyproject.toml", PYPROJECT, PYPROJECT.replace(*change), "AW009")
    assert [message in f.message for f in found] == [True], [f.message for f in found]


@pytest.mark.parametrize(
    "change",
    [
        ('"-ra --strict-markers"', '"-ra --strict-markers -x -m slow"'),  # selecting, not excluding
        ("fail_under = 90", "fail_under = 95"),
        ('["tests", "integration"]', '["tests", "integration", "e2e"]'),
        ('["tests", "integration"]', '["tests", "."]'),  # moved under a broader path
        ("[tool.coverage.report]", "[tool.ruff]\nline-length = 100\n\n[tool.coverage.report]"),
    ],
)
def test_aw009_pyproject_tightened_or_unrelated(change):
    assert file_rules("pyproject.toml", PYPROJECT, PYPROJECT.replace(*change)) == []


@pytest.mark.parametrize(
    ("path", "before", "after"),
    [
        ("pytest.ini", "[pytest]\naddopts = -q\n", "[pytest]\naddopts = -q --deselect t.py::x\n"),
        ("tox.ini", "[pytest]\naddopts = -q\n", "[pytest]\naddopts = -q --last-failed\n"),
        ("setup.cfg", "[tool:pytest]\naddopts = -q\n", "[tool:pytest]\naddopts = -q -k 'not a'\n"),
        (".coveragerc", "[report]\nfail_under = 80\n", "[report]\nfail_under = 50\n"),
        ("setup.cfg", "[coverage:report]\nfail_under = 80\n", "[coverage:report]\n"),
        (
            "pytest.ini",
            "[pytest]\naddopts = --cov-fail-under=90\n",
            "[pytest]\naddopts = --cov-fail-under 70\n",
        ),
        ("pytest.ini", "[pytest]\naddopts = --cov-fail-under=90\n", None),
    ],
)
def test_aw009_ini_configs_loosened(path, before, after):
    assert len(file_rules(path, before, after, "AW009")) == 1


def test_aw009_testpaths_file_moved_into_directory():
    before, after = "[tool:pytest]\ntestpaths = tests.py\n", "[tool:pytest]\ntestpaths = tests\n"
    assert file_rules("setup.cfg", before, after) == []


def test_aw009_cov_fail_under_with_separate_value():
    before, after = (
        "[pytest]\naddopts = --cov-fail-under=90\n",
        "[pytest]\naddopts = --cov-fail-under 70 -q\n",
    )
    assert "lowered to 70 (was 90)" in file_rules("pytest.ini", before, after)[0].message


def test_aw009_conftest():
    before = 'import pytest\n\ncollect_ignore = ["setup.py"]\n'
    ignore = before + 'collect_ignore.append("tests/test_refund.py")\n'
    found = file_rules("tests/conftest.py", before, ignore, "AW009")
    assert len(found) == 1 and "tests/test_refund.py" in found[0].message
    glob = before + 'collect_ignore_glob = ["tests/test_pay*.py"]\n'
    assert len(file_rules("tests/conftest.py", before, glob, "AW009")) == 1
    skip_hook = before + dedent(
        """
        def pytest_collection_modifyitems(items):
            for item in items:
                if "refund" in item.name:
                    item.add_marker(pytest.mark.skip(reason="later"))
        """
    )
    assert "skip or xfail" in file_rules("tests/conftest.py", before, skip_hook)[0].message
    fixture = before + "\n@pytest.fixture\ndef client():\n    return make()\n"
    assert file_rules("tests/conftest.py", before, fixture) == []


# --- git layer ----------------------------------------------------------------------------


def test_parse_unified_keeps_yaml_document_markers_as_lines():
    patch = dedent(
        """\
        diff --git a/ci.yml b/ci.yml
        --- a/ci.yml
        +++ b/ci.yml
        @@ -1,2 +1 @@
        ----
        -name: ci
        +name: build
        diff --git a/new.txt b/new.txt
        --- /dev/null
        +++ "b/sp\\303\\251cial.txt"
        @@ -0,0 +1,2 @@
        +--- x
        +two
        """
    )
    assert _parse_unified(patch) == [
        DiffLine("ci.yml", 1, "---", False),
        DiffLine("ci.yml", 2, "name: ci", False),
        DiffLine("ci.yml", 1, "name: build", True),
        DiffLine("spécial.txt", 1, "--- x", True),
        DiffLine("spécial.txt", 2, "two", True),
    ]


@pytest.fixture
def repo(tmp_path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    write(tmp_path, "tests/test_refund.py", "def test_refund():\n    assert refund(1) == 1\n")
    write(tmp_path, ".claude/settings.json", json.dumps(HOOKED))
    write(tmp_path, "assertwatch.toml", '[severity]\nAW011 = "warn"\n')
    commit_all(tmp_path, "base")
    git(tmp_path, "tag", "base")
    return tmp_path


def test_diff_lines_and_messages(repo):
    write(repo, "tests/test_refund.py", "def test_refund():\n    assert refund(1) == 2\n")
    write(repo, "notes.txt", "untracked\n")
    found = diff_lines(repo, "base")
    assert DiffLine("tests/test_refund.py", 2, "    assert refund(1) == 2", True) in found
    assert DiffLine("tests/test_refund.py", 2, "    assert refund(1) == 1", False) in found
    assert DiffLine("notes.txt", 1, "untracked", True) in found
    commit_all(repo, 'Change refund\n\nAssertWatch-Allow: AW005 reason="spec changed"')
    assert commit_messages(repo, "base") == [
        'Change refund\n\nAssertWatch-Allow: AW005 reason="spec changed"'
    ]


def test_done_when_agent_edits_config(repo):
    write(repo, "assertwatch.toml", '[severity]\nAW011 = "warn"\nAW003 = "off"\n')
    write(
        repo,
        "tests/test_refund.py",
        "import pytest\n\n@pytest.mark.skip\ndef test_refund():\n    assert refund(1) == 1\n",
    )
    found = check_diff(repo, "base").findings
    # The head config turning AW003 off doesn't apply: config comes from base.
    assert sorted(f.rule for f in found) == ["AW003", "AW009"]


def test_done_when_agent_removes_hook(repo):
    write(repo, ".claude/settings.json", "{}")
    assert [f.rule for f in check_diff(repo, "base").findings] == ["AW009"]


def test_done_when_agent_adds_inline_waiver(repo):
    write(
        repo,
        "tests/test_refund.py",
        "import pytest\n\n"
        '@pytest.mark.skip  # assertwatch: allow AW003 reason="flaky"\n'
        "def test_refund():\n    assert refund(1) == 1\n",
    )
    found = check_diff(repo, "base").findings
    assert sorted(f.rule for f in found) == ["AW000", "AW003"]
    assert all(f.waiver is None for f in found)  # the proposed waiver suppresses nothing


def test_standing_waiver_from_base_applies(repo):
    write(
        repo,
        "assertwatch.toml",
        '[[allow]]\nrule = "AW001"\npath = "tests/**"\nreason = "suite retired"\n',
    )
    commit_all(repo, "waive")
    git(repo, "tag", "-f", "base")
    (repo / "tests/test_refund.py").unlink()
    found = check_diff(repo, "base").findings
    assert [(f.rule, f.waiver) for f in found] == [("AW001", "suite retired")]


def test_test_paths_from_base_config(repo):
    write(repo, "assertwatch.toml", 'test_paths = ["integration/**"]\n')
    commit_all(repo, "narrow")
    git(repo, "tag", "-f", "base")
    (repo / "tests/test_refund.py").unlink()
    assert check_diff(repo, "base").findings == []


def test_broken_base_config_falls_back_to_defaults(repo):
    write(repo, "assertwatch.toml", "severity = [\n")
    commit_all(repo, "break")
    git(repo, "tag", "-f", "base")
    (repo / "tests/test_refund.py").unlink()
    result = check_diff(repo, "base")
    assert [f.rule for f in result.findings] == ["AW001"]
    assert "using defaults" in result.errors[0]
