"""GitHub-flavored markdown, for a PR comment or the Actions job summary."""

from __future__ import annotations

from assertwatch.model import Finding, Severity
from assertwatch.report import WAIVER_RULE, Report, authored, location, plural


def render_github(report: Report) -> str:
    out = ["## AssertWatch", ""]
    proposed = [f for f in report.active if f.rule == WAIVER_RULE]
    regular = [f for f in report.active if f.rule != WAIVER_RULE]
    scope = (
        f"{plural(report.test_files, 'test file')} checked, "
        f"`{report.base[:12]}..{report.head_label}`"
    )
    if not report.active:
        out.append(f"No test weakening found ({scope}).")
    else:
        counts = ", ".join(
            f"{report.count(s)} {s}"
            for s in (Severity.BLOCK, Severity.WARN, Severity.INFO)
            if report.count(s)
        )
        out.append(f"**{plural(len(report.active), 'finding')}** ({counts}); {scope}.")

    if proposed:
        out += ["", "### Waivers requested (needs human approval)", ""]
        out.append(
            "These were added in this change, so they don't suppress anything yet. "
            "Approve one by merging it; it applies from the base branch afterwards."
        )
        out.append("")
        out.extend(f"- `{location(f)}`: {_cell(f.message)}" for f in proposed)

    if regular:
        out += ["", "### Findings", "", "| Severity | Rule | Location | Finding | Author |"]
        out.append("|---|---|---|---|---|")
        out.extend(
            f"| {f.severity} | {f.rule} | `{_cell(location(f))}` | {_cell(f.message)} "
            f"| {_cell(authored(f) or 'not attributed')} |"
            for f in regular
        )
        details = [f for f in regular if f.before or f.after]
        for finding in details:
            out += ["", *_details(finding)]

    if report.waived:
        out += ["", "### Covered by standing waivers", ""]
        out.extend(
            f"- {f.rule} `{location(f)}`: {_cell(f.message)} Waiver: {_cell(f.waiver or '')}"
            for f in report.waived
        )
    if report.errors:
        out += ["", "### Not checked", ""]
        out.extend(f"- {_cell(error)}" for error in report.errors)
    return "\n".join(out) + "\n"


def _details(finding: Finding) -> list[str]:
    body = [f"-{line}" for line in (finding.before or "").splitlines()]
    body += [f"+{line}" for line in (finding.after or "").splitlines()]
    return [
        f"<details><summary>{finding.rule} <code>{_html(location(finding))}</code></summary>",
        "",
        "```diff",
        *(line.replace("```", "`​``") for line in body),
        "```",
        "",
        *([finding.hint] if finding.hint else []),
        "</details>",
    ]


def _cell(text: str) -> str:
    """Make text safe inside a markdown table cell or list item."""
    return " ".join(text.split()).replace("|", "\\|")


def _html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
