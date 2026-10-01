"""GitHub Actions output: annotations on the PR diff, the job summary and step outputs.

Used by `assertwatch check --github-actions`. The text report still goes to the log; this adds
workflow commands after it, and writes to the files GitHub names in $GITHUB_STEP_SUMMARY and
$GITHUB_OUTPUT when they are set.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TextIO

from assertwatch.model import Finding, Severity
from assertwatch.report import Report, failing
from assertwatch.report.github import render_github

_LEVEL = {Severity.BLOCK: "warning", Severity.WARN: "warning", Severity.INFO: "notice"}


def annotations(report: Report, fail_on: str) -> list[str]:
    """One workflow command per active finding. Findings that fail the job are errors."""
    fails = {id(f) for f in failing(report, fail_on)}
    lines = []
    for finding in report.active:
        level = "error" if id(finding) in fails else _LEVEL[finding.severity]
        props = f"file={_prop(finding.path)}"
        if finding.line and finding.side == "head":
            props += f",line={finding.line}"  # GitHub can only place head-side lines
        props += f",title={_prop(f'{finding.rule} ({finding.severity})')}"
        lines.append(f"::{level} {props}::{_data(_message(finding))}")
    for error in report.errors:
        lines.append(f"::error title=AssertWatch could not check a file::{_data(error)}")
    return lines


def outputs(report: Report, fail_on: str) -> dict[str, str]:
    return {
        "findings": str(len(report.active)),
        "block": str(report.count(Severity.BLOCK)),
        "warn": str(report.count(Severity.WARN)),
        "info": str(report.count(Severity.INFO)),
        "failed": "true" if failing(report, fail_on) else "false",
    }


def write_actions(report: Report, fail_on: str, stdout: TextIO, env: Mapping[str, str]) -> None:
    for line in annotations(report, fail_on):
        stdout.write(line + "\n")
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(render_github(report))
    output = env.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.writelines(f"{k}={v}\n" for k, v in outputs(report, fail_on).items())


def _message(finding: Finding) -> str:
    text = finding.message
    if finding.side == "base" and finding.line:
        text += f" (line {finding.line} before the change)"
    if finding.hint:
        text += f"\n{finding.hint}"
    return text


def _data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _prop(text: str) -> str:
    return _data(text).replace(":", "%3A").replace(",", "%2C")
