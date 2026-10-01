import io
import json
from pathlib import Path

import pytest
from gitutil import git, write

from assertwatch import init
from assertwatch.cli import main
from assertwatch.config import parse_config


@pytest.fixture
def repo(tmp_path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    return tmp_path


@pytest.fixture(autouse=True)
def on_path(monkeypatch):
    """Whether the test runner's PATH has assertwatch must not change the output."""
    monkeypatch.setattr(init.shutil, "which", lambda name: f"/opt/aw/bin/{name}")


def no_gh(args, repo):
    return 1, "", "gh: not found"


def fake_gh(private=False, protection=None, rules="[]"):
    """protection: None = 404 not protected, "forbidden" = no access, else protected."""

    def gh(args, repo):
        if args[:2] == ["repo", "view"]:
            info = {
                "nameWithOwner": "me/app",
                "isPrivate": private,
                "defaultBranchRef": {"name": "main"},
            }
            return 0, json.dumps(info), ""
        if args[1].endswith("/protection"):
            if protection is None:
                return 1, "", "gh: Branch not protected (HTTP 404)"
            if protection == "forbidden":
                return 1, "", "gh: Not Found (HTTP 404)"
            return 0, "{}", ""
        if "/rules/branches/" in args[1]:
            return (0, rules, "") if rules is not None else (1, "", "gh: HTTP 403")
        raise AssertionError(args)

    return gh


def run_init(repo: Path, **kwargs) -> str:
    out = io.StringIO()
    kwargs.setdefault("gh", no_gh)
    assert init.run(repo, out, **kwargs) == 0
    return out.getvalue()


def test_writes_a_valid_default_config(repo):
    out = run_init(repo)
    assert "assertwatch.toml: written" in out
    config = parse_config((repo / "assertwatch.toml").read_text())
    assert config.errors == [] and config.test_paths == () and config.allow == ()


def test_keeps_an_existing_config(repo):
    write(repo, "assertwatch.toml", 'test_paths = ["t/**"]\n')
    assert "already exists, left unchanged" in run_init(repo)
    assert (repo / "assertwatch.toml").read_text() == 'test_paths = ["t/**"]\n'


def test_prints_the_hook_snippet(repo):
    out = run_init(repo)
    assert ".claude/settings.json" in out
    snippet = json.loads(init.hook_snippet())
    for event in ("SessionStart", "Stop"):
        assert snippet["hooks"][event] == [
            {"hooks": [{"type": "command", "command": "assertwatch hook"}]}
        ]
    assert '"command": "assertwatch hook"' in out


def test_detects_an_installed_hook(repo):
    write(repo, ".claude/settings.json", init.hook_snippet())
    assert "Claude Code hook: already installed." in run_init(repo)


def test_detects_a_hook_installed_by_absolute_path(repo):
    snippet = init.hook_snippet().replace('"assertwatch hook"', '"/opt/aw/bin/assertwatch hook"')
    write(repo, ".claude/settings.json", snippet)
    assert "Claude Code hook: already installed." in run_init(repo)


def test_partly_installed_hook_prints_the_snippet(repo):
    hook = {"type": "command", "command": "assertwatch hook"}
    stop_only = {"hooks": {"Stop": [{"hooks": [hook]}]}}
    write(repo, ".claude/settings.local.json", json.dumps(stop_only))
    assert "add this to .claude/settings.json" in run_init(repo)


@pytest.mark.parametrize(
    "text", [None, "", "{bad", "[]", '{"hooks": []}', '{"hooks": {"Stop": 1}}']
)
def test_installed_events_tolerates_odd_settings(text):
    assert init.installed_events(text) == set()


def test_warns_when_assertwatch_is_not_on_path(repo, monkeypatch):
    monkeypatch.setattr(init.shutil, "which", lambda name: None)
    assert "isn't on PATH" in run_init(repo)
    monkeypatch.setattr(init.shutil, "which", lambda name: "/bin/assertwatch")
    assert "isn't on PATH" not in run_init(repo)


def test_codeowners_lines_for_a_new_file(repo):
    out = run_init(repo, owner="@acme/qa")
    assert "add these lines to .github/CODEOWNERS" in out
    for path in ("/assertwatch.toml", "/.claude/", "/.github/workflows/"):
        assert any(line.split() == [path, "@acme/qa"] for line in out.splitlines())
    assert "Replace" not in out


def test_codeowners_placeholder_owner_is_flagged(repo):
    assert f"Replace {init.DEFAULT_OWNER}" in run_init(repo)


def test_codeowners_only_missing_lines(repo):
    write(repo, "CODEOWNERS", "* @all\nassertwatch.toml @qa\n.claude/** @qa  # agent config\n")
    out = run_init(repo)
    assert "add these lines to CODEOWNERS" in out
    assert "/.github/workflows/" in out
    assert "/assertwatch.toml " not in out and "/.claude/ " not in out


def test_codeowners_already_covered(repo):
    lines = "/assertwatch.toml @qa\n/.claude/ @qa\n/.github/workflows/* @qa\n"
    write(repo, ".github/CODEOWNERS", lines)
    assert "CODEOWNERS: .github/CODEOWNERS already covers the guard files." in run_init(repo)


def test_codeowners_pattern_without_owner_does_not_count(repo):
    write(repo, "CODEOWNERS", "/assertwatch.toml\n/.claude/ @qa\n/.github/workflows/ @qa\n")
    out = run_init(repo)
    assert "    /assertwatch.toml" in out


def test_branch_protection_unchecked_without_gh(repo):
    assert "Branch protection: not checked" in run_init(repo)


def test_unprotected_private_repo_warns(repo):
    out = run_init(repo, gh=fake_gh(private=True))
    assert "warning: main on me/app has no branch protection" in out
    assert "Private repos need a paid GitHub plan" in out


def test_unprotected_public_repo_warns_without_the_plan_note(repo):
    out = run_init(repo, gh=fake_gh(private=False))
    assert "warning: main on me/app has no branch protection" in out
    assert "paid GitHub plan" not in out


def test_classic_protection(repo):
    out = run_init(repo, gh=fake_gh(protection="on"))
    assert "Branch protection: main is protected" in out and "warning" not in out


def test_ruleset_counts_as_protection(repo):
    rules = json.dumps([{"type": "pull_request", "parameters": {}}])
    out = run_init(repo, gh=fake_gh(rules=rules))
    assert "Branch protection: main is protected" in out


@pytest.mark.parametrize(
    "gh", [fake_gh(protection="forbidden"), fake_gh(rules=None), fake_gh(rules="not json")]
)
def test_unknown_protection_does_not_warn(repo, gh):
    out = run_init(repo, gh=gh)
    assert "couldn't read the settings for main on me/app" in out and "warning:" not in out


def test_cli_init(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    monkeypatch.setattr(init, "_run_gh", no_gh)
    assert main(["init", "--owner", "@me"]) == 0
    assert "    /.claude/" in capsys.readouterr().out
    assert (repo / "assertwatch.toml").is_file()


def test_cli_init_outside_a_repo(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 2
    assert "assertwatch:" in capsys.readouterr().err
