"""Who made a change, an AI agent or a human, and how that routes a finding's severity.

Each finding's file gets an author class, from the strongest evidence available:
1. The Claude Code hook: everything since the session's recorded start is "ai". Without a
   recorded start, uncommitted changes are "ai", and commits without an AI signal are
   "unknown", not "human": an earlier agent session may have made them.
2. Agent Trace records (https://agent-trace.dev) under .agent-trace/ at the head that name a
   commit in the range and the file, with an "ai" or "mixed" contributor. Read only.
3. Commit identities and trailers: `Co-Authored-By: Claude`, agent and `[bot]` accounts, and
   the `[routing] ai_patterns` of the config.
4. Otherwise a file changed only by commits without those signals is "human"; one touched by
   both kinds is "mixed"; uncommitted changes outside the hook, or no commit, are "unknown".

Evidence only ever adds AI: a record or trailer claiming a human never outweighs an AI signal.
Identities and trailers are self-reported, so anyone who can commit can forge "human"; the
hook's session evidence is the only kind an agent can't rewrite.

Routing then lowers a blocking finding to a warning when the config says so for its class
(by default only "human"). It never raises a severity and never touches proposed waivers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from assertwatch.config import Config
from assertwatch.gitdiff import GitError, run_git
from assertwatch.model import Finding, Severity

# Matched case-insensitively against each author and committer ("Name <email>") and each
# line of the commit message.
DEFAULT_AI_PATTERNS = (
    r"^co-authored-by:.*\b(claude|anthropic|copilot|codex|openai|chatgpt|cursor|devin|jules"
    r"|gemini|aider|openhands|windsurf|codeium|cline|tabnine)\b",
    r"\bgenerated (with|by) \[?(claude code|codex|copilot|cursor|aider|gemini)",
    r"noreply@anthropic\.com",
    r"cursoragent@cursor\.com",
    r"\+copilot@users\.noreply\.github\.com",  # GitHub's Copilot coding agent
    r"@ampcode\.com",
    r"\[bot\]\s*[<@]",  # GitHub App accounts: copilot-swe-agent[bot], devin-ai-integration[bot]
    r"\(aider\)",  # aider appends this to the author name
    r"^aider: ",
)
TRACE_DIR = ".agent-trace"
_TRACE_MAX_FILES = 200
_TRACE_MAX_BYTES = 5_000_000
_HEADER = "\x01commit "
_WAIVER_RULE = "AW000"


@dataclass(frozen=True)
class Attribution:
    author: str  # "ai", "mixed", "human" or "unknown"
    source: str  # the evidence, for the report


@dataclass
class Commit:
    sha: str
    ident: str  # "Author Name <email>", for the report
    paths: frozenset[str]
    ai_signal: str | None = None  # what marked it as AI, if anything
    trace: dict[str, str] = field(default_factory=dict)  # path -> Agent Trace contributor


@dataclass
class Attributor:
    commits: list[Commit] = field(default_factory=list)
    uncommitted: frozenset[str] = frozenset()  # paths changed in the working tree
    hook: bool = False  # the hook is running: the working tree is the agent's
    session: str | None = None  # hook: the session's recorded start; everything since is ai

    def of(self, path: str) -> Attribution:
        if self.session is not None:
            return Attribution("ai", f"Claude Code session since {self.session[:12]}")
        ai: list[str] = []
        human: list[Commit] = []
        unknown: list[str] = []
        for commit in self.commits:
            if path not in commit.paths:
                continue
            signal = commit.ai_signal
            traced = commit.trace.get(path)
            if signal is None and traced in ("ai", "mixed"):
                signal = f"Agent Trace record: {traced}"
            if signal is not None:
                ai.append(f"commit {commit.sha[:12]}: {signal}")
            else:
                human.append(commit)
        if path in self.uncommitted:
            if self.hook:
                ai.append("uncommitted changes in a Claude Code session")
            else:
                unknown.append("uncommitted changes")
        if human and self.hook:
            unknown.append(f"{_human(human)}, made in an agent's workspace")
            human = []
        if ai and human:
            return Attribution("mixed", f"{ai[0]}; also {_human(human)}")
        if ai:
            return Attribution("ai", ai[0])
        if unknown:
            return Attribution("unknown", unknown[0])
        if human:
            return Attribution("human", _human(human))
        return Attribution("unknown", "no commit in the range changed it")


def _human(commits: list[Commit]) -> str:
    first = commits[0]
    more = f" and {len(commits) - 1} more" if len(commits) > 1 else ""
    return f"commit {first.sha[:12]} by {first.ident}{more}, no AI signal"


def attributor(
    repo: Path,
    base: str,
    head: str | None,
    config: Config,
    *,
    hook: bool = False,
    session: str | None = None,
) -> Attributor:
    """Collect the evidence for base..head (head None = the working tree)."""
    if session is not None:  # only the hook records a session
        return Attributor(session=session)
    patterns = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in DEFAULT_AI_PATTERNS]
    for extra in config.ai_patterns:
        patterns.append(re.compile(extra, re.IGNORECASE | re.MULTILINE))
    commits = _commits(repo, base, head or "HEAD", patterns)
    traces = _traces(repo, head)
    for commit in commits:
        commit.trace = traces.get(commit.sha, {})
    uncommitted = _uncommitted(repo) if head is None else frozenset()
    return Attributor(commits, uncommitted, hook=hook)


def route_findings(findings: list[Finding], who: Attributor, config: Config) -> list[Finding]:
    """Mark each finding with its author, and lower blocks the routing config lowers."""
    result = []
    cache: dict[str, Attribution] = {}
    for finding in findings:
        if finding.path not in cache:
            cache[finding.path] = who.of(finding.path)
        found = cache[finding.path]
        finding = replace(finding, author=found.author, author_source=found.source)
        if (
            finding.rule != _WAIVER_RULE
            and finding.severity == Severity.BLOCK
            and config.routing.get(found.author, "block") == "warn"
        ):
            finding = replace(finding, severity=Severity.WARN, routed_from=Severity.BLOCK)
        result.append(finding)
    return result


def _commits(repo: Path, base: str, head: str, patterns: list[re.Pattern]) -> list[Commit]:
    try:
        idents = run_git(
            ["log", "-z", "--format=%H%x1f%an <%ae>%x1f%cn <%ce>%x1f%B", f"{base}..{head}"], repo
        )
        names = run_git(
            [
                "log",
                "-z",
                "--no-renames",
                "--name-only",
                f"--format={_HEADER}%H",
                f"{base}..{head}",
            ],
            repo,
        )
    except GitError:
        return []
    paths = _paths_by_commit(names.decode("utf-8", "surrogateescape"))
    commits = []
    for entry in idents.decode("utf-8", "replace").split("\0"):
        sha, _, rest = entry.strip("\n").partition("\x1f")
        if not sha:
            continue
        author, _, rest = rest.partition("\x1f")
        committer, _, message = rest.partition("\x1f")
        signal = None
        for text in (author, committer, message):
            signal = signal or _match(patterns, text)
        commits.append(Commit(sha, author, paths.get(sha, frozenset()), signal))
    return commits


def _match(patterns: list[re.Pattern], text: str) -> str | None:
    for pattern in patterns:
        found = pattern.search(text)
        if found:  # report the whole line it matched in
            start = text.rfind("\n", 0, found.start()) + 1
            end = text.find("\n", found.end())
            return text[start : None if end < 0 else end].strip()[:120]
    return None


def _paths_by_commit(text: str) -> dict[str, frozenset[str]]:
    """Parse `git log -z --name-only --format=<header>%H`: a header, then its paths."""
    result: dict[str, set[str]] = {}
    current: set[str] | None = None
    for token in text.split("\0"):
        token = token.removeprefix("\n")
        if token.startswith(_HEADER):
            current = result.setdefault(token.removeprefix(_HEADER), set())
        elif token and current is not None:
            current.add(token.encode("utf-8", "surrogateescape").decode("utf-8", "replace"))
    return {sha: frozenset(paths) for sha, paths in result.items()}


def _uncommitted(repo: Path) -> frozenset[str]:
    try:
        changed = run_git(["diff", "--name-only", "-z", "--no-renames", "HEAD"], repo)
        untracked = run_git(["ls-files", "--others", "--exclude-standard", "-z"], repo)
    except GitError:
        return frozenset()
    raw = changed + b"\0" + untracked
    return frozenset(p.decode("utf-8", "replace") for p in raw.split(b"\0") if p)


def _traces(repo: Path, head: str | None) -> dict[str, dict[str, str]]:
    """commit sha -> path -> contributor type, from Agent Trace records at head.

    The spec leaves storage to implementations; this reads JSON and JSON Lines files under
    .agent-trace/. Anything malformed is skipped: the records are optional evidence.
    """
    result: dict[str, dict[str, str]] = {}
    for text in _trace_files(repo, head):
        for record in _records(text):
            _add_record(record, result)
    return result


def _trace_files(repo: Path, head: str | None) -> list[str]:
    texts: list[str] = []
    budget = _TRACE_MAX_BYTES
    try:
        if head is None:
            root = repo / TRACE_DIR
            if not root.is_dir():
                return []
            files = sorted(p for p in root.rglob("*") if p.suffix in (".json", ".jsonl"))
            for path in files[:_TRACE_MAX_FILES]:
                if path.is_symlink() or not path.is_file():
                    continue
                data = path.read_bytes()[:budget]
                budget -= len(data)
                texts.append(data.decode("utf-8", "replace"))
                if budget <= 0:
                    break
            return texts
        listing = run_git(["ls-tree", "-r", "-z", "--name-only", head, "--", TRACE_DIR], repo)
        names = [n.decode() for n in listing.split(b"\0") if n.endswith((b".json", b".jsonl"))]
        for name in names[:_TRACE_MAX_FILES]:
            data = run_git(["cat-file", "blob", f"{head}:{name}"], repo)[:budget]
            budget -= len(data)
            texts.append(data.decode("utf-8", "replace"))
            if budget <= 0:
                break
    except (GitError, OSError, UnicodeDecodeError):
        pass
    return texts


def _records(text: str) -> list[object]:
    try:
        data = json.loads(text)
    except ValueError:
        records = []
        for line in text.splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        return records
    return data if isinstance(data, list) else [data]


_RANK = {"human": 0, "unknown": 0, "mixed": 1, "ai": 2}


def _add_record(record: object, result: dict[str, dict[str, str]]) -> None:
    if not isinstance(record, dict):
        return
    vcs = record.get("vcs")
    revision = vcs.get("revision") if isinstance(vcs, dict) else None
    files = record.get("files")
    if not isinstance(revision, str) or not isinstance(files, list):
        return
    by_path = result.setdefault(revision.lower(), {})
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        conversations = entry.get("conversations")
        for conversation in conversations if isinstance(conversations, list) else []:
            if not isinstance(conversation, dict):
                continue
            contributor = conversation.get("contributor")
            kind = contributor.get("type") if isinstance(contributor, dict) else None
            if kind in _RANK and _RANK[kind] > _RANK.get(by_path.get(entry["path"], ""), -1):
                by_path[entry["path"]] = kind
