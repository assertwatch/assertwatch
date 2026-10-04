"""`assertwatch init`: write assertwatch.toml, print the hook and CODEOWNERS setup.

Only assertwatch.toml is written. Settings and CODEOWNERS are shared files people edit by
hand, so their lines are printed for a human to add.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from assertwatch.paths import CONFIG_PATH, SETTINGS

HOOK_COMMAND = "assertwatch hook"
HOOK_EVENTS = ("SessionStart", "Stop")
GUARD_OWNED = ("/assertwatch.toml", "/.claude/", "/.github/workflows/")
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
DEFAULT_OWNER = "@your-org/your-team"

CONFIG_TEMPLATE = """\
# AssertWatch config. It is always read from the base branch, so a change to this file
# applies only after it is reviewed and merged.

# Globs (repo-relative) for the files that hold tests. Empty: any test_*.py or *_test.py.
test_paths = []

# Severity per rule: block | warn | info | off. `assertwatch rules` lists the rules.
[severity]
# AW011 = "warn"

# Standing waivers: matching findings are still reported but don't fail. Each needs a
# rule, a path glob and a reason.
# [[allow]]
# rule = "AW001"
# path = "tests/legacy/**"
# reason = "legacy suite is being retired"

# What a blocking finding becomes, by who made the change (ai | mixed | human | unknown).
# Commit identities and trailers decide "human", and they can be forged: if agents commit
# here without a reliable Co-Authored-By trailer, set human = "block".
[routing]
# human = "warn"
"""

# (exit code, stdout, stderr) of a `gh` command; replaced in tests.
GhRunner = Callable[[list[str], Path], tuple[int, str, str]]


def run(repo: Path, out: TextIO, owner: str = DEFAULT_OWNER, gh: GhRunner | None = None) -> int:
    _write_config(repo, out)
    _hook(repo, out)
    _codeowners(repo, out, owner)
    _branch_protection(repo, out, gh or _run_gh)
    return 0


def _write_config(repo: Path, out: TextIO) -> None:
    path = repo / CONFIG_PATH
    if path.exists():
        out.write(f"{CONFIG_PATH}: already exists, left unchanged.\n")
        return
    path.write_text(CONFIG_TEMPLATE)
    out.write(
        f"{CONFIG_PATH}: written. Commit it; it takes effect once it is on the base branch.\n"
    )


# --- Claude Code hook -----------------------------------------------------------------------


def hook_snippet() -> str:
    entry = [{"hooks": [{"type": "command", "command": HOOK_COMMAND}]}]
    return json.dumps({"hooks": {event: entry for event in HOOK_EVENTS}}, indent=2)


def installed_events(settings_text: str | None) -> set[str]:
    """Events whose hooks already run `assertwatch hook`."""
    try:
        data = json.loads(settings_text) if settings_text else {}
    except ValueError:
        return set()
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return set()
    found = set()
    for event, groups in hooks.items():
        for group in groups if isinstance(groups, list) else []:
            inner = group.get("hooks") if isinstance(group, dict) else None
            for hook in inner if isinstance(inner, list) else []:
                command = hook.get("command") if isinstance(hook, dict) else None
                if isinstance(command, str) and HOOK_COMMAND in command:
                    found.add(event)
    return found


def _hook(repo: Path, out: TextIO) -> None:
    installed: set[str] = set()
    for name in SETTINGS:
        path = repo / name
        installed |= installed_events(path.read_text() if path.is_file() else None)
    missing = [e for e in HOOK_EVENTS if e not in installed]
    out.write("\n")
    if not missing:
        out.write("Claude Code hook: already installed.\n")
    else:
        out.write(
            f"Claude Code hook: add this to {SETTINGS[0]} (merge it into any existing "
            '"hooks"):\n\n' + _indent(hook_snippet())
        )
    if shutil.which("assertwatch") is None:
        out.write(
            "  warning: `assertwatch` isn't on PATH, so Claude Code can't run the hook. "
            "Install it with `pipx install` or `uv tool install` to put it there.\n"
        )


# --- CODEOWNERS -----------------------------------------------------------------------------


def _codeowners(repo: Path, out: TextIO, owner: str) -> None:
    existing = next((p for p in CODEOWNERS_PATHS if (repo / p).is_file()), None)
    covered = _owned_patterns((repo / existing).read_text()) if existing else set()
    missing = [p for p in GUARD_OWNED if _normalize(p) not in covered]
    out.write("\n")
    if not missing:
        out.write(f"CODEOWNERS: {existing} already covers the guard files.\n")
        return
    target = existing or CODEOWNERS_PATHS[0]
    out.write(
        f"CODEOWNERS: add these lines to {target}, so changes to the guard files need a "
        "human's review:\n\n"
    )
    width = max(len(p) for p in missing)
    out.writelines(f"    {p:<{width}}  {owner}\n" for p in missing)
    if owner == DEFAULT_OWNER:
        out.write(f"\n  Replace {DEFAULT_OWNER} with the people who approve guard changes.\n")


def _owned_patterns(text: str) -> set[str]:
    patterns = set()
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) >= 2:  # a pattern with no owner un-owns the path
            patterns.add(_normalize(fields[0]))
    return patterns


def _normalize(pattern: str) -> str:
    return "/" + pattern.strip("/").removesuffix("/**").removesuffix("/*")


# --- branch protection ----------------------------------------------------------------------


def _branch_protection(repo: Path, out: TextIO, gh: GhRunner) -> None:
    out.write("\n")
    fields = "nameWithOwner,isPrivate,defaultBranchRef"
    code, stdout, _ = gh(["repo", "view", "--json", fields], repo)
    try:
        info = json.loads(stdout) if code == 0 else None
        name, private = info["nameWithOwner"], info["isPrivate"]
        branch = info["defaultBranchRef"]["name"]
    except (TypeError, KeyError, ValueError):
        out.write("Branch protection: not checked (needs the gh CLI and a GitHub remote).\n")
        return
    protected = _is_protected(repo, gh, name, branch)
    if protected is None:
        out.write(f"Branch protection: couldn't read the settings for {branch} on {name}.\n")
    elif protected:
        out.write(
            f"Branch protection: {branch} is protected. Make sure it requires review from code "
            "owners, or CODEOWNERS isn't enforced.\n"
        )
    else:
        out.write(
            f"warning: {branch} on {name} has no branch protection. CODEOWNERS isn't enforced "
            "without it, so an agent's change to the guard files can merge unreviewed.\n"
        )
        if private:
            out.write(
                "  Private repos need a paid GitHub plan for branch protection; without it, "
                "treat the CI check as advisory.\n"
            )


def _is_protected(repo: Path, gh: GhRunner, name: str, branch: str) -> bool | None:
    """True with classic protection or an active ruleset, False with neither, None unknown."""
    code, _, err = gh(["api", f"repos/{name}/branches/{branch}/protection"], repo)
    if code == 0:
        return True
    classic_absent = "Branch not protected" in err
    code, stdout, _ = gh(["api", f"repos/{name}/rules/branches/{branch}"], repo)
    try:
        rules = json.loads(stdout) if code == 0 else None
    except ValueError:
        rules = None
    if isinstance(rules, list) and rules:
        return True
    if classic_absent and rules == []:
        return False
    return None


def _run_gh(args: list[str], repo: Path) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["gh", *args], cwd=repo, capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return result.returncode, result.stdout, result.stderr


def _indent(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.splitlines()) + "\n"
