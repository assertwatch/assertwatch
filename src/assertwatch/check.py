"""Run the rules over a git diff: read changed files on both sides, extract, compare."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from assertwatch.config import CONFIG_PATH, Config, parse_config, path_matches
from assertwatch.extract.python import extract
from assertwatch.gitdiff import (
    ChangedFile,
    changed_files,
    commit_messages,
    diff_lines,
    files_defining,
    read_text,
)
from assertwatch.model import Finding, TestFile
from assertwatch.paths import is_guard_path, is_test_path
from assertwatch.python_support import python2_dropped
from assertwatch.rules import FileChange, run_rules

# Upper bound on unchanged files read when looking for copies of removed tests.
_MAX_COPY_FILES = 50


@dataclass
class CheckResult:
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # skipped files, config problems
    test_files: int = 0  # changed test files that were compared


def check_diff(repo: Path, base: str, head: str | None = None) -> CheckResult:
    """Check the change from base to head (None = the working tree).

    The config and the declared Python versions come from base, never from the change itself.
    A test file that doesn't parse on either side is skipped and listed in errors: its tests
    are unknown, so they must not be reported as removed.
    """
    config = parse_config(read_text(repo, base, CONFIG_PATH))
    errors = list(config.errors)
    base_files: list[TestFile] = []
    head_files: list[TestFile] = []
    guard_files: list[FileChange] = []
    path_map: dict[str, str] = {}
    changes = changed_files(repo, base, head)
    for change in changes:
        old_path, new_path = change.base_path, change.head_path
        if any(p and is_guard_path(p) for p in (old_path, new_path)):
            guard_files.append(
                FileChange(
                    old_path,
                    new_path,
                    read_text(repo, base, old_path) if old_path else None,
                    read_text(repo, head, new_path) if new_path else None,
                )
            )
        old = _load(repo, base, old_path, config)
        new = _load(repo, head, new_path, config)
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
    findings = run_rules(
        base_files,
        head_files,
        path_map,
        files=guard_files,
        lines=diff_lines(repo, base, head),
        messages=commit_messages(repo, base, head),
        config=config,
        python2_dropped=python2_dropped(lambda path: read_text(repo, base, path)),
        find_copies=lambda names: _unchanged_copies(repo, head, names, changes, config),
    )
    # A renamed file counts once, under its head path.
    paths = {f.path for f in head_files} | {f.path for f in base_files if f.path not in path_map}
    return CheckResult(findings, errors, len(paths))


def _unchanged_copies(
    repo: Path, head: str | None, names: list[str], changes: list[ChangedFile], config: Config
) -> list[TestFile]:
    """Test files the change didn't touch that define a test with one of these names.

    They are the same on both sides, so a copy found there ran before and still runs.
    """
    touched = {p for c in changes for p in (c.base_path, c.head_path) if p}
    paths = [p for p in files_defining(repo, head, names) if p not in touched]
    loaded = (_load(repo, head, p, config) for p in paths[:_MAX_COPY_FILES])
    return [f for f in loaded if f is not None]


def _load(repo: Path, ref: str | None, path: str | None, config: Config) -> TestFile | None:
    if path is None or not is_test_path(path):
        return None
    if config.test_paths and not path_matches(path, config.test_paths):
        return None
    source = read_text(repo, ref, path)
    return None if source is None else extract(source, path)
