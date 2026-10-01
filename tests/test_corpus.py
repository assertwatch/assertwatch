"""Every corpus case gets the verdict its label asks for, unless it is marked as a known miss.

A known miss that starts passing fails too, so the mark (and the README numbers) get updated.
"""

import functools
import importlib.util
import sys
from pathlib import Path

import pytest

from assertwatch.model import Finding, Severity

_spec = importlib.util.spec_from_file_location(
    "corpus_run", Path(__file__).parent.parent / "corpus" / "run.py"
)
corpus = importlib.util.module_from_spec(_spec)
sys.modules["corpus_run"] = corpus  # dataclasses look their module up while being defined
_spec.loader.exec_module(corpus)

CASES = corpus.load_all()


@functools.cache
def outcome(name):
    return corpus.evaluate(next(c for c in CASES if c.name == name))


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_case(case):
    result = outcome(case.name)
    if case.known:
        assert not result.correct, f"{case.name} passes now: remove its `known` mark"
    else:
        assert result.correct, f"{case.name}: got {result.rules or 'no findings'}"


def test_corpus_size_and_balance():
    labels = [c.label for c in CASES]
    assert labels.count("cheat") >= 15 and labels.count("refactor") >= 15


def test_case_files_are_validated(tmp_path):
    bad = tmp_path / "x.toml"
    bad.write_text('label = "cheat"\ntitle = "t"\n[before]\n[after]\n')
    with pytest.raises(ValueError, match="needs the rules it expects"):
        corpus.load(bad)
    bad.write_text('label = "other"\ntitle = "t"\n[before]\n[after]\n')
    with pytest.raises(ValueError, match="label must be one of"):
        corpus.load(bad)


def _outcome(label, expect, *findings):
    case = corpus.Case("c", label, "t", {}, {}, expect=list(expect))
    return corpus.Outcome(case, list(findings))


def test_outcome_verdicts():
    block = Finding("AW001", Severity.BLOCK, "t.py", 1, "m")
    warn = Finding("AW005", Severity.WARN, "t.py", 1, "m")
    info = Finding("AW009", Severity.INFO, "t.py", 1, "m")
    assert _outcome("cheat", ["AW001"], block).correct
    assert not _outcome("cheat", ["AW002"], block).correct  # caught, but by the wrong rule
    assert _outcome("cheat", ["AW005"], warn).correct
    assert not _outcome("cheat", ["AW009"], info).correct  # info doesn't count as caught
    assert _outcome("refactor", [], warn).correct and not _outcome("refactor", [], block).correct
    outcomes = [
        _outcome("cheat", ["AW001"], block),
        _outcome("cheat", ["AW005"], warn),
        _outcome("cheat", ["AW002"]),
        _outcome("refactor", [], warn),
        _outcome("refactor", [], block),
        _outcome("refactor", []),
    ]
    assert corpus.summary(outcomes) == {
        "cheats": 3,
        "caught": 2,
        "blocked": 1,
        "right_rule": 2,
        "refactors": 3,
        "false_positives": 1,
        "warned": 1,
    }


def test_readme_states_the_current_numbers():
    s = corpus.summary([outcome(case.name) for case in CASES])
    readme = (Path(__file__).parent.parent / "README.md").read_text()
    stated = (
        f"{s['caught']} of {s['cheats']} cheats caught, "
        f"{s['false_positives']} of {s['refactors']} refactors blocked"
    )
    assert stated in readme, f"update the README's corpus numbers: {stated}"
