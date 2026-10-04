import io
import json
from pathlib import Path

import pytest
from gitutil import commit_all, git, write

from assertwatch import hook
from assertwatch.attribution import Attributor, Commit, _paths_by_commit
from assertwatch.check import check_diff
from assertwatch.config import DEFAULT_ROUTING, STRICT_ROUTING, parse_config
from assertwatch.model import Severity
from assertwatch.report import Report, render

TESTS = """\
def test_total():
    assert total() == 10


def test_tax():
    assert tax() == 2
"""
WEAKENED = TESTS.replace("== 10", "is not None")
HUMAN = ("Dev", "dev@example.com")
TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"


@pytest.fixture
def repo(tmp_path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    write(tmp_path, "tests/test_cart.py", TESTS)
    write(tmp_path, "src/cart.py", "def total():\n    return 10\n")
    commit_all(tmp_path, "base")
    return tmp_path


def commit_as(repo: Path, who: tuple[str, str], message: str = "change") -> str:
    git(repo, "add", "-A")
    git(repo, "-c", f"user.name={who[0]}", "-c", f"user.email={who[1]}", "commit", "-q",
        "--author", f"{who[0]} <{who[1]}>", "-m", message)  # fmt: skip
    return git(repo, "rev-parse", "HEAD")


def only(result):
    assert len(result.findings) == 1, result.findings
    return result.findings[0]


@pytest.fixture(autouse=True)
def human_committer(monkeypatch):
    # conftest commits as an agent account; these tests pick the identity per commit
    for var in ("AUTHOR", "COMMITTER"):
        monkeypatch.delenv(f"GIT_{var}_NAME")
        monkeypatch.delenv(f"GIT_{var}_EMAIL")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Base")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "base@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Base")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "base@example.com")


def test_human_commit_routes_block_to_warn(repo):
    write(repo, "tests/test_cart.py", WEAKENED)
    sha = commit_as(repo, HUMAN)
    finding = only(check_diff(repo, f"{sha}~1", sha))
    assert (finding.rule, finding.severity, finding.routed_from) == (
        "AW004",
        Severity.WARN,
        Severity.BLOCK,
    )
    assert finding.author == "human"
    assert finding.author_source == f"commit {sha[:12]} by Dev <dev@example.com>, no AI signal"


@pytest.mark.parametrize(
    "who, message, evidence",
    [
        (HUMAN, f"change\n\n{TRAILER}", TRAILER),
        (HUMAN, "change\n\nco-authored-by: GitHub Copilot <copilot@github.com>", "copilot"),
        (
            HUMAN,
            "change\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)",
            "Claude",
        ),
        (("Copilot", "198982749+Copilot@users.noreply.github.com"), "change", "Copilot@"),
        (("Copilot", "dev@example.com"), "change", None),  # a name alone is no signal
        (("copilot-swe-agent[bot]", "1+copilot-swe-agent[bot]@x.invalid"), "change", "[bot]"),
        (("Cursor Agent", "cursoragent@cursor.com"), "change", "cursoragent"),
        (("Copilot SWE", "1+copilot-swe-agent[bot]@x.invalid"), "change", "[bot]@"),
        (("Dev (aider)", "dev@example.com"), "change", "aider"),
    ],
)
def test_ai_signals_keep_the_block(repo, who, message, evidence):
    write(repo, "tests/test_cart.py", WEAKENED)
    sha = commit_as(repo, who, message)
    finding = only(check_diff(repo, f"{sha}~1", sha))
    if evidence is None:
        assert finding.author == "human"
        return
    assert (finding.severity, finding.author, finding.routed_from) == (Severity.BLOCK, "ai", None)
    assert finding.author_source.startswith(f"commit {sha[:12]}: ")
    assert evidence.lower() in finding.author_source.lower()


def test_ai_committer_counts(repo, monkeypatch):
    # A human author whose commit an agent account made (and committed) is the agent's.
    write(repo, "tests/test_cart.py", WEAKENED)
    monkeypatch.setenv("GIT_COMMITTER_NAME", "agent[bot]")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "agent[bot]@x.invalid")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--author", "Dev <dev@example.com>", "-m", "change")
    sha = git(repo, "rev-parse", "HEAD")
    finding = only(check_diff(repo, f"{sha}~1", sha))
    assert (finding.author, finding.severity) == ("ai", Severity.BLOCK)
    assert finding.author_source.endswith("agent[bot] <agent[bot]@x.invalid>")


