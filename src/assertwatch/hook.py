"""Claude Code hook: stop the agent from ending a turn whose changes weaken tests.

`assertwatch hook` reads the hook payload on stdin and is installed for two events (see
`assertwatch init`):
- SessionStart records HEAD, so the Stop check covers everything the session changed,
  commits included.
- Stop compares that commit (without one, the merge-base with main/master) with the working
  tree. New blocking findings block the stop with exit 2: Claude reads the report on stderr
  and must restore the tests, or tell the user why the change is intended. Findings it was
  already shown, and any stop while `stop_hook_active` is set, only warn the user, so the
  hook can't trap the agent in a loop.

When no test, test-config or guard file changed, the hook exits before importing the rules.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from assertwatch.paths import is_guard_path, is_test_path

TYPE_CHECKING = False  # typing.TYPE_CHECKING without importing typing (4 ms)
if TYPE_CHECKING:
    from typing import TextIO

EXIT_OK = 0
EXIT_ERROR = 1  # Claude Code shows it to the user and carries on
EXIT_BLOCK = 2  # Claude Code feeds stderr back to the agent and keeps it working

CHECK_EVENTS = (None, "Stop", "SubagentStop")  # None: run by hand, no payload
STATE_DIR = "assertwatch/sessions"  # inside the git dir, so never committed
STATE_MAX_AGE = 30 * 24 * 3600

BLOCK_HEADER = """\
AssertWatch blocked the end of this turn: this session's changes weaken tests.
Restore what was weakened and fix the code so the tests pass. If the user asked for this
change, don't work around the check: tell the user which tests changed and why, so a human
can approve it. Waivers added in this change don't suppress anything.

