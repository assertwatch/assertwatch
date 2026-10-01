# AssertWatch

> Your agent says the tests pass. AssertWatch checks the tests weren't weakened to make that true.

**Status: pre-alpha.** The Python rules and the CLI work; the Claude Code hook, the GitHub
Action and Java support are next. Building in public.

AssertWatch reads a git diff and flags test weakening by AI coding agents: deleted tests, dropped
or loosened assertions, new skips, rewritten expected values, mocks of the code under test, and
relaxed test or coverage config. Deterministic, no LLM, runs offline. Python (pytest) first, then
Java (JUnit 5).

## Usage

```sh
assertwatch check                 # HEAD vs its merge-base with main/master
assertwatch check --worktree      # include uncommitted changes
assertwatch check --base origin/main --format github   # markdown PR summary
assertwatch rules                 # list the rules
```

Exit codes: `0` clean, `1` findings at or above `--fail-on` (default `block`), `2` usage, git
or parse error. Formats: `text`, `json`, `github`.

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
