"""Git access: resolve the base, list changed files, read files at either side of a diff.

All git calls go through `git` as a subprocess, with NUL-separated output so paths with
spaces, quotes or newlines survive intact.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Candidate default branches, in order, when no --base is given.
DEFAULT_BRANCHES = ("main", "master", "origin/main", "origin/master")

# Same heuristic git uses: a NUL byte in the first 8000 bytes means binary.
_BINARY_SNIFF_BYTES = 8000


class GitError(Exception):
    """A git command failed, or the repository is not in the expected state."""


@dataclass(frozen=True)
class ChangedFile:
    """One file that differs between base and head.

    status is one of "A" (added), "M" (modified), "D" (deleted), "R" (renamed),
    "T" (type changed). For renames, old_path is the path on the base side.
    """

    status: str
    path: str
    old_path: str | None = None

    @property
    def base_path(self) -> str | None:
        """Path of this file on the base side, or None if it didn't exist there."""
        if self.status == "A":
            return None
        return self.old_path or self.path

    @property
    def head_path(self) -> str | None:
        """Path of this file on the head side, or None if it was deleted."""
        if self.status == "D":
            return None
        return self.path


def run_git(args: list[str], cwd: Path | str) -> bytes:
    """Run a git command and return raw stdout. Raises GitError on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args)} failed: {message}")
    return result.stdout


def repo_root(cwd: Path | str = ".") -> Path:
    """Return the top-level directory of the repository containing cwd."""
    out = run_git(["rev-parse", "--show-toplevel"], cwd)
    return Path(out.decode().strip())


def _verify_commit(repo: Path, ref: str) -> str | None:
    try:
        out = run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], repo)
    except GitError:
        return None
    return out.decode().strip() or None


def resolve_commit(repo: Path, ref: str) -> str:
    """Resolve a ref to a full commit SHA, or raise GitError."""
    sha = _verify_commit(repo, ref)
    if sha is None:
        raise GitError(f"unknown revision: {ref}")
    return sha


def resolve_base(repo: Path, base: str | None = None, head: str | None = None) -> str:
    """Return the commit to compare against: the merge-base of the base ref and head.

    Using the merge-base (not the base branch tip) means commits that landed on the base
    branch after this branch was created are not reported as changes. If base is None,
    the first of DEFAULT_BRANCHES that exists is used.
    """
    head_sha = resolve_commit(repo, head or "HEAD")
    if base is None:
        for candidate in DEFAULT_BRANCHES:
            if _verify_commit(repo, candidate):
                base = candidate
                break
        else:
            raise GitError(
                "could not find a base branch (tried "
                + ", ".join(DEFAULT_BRANCHES)
                + "); pass --base"
            )
    base_sha = resolve_commit(repo, base)
    try:
        out = run_git(["merge-base", base_sha, head_sha], repo)
    except GitError as exc:
        raise GitError(f"no common ancestor between {base} and {head or 'HEAD'}") from exc
    return out.decode().strip()


def _parse_name_status(raw: bytes) -> list[ChangedFile]:
    """Parse `git diff --name-status -z` output."""
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    changes: list[ChangedFile] = []
    i = 0
    while i < len(fields):
        status = fields[i].decode()
        kind = status[0]
        if kind in ("R", "C"):
            old, new = fields[i + 1].decode(), fields[i + 2].decode()
            i += 3
            if kind == "C":  # a copy leaves the original in place: treat as an add
                changes.append(ChangedFile("A", new))
            else:
                changes.append(ChangedFile("R", new, old))
        else:
            path = fields[i + 1].decode()
            i += 2
            changes.append(ChangedFile(kind, path))
    return changes


def changed_files(
    repo: Path,
    base: str,
    head: str | None = None,
    *,
    include_binary: bool = False,
) -> list[ChangedFile]:
    """List files that differ between base and head.

    head=None compares against the working tree, including staged, unstaged and untracked
    (not ignored) files. Untracked files are always reported as added: git can't detect a
    rename to a file it doesn't track yet.
    """
    args = ["diff", "--name-status", "-z", "-M", "--no-ext-diff", "--no-textconv", base]
    if head is not None:
        args.append(head)
    changes = _parse_name_status(run_git(args, repo))

    if head is None:
        untracked = run_git(["ls-files", "--others", "--exclude-standard", "-z"], repo)
        for path in untracked.split(b"\0"):
            if path:
                changes.append(ChangedFile("A", path.decode()))

    if not include_binary:
        changes = [c for c in changes if not _is_binary_change(repo, base, head, c)]
    return sorted(changes, key=lambda c: c.path)


def read_bytes(repo: Path, ref: str | None, path: str) -> bytes | None:
    """Read a file at a commit (ref) or from the working tree (ref=None).

    Returns None if the file does not exist on that side.
    """
    if ref is None:
        target = repo / path
        if not target.is_file():
            return None
        return target.read_bytes()
    try:
        return run_git(["cat-file", "blob", f"{ref}:{path}"], repo)
    except GitError:
        return None


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:_BINARY_SNIFF_BYTES]


def read_text(repo: Path, ref: str | None, path: str) -> str | None:
    """Read a text file at ref (or the working tree). None if missing or binary."""
    data = read_bytes(repo, ref, path)
    if data is None or is_binary(data):
        return None
    return data.decode("utf-8", "replace")


def _is_binary_change(repo: Path, base: str, head: str | None, change: ChangedFile) -> bool:
    for ref, path in ((head, change.head_path), (base, change.base_path)):
        if path is None:
            continue
        data = read_bytes(repo, ref, path)
        if data is not None:
            return is_binary(data)
    return False
