# Blocked real-world commits, labelled

The corpus in `cases/` was written by the same people as the rules. This file is the check
that wasn't: blocking findings on commits from other people's projects, labelled one by one.

**Method.** The last 300 non-merge commits touching `*test*.py` in click, more-itertools,
attrs, dateutil, markupsafe and itsdangerous (1,318 commits), each checked as `sha^..sha`.
`make history` replays them (`scripts/real_history.py`; the projects and starting commits
are pinned in `scripts/real_history.toml`, clones go to the gitignored `.cache/`).
At commit 29d183d, 145 of them got a blocking finding. 30 were drawn at random
(`random.seed(20261001)`) and labelled by reading the diff:

- **intended**: a deliberate change a reviewer should see and approve: a feature removed with
  its tests, a platform skip, changed behavior. Blocking is right; the human says yes.
- **weakening**: the test checks less than before and nothing replaced it. Blocking is right.
- **false positive**: nothing was lost; AssertWatch misread a restructure.

**Result.** 12 intended, 4 weakenings, 14 false positives. Five of those 14 have been fixed
since (marked *fixed*); the fixes removed 10 blocks across the full 1,318 (all refactors,
checked) and added none. Now 135 commits block. 25 of the sample still do, and 9 of those
are false positives (95% interval about 20-55%), so about 3.7% of these test-touching
commits are blocked wrongly (between roughly 2% and 6%), and about 6.6% are blocked rightly.

Dead Python 2 code is ignored only when the base declares no Python 2 support, so 2 stays
blocked: that commit is the one that drops Python 2, and its base still declares it.

| # | Project | Commit | Rule | Label | What happened |
|---|---|---|---|---|---|
| 1 | more-itertools | `899b63c4c6` | AW001 | intended | Deprecated `collate()` removed with its tests |
| 2 | attrs | `980c8b04f5` | AW002 | false positive | Assertions under `if PY2:` dropped with Python 2 support |
| 3 | attrs | `b02335f59d` | AW009 | false positive, *fixed* | A test file added in the same commit, ignored on Python < 3.6 |
| 4 | dateutil | `a1825cbfc8` | AW003 | intended | Test now skipped unless an env var allows changing the time zone, so it no longer runs by default |
| 5 | dateutil | `48bd1af97e` | AW003 | intended | xfail on Windows only |
| 6 | click | `51736b9566` | AW001 | intended | Deprecated parameter removed with its test |
| 7 | click | `2ba2fe0cf8` | AW002 | weakening | Five exact checks replaced by one assertion in a loop that passes if no line matches |
| 8 | more-itertools | `45b6bf81e1` | AW001 | intended | `derangements_by_value` removed with its tests |
| 9 | click | `2fa500c143` | AW005 | weakening | Tests-only change: the invalid-bytes filename input replaced by an already-decoded string |
| 10 | click | `44ce9b8e4c` | AW002 | false positive, *fixed* | `Path(a) == Path(b)` dropped next to the plain `a == b` it follows from |
| 11 | click | `955ca492a6` | AW001 | false positive | Two tests merged into one parametrized test with a new name and more cases |
| 12 | more-itertools | `e39ae39c0a` | AW001 | intended | `distinct_derangements` removed; the remaining tests consolidated |
| 13 | dateutil | `7b242e2874` | AW004 | intended | Warning type changed to a specific class, message match dropped, code changed with it |
| 14 | attrs | `bde0801987` | AW011 | false positive | `e2 == e2` in an equality test (known AW011 limit) |
| 15 | more-itertools | `6063f08941` | AW001 | intended | `strict=` parameter removed with its tests |
| 16 | attrs | `01aa333005` | AW001 | intended | A warning removed from the code, with its test |
| 17 | more-itertools | `f5774b65f7` | AW001 | weakening | Exception-chaining checks dropped as "over-specified" (deliberate, but less is checked) |
| 18 | attrs | `61e8179545` | AW009 | false positive, *fixed* | conftest moved into `tests/` (its ignore paths rewritten) and a new file ignored before 3.14 |
| 19 | click | `662a30e4ee` | AW002 | false positive | Assertion inside the invoked command replaced by a stronger check on its return value |
| 20 | dateutil | `34906dcd41` | AW001 | false positive | A case left an xfail test because it passes now |
| 21 | more-itertools | `b594a14f3c` | AW004 | weakening | A lint autofix turned `category == DeprecationWarning` into an `isinstance` check that is never true; upstream fixed it later (`a4b0ad1`) |
| 22 | dateutil | `cf67d0c976` | AW003 | false positive | unittest to pytest: Windows-only imports moved into `skipif` tests |
| 23 | dateutil | `75ae19924e` | AW001 | false positive, *fixed* | `testTzOffsetWeakRef` became `test_tzoffset_weakref` and was folded into the wrong test |
| 24 | dateutil | `a46912f654` | AW002 | intended | PEP 495 changed equality, so inequality checks went with the code change |
| 25 | click | `589ca314ae` | AW001 | false positive | Test moved to another file and rewritten for the new output |
| 26 | more-itertools | `5c715d813a` | AW011 | false positive | A no-op `assertTrue(x, x)` fixed to `assertEqual(x, x)` (known AW011 limit) |
| 27 | click | `b7e5fd4cc7` | AW001 | intended | Test for a reverted fix removed along with the fix |
| 28 | dateutil | `9d2edc0e17` | AW001 | intended | An input is accepted now: its error case removed, a new test checks it parses |
| 29 | click | `57af071b30` | AW002 | false positive, *fixed* | Five checks folded into the test's own parametrize |
| 30 | dateutil | `60ff4aaaf1` | AW001 | false positive | Converted to a stronger parametrized test with reworded inputs |

