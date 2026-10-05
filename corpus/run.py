"""Evaluate AssertWatch on the labelled corpus: recall on cheats, false positives on refactors.

Each case in corpus/cases/*.toml is one change: the files before and after it, a label
("cheat" or "refactor") and, for cheats, the rules that should catch it. The harness commits
both sides to a throwaway git repository and runs the same check as `assertwatch check`.

    python corpus/run.py            # table and summary
    python corpus/run.py -v         # also every finding

A cheat counts as caught when any finding (block or warn) reports it, and blocked when a
block finding does. A refactor is a false positive when a block finding fires; warnings on
refactors are counted separately. The change is committed as an AI agent's unless the case
says `author = "human"`, so findings keep the rules' severities by default. Cases marked
`known = "..."` are current misses or false positives, kept so the numbers stay honest;
tests/test_corpus.py fails if one starts passing, so the mark gets removed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from assertwatch.check import check_diff
from assertwatch.model import Finding, Severity

CASES = Path(__file__).parent / "cases"
LABELS = ("cheat", "refactor")
# Commit identities by case author: name and email.
AUTHORS = {
    "ai": ("Claude", "noreply@anthropic.com"),
    "human": ("corpus", "corpus@example.invalid"),
}


@dataclass
class Case:
    name: str
    label: str
    title: str
    before: dict[str, str]
    after: dict[str, str]
    expect: list[str] = field(default_factory=list)  # cheats: rules that should fire
    message: str = "Change"  # commit message of the change
    source: str = ""  # the real change it is modeled on, if any
    known: str = ""  # why this case is a known miss or false positive
    author: str = "ai"  # who commits the change: "ai" (an agent account) or "human"
    blocks: bool | None = None  # cheats: whether it must block (true) or only warn (false)


@dataclass
class Outcome:
    case: Case
    findings: list[Finding]

    @property
    def rules(self) -> list[str]:
        return sorted({f.rule for f in self.findings})

    @property
    def flagged(self) -> bool:
        return any(f.severity in (Severity.BLOCK, Severity.WARN) for f in self.findings)

    @property
    def blocked(self) -> bool:
        return any(f.severity == Severity.BLOCK for f in self.findings)

    @property
    def correct(self) -> bool:
        """What the label asks for: a cheat flagged by the expected rules, a refactor unblocked."""
        if self.case.label == "cheat":
            routed = self.case.blocks is None or self.case.blocks == self.blocked
            return self.flagged and set(self.case.expect) <= set(self.rules) and routed
        return not self.blocked


def load(path: Path) -> Case:
    data = tomllib.loads(path.read_text())
    case = Case(name=path.stem, **data)
    if case.label not in LABELS:
        raise ValueError(f"{path.name}: label must be one of {LABELS}")
    if case.author not in AUTHORS:
        raise ValueError(f"{path.name}: author must be one of {tuple(AUTHORS)}")
    if case.label == "cheat" and not case.expect:
        raise ValueError(f"{path.name}: a cheat needs the rules it expects")
    return case


def load_all() -> list[Case]:
    return [load(path) for path in sorted(CASES.glob("*.toml"))]


def evaluate(case: Case) -> Outcome:
    with tempfile.TemporaryDirectory(prefix="assertwatch-corpus-") as tmp:
        repo = Path(tmp)
        _git(repo, "init", "-q", "-b", "main")
        _write_tree(repo, case.before)
        base = _commit(repo, "Base")
        for path in case.before.keys() - case.after.keys():
            (repo / path).unlink()
        _write_tree(repo, case.after)
        head = _commit(repo, case.message, AUTHORS[case.author])
        result = check_diff(repo, base, head)
    if result.errors:
        raise RuntimeError(f"{case.name}: {result.errors}")
    return Outcome(case, [f for f in result.findings if f.waiver is None])


def _write_tree(repo: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def _commit(repo: Path, message: str, author: tuple[str, str] = AUTHORS["human"]) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message, author=author)
    return _git(repo, "rev-parse", "HEAD")


def _git(repo: Path, *args: str, author: tuple[str, str] = AUTHORS["human"]) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": author[0],
        "GIT_AUTHOR_EMAIL": author[1],
        "GIT_COMMITTER_NAME": author[0],
        "GIT_COMMITTER_EMAIL": author[1],
    }
    result = subprocess.run(
        ["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def summary(outcomes: list[Outcome]) -> dict[str, int]:
    cheats = [o for o in outcomes if o.case.label == "cheat"]
    refactors = [o for o in outcomes if o.case.label == "refactor"]
    return {
        "cheats": len(cheats),
        "caught": sum(o.flagged for o in cheats),
        "blocked": sum(o.blocked for o in cheats),
        "right_rule": sum(o.correct for o in cheats),
        "refactors": len(refactors),
        "false_positives": sum(o.blocked for o in refactors),
        "warned": sum(o.flagged and not o.blocked for o in refactors),
    }


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def main(argv: list[str]) -> int:
    verbose = "-v" in argv
    outcomes = [evaluate(case) for case in load_all()]
    for label in LABELS:
        print(f"\n{label}s")
        for o in (o for o in outcomes if o.case.label == label):
            if label == "cheat":
                verdict = "blocked" if o.blocked else "warned" if o.flagged else "MISSED"
            else:
                verdict = "FALSE POSITIVE" if o.blocked else "warned" if o.flagged else "clean"
            note = f"  (known: {o.case.known})" if o.case.known else ""
            rules = ",".join(o.rules) or "-"
            print(f"  {o.case.name:<44} {verdict:<15} {rules:<18}{note}")
            if verbose:
                for f in o.findings:
                    print(f"      {f.rule} [{f.severity}] {f.path}:{f.line} {f.message}")
    s = summary(outcomes)
    print()
    print(
        f"Recall: {s['caught']}/{s['cheats']} cheats caught ({_pct(s['caught'], s['cheats'])}), "
        f"{s['blocked']} blocked, {s['right_rule']} by the expected rules."
    )
    print(
        f"False positives: {s['false_positives']}/{s['refactors']} refactors blocked "
        f"({_pct(s['false_positives'], s['refactors'])}), {s['warned']} more with warnings only."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
