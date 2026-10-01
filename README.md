# AssertWatch

> Your agent says the tests pass. AssertWatch checks the tests weren't weakened to make that true.

**Status: pre-alpha.** The Python rules, the CLI and the Claude Code hook work; the GitHub
Action and Java support are next. Building in public.

AssertWatch reads a git diff and flags test weakening by AI coding agents: deleted tests, dropped
or loosened assertions, new skips, rewritten expected values, mocks of the code under test, and
relaxed test or coverage config. Deterministic, no LLM, runs offline. Python (pytest) first, then
Java (JUnit 5).

## Claude Code hook

```sh
assertwatch init       # writes assertwatch.toml, prints the hook and CODEOWNERS setup
```

Add the printed snippet to `.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "assertwatch hook" }] }],
    "Stop": [{ "hooks": [{ "type": "command", "command": "assertwatch hook" }] }]
  }
}
```

At session start the hook records `HEAD`. When the agent tries to end its turn, the hook
checks everything the session changed, commits included. If tests were weakened, the turn
is blocked and the agent gets the report: it has to restore the tests, or tell you why the
change is intended. It is blocked once per finding, so it can always hand the decision back
to you. Turns that change no test, test-config or guard file are skipped quickly
(about 50-65 ms on a laptop, mostly Python start-up and one `git status`).

`init` also prints CODEOWNERS lines for `assertwatch.toml`, `.claude/` and
`.github/workflows/`, and warns if the default branch has no protection (via `gh`), since
CODEOWNERS isn't enforced without it. The hook runs on the agent's machine; the CI check
is what makes it binding.

## Usage

```sh
assertwatch check                 # HEAD vs its merge-base with main/master
assertwatch check --worktree      # include uncommitted changes
assertwatch check --base origin/main --format github   # markdown PR summary
assertwatch rules                 # list the rules
```

Exit codes: `0` clean, `1` findings at or above `--fail-on` (default `block`), `2` usage, git
or parse error. Formats: `text`, `json`, `github`. `assertwatch hook` follows Claude Code's
convention instead: `2` blocks the agent's stop.

| Rule | Catches |
|---|---|
| AW000 | A waiver added in the same change it would excuse |
| AW001 | A test or parametrized case removed (renames, moves and splits are matched) |
| AW002 | A test that lost assertions |
| AW003 | A new skip, skipif, xfail, skip call or early return |
| AW004 | An assertion replaced by a weaker one (`==` to `is not None`, broad exceptions, wider tolerance) |
| AW008 | A test body emptied |
| AW009 | Test config or the guard loosened (`--deselect`, lower `fail_under`, hook removed, ...) |
| AW011 | An assertion that can never fail (`assert True`, `x == x`) |

Configuration lives in `assertwatch.toml` and is always read from the base branch, so a change
can't relax the rules it is checked against:

```toml
test_paths = ["tests/**"]
[severity]
AW011 = "warn"
[[allow]]
rule = "AW001"
path = "tests/legacy/**"
reason = "legacy suite is being retired"
```

Waivers added in the change itself (a new `[[allow]]`, an inline
`# assertwatch: allow AW003 reason="..."`, an `AssertWatch-Allow:` commit trailer) don't
suppress anything; they are reported as AW000 for a human to approve.

## Development

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest -q
assertwatch --version
```

With [uv](https://docs.astral.sh/uv/): `uvx --from . assertwatch --version`.

## License

MIT