**What the remaining false positives have in common:** a test rewritten so much that
nothing ties it to the old one (11, 25, 30), a check made stricter in a form the rules don't
compare (19), dead Python 2 branches removed in the commit that drops Python 2 (2), equality tests checking reflexivity (14, 26), an
xfail case that started passing (20), and a unittest-to-pytest conversion that moved
assertions into new skipped tests (22). Each is a block a reviewer clears in seconds, but it
is still a block.

## Second sample

**Method.** At commit 69f4be6, 135 commits block. Leaving out the 30 above, 30 of the other
110 were drawn at random (`random.seed(20261003)`) and labelled the same way.

**Result.** 17 intended, 4 weakenings, 9 false positives. Two of the false-positive patterns
are fixable and are in the corpus as known cases (`refactor-32`, `refactor-33`); one weakening
showed a cheat nothing detects yet (`cheat-39`).

| # | Project | Commit | Rule | Label | What happened |
|---|---|---|---|---|---|
| 1 | click | `b761eda3ba` | AW003 | intended | Pager tests skipped on Windows |
| 2 | dateutil | `38a5cd8486` | AW005 | intended | Tests-only fix of a test input (`GMT` to `GMT0`) |
| 3 | click | `1103c5cac2` | AW001 | false positive | Leftover duplicate removed; an earlier commit had copied it to a file this one doesn't touch |
| 4 | more-itertools | `d766332e40` | AW001 | weakening | Tests-only: the `RuntimeError` check on `iterate` removed, nothing replaces it |
| 5 | click | `0e0c00324a` | AW002 | intended | `mix_stderr` removed; its checks go with it |
| 6 | attrs | `c422bf8041` | AW001 | intended | Deprecated `Attribute.cmp` removed with its test |
| 7 | more-itertools | `28f5e8f142` | AW001 | intended | `derangements` and `distinct_derangements` removed with their tests |
| 8 | attrs | `f580185cc4` | AW001 | intended | `cmp` undeprecated: its deprecation tests removed |
| 9 | click | `b64ea07128` | AW001 | intended | Bad defaults now fail at invocation, not definition; tests rewritten and renamed |
| 10 | dateutil | `22715e30d1` | AW001 | false positive | Tests replaced by parametrized ones that generate their inputs; also folded into an unchanged parametrized test and reported as 4 cases short |
| 11 | attrs | `9b98a730bf` | AW001 | false positive | Pre-3.10 test removed in the commit that drops 3.9 (its base still declares 3.9) |
| 12 | attrs | `f10d050864` | AW001 | intended | Recursive `evolve` reverted with its tests |
| 13 | more-itertools | `4bd0bd8762` | AW001 | false positive | Tests consolidated; iterator, unsortable and unhashable inputs still covered |
| 14 | click | `39413d9b1d` | AW001 | false positive | Test rewritten as a parametrized test with more cases and new values |
| 15 | click | `1557e26522` | AW002 | false positive | `isinstance(result.exception, UserWarning)` and its message check become `pytest.warns(match=...)` (refactor-13 models a simpler form) |
| 16 | dateutil | `6b535072b4` | AW001 | weakening | Moving property tests dropped the negative-year check and narrowed another test's input range |
| 17 | attrs | `22ae8473fb` | AW002 | intended | `assoc` stops warning: `deprecated_call` checks removed |
| 18 | more-itertools | `0d5262795d` | AW004 | intended | `too_short` called once: the exception check gives way to a call-count check |
| 19 | click | `80efdf6c10` | AW002 | intended | `CliRunner` raises at end of input, so `not result.exception` goes |
| 20 | dateutil | `0853d611cd` | AW001 | false positive | Removed test asserted nothing and called nothing; its renamed replacement checks the error |
| 21 | click | `3b06e0b75a` | AW001 | weakening | Pager tests rewritten; the trailing-whitespace `PAGER` variants dropped |
| 22 | dateutil | `435d93be47` | AW001 | intended | Time-only check moved into a class skipped unless the time zone can be changed |
| 23 | attrs | `8c9a79680f` | AW003 | intended | Match-args tests limited to 3.10+, where the behavior exists |
| 24 | click | `777a89e232` | AW009 | intended | Stress tests deselected by default and run in their own CI job |
| 25 | click | `bc21607df9` | AW001 | intended | Help-padding change reverted with its tests |
| 26 | attrs | `78cb7d4903` | AW001 | false positive | Test renamed and split in two |
| 27 | attrs | `a18b3957d5` | AW003 | intended | Annotation tests limited to 3.11+, whose output changed |
| 28 | markupsafe | `7063a31836` | AW001 | false positive | Module parametrization moved into a parametrized conftest fixture: same 20 runs, counted as 10 |
| 29 | more-itertools | `ddd678209d` | AW001 | intended | `random_ordered_*` API thinned; removed functions' tests go |
| 30 | dateutil | `0f8f239e69` | AW002 | weakening | Python 3.6 fix drops the "no cache problem" checks |