def test_warnings_are_not_marked_routed(repo):
    # AW005 with a production change is a warning already: routing leaves it as it is
    write(repo, "tests/test_cart.py", TESTS.replace("== 10", "== 11"))
    write(repo, "src/cart.py", "def total():\n    return 11\n")
    sha = commit_as(repo, HUMAN)
    finding = only(check_diff(repo, f"{sha}~1", sha))
    assert (finding.rule, finding.severity, finding.author) == ("AW005", Severity.WARN, "human")
    assert finding.routed_from is None


def test_ai_and_human_commits_on_one_file_are_mixed_and_block(repo):
    write(repo, "tests/test_cart.py", TESTS + "\n\ndef test_more():\n    assert more()\n")
    commit_as(repo, HUMAN)
    write(repo, "tests/test_cart.py", WEAKENED)
    sha = commit_as(repo, HUMAN, f"weaken\n\n{TRAILER}")
    finding = only(check_diff(repo, f"{sha}~2", sha))
    assert (finding.author, finding.severity) == ("mixed", Severity.BLOCK)
    assert TRAILER in finding.author_source and "by Dev" in finding.author_source


def test_attribution_is_per_file(repo):
    write(repo, "tests/test_cart.py", WEAKENED)
    commit_as(repo, HUMAN, f"weaken\n\n{TRAILER}")
    write(repo, "tests/test_other.py", "def test_x():\n    assert x() == 1\n")
    sha = commit_as(repo, HUMAN)
    finding = only(check_diff(repo, f"{sha}~2", sha))
    assert (finding.path, finding.author) == ("tests/test_cart.py", "ai")


def test_routing_comes_from_the_base_config(repo):
    write(repo, "assertwatch.toml", '[routing]\nhuman = "block"\n')
    base = commit_as(repo, HUMAN)
    write(repo, "assertwatch.toml", '[routing]\nhuman = "warn"\n')
    write(repo, "tests/test_cart.py", WEAKENED)
    sha = commit_as(repo, HUMAN)
    (finding,) = [f for f in check_diff(repo, base, sha).findings if f.rule == "AW004"]
    assert (finding.author, finding.severity, finding.routed_from) == ("human", "block", None)


def test_head_cannot_add_routing_or_patterns(repo):
    # The head turns AI changes into warnings; only the base config counts.
    write(repo, "tests/test_cart.py", WEAKENED)
    write(repo, "assertwatch.toml", '[routing]\nai = "warn"\n')
    sha = commit_as(repo, HUMAN, f"weaken\n\n{TRAILER}")
    findings = check_diff(repo, f"{sha}~1", sha).findings
    assert [(f.rule, f.severity) for f in findings if f.rule == "AW004"] == [("AW004", "block")]


def test_ai_patterns_from_config(repo):
    write(repo, "assertwatch.toml", '[routing]\nai_patterns = ["^Dev <dev@example[.]com>$"]\n')
    base = commit_as(repo, HUMAN)
    write(repo, "tests/test_cart.py", WEAKENED)
    sha = commit_as(repo, HUMAN)
    finding = only(check_diff(repo, base, sha))
    assert (finding.author, finding.severity) == ("ai", Severity.BLOCK)
    assert finding.author_source.endswith("Dev <dev@example.com>")


def test_routing_config_errors():
    config = parse_config('[routing]\nai = "warn"\nai_patterns = ["(", "ok"]\n')
    assert config.routing == {**DEFAULT_ROUTING, "ai": "warn"}  # the base's own choice
    assert config.ai_patterns == ("ok",)
    assert len(config.errors) == 1
    assert parse_config('[routing]\nai_patterns = "x"\n').errors == [
        "assertwatch.toml: [routing] ai_patterns must be a list of strings"
    ]
    assert parse_config(None).routing == DEFAULT_ROUTING
    assert parse_config("").routing == DEFAULT_ROUTING


