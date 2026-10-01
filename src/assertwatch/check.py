"""Run the rules over a git diff: read changed test files on both sides, extract, compare."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from assertwatch.extract.python import extract, is_test_path
from assertwatch.gitdiff import changed_files, read_text
from assertwatch.model import Finding, TestFile
from assertwatch.rules import run_rules


@dataclass
class CheckResult:
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # files skipped because they didn't parse


def check_diff(repo: Path, base: str, head: str | None = None) -> CheckResult:
    """Compare test files between base and head (None = the working tree).

    A test file that doesn't parse on either side is skipped and listed in errors: its
    tests are unknown, so they must not be reported as removed.
    """
    base_files: list[TestFile] = []
    head_files: list[TestFile] = []
    path_map: dict[str, str] = {}
    errors: list[str] = []
    for change in changed_files(repo, base, head):
        old = _load(repo, base, change.base_path)
        new = _load(repo, head, change.head_path)
        broken = [f for f in (old, new) if f is not None and f.parse_error]
        if broken:
            errors.extend(f"{f.path}: {f.parse_error}" for f in broken)
            continue
        if old is not None:
            base_files.append(old)
        if new is not None:
            head_files.append(new)
        if old is not None and new is not None and old.path != new.path:
            path_map[old.path] = new.path
    return CheckResult(run_rules(base_files, head_files, path_map), errors)


def _load(repo: Path, ref: str | None, path: str | None) -> TestFile | None:
    if path is None or not is_test_path(path):
        return None
    source = read_text(repo, ref, path)
    return None if source is None else extract(source, path)