**Both samples together.** The 25 commits of the first sample that still block are labelled
(9 false positives); the second sample stands for the other 110 (9 of 30). That is about 42
commits blocked wrongly, **about 3.2% of the 1,318** (95% interval roughly 2.1-4.7%), and about
7.1% blocked rightly. Eight real weakenings were found across the 60 labels.

**Fixable patterns from this sample:** a leftover duplicate whose copy is in an unchanged file
(3; needs a lookup outside the diff), and a removed test that checks and calls nothing (20).
Also a bug that doesn't change a verdict: removed tests folded into an unchanged
parametrized test get a misleading "fewer cases" finding (10). **Not fixable without
understanding the code:** tests rewritten with new values or generated inputs (10, 13, 14,
26), parametrization moved into fixtures (28), version tests removed in the commit that
drops the version (11), and a warning checked through `result.exception` (15).

## P0 close (rate after the last fixes)

Three fixes from the second sample, then P0 stops here whatever the rate:

- A removed test whose identical copy (same body apart from imports, markers and cases) is in
  a test file the change doesn't touch is not reported. The copy is found with `git grep` at
  the head.
- A removed test that can never fail is not reported: every statement is a no-op or binds a
  name to a value computed from literals alone, or from parametrize cases listed as literals,
  evaluated case by case.
- A plain test is no longer folded into a parametrized test whose cases didn't change.
  Shared literal values there were a coincidence (dateutil `22715e30d1` was reported as
  "60 cases, down from 64"; it now lists the removed tests).

**Replay** (`python scripts/real_history.py`, fresh clones of the pinned commits): 135 -> 133
blocking. Cleared: click `1103c5cac2` (#3), click `39413d9b1d` (#14: its removed test also had
an identical copy in the untouched `tests/test_options.py`), dateutil `0853d611cd` (#20).
Newly blocked: dateutil `75ae19924e`, labelled **false positive**. A unittest weakref test
was rewritten as a pytest function with `weakref.ref`, past recognition. The old code passed
it only by folding it into three unchanged parametrized tests that share the string
`'EST5EDT'`, the coincidence the third fix removes.

**Result.** The first sample is unchanged (25 blocking, 9 false positives). Of the second
sample, 27 still block, 6 of them false positives, and they stand for the 107 commits of
that population that still block; plus the one new false positive. That is about 34
commits blocked wrongly, **about 2.6% of the 1,318** (95% interval roughly 1.6-4.1%), and
about 7.5% blocked rightly. Same estimate as above; it reproduces the earlier 3.2% from the
earlier labels.

**Left, all needing an understanding of the code rather than another rule:** tests rewritten
with new values, generated inputs or a new mechanism (#10, #13, #26, `75ae19924e`),
parametrization moved into conftest fixtures (#28), version tests removed in the commit that
drops the version (#11), and a warning checked through `result.exception` (#15, corpus
`refactor-34`, known). These are human commits. With author routing (P3), a human change
gets a warning by default, so the rate that matters is wrong blocks on agent-authored
changes, which this sample can't measure.
