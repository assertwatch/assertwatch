"""AW000: a waiver was proposed in the change being checked.

A cheat and its waiver would arrive together, so waivers proposed in the diff never suppress
anything. Each becomes a finding that shows the proposed reason, for a human to approve:
- a new `[[allow]]` entry in assertwatch.toml (the config is read from the base ref)
- an added inline comment `# assertwatch: allow AW003 reason="..."` (or `//` for Java)
- an `AssertWatch-Allow:` trailer in a commit message
Once a human merges a config waiver, it is on the base ref and applies from then on.
"""

from __future__ import annotations

import re
import tomllib
from collections import Counter
from collections.abc import Iterator

from assertwatch.config import CONFIG_PATH, allow_entries
from assertwatch.model import Finding, Severity
from assertwatch.rules import Context, FileChange, Rule

ID = "AW000"
HINT = (
    "Waivers can't be added in the same change: fix the test, or stop and ask a human to "
    "approve this waiver."
)

INLINE = re.compile(
    r"(?:#|//)\s*assertwatch\s*:\s*allow\s+(?P<rule>AW\d{3})(?:\s+reason\s*=\s*\"(?P<reason>[^\"]*)\")?",
    re.IGNORECASE,
)
TRAILER = re.compile(r"^\s*AssertWatch-Allow\s*:\s*(?P<value>.*?)\s*$", re.IGNORECASE | re.M)
TRAILER_VALUE = re.compile(
    r"(?P<rule>AW\d{3})\b[\s:,-]*(?:reason\s*=\s*)?\"?(?P<reason>[^\"]*)\"?", re.IGNORECASE
)
COMMIT_PATH = "(commit message)"


def check(context: Context) -> Iterator[Finding]:
    yield from _inline(context)
    for change in context.files:
        if change.path == CONFIG_PATH:
            yield from _config(change)
    for message in context.messages:
        for match in TRAILER.finditer(message):
            value = TRAILER_VALUE.match(match["value"])
            rule = value["rule"] if value else "?"
            reason = value["reason"] if value else match["value"]
            yield _finding(COMMIT_PATH, 0, f"AssertWatch-Allow trailer for {rule}", reason)


def _inline(context: Context) -> Iterator[Finding]:
    # A comment that was only moved shows up as removed and added; it isn't new.
    removed = Counter(
        (line.path, m.group(0).lower())
        for line in context.lines
        if not line.added and (m := INLINE.search(line.text))
    )
    for line in context.lines:
        match = INLINE.search(line.text) if line.added else None
        if match is None:
            continue
        key = (line.path, match.group(0).lower())
        if removed[key]:
            removed[key] -= 1
            continue
        yield _finding(line.path, line.line, f"inline allow {match['rule']}", match["reason"])


def _config(change: FileChange) -> Iterator[Finding]:
    before = Counter(_allows(change.before))
    for entry in _allows(change.after):
        if before[entry]:
            before[entry] -= 1
            continue
        rule, path, reason = entry
        line = _line_of(change.after or "", reason or path or rule)
        yield _finding(CONFIG_PATH, line, f"[[allow]] {rule} for {path}", reason)


def _allows(text: str | None) -> list[tuple[str, str, str]]:
    if not text:
        return []
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []  # AW009 reports the config change itself
    return [
        (str(e.get("rule", "")), str(e.get("path", "")), str(e.get("reason", "")))
        for e in allow_entries(data)
    ]


def _line_of(text: str, needle: str) -> int:
    for number, line in enumerate(text.splitlines(), 1):
        if needle and needle in line:
            return number
    return 1


def _finding(path: str, line: int, what: str, reason: str | None) -> Finding:
    if reason and reason.strip():
        detail = f'reason given: "{reason.strip()}"'
    else:
        detail = "no reason given, so it would be invalid even after approval"
    return Finding(
        rule=ID,
        severity=RULE.severity,
        path=path,
        line=line,
        message=f"Waiver proposed in this change ({what}); {detail}. It needs human approval.",
        hint=HINT,
    )


RULE = Rule(
    id=ID,
    title="Waiver added in this change",
    severity=Severity.BLOCK,
    summary="A waiver in the diff never applies to itself; a human has to approve it.",
    check=check,
)
