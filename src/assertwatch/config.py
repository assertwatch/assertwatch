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
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

CONFIG_PATH = "assertwatch.toml"
SEVERITIES = frozenset({"block", "warn", "info", "off"})


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
        return Config(errors=[f"{CONFIG_PATH}: {exc}; using defaults"])
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
    return config


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
