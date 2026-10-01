"""Render a check result as text, JSON or a GitHub PR summary, and pick the exit code."""

from __future__ import annotations

from dataclasses import dataclass, field

from assertwatch.model import Finding, Severity

FORMATS = ("text", "json", "github")
WAIVER_RULE = "AW000"

_RANK = {Severity.INFO: 0, Severity.WARN: 1, Severity.BLOCK: 2}


@dataclass
class Report:
    """What one `assertwatch check` run found. head is None for the working tree."""

    base: str
    head: str | None
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    test_files: int = 0

    @property
    def active(self) -> list[Finding]:
        """Findings not covered by a standing waiver."""
        return [f for f in self.findings if f.waiver is None]

    @property
    def waived(self) -> list[Finding]:
        return [f for f in self.findings if f.waiver is not None]

    @property
    def head_label(self) -> str:
        return "working tree" if self.head is None else self.head[:12]

    def count(self, severity: Severity) -> int:
        return sum(1 for f in self.active if f.severity == severity)


def failing(report: Report, fail_on: str) -> list[Finding]:
    """Active findings at or above the fail_on severity."""
    threshold = _RANK[Severity(fail_on)]
    return [f for f in report.active if _RANK[f.severity] >= threshold]


def render(report: Report, fmt: str) -> str:
    if fmt == "text":
        from assertwatch.report.text import render_text

        return render_text(report)
    if fmt == "json":
        from assertwatch.report.json import render_json

        return render_json(report)
    if fmt == "github":
        from assertwatch.report.github import render_github

        return render_github(report)
    raise ValueError(f"unknown format: {fmt}")


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def location(finding: Finding) -> str:
    return f"{finding.path}:{finding.line}" if finding.line else finding.path
