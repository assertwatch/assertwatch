# AssertWatch

> Your agent says the tests pass. AssertWatch checks the tests weren't weakened to make that true.

**Status: pre-alpha.** The Python rules, the CLI, the Claude Code hook and the GitHub Action
work; Java support is next. Building in public.

AssertWatch reads a git diff and flags test weakening by AI coding agents: deleted tests, dropped
or loosened assertions, new skips, rewritten expected values, mocks of the code under test, and
relaxed test or coverage config. Deterministic, no LLM, runs offline. Python (pytest) first, then
Java (JUnit 5).

It checks at two points: when the agent tries to end its turn, and on the pull request.

```text
         agent edits code and tests, then tries to stop
                 │
  ╔══════════════▼══════════════╗
  ║ Claude Code Stop hook       ║── weakened ──▶ the agent gets the report and
  ║ diff since session start    ║                must restore the test or say
  ╚══════════════╤══════════════╝                why it changed
                 │ clean, or explained
  ┌──────────────▼──────────────┐
  │ pull request                │
  └──────────────┬──────────────┘
  ╔══════════════▼══════════════╗
  ║ GitHub Action               ║── weakened ──▶ annotations and a job summary;
  ║ PR head vs its merge-base   ║                with fail-on: block, the PR
  ╚══════════════╤══════════════╝                can't merge until a human
                 │ clean                         resolves it
                 ▼
               merge
```

The hook gives the agent a chance to put the test back before you see the change. The CI
check is what makes it binding, since the agent can edit anything on its own machine.

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

## GitHub Action

```yaml
# .github/workflows/assertwatch.yml
name: assertwatch
on: [pull_request]
permissions:
  contents: read
jobs:
  assertwatch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: assertwatch/assertwatch@main
        with:
          fail-on: never   # report only (default); "block" makes it a required check
```

It compares the PR head with its merge-base on the base branch, so the config and waivers come
from the base and the PR can't relax them. Findings appear as annotations on the diff and as a
job summary; outputs `findings`, `block`, `warn` and `failed` are there for later steps. Start
with `never`, then switch to `block` and mark the job required in branch protection: that is
what makes the check binding, since the hook runs where the agent can edit it. The action
fetches the history it needs, so the default shallow checkout is fine. It also works on `push`
(comparing with the previous commit) and `merge_group`.

## Usage

```sh
assertwatch check                 # HEAD vs its merge-base with main/master
assertwatch check --worktree      # include uncommitted changes
assertwatch check --base origin/main --format github   # markdown PR summary
assertwatch rules                 # list the rules
```

Exit codes: `0` clean, `1` findings at or above `--fail-on` (`block`, the default, `warn` or
`never`), `2` usage, git or parse error. `--github-actions` adds annotations, the job summary
and step outputs when run in GitHub Actions. Formats: `text`, `json`, `github`.
`assertwatch hook` follows Claude Code's convention instead: `2` blocks the agent's stop.

Each check reads both sides of the diff, pairs every old test with its new version, and then
runs the rules on the pairs:

```text
  base ─┐  ┌─────────────┐  ┌────────────┐  ╔═════════════╗  ┌──────────┐
        ├─▶│ read diff   │─▶│ extract    │─▶║ match tests ║─▶│ rules    │─▶ block
  head ─┘  │ config from │  │ tests and  │  ║ renamed,    ║  │ AW000 to │   or warn
           │ base only   │  │ assertions │  ║ moved,      ║  │ AW011    │
           └─────────────┘  └────────────┘  ║ split       ║  └──────────┘
                                            ╚═════════════╝
```

Matching is what keeps refactors quiet: a test that was renamed, moved, split, merged into a
parametrized test or converted from unittest is compared with its new self, not reported
as removed.

| Rule | Catches |
|---|---|
| AW000 | A waiver added in the same change it would excuse |
| AW001 | A test or parametrized case removed (renames, moves and splits are matched) |
| AW002 | A test that lost assertions |
| AW003 | A new skip, skipif, xfail, skip call or early return |
| AW004 | An assertion replaced by a weaker one (`==` to `is not None`, broad exceptions, wider tolerance) |
| AW005 | An expected value rewritten (`== 10.05` to `== 10`, a parametrized expected value). Blocks when no production code changed; a warning when it did |
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

## How well it works

`make eval` replays the labelled corpus in [`corpus/cases/`](corpus/cases): small, complete
changes, each one a cheat that should be caught or a refactor that shouldn't be blocked. The
refactors are modeled on real commits from open-source projects that earlier versions
blocked. Results today: **30 of 32 cheats caught, 1 of 24 refactors blocked** (94% recall,
4% false positives on refactors; 1 more refactor gets a warning).

- **Missed:** a failing assertion wrapped in `try/except AssertionError: pass`, and the
  function under test patched to return the expected value. Those are AW007 and AW006,
  not built yet.
- **Blocked by mistake:** three asserts on a recorded warning replaced by one
  `pytest.warns(DeprecationWarning, match=...)`. It reads as two assertions dropped.
- **Warned:** an intended behavior change, where the code and the expected value change
  together (AW005 asks for confirmation).

Read these numbers with care. The corpus is small, and most of it was written by the same
people as the rules, after the rules. Cheats an agent might invent that we haven't thought of
aren't in it. Cases that fail on purpose are marked `known` in their file, and the test
suite fails when one starts passing, so the numbers here stay current.

**On real history**, which nobody wrote for us: we ran the last 300 test-touching commits of
six open-source Python projects (click, more-itertools, attrs, dateutil, markupsafe,
itsdangerous; 1,318 commits). 189 of them (14%) get a finding: 136 a blocking one, 53 only a
warning that a value changed along with the code. We labelled a random 30 of the blocked
commits by hand ([`corpus/real-history.md`](corpus/real-history.md)):

- **Blocked rightly, about 6% of commits.** Mostly deliberate changes a reviewer should
  approve: a feature removed with its tests, a platform skip, changed behavior. Four were
  real weakenings, including a lint autofix that turned a warning check into one that can
  never pass (the project fixed it later), and a test whose invalid input was swapped for a
  valid one.
- **Blocked wrongly, about 4% of commits** (roughly 2-6%): tests rewritten past
  recognition, checks made stricter in a form the rules don't compare, dead Python 2
  branches. Labelling found four fixable patterns; they are fixed and in the corpus.

AW005 blocks a changed expected value only when no production code changed, because then the
test was edited to agree with the code. A human correcting a wrong test value looks exactly
the same, so expect the occasional block there (8 of the 1,318 commits). If that's too strict
for your team, set `AW005 = "warn"` under `[severity]`.

## Development

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest -q   # or: make check
make eval                                            # recall and false positives
assertwatch --version
```

With [uv](https://docs.astral.sh/uv/): `uvx --from . assertwatch --version`.

## License

[Apache-2.0](LICENSE). Contributions are accepted under the same license. The license doesn't
grant use of the AssertWatch name or logo.