@pytest.mark.parametrize(
    "text",
    [
        '[routing]\nhuman = "off"\n',  # not block or warn
        '[routing]\nhuman = "Warn"\n',
        "[routing]\nhuman = 1\n",
        '[routing]\nhumans = "block"\n',  # misspelled: it meant to block humans
        '[routing]\nai = "warn"\nrobot = "warn"\n',
        "routing = 1\n",
        'routing = "human=block"\n',
        '[routing\nhuman = "block"\n',  # the whole file is unreadable
    ],
)
def test_invalid_routing_fails_closed(text):
    config = parse_config(text)
    assert config.routing == STRICT_ROUTING
    assert config.errors


def test_empty_ai_patterns_keep_the_defaults(repo):
    write(repo, "assertwatch.toml", "[routing]\nai_patterns = []\n")
    base = commit_as(repo, HUMAN)
    write(repo, "tests/test_cart.py", WEAKENED)
    sha = commit_as(repo, HUMAN, f"weaken\n\n  {TRAILER.lower()}")  # indented, lower case
    finding = only(check_diff(repo, base, sha))
    assert (finding.author, finding.severity) == ("ai", Severity.BLOCK)


def test_a_file_named_like_a_log_header_cannot_move_paths_to_another_commit(repo):
    # The agent's commit adds a file whose name looks like git log's commit header, naming
    # the human commit: under --name-only, every path after it went to that commit, so the
    # test file looked touched by the human only and was routed to a warning.
    write(repo, "tests/test_cart.py", TESTS + "\n\ndef test_more():\n    assert more()\n")
    human = commit_as(repo, HUMAN)
    write(repo, "tests/test_cart.py", WEAKENED + "\n\ndef test_more():\n    assert more()\n")
    write(repo, f"\x01commit {human}", "x\n")
    sha = commit_as(repo, HUMAN, f"weaken\n\n{TRAILER}")
    (finding,) = [f for f in check_diff(repo, f"{human}~1", sha).findings if f.rule == "AW004"]
    assert (finding.author, finding.severity) == ("mixed", Severity.BLOCK)


