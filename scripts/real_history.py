"""Replay the real-history sample: check every pinned commit of the projects as sha^..sha.

    python scripts/real_history.py out.json                 # clone (once) and replay
    python scripts/real_history.py out.json --before old.json  # also list what changed
    python scripts/real_history.py out.json --lang java     # only the Java projects

Clones go to .cache/real-history/ (gitignored) and are reused. The projects and the commit
each walk starts from are pinned in real_history.toml, so a fresh checkout replays the
same commits. This is development tooling: it needs the network once, to clone.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path

from assertwatch.check import check_diff

ROOT = Path(__file__).resolve().parent.parent
PINNED = Path(__file__).with_name("real_history.toml")
CACHE = ROOT / ".cache" / "real-history"
# Which commits a project's walk picks: those touching its test files.
TEST_PATHS = {"python": ("*test*.py",), "java": ("*Test*.java", "*IT.java")}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    ).stdout


def clone(name: str, url: str, head: str) -> Path:
    repo = CACHE / name
    if not repo.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--no-checkout", url, str(repo)], check=True)
    try:
        git(repo, "cat-file", "-e", f"{head}^{{commit}}")
    except subprocess.CalledProcessError:
        git(repo, "fetch", "-q", "origin", head)
    return repo


def commits(repo: Path, head: str, count: int, lang: str) -> list[str]:
    out = git(
        repo,
        "log",
        "--no-merges",
        "--min-parents=1",
        "--format=%H",
        "-n",
        str(count),
        head,
        "--",
        *TEST_PATHS[lang],
    )
    return out.split()


def replay(only: str | None) -> tuple[dict[str, int], list[dict]]:
    pinned = tomllib.loads(PINNED.read_text())
    totals: dict[str, int] = {}
    flagged = []
    for entry in pinned["repo"]:
        lang = entry.get("lang", "python")
        if only and lang != only:
            continue
        repo = clone(entry["name"], entry["url"], entry["head"])
        shas = commits(repo, entry["head"], pinned["commits"], lang)
        totals[lang] = totals.get(lang, 0) + len(shas)
        for sha in shas:
            try:
                result = check_diff(repo, f"{sha}^", sha)
            except Exception as exc:  # report and go on: one odd commit shouldn't stop the run
                print(f"error: {entry['name']} {sha[:10]}: {exc}", file=sys.stderr)
                continue
            if result.findings:
                findings = [
                    dict(rule=f.rule, sev=f.severity.value, test=f.test_id, msg=f.message)
                    for f in result.findings
                ]
                flagged.append(
                    dict(
                        repo=entry["name"],
                        lang=lang,
                        sha=sha,
                        block=any(f["sev"] == "block" for f in findings),
                        findings=findings,
                    )
                )
    return totals, flagged


def compare(before: list[dict], after: list[dict]) -> None:
    old = {o["sha"]: o for o in before}
    new = {o["sha"]: o for o in after}
    for sha in sorted(old.keys() | new.keys(), key=lambda s: (old.get(s) or new[s])["repo"]):
        a, b = old.get(sha), new.get(sha)
        was = a["block"] if a else False
        now = b["block"] if b else False
        rules = lambda o: sorted({f["rule"] for f in o["findings"]}) if o else []  # noqa: E731
        if was != now or rules(a) != rules(b):
            repo = (a or b)["repo"]
            print(f"{repo:15} {sha[:10]}  block {was!s:5} -> {now!s:5}  {rules(a)} -> {rules(b)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="where to write the flagged commits (JSON)")
    parser.add_argument("--before", type=Path, help="an earlier run to compare with")
    parser.add_argument("--lang", choices=sorted(TEST_PATHS), help="replay only these projects")
    args = parser.parse_args()
    totals, flagged = replay(args.lang)
    args.out.write_text(json.dumps(flagged, indent=1) + "\n")
    for lang, total in totals.items():
        mine = [o for o in flagged if o["lang"] == lang]
        blocking = sum(o["block"] for o in mine)
        print(f"{lang}: {total} commits, {len(mine)} flagged, {blocking} blocking")
    if args.before:
        before = json.loads(args.before.read_text())
        print(f"before: {sum(o['block'] for o in before)} blocking")
        compare(before, flagged)


if __name__ == "__main__":
    main()
