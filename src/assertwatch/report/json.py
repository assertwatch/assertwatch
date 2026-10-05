"""JSON report. The shape is versioned; fields are only ever added within a version."""

from __future__ import annotations

import json
from dataclasses import asdict

from assertwatch.model import Severity
from assertwatch.report import Report

VERSION = 1


def render_json(report: Report) -> str:
    data = {
        "version": VERSION,
        "base": report.base,
        "head": report.head,
        "test_files": report.test_files,
        "summary": {
            "findings": len(report.active),
            "block": report.count(Severity.BLOCK),
            "warn": report.count(Severity.WARN),
            "info": report.count(Severity.INFO),
            "waived": len(report.waived),
        },
        "findings": [
            {
                **asdict(f),
                "severity": str(f.severity),
                "routed_from": None if f.routed_from is None else str(f.routed_from),
            }
            for f in report.findings
        ],
        "errors": report.errors,
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"