"""


UNREADABLE = """\
AssertWatch couldn't read these changed test files, so it can't tell whether their tests
were weakened:
{files}
If a file has a syntax error, fix it. If it is valid code, tell the user: the change needs
a human review, because AssertWatch can't check it.
"""


def run(payload_text: str, stdout: TextIO, stderr: TextIO) -> int:
    payload = _payload(payload_text)
    event = payload.get("hook_event_name")
    if event != "SessionStart" and event not in CHECK_EVENTS:
        return EXIT_OK
    repo = Repo.find(payload.get("cwd") or ".")
    if repo is None:
        return EXIT_OK  # nothing to guard
    session = Session.load(repo.git_dir, payload.get("session_id"))

    if event == "SessionStart":
        head = repo.head()
        if session is not None and session.base is None and head:
            session.base = head
            session.save()
            session.prune()
        return EXIT_OK  # SessionStart stdout would go into Claude's context: stay silent

    base = session.base if session is not None else None
    changed = repo.changed_paths(base) if base else None
    recorded = changed is not None
    if changed is None:  # no recorded base, or it is gone
        base = _default_base(repo)
        changed = repo.changed_paths(base) if base else None
    if not changed or not any(is_test_path(p) or is_guard_path(p) for p in changed):
        return EXIT_OK
    return _check(
        repo.top,
        base,
        session,
        bool(payload.get("stop_hook_active")),
        stdout,
        stderr,
        recorded=recorded,
    )


def _check(
    repo: Path,
    base: str,
    session: Session | None,
    stop_hook_active: bool,
    stdout: TextIO,
    stderr: TextIO,
    *,
    recorded: bool = False,
) -> int:
    """Check base..working tree. recorded: base is this session's start, so the session (the
    agent) made every change since; otherwise only uncommitted changes are known to be its."""
    from assertwatch.check import check_diff
    from assertwatch.gitdiff import GitError
    from assertwatch.report import Report, failing, plural, render

    try:
        result = check_diff(repo, base, None, hook=True, session=base if recorded else None)
    except GitError as exc:
        stderr.write(f"assertwatch hook: {exc}\n")
        return EXIT_ERROR
    report = Report(base, None, result.findings, result.errors, result.test_files)
    blocking = failing(report, "block")
    if not blocking and not result.unreadable:
        return EXIT_OK

    # A test file that doesn't parse can't be checked, so it blocks (fail closed): otherwise
    # code the parser can't read would hide any change in that file.
    keys = {finding_key(f) for f in blocking} | {f"unreadable|{p}" for p in result.unreadable}
    new = keys - session.shown if session is not None else keys
    if session is not None:
        session.shown |= keys
        session.save()
    if new and not stop_hook_active:
        if blocking:
            stderr.write(BLOCK_HEADER + render(report, "text"))
        if result.unreadable:
            stderr.write(UNREADABLE.format(files="\n".join(f"  {p}" for p in result.unreadable)))
        return EXIT_BLOCK
    count = len(blocking) + len(result.unreadable)
    message = (
        f"AssertWatch: {plural(count, 'blocking finding')} in this session's changes "
        f"{'is' if count == 1 else 'are'} unresolved. "
        f"See them with: assertwatch check --worktree --base {base[:12]}"
    )
    stdout.write(json.dumps({"systemMessage": message}) + "\n")
    return EXIT_OK


def finding_key(finding) -> str:
    """Identity of a finding across runs; the line number is left out because edits move it."""
    return "|".join((finding.rule, finding.path, finding.test_id or "", finding.message))


def _payload(text: str) -> dict:
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# --- git, with as few subprocesses as possible: each costs 5-20 ms -------------------------

_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _git_out(cwd: Path | str, *args: str) -> str | None:
    """Raw stdout of a git command, or None if it failed."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}  # don't take the index lock from the agent
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True
        )
    except (FileNotFoundError, NotADirectoryError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", "replace")


class Repo:
    """A working tree and its git dirs, found and read without git where that's safe."""

    def __init__(self, top: Path, git_dir: Path, common_dir: Path):
        self.top = top
        self.git_dir = git_dir  # per worktree: HEAD, index, hook state
        self.common_dir = common_dir  # shared by worktrees: refs, packed-refs

    @classmethod
    def find(cls, cwd: str) -> Repo | None:
        found = None if "GIT_DIR" in os.environ else cls._walk(Path(cwd))
        if found is not None:
            return found
        out = _git_out(
            cwd, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir",
            "--git-common-dir",
        )  # fmt: skip
        lines = out.splitlines() if out else []
        return cls(*map(Path, lines)) if len(lines) == 3 else None

    @classmethod
    def _walk(cls, start: Path) -> Repo | None:
        """Find .git upwards from start, as git does. None means: ask git."""
        try:
            start = start.resolve(strict=True)
            for top in (start, *start.parents):
                dot = top / ".git"
                if dot.is_file():  # a linked worktree or submodule: "gitdir: <path>"
                    text = dot.read_text().strip()
                    if not text.startswith("gitdir:"):
                        return None
                    git_dir = (top / text.removeprefix("gitdir:").strip()).resolve()
                elif dot.is_dir():
                    git_dir = dot
                else:
                    continue
                if not (git_dir / "HEAD").is_file():
                    return None
                common = git_dir / "commondir"
                if common.is_file():
                    return cls(top, git_dir, (git_dir / common.read_text().strip()).resolve())
                return cls(top, git_dir, git_dir)
        except OSError:
            return None
        return None

    def head(self) -> str | None:
        """The commit HEAD points to; None on an unborn branch."""
        sha = self._read_head()
        if sha is None:
            sha = _git_out(self.top, "rev-parse", "--verify", "--quiet", "HEAD^{commit}") or ""
            sha = sha.strip() or None
        return sha

    def _read_head(self) -> str | None:
        """HEAD from the files: detached, a loose ref or packed-refs. None means: ask git."""
        try:
            text = (self.git_dir / "HEAD").read_text().strip()
            if _SHA.fullmatch(text):
                return text
            if not text.startswith("ref: "):
                return None
            ref = text.removeprefix("ref: ").strip()
            for directory in (self.git_dir, self.common_dir):
                loose = directory / ref
                if loose.is_file():  # a loose ref wins over packed-refs
                    sha = loose.read_text().strip()
                    return sha if _SHA.fullmatch(sha) else None
            for line in (self.common_dir / "packed-refs").read_text().splitlines():
                sha, _, name = line.partition(" ")
                if name == ref and _SHA.fullmatch(sha):
                    return sha
        except OSError:
            pass
        return None  # unborn branch, reftable, or something unusual

    def changed_paths(self, base: str) -> list[str] | None:
        """Paths that differ between base and the working tree, untracked files included.

        One `git status` when HEAD is still base; a second call lists what was committed.
        """
        status = _git_out(
            self.top, "status", "--porcelain", "-z", "--untracked-files=all", "--no-renames"
        )
        if status is None:
            return None
        paths = [entry[3:] for entry in status.split("\0") if entry]
        if self.head() != base:
            diff = _git_out(self.top, "diff", "--name-only", "-z", "--no-renames", base, "HEAD")
            if diff is None:
                return None
            paths += [p for p in diff.split("\0") if p]
        return paths


def _default_base(repo: Repo) -> str | None:
    """The merge-base with main/master, or HEAD when there's no such branch."""
    from assertwatch.gitdiff import GitError, resolve_base

    try:
        return resolve_base(repo.top)
    except GitError:
        return repo.head()


# --- per-session state ----------------------------------------------------------------------


class Session:
    """What one Claude Code session started from and which findings it was already shown."""

    def __init__(self, path: Path, base: str | None = None, shown: set[str] | None = None):
        self.path = path
        self.base = base
        self.shown = shown or set()

    @classmethod
    def load(cls, git_dir: Path, session_id: object) -> Session | None:
        if not isinstance(session_id, str):
            return None
        name = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)[:128].lstrip(".")
        if not name:
            return None
        path = git_dir / STATE_DIR / f"{name}.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls(path)
        base = data.get("base") if isinstance(data, dict) else None
        shown = data.get("shown") if isinstance(data, dict) else None
        return cls(
            path,
            base if isinstance(base, str) and re.fullmatch(r"[0-9a-f]{40,64}", base) else None,
            {s for s in shown if isinstance(s, str)} if isinstance(shown, list) else set(),
        )

    def save(self) -> None:
        data = {"base": self.base, "shown": sorted(self.shown)}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1) + "\n")
            os.replace(tmp, self.path)
        except OSError:
            pass  # without state the hook still works; it just can't remember

    def prune(self, now: float | None = None) -> None:
        """Forget sessions untouched for STATE_MAX_AGE."""
        cutoff = (now or time.time()) - STATE_MAX_AGE
        for path in self.path.parent.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
