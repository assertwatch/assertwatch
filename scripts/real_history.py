"""Replay the real-history sample: check every pinned commit of the projects as sha^..sha.

    python scripts/real_history.py out.json                 # clone (once) and replay
    python scripts/real_history.py out.json --before old.json  # also list what changed
    python scripts/real_history.py out.json --lang java     # only the Java projects
    python scripts/real_history.py out.json --routing       # also block vs warn by author

Clones go to .cache/real-history/ (gitignored) and are reused. The projects and the commit
each walk starts from are pinned in real_history.toml, so a fresh checkout replays the
same commits. `block` is the rules' verdict; each finding also records its author (from the
commit's identity and trailers), and `routed_block` whether it still blocks under the default
[routing] (human = "warn"). This is development tooling: it needs the network once, to clone.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path

from assertwatch.check import check_diff
from assertwatch.config import AUTHORS, DEFAULT_ROUTING

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
                result = check_diff(repo, f"{sha}^", sha, route=False)
            except Exception as exc:  # report and go on: one odd commit shouldn't stop the run
                print(f"error: {entry['name']} {sha[:10]}: {exc}", file=sys.stderr)
                continue
            if result.findings:
                findings = [
                    dict(
                        rule=f.rule,
                        sev=f.severity.value,
                        test=f.test_id,
                        msg=f.message,
                        author=f.author,
                    )
                    for f in result.findings
                ]
                flagged.append(
                    dict(
                        repo=entry["name"],
                        lang=lang,
                        sha=sha,
                        block=any(f["sev"] == "block" for f in findings),
                        routed_block=any(_routed_block(f) for f in findings),
                        findings=findings,
                    )
                )
    return totals, flagged


def _routed_block(finding: dict) -> bool:
    if finding["sev"] != "block":
        return False
    return finding["rule"] == "AW000" or DEFAULT_ROUTING.get(finding["author"], "block") == "block"


def routing_summary(lang: str, flagged: list[dict]) -> None:
    """Rule-blocking commits by the author of their blocking findings, and what still blocks."""
    blocking = [o for o in flagged if o["lang"] == lang and o["block"]]
    by_author: dict[str, int] = {}
    for o in blocking:
        authors = {f["author"] for f in o["findings"] if f["sev"] == "block"}
        key = next((a for a in AUTHORS if a in authors), "none")  # ai, mixed, unknown, human
        by_author[key] = by_author.get(key, 0) + 1
    still = sum(o["routed_block"] for o in blocking)
    parts = ", ".join(f"{a} {n}" for a, n in sorted(by_author.items()))
    print(f"{lang} routed: {still} of {len(blocking)} rule-blocking commits still block ({parts})")


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
    parser.add_argument(
        "--routing", action="store_true", help="also report block vs warn by author class"
    )
    args = parser.parse_args()
    totals, flagged = replay(args.lang)
    args.out.write_text(json.dumps(flagged, indent=1) + "\n")
    for lang, total in totals.items():
        mine = [o for o in flagged if o["lang"] == lang]
        blocking = sum(o["block"] for o in mine)
        print(f"{lang}: {total} commits, {len(mine)} flagged, {blocking} blocking")
        if args.routing:
            routing_summary(lang, flagged)
    if args.before:
        before = json.loads(args.before.read_text())
        print(f"before: {sum(o['block'] for o in before)} blocking")
        compare(before, flagged)


if __name__ == "__main__":
    main()
