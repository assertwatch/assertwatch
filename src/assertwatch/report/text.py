"""Plain-text report, for terminals and for coding agents reading hook output."""

from __future__ import annotations

from assertwatch.model import Finding, Severity
from assertwatch.report import WAIVER_RULE, Report, location, plural


def render_text(report: Report) -> str:
    out = [_headline(report)]
    proposed = [f for f in report.active if f.rule == WAIVER_RULE]
    regular = [f for f in report.active if f.rule != WAIVER_RULE]
    for finding in regular:
        out.append("")
        out.extend(_finding(finding))
    if proposed:
        out += ["", "Waivers requested (need human approval; they don't suppress anything):"]
        for finding in proposed:
            out.append(f"  {location(finding)}  {finding.message}")
    if report.waived:
        out += ["", "Covered by standing waivers (reported, not counted):"]
        for finding in report.waived:
            out.append(f"  {location(finding)}  {finding.rule}  {finding.message}")
            out.append(f"      waiver: {finding.waiver}")
    if report.errors:
        out += ["", "Not checked:"]
        out.extend(f"  {error}" for error in report.errors)
    return "\n".join(out) + "\n"


def _headline(report: Report) -> str:
    scope = f"{plural(report.test_files, 'test file')}, {report.base[:12]}..{report.head_label}"
    if not report.active:
        return f"AssertWatch: no test weakening found ({scope})."
    counts = ", ".join(
        f"{report.count(s)} {s}"
        for s in (Severity.BLOCK, Severity.WARN, Severity.INFO)
        if report.count(s)
    )
    return f"AssertWatch: {plural(len(report.active), 'finding')} ({counts}) in {scope}."


def _finding(finding: Finding) -> list[str]:
    lines = [f"{location(finding)}  {finding.rule} [{finding.severity}]  {finding.message}"]
    if finding.test_id:
        lines.append(f"    test: {finding.test_id}")
    for prefix, text in (("- ", finding.before), ("+ ", finding.after)):
        if text:
            lines.extend(f"    {prefix}{line}" for line in text.splitlines())
    if finding.hint:
        lines.append(f"    fix: {finding.hint}")
    return lines
