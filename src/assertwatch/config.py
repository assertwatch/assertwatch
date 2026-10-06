"""assertwatch.toml: which files are tests, rule severities, and standing waivers.

The config is always read from the base ref, never from the change being checked, so a
change can't relax the rules it is checked against. With no config file, the defaults apply.

    test_paths = ["tests/**"]
    [severity]
    AW011 = "warn"          # block | warn | info | off
    [[allow]]
    rule = "AW001"
    path = "tests/legacy/**"
    reason = "legacy suite is being retired"
    [routing]               # what a blocking finding becomes, by who made the change
    human = "warn"          # default; ai, mixed and unknown default to "block"
    human_authors = ["dev@example.com", "*@example.org"]  # who counts as human (emails)
    ai_patterns = ["my-agent@example[.]com"]  # extra regexes marking a commit as AI-made
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from assertwatch.paths import CONFIG_PATH

SEVERITIES = frozenset({"block", "warn", "info", "off"})
AUTHORS = ("ai", "mixed", "unknown", "human")
ROUTES = frozenset({"block", "warn"})
DEFAULT_ROUTING = {"ai": "block", "mixed": "block", "unknown": "block", "human": "warn"}
# Routing when [routing] or the whole file can't be read: nothing is lowered (fail closed).
STRICT_ROUTING = dict.fromkeys(AUTHORS, "block")


@dataclass(frozen=True)
class Allow:
    """A standing waiver: findings of rule under path are reported but don't count."""

    rule: str
    path: str
    reason: str

    def covers(self, rule: str, path: str) -> bool:
        return rule == self.rule and path_matches(path, (self.path,))


@dataclass
class Config:
    test_paths: tuple[str, ...] = ()  # empty: any file named like a test
    severity: dict[str, str] = field(default_factory=dict)
    allow: tuple[Allow, ...] = ()
    # author class -> "block" (keep the rule's severity) or "warn" (a block becomes a warning)
    routing: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ROUTING))
    ai_patterns: tuple[str, ...] = ()  # regexes added to attribution's defaults
    # author emails (globs, any case) whose commits without an AI signal are "human"
    human_authors: tuple[str, ...] = ()
    errors: list[str] = field(default_factory=list)


def path_matches(path: str, patterns: tuple[str, ...]) -> bool:
    """Glob match on the repo-relative path; `*` and `**` both cross directories."""
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def parse_config(text: str | None) -> Config:
    """Parse assertwatch.toml. Invalid parts are dropped and described in errors."""
    if text is None:
        return Config()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        # The defaults, except that nothing is lowered: the file may say human = "block".
        return Config(
            errors=[f"{CONFIG_PATH}: {exc}; using defaults"], routing=dict(STRICT_ROUTING)
        )
    config = Config()

    paths = data.get("test_paths", [])
    if isinstance(paths, list) and all(isinstance(p, str) for p in paths):
        config.test_paths = tuple(paths)
    else:
        config.errors.append(f"{CONFIG_PATH}: test_paths must be a list of strings")

    severity = data.get("severity", {})
    if not isinstance(severity, dict):
        severity = {}
        config.errors.append(f"{CONFIG_PATH}: [severity] must be a table")
    for rule, level in severity.items():
        if level in SEVERITIES:
            config.severity[rule] = level
        else:
            config.errors.append(
                f"{CONFIG_PATH}: severity {rule} = {level!r} is not one of "
                + ", ".join(sorted(SEVERITIES))
            )

    allows = []
    for entry in allow_entries(data):
        allow = _allow(entry)
        if isinstance(allow, Allow):
            allows.append(allow)
        else:
            config.errors.append(f"{CONFIG_PATH}: ignoring [[allow]] {entry}: {allow}")
    config.allow = tuple(allows)
    _routing(data, config)
    return config


def _routing(data: dict, config: Config) -> None:
    """Read [routing]. Anything invalid in it means "block" for every author: a typo must
    never lower a finding the config meant to keep blocking."""
    routing = data.get("routing", {})
    if not isinstance(routing, dict):
        config.errors.append(f"{CONFIG_PATH}: [routing] must be a table; blocking for all")
        config.routing = dict(STRICT_ROUTING)
        return
    invalid = False
    for key, value in routing.items():
        if key in ("ai_patterns", "human_authors"):
            continue
        if key not in AUTHORS:
            invalid = True
            config.errors.append(
                f"{CONFIG_PATH}: [routing] {key} is not one of {', '.join(AUTHORS)}; "
                "blocking for all"
            )
        elif value in ROUTES:
            config.routing[key] = value
        else:
            invalid = True
            config.errors.append(
                f"{CONFIG_PATH}: [routing] {key} = {value!r} is not one of block, warn; "
                "blocking for all"
            )
    if invalid:
        config.routing = dict(STRICT_ROUTING)
    humans = routing.get("human_authors", [])
    if isinstance(humans, list) and all(isinstance(h, str) and h.strip() for h in humans):
        config.human_authors = tuple(h.strip().lower() for h in humans)
    else:  # nobody is listed: no commit is lowered as a human's
        config.errors.append(
            f"{CONFIG_PATH}: [routing] human_authors must be a list of emails; ignoring it"
        )
    patterns = routing.get("ai_patterns", [])
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        config.errors.append(f"{CONFIG_PATH}: [routing] ai_patterns must be a list of strings")
        return
    valid = []
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            config.errors.append(f"{CONFIG_PATH}: [routing] ai_patterns {pattern!r}: {exc}")
        else:
            valid.append(pattern)
    config.ai_patterns = tuple(valid)


def allow_entries(data: dict) -> list[dict]:
    entries = data.get("allow", [])
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _allow(entry: dict) -> Allow | str:
    rule, path, reason = entry.get("rule"), entry.get("path"), entry.get("reason")
    if not isinstance(rule, str) or not rule:
        return "missing rule"
    if rule == "AW000":
        return "proposed waivers always need a human"
    if not isinstance(reason, str) or not reason.strip():
        return "a waiver needs a reason"
    if not isinstance(path, str) or not path:
        return "missing path"
    return Allow(rule, path, reason.strip())