def test_an_agent_merge_that_edits_the_test_counts(repo):
    # An evil merge: the weakening is made while resolving a merge the agent commits.
    git(repo, "checkout", "-q", "-b", "side")
    write(repo, "src/other.py", "x = 1\n")
    commit_as(repo, HUMAN)
    git(repo, "checkout", "-q", "main")
    write(repo, "tests/test_cart.py", TESTS + "\n\ndef test_more():\n    assert more()\n")
    commit_as(repo, HUMAN)
    git(repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    write(repo, "tests/test_cart.py", WEAKENED + "\n\ndef test_more():\n    assert more()\n")
    sha = commit_as(repo, HUMAN, f"Merge side\n\n{TRAILER}")
    (finding,) = [f for f in check_diff(repo, f"{sha}~1~1", sha).findings if f.rule == "AW004"]
    assert (finding.author, finding.severity) == ("mixed", Severity.BLOCK)


def test_uncommitted_changes_are_unknown_and_block(repo):
    write(repo, "tests/test_cart.py", WEAKENED)
    finding = only(check_diff(repo, "main"))
    assert (finding.author, finding.author_source) == ("unknown", "uncommitted changes")
    assert finding.severity == Severity.BLOCK


def test_unknown_beats_human(repo):
    write(repo, "tests/test_cart.py", TESTS + "\n\ndef test_more():\n    assert more()\n")
    base = git(repo, "rev-parse", "HEAD")
    commit_as(repo, HUMAN)
    write(repo, "tests/test_cart.py", WEAKENED + "\n\ndef test_more():\n    assert more()\n")
    finding = only(check_diff(repo, base))
    assert (finding.author, finding.severity) == ("unknown", Severity.BLOCK)


def test_route_false_keeps_severity_but_attributes(repo):
    write(repo, "tests/test_cart.py", WEAKENED)
    sha = commit_as(repo, HUMAN)
    finding = only(check_diff(repo, f"{sha}~1", sha, route=False))
    assert (finding.author, finding.severity, finding.routed_from) == ("human", "block", None)


def test_proposed_waivers_are_never_routed(repo):
    allow = "is not None  # assertwatch: allow AW004 reason"
    write(repo, "tests/test_cart.py", WEAKENED.replace("is not None", allow))
    sha = commit_as(repo, HUMAN)
    findings = check_diff(repo, f"{sha}~1", sha).findings
    waiver = [f for f in findings if f.rule == "AW000"]
    assert waiver and all(f.severity == Severity.BLOCK and f.author == "human" for f in waiver)


# --- the hook ------------------------------------------------------------------------------


def run_hook(repo: Path, event: str = "Stop") -> tuple[int, str, str]:
    payload = {"session_id": "s1", "cwd": str(repo), "hook_event_name": event}
    out, err = io.StringIO(), io.StringIO()
    return hook.run(json.dumps(payload), out, err), out.getvalue(), err.getvalue()


def test_hook_session_is_ai_whatever_the_commit_identity(repo):
    run_hook(repo, "SessionStart")
    start = git(repo, "rev-parse", "HEAD")
    write(repo, "tests/test_cart.py", WEAKENED)
    commit_as(repo, HUMAN)  # the agent commits under the user's identity, no trailer
    code, _, err = run_hook(repo)
    assert code == hook.EXIT_BLOCK
    assert f"author: ai (Claude Code session since {start[:12]})" in err
    finding = only(check_diff(repo, start, None, hook=True, session=start))
    assert (finding.author, finding.severity) == ("ai", Severity.BLOCK)


def test_hook_session_survives_rewritten_history(repo):
    # The agent rewrites history so the recorded start is no longer an ancestor of HEAD.
    write(repo, "src/cart.py", "def total():\n    return 10  # x\n")
    commit_as(repo, HUMAN)
    run_hook(repo, "SessionStart")
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    write(repo, "tests/test_cart.py", WEAKENED)
    commit_as(repo, HUMAN)
    code, _, err = run_hook(repo)
    assert code == hook.EXIT_BLOCK
    assert "author: ai (Claude Code session since" in err


def test_hook_without_a_session_trusts_no_human_commit(repo):
    # No SessionStart recorded: commits on the branch may be an earlier agent session's.
    git(repo, "checkout", "-q", "-b", "work")
    write(repo, "tests/test_cart.py", WEAKENED)
    commit_as(repo, HUMAN)
    code, _, err = run_hook(repo)
    assert code == hook.EXIT_BLOCK
    assert "author: unknown (commit" in err and "made in an agent's workspace" in err


def test_hook_without_a_session_counts_uncommitted_changes_as_ai(repo):
    write(repo, "tests/test_cart.py", WEAKENED)
    finding = only(check_diff(repo, "main", None, hook=True))
    assert finding.author_source == "uncommitted changes in a Claude Code session"
    assert finding.author == "ai"


# --- Agent Trace ---------------------------------------------------------------------------


def trace(sha: str, path: str, kind: str) -> dict:
    return {
        "version": "0.1.0",
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "timestamp": "2026-01-25T10:00:00Z",
        "vcs": {"type": "git", "revision": sha},
        "files": [
            {
                "path": path,
                "conversations": [
                    {"contributor": {"type": kind}, "ranges": [{"start_line": 1, "end_line": 5}]}
                ],
            }
        ],
    }


def test_agent_trace_marks_a_commit_ai(repo):
    write(repo, "tests/test_cart.py", WEAKENED)
    sha = commit_as(repo, HUMAN)
    lines = ["not json", json.dumps(trace(sha, "tests/test_cart.py", "ai")), "[1, 2]"]
    write(repo, ".agent-trace/traces.jsonl", "\n".join(lines) + "\n")
    write(repo, ".agent-trace/broken.json", "{")
    head = commit_as(repo, HUMAN)
    finding = only(check_diff(repo, f"{sha}~1", head))
    assert (finding.author, finding.severity) == ("ai", Severity.BLOCK)
    assert finding.author_source == f"commit {sha[:12]}: Agent Trace record: ai"
    # the working tree is read too
    finding = only(check_diff(repo, f"{sha}~1", None))
    assert finding.author == "ai"


def test_agent_trace_claiming_human_does_not_outweigh_a_trailer(repo):
    write(repo, "tests/test_cart.py", WEAKENED)
    sha = commit_as(repo, HUMAN, f"weaken\n\n{TRAILER}")
    write(repo, ".agent-trace/t.json", json.dumps([trace(sha, "tests/test_cart.py", "human")]))
    head = commit_as(repo, HUMAN)
    assert only(check_diff(repo, f"{sha}~1", head)).author == "ai"


def test_agent_trace_mixed_and_other_files(repo):
    write(repo, "tests/test_cart.py", WEAKENED)
    sha = commit_as(repo, HUMAN)
    records = [trace(sha, "tests/test_cart.py", "mixed"), trace(sha, "src/cart.py", "ai")]
    write(repo, ".agent-trace/t.json", json.dumps(records))
    head = commit_as(repo, HUMAN)
    assert only(check_diff(repo, f"{sha}~1", head)).author_source.endswith("record: mixed")


def test_agent_trace_human_record_does_not_override_an_ai_record(repo):
    write(repo, "tests/test_cart.py", WEAKENED)
    sha = commit_as(repo, HUMAN)
    records = [trace(sha, "tests/test_cart.py", "ai"), trace(sha, "tests/test_cart.py", "human")]
    write(repo, ".agent-trace/t.json", json.dumps(records))
    head = commit_as(repo, HUMAN)
    assert only(check_diff(repo, f"{sha}~1", head)).author == "ai"


def test_agent_trace_for_another_revision_is_ignored(repo):
    write(repo, "tests/test_cart.py", WEAKENED)
    sha = commit_as(repo, HUMAN)
    write(repo, ".agent-trace/t.json", json.dumps(trace("0" * 40, "tests/test_cart.py", "ai")))
    head = commit_as(repo, HUMAN)
    assert only(check_diff(repo, f"{sha}~1", head)).author == "human"


# --- units ---------------------------------------------------------------------------------


def test_paths_by_commit_parses_odd_names():
    a, b, c = "a" * 40, "b" * 40, "c" * 64
    text = (
        f"\x01commit {a}\0\n1\t0\tsp ace.py\0-\t-\tnew\nline.py\0" + "2\t2\t lead.py \0"
        f"3\t0\t\x01commit {b}\0"  # a file named like a header stays a path of a
        f"\x01commit {b}\0\x01commit {c}\0\n1\t1\tx.py\0"
        "\x01commit nothex\0"  # not a commit: ignored, paths stay with c
        "1\t1\ty.py\0"
    )
    assert _paths_by_commit(text) == {
        a: frozenset({"sp ace.py", "new\nline.py", " lead.py ", f"\x01commit {b}"}),
        b: frozenset(),
        c: frozenset({"x.py", "y.py"}),
    }


def test_attributor_classes():
    ai = Commit("a" * 40, "Bot <b@x>", frozenset({"t.py"}), "Co-Authored-By: Claude")
    human = Commit("b" * 40, "Dev <d@x>", frozenset({"t.py", "u.py"}))
    who = Attributor([ai, human])
    assert who.of("t.py").author == "mixed"
    assert who.of("u.py").author == "human"
    assert who.of("v.py").author == "unknown"
    assert who.of("v.py").source == "no commit in the range changed it"
    assert Attributor([human, human]).of("u.py").source.endswith("and 1 more, no AI signal")
    assert Attributor([ai], frozenset({"t.py"})).of("t.py").author == "ai"
    assert Attributor([human], frozenset({"t.py"})).of("t.py").author == "unknown"
    assert Attributor([human], hook=True).of("u.py").author == "unknown"
    assert Attributor([human], session="c" * 40).of("u.py").author == "ai"


def test_text_report_shows_author_and_routing(repo):
    write(repo, "tests/test_cart.py", WEAKENED)
    sha = commit_as(repo, HUMAN)
    result = check_diff(repo, f"{sha}~1", sha)
    text = render(Report(f"{sha}~1", sha, result.findings), "text")
    assert "AW004 [warn]" in text
    assert "    author: human (commit" in text and "block lowered to warn by [routing]" in text
