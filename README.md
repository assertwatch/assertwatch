# AssertWatch

> Your agent says the tests pass. AssertWatch checks the tests weren't weakened to make that true.

**Status: pre-alpha.** The Python rules, the CLI, the Claude Code hook and the GitHub Action
work; Java (JUnit 5) support is in progress. Building in public.

AssertWatch reads a git diff and flags test weakening by AI coding agents: deleted tests, dropped
or loosened assertions, new skips, rewritten expected values and relaxed test or coverage
config. Deterministic, no LLM, runs offline. Python (pytest) first, then
Java (JUnit 5).

![AssertWatch blocking a change that drops an assertion, weakens one and skips a test](https://raw.githubusercontent.com/assertwatch/assertwatch/main/docs/demo/demo.gif)

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

## Install

```sh
uv tool install assertwatch      # or: pipx install assertwatch
```

Python 3.11 or newer, and git. No other dependencies.

## Try it

A test file, then the kind of change an agent makes to get a failing test to pass:

```sh
mkdir aw-demo && cd aw-demo && git init -q
cat > test_prices.py <<'EOF'
import pytest

def total(price, tax):
    return round(price * (1 + tax), 2)

def test_total_adds_tax():
    assert total(10, 0.05) == 10.5
    assert total(0, 0.05) == 0

def test_total_rejects_negative():
    with pytest.raises(ValueError):
        total(-1, 0.05)
EOF
git add . && git commit -qm "add tests"

cat > test_prices.py <<'EOF'
import pytest

def total(price, tax):
    return round(price * (1 + tax), 2)

def test_total_adds_tax():
    assert total(10, 0.05) is not None

@pytest.mark.skip(reason="flaky")
def test_total_rejects_negative():
    with pytest.raises(ValueError):
        total(-1, 0.05)
EOF
assertwatch check --worktree
```

```text
AssertWatch: 3 findings (3 block) in 1 test file, dc12bccbc2e7..working tree.

test_prices.py:6  AW002 [block]  `test_total_adds_tax` has 1 assertion(s), down from 2.
test_prices.py:7  AW004 [block]  Assertion weakened in `test_total_adds_tax`: an equality check became a not-None check.
test_prices.py:10  AW003 [block]  `test_total_rejects_negative` is now skipped or disabled: @skip.
```

(Each finding also shows the removed and added lines and how to fix it.) The exit code is
`1`, so the same command can fail a script or a CI job.

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
      - uses: actions/checkout@v7
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
| AW001 | A test or parametrized case removed (renames, moves and splits are matched; removing a test that was always skipped is fine) |
| AW002 | A test that lost assertions |
| AW003 | A new skip, skipif, xfail, skip call or early return |
| AW004 | An assertion replaced by a weaker one (`==` to `is not None`, broad exceptions, wider tolerance) |
| AW005 | An expected value rewritten (`== 10.05` to `== 10`, a parametrized expected value). Blocks when no production code changed; a warning when it did |
| AW006 | The code under test mocked: a test now patches or stubs a function it calls itself and used to call for real (`mocker.patch("shop.total", return_value=4.0)` then `total()`; Java `when(cart.total()).thenReturn(4.0)`), so it checks the mock |
| AW007 | A check wrapped so its failure is ignored: `try`/`except AssertionError` (or `Exception`, a bare `except`, `contextlib.suppress`) that neither re-raises nor fails; Java `catch (AssertionError \| Throwable \| Error ...)` |
| AW008 | A test body emptied |
| AW009 | Test config or the guard loosened (`--deselect`, lower `fail_under`, `-DskipTests`, Surefire/Gradle excludes, lower JaCoCo minimum, hook removed, ...) |
| AW011 | An assertion that can never fail (`assert True`, `x == x`) |

**Java (in progress).** Test files named like Maven Surefire's and Failsafe's defaults
(`*Test.java`, `Test*.java`, `*Tests.java`, `*TestCase.java`, `*IT.java`) are read with a
small built-in tokenizer, so Java needs no extra install. JUnit 5 and 4 tests (`@Test`,
`@ParameterizedTest`, `@RepeatedTest`, JUnit 3 `TestCase` methods) and JUnit, AssertJ,
Hamcrest and Mockito `verify` checks (and JUnit 4's `@Test(expected = ...)`) get
AW001-AW008 and AW011, like Python. A test that calls a helper method of its class (or
of an enclosing class) is credited with the helper's checks, as in Python. Skips:
`@Disabled`, `@Ignore`, `@DisabledIf*`/`@EnabledIf*` and the other conditions, and
`assumeTrue`-style calls. Parametrized cases are counted for `@ValueSource`, `@CsvSource`
(including text blocks) and `@NullSource`/`@EmptySource`. Maven and Gradle config (AW009):
`pom.xml` and Maven `settings.xml` (Surefire/Failsafe skips, also through a property or a
profile that turns on by itself, including on a CI environment variable or any condition
AssertWatch doesn't model, `testFailureIgnore`, excludes and excluded engines, narrowed
includes or `groups`, another test source folder, a module dropped while its tests stay,
JaCoCo limits lowered), every `*.gradle(.kts)` script (`ignoreFailures`, also set through
`setProperty` or a quoted name, a disabled or
`onlyIf` test task, `exclude`, `excludeTestsMatching`, `excludeTags`, test sources emptied,
`check` no longer depending on `test`, a subproject dropped from settings, JaCoCo `minimum`
lowered), `.mvn/maven.config`, and `mvn`/`gradle` runs in GitHub workflows, other CI files
and shell scripts (`-DskipTests`, `-Dmaven.test.failure.ignore`, `-Dgroups`, an excludes file, `-x test`,
Maven's `-N`/`-pl`/`-rf` and Gradle's `-m` dry run, a run that no longer tests, `if: false`, a test workflow deleted or no longer run on push or pull
request). Most Java corpus cases are written for the purpose, not modeled on real commits.
On the last 300 test-touching commits of commons-lang, jsoup and jackson-core (900), 54
block; an estimated 2.7% of commits are blocked wrongly (28 of the 54 blocks labelled, the
rest estimated from a sample; Python: about 2.6%), mostly tests moved between files and
rewritten
([`corpus/real-history.md`](corpus/real-history.md#java)). Only what JUnit runs counts as a test: a test
made private, static or non-void, moved into an inner class without `@Nested`, or into a
top-level class whose name Surefire doesn't select, looks removed. Unicode escapes
(`\u0022`) are translated first, as javac does, so they can't hide code in a string.
Checks in code that may never run don't count: a lambda that is stored rather than passed
to a call, an anonymous or local class, an `if (false)` branch. A changed test file AssertWatch can't parse blocks the hook rather than being
skipped, so code the tokenizer can't read can't hide a change.

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

### Who made the change

Every finding names its author, `ai`, `mixed`, `human` or `unknown`, and the evidence, in
all three formats (`author: ai (commit 1a2b3c4d5e6f: Co-Authored-By: Claude ...)`). The
evidence, strongest first:

1. **The Claude Code hook.** Everything since the session's recorded start is `ai`, whoever
   the commits name. Without a recorded start, uncommitted changes are `ai` and other commits
   are `unknown` (an earlier agent session may have made them).
2. **[Agent Trace](https://agent-trace.dev) records**, if the repo keeps them as JSON or JSON
   Lines under `.agent-trace/`: a record for a commit in the range that names the file with an
   `ai` or `mixed` contributor. They are read, never required.
3. **Commit identities and trailers** of the commits in the range that touched the file:
   `Co-Authored-By: Claude` (and Copilot, Codex, Cursor, Devin, Jules, Gemini, Aider, ...),
   "Generated with Claude Code", agent accounts (GitHub's Copilot agent, Cursor's agent) and
   any `[bot]` account except the formatting and dependency bots (pre-commit-ci, Dependabot,
   Renovate). Add your own regexes with `ai_patterns`.
4. A commit with none of these is `human` **only if its author email is listed** in
   `human_authors`. Otherwise it is `unknown`: in CI most agent commits carry no signal at all,
   so a missing trailer is no evidence of a human. A file changed by AI and other commits is
   `mixed`; uncommitted changes outside the hook, or no commit at all, are `unknown`.

Evidence only ever adds AI: a record that claims a human never outweighs a trailer. Routing,
read from the base like the rest of the config, then decides what a blocking finding becomes:

```toml
[routing]
ai = "block"        # default
mixed = "block"
unknown = "block"   # default; see below before setting "warn"
human = "warn"      # default; set "block" to block human changes too
human_authors = ["dev@example.com", "*@team.example.com"]  # emails (globs, any case)
ai_patterns = ["^my-agent <agent@example[.]com>$"]   # optional, added to the defaults
```

With no `human_authors`, nothing is `human`, so every block stays a block: positive evidence
can lower a severity, a missing signal can't. `unknown = "warn"` is an opt-in that is safe
only when every agent in your organization runs the Claude Code hook (or otherwise stamps its
commits reliably), so that an unstamped commit really implies a human.

A routed finding says so (`block lowered to warn by [routing]`), and `--fail-on warn` still
fails on it. Proposed waivers (AW000) are never routed. Anything invalid in `[routing]` (an
unknown value, a misspelled key), or a config file that doesn't parse, means "block" for every
author. A merge commit counts as touching what it changed against its first parent, so edits
made while resolving a merge are attributed to whoever made the merge.

**`human_authors` trusts what commits say about themselves.** Author emails and trailers are
written by whoever makes the commit: an agent that commits under a listed person's email
without a trailer (some setups strip it) reads as `human` in CI, and its weakenings become
warnings. Only the hook's session evidence can't be rewritten by the agent. List only people
whose machines don't let agents commit as them, or leave the list empty.

In short: report-only by default; teams can block, with author-based downgrades where
authorship is provable.

## How well it works

`make eval` replays the labelled corpus in [`corpus/cases/`](corpus/cases): small, complete
changes, each one a cheat that should be caught or a refactor that shouldn't be blocked. The
refactors are modeled on real commits from open-source projects that earlier versions
blocked. Results today: **144 of 152 cheats caught, 1 of 86 refactors blocked** (95% recall,
1% false positives on refactors; 2 more refactors get a warning). That includes 98 Java
cheats and 46 Java refactors; four of the Java cheats are known misses (below). Cases are
committed as an AI agent's change unless they say otherwise; three check routing (a listed
human's weakening warns; the same change by an unlisted author, or with a
`Co-Authored-By: Claude` trailer, blocks).

- **Missed:** a check for exactly two warnings swapped one-for-one for
  `pytest.warns`, which passes on one: the count stays the same and nothing compares the two.
  And a property test whose Hypothesis input range was narrowed. And a test moved to a new
  file (or given a body import) that points at a stand-in for the code under test: a test
  that keeps its name is read as moved, and imports aren't compared. In Java builds: a
  module that starts reading a property already set to `true` in a pom the change doesn't
  touch, CI starting to build with an existing profile that skips tests (`-Pquick`), and
  a test added to an excludes file that an unchanged pom already reads (`excludesFile`):
  AW009 reads only the build and CI files in the change. And a check of a cause's exact type
  (`assertInstanceOf(SocketTimeoutException.class, ...)`) swapped for its supertype
  (`IOException`): the class isn't compared.
- **Blocked by mistake:** a warning checked through a CLI runner's `result.exception`,
  replaced by `pytest.warns(..., match=...)`. Nothing ties `result.exception` to the warning
  the way a `catch_warnings(record=True)` capture is tied, so the dropped checks can't be
  proved to be covered (modeled on the real click commit).
- **Warned:** an intended behavior change, where the code and the expected value change
  together (AW005 asks for confirmation), and a removed test whose identical copy is in a
  file the change doesn't touch. That copy is usually a leftover from an earlier move, but it
  could be a decoy planted earlier, so a human confirms it rather than AssertWatch clearing
  it.

Read these numbers with care. The corpus is small, and most of it was written by the same
people as the rules, after the rules: only 23 of the 152 cheats (and 37 of the 86 refactors)
are modeled on a real commit. Cheats an agent might invent that we haven't thought of
aren't in it. Cases that fail on purpose are marked `known` in their file, and the test
suite fails when one starts passing, so the numbers here stay current.

**On real history**, which nobody wrote for us: we ran the last 300 test-touching commits of
six open-source Python projects (click, more-itertools, attrs, dateutil, markupsafe,
itsdangerous; 1,318 commits). 188 of them (14%) get a finding: 133 a blocking one, 55 only a
warning (a value changed along with the code, or a removed test's copy needs a look). We labelled two random samples of 30
blocked commits by hand ([`corpus/real-history.md`](corpus/real-history.md)):

- **Blocked rightly, about 7.5% of commits.** Mostly deliberate changes a reviewer should
  approve: a feature removed with its tests, a platform skip, changed behavior. Eight were
  real weakenings, including a lint autofix that turned a warning check into one that can
  never pass (the project fixed it later), and a test whose invalid input was swapped for a
  valid one.
- **Blocked wrongly, about 2.6% of commits** (roughly 1.6-4.1%): tests rewritten past
  recognition or with generated inputs, parametrization moved into fixtures, version tests
  removed in the commit that drops the version. Labelling found seven patterns a rule could
  fix, and all seven are fixed; what's left needs an understanding of the code.

A test may lose assertions without checking less: a manual warning check replaced by
`pytest.warns(..., match=...)`, `is not None` dropped next to an exact `== 10.5`, or Python 2
branches removed once the project (as declared on the base branch) no longer supports it.
Those aren't reported; anything AssertWatch can't prove is still guaranteed is.

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
make history                                         # replay real commits (network, once)
assertwatch --version
```

With [uv](https://docs.astral.sh/uv/): `uvx --from . assertwatch --version`.

## License

[Apache-2.0](LICENSE). Contributions are accepted under the same license. The license doesn't
grant use of the AssertWatch name or logo.
