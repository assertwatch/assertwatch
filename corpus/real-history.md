# Blocked real-world commits, labelled

The corpus in `cases/` was written by the same people as the rules. This file is the check
that wasn't: blocking findings on commits from other people's projects, labelled one by one.

## Current rates

Every figure below is a share of **all** replayed commits, not of the blocked ones. Blocked
splits into blocked rightly (intended or weakening) and blocked wrongly (false positive).
Not every blocked commit is labelled: the labelled ones are counted as labelled, and the
false-positive share of a random sample stands in for the rest. The interval is a 95% Wilson
interval on that sample share, carried to the unlabelled commits; it covers sampling only,
not labelling mistakes. All labels so far were made by one reader, the same agent that wrote
the rules, and are being spot-checked by a human.

| | Python | Java |
|---|---|---|
| Commits replayed | 1,318 | 900 |
| Blocked | 133 (10.1%) | 53 (5.9%) |
| Labelled of those | 53 (16 false positives) | 27 (10 false positives) |
| Unlabelled, estimated from | 80, at 6 of 27 (second sample) | 26, at 9 of 16 (first sample) |
| **Blocked wrongly** | **about 34, 2.6% (1.9-3.7%)** | **about 25, 2.7% (2.1-3.3%)** |
| Blocked rightly | about 99, 7.5% | about 28, 3.2% |

As of 6fedf88. The Java interval quoted further down (2.4-3.1%) was too narrow, and the
Python one (1.6-4.1%) used different arithmetic; this table replaces both. P0 rule work on
the wrong-block rate is closed at these numbers: what remains needs an understanding of the
code (tests moved and rewritten), and the plan is to route human-authored changes to a
warning (P3) rather than chase it with more rules.

## Python

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

**Follow-up (security review).** Clearing a removed test because a copy exists outside the
diff let a two-step cheat through: plant an identical-looking copy (importing a stand-in, or
in a file that isn't collected), then delete the real test later. Such a removal is now an
AW001 **warning** naming the copy, for a human to confirm (`cheat-44`). Duplicates also stop
ignoring `from x import y` and aliased imports in the body. Replay: still 133 blocking; click
`1103c5cac2` and `39413d9b1d` go from no finding to that warning. A related gap predates P0
and stays known: a test that keeps its name when moved is paired as a move and its imports
aren't compared, so a body or module import of a stand-in goes unnoticed (`cheat-45`,
`cheat-46`).

## Java

**Method.** The last 300 non-merge commits touching `*Test*.java` or `*IT.java` in
commons-lang, jsoup and jackson-core (900 commits), pinned in `scripts/real_history.toml`
(`lang = "java"`; `python scripts/real_history.py out.json --lang java`), each checked as
`sha^..sha`. At commit 6a24b31, 59 blocked and 6 jsoup commits crashed the check (see below).
30 of the 59 were drawn at random (`random.seed(20261004)`) and labelled like the Python
samples.

**Result.** 6 intended, 2 weakenings, 22 false positives: Java blocks wrongly far more often
than Python (14 of 30 in the first Python sample). Three of the 22 are fixed (*fixed*).

| # | Project | Commit | Rule | Label | What happened |
|---|---|---|---|---|---|
| 1 | jackson-core | `3155455f2f` | AW001 | false positive | `@Test` dropped and the class made to extend `BaseTest`, a JUnit 3 `TestCase`: the `test*` methods still run |
| 2 | commons-lang | `8bb9dfccb4` | AW002 | false positive | Six `assertNull` calls became one `@ParameterizedTest` with nine values |
| 3 | jsoup | `bf13b496cd` | AW005 | false positive | Tests-only: an attribute added to the input and to the expected output |
| 4 | jackson-core | `797d509fe0` | AW002 | false positive | Checks moved into a private helper of the test class |
| 5 | jackson-core | `ff20a92732` | AW002 | false positive | Checks moved into a helper (`_verifyOneClosedOutput`), which also checks the close count |
| 6 | jackson-core | `6c7daa05dd` | AW001 | false positive | JUnit 4 to 5: `testFoo` renamed `foo`, `@Parameterized` runner became `@MethodSource` |
| 7 | jsoup | `582aaa0d72` | AW002 | intended | Attribute matching stops trimming values; the expected matches change with the code |
| 8 | jackson-core | `327d2ff128` | AW002 | intended | A `@JacksonTestFailureExpected` test fixed; expected output changed with the code |
| 9 | jsoup | `bbb56af691` | AW002 | false positive, *fixed* | try/catch with a `threw` flag became `X ex = assertThrows(...)` (an assigned check wasn't counted), parametrized over two queries |
| 10 | jsoup | `b5761608fc` | AW002 | intended | `isNode` replaced by `nameIs` in the code; two checks of the old API become one |
| 11 | jsoup | `9b455ae355` | AW002 | weakening | Timeout tests: the upper bound on the elapsed time dropped (deliberate, against flakiness) |
| 12 | commons-lang | `fa2470369b` | AW001 | intended | A bad cached hash is now recomputed instead of rejected; the test changes with it |
| 13 | jsoup | `209151fddc` | AW001 | false positive | Test moved to the new `NamespaceBindingsTest` and rewritten for the extracted class |
| 14 | jackson-core | `11e3bd7c0a` | AW011 | false positive | `assertTrue(unk.equals(unk))` became `assertEquals(unk, unk)`: the same reflexivity check |
| 15 | jsoup | `e975f4f508` | AW001 | false positive | Network test rewritten as a unit test in another file |
| 16 | jsoup | `7707efccde` | AW001 | intended | `TokenQueue.addFirst` now throws; its test goes |
| 17 | jackson-core | `b2c1e70ede` | AW002 | false positive | Loop body moved into a private helper |
| 18 | jackson-core | `3dcca23cf4` | AW009 | false positive | `continue-on-error` on a step that downloads a coverage report, not one that runs tests |
| 19 | jsoup | `7e9160110f` | AW002 | false positive | Several structural checks replaced by one exact comparison of the whole parse tree |
| 20 | commons-lang | `a593c2f5c1` | AW002 | false positive | A timing check replaced by a Mockito `mocked.verify(...)` of the call |
| 21 | jackson-core | `55261befb1` | AW001 | false positive | Renamed `looksLikeValidNumberTrue` with more cases |
| 22 | jsoup | `f9f34df1a1` | AW003 | intended | Slow fuzz integration tests `@Disabled`, to run by hand |
| 23 | jsoup | `e0019ebf33` | AW004 | false positive, *fixed* | `assertThrows(...)` became `X e = assertThrows(...)` plus a message check |
| 24 | jsoup | `af7999df52` | AW001 | false positive | Test moved to `QuietAppendableTest` under the same name, with more checks |
| 25 | jackson-core | `67f2d5fea8` | AW001 | false positive | Test file replaced by a larger one covering the same failure |
| 26 | jsoup | `c778c3aea7` | AW005 | weakening | Progress-count tolerance widened from 1.25 to 1.5 (deliberate, against flakiness) |
| 27 | jsoup | `c1d1945a97` | AW001 | false positive | A test that was already `@Disabled` removed: nothing that ran is lost |
| 28 | jsoup | `9d2241ff46` | AW002 | false positive, *fixed* | try/catch with a `threw` flag became an assigned `assertThrows`; message changed with the code |
| 29 | jsoup | `05f08bb64d` | AW011 | false positive | A class-name string check became `assertInstanceOf(X.class, ...)`, read as a presence check |
| 30 | commons-lang | `8a28feb133` | AW011 | false positive | `assertTrue(sb1.equals(sb1))` became `assertEquals(sb1, sb1)` (as 14) |

**Fixed with this sample.** A check whose result is kept (`X e = assertThrows(...)`,
`e = ...`) counted as nothing: besides the false positives, broadening it
(`Exception e = assertThrows(Exception.class, ...)`) went unseen (`cheat-j22`,
`refactor-j12`). A `😀` escape pair was translated into two lone surrogates that
crashed the check (`refactor-j11`), and duplicate detection read Java bodies as Python, so
`'a'` (a char) and `"a"` (a String) matched (`cheat-j21`).

**Replay after the fixes:** 59 -> 62 blocking. Cleared: #9, #23, #28 and `63ed60b84c` (the
same try/catch rewrite, checked). Newly blocked, all labelled right: the six commits that
crashed (jsoup `be8c37562c` CDATA parsing changed, `9d15c00daf` old network tests removed,
`959994d5c4` deprecated option's test removed, `0ef4b70b2a` and `92f1aca552` tag-name parsing
changed, `ccd90e9cfc` `containsIgnoreCase` removed, all intended) and `9851ac5d9c`, a
**weakening**: an `X e = assertThrows(...)` replaced by a try/catch that passes whether or not
it throws.

**Rate.** 27 of the sample still block, 19 of them false positives; they stand for the 28
unsampled commits that still block. That is about 39 commits blocked wrongly, **about 4.3% of
the 900** (roughly 3.7-4.7%), and about 2.6% blocked rightly. Python is at about 2.6% wrong.

**Fixable patterns, by count:** checks moved into a helper of the test class (4, 5, 17; Python
already credits same-file helpers), a reflexive `equals` check rewritten as `assertEquals(a, a)`
(14, 30), JUnit 4 to 5 renames dropping the `test` prefix (6) and JUnit 3 tests through an
inherited `TestCase` (1), a test made parametrized with more values (2), a removed test that
was already disabled (27), `assertInstanceOf` read as a presence check (29), Mockito
`mocked.verify` not counted (20), and AW009 flagging `continue-on-error` on a step that runs no
tests (18). **Not fixable without understanding the code:** tests moved to another file and
rewritten (13, 15, 24, 25), a stricter check in another form (19), a rename with new cases
(21), and an input and its expected output extended together (3).

### Java false-positive fixes

**Fixed.** Checks in helper methods of the test class (or an enclosing class) are credited to
the tests that call them, as in Python, with overloads told apart by argument count and by
argument types that are plain to see (4, 5, 17; `refactor-j13`, `cheat-j23`). A helper's checks
stop counting against a test when the helper leaves the file (a base class, a utils class) or
becomes a test of its own; a helper gutted in place still blocks (`refactor-j20`, `refactor-j21`,
`cheat-j30`). `assertTrue(a.equals(a))` is a self-comparison like `assertEquals(a, a)` (14, 30;
`refactor-j14`, `cheat-j24`). `testFoo` and `foo` in the same class are the same test, and
JUnit 4's `@Test(expected = X.class)` is an expected-exception check, so broadening it is caught
(6; `refactor-j15`, `cheat-j25`). Removing a test that was always skipped loses nothing, in both
languages (27; `refactor-j16`, `cheat-j26`). A class-name check made instead as
`assertInstanceOf` on the same class (29; `refactor-j17`, `cheat-j27`). Mockito's
`mocked.verify(() -> ...)` on a `MockedStatic` (20; `refactor-j18`, `cheat-j28`). AW009 counts
`continue-on-error` only on the step or job that runs tests (18; `refactor-j19`, `cheat-j29`).
Calls named `verifyXxx(...)` count as checks, like `assertXxx(...)` (jackson's `verifyException`).

**Replay after the fixes:** Java 62 -> 53 blocking; Python unchanged at 133. Of the labelled
sample, 16 still block: 9 false positives (1, 2, 3, 13, 15, 19, 21, 24, 25), 5 intended and the
2 weakenings. Cleared: the 10 sampled false positives fixed above, #8 (intended: a
`@JacksonTestFailureExpected` test fixed, its checks moved into a helper) and, outside the
sample, `9d15c00daf` (a `@Disabled` network test class removed), `4fb17b7e97`, `ff20a92732`,
`797d509fe0`, `b2c1e70ede` (helpers) and `78383995e7` (now AW005 only). Newly blocked, labelled:

| Project | Commit | Rule | Label | What happened |
|---|---|---|---|---|
| jackson-core | `bbfe0d2073` | AW002 | intended | One of two calls to a checking helper removed: one input no longer tested |
| jackson-core | `42f94c4435` | AW002 | intended | A fixed case dropped from the expected-to-fail tests (`@JacksonTestFailureExpected`) |
| jackson-core | `7d81ac82d6` | AW002, AW005 | intended | `@Disabled` tests enabled again with new expected messages |
| jackson-core | `034171bd24` | AW002 | intended | `getNumberType()` now returns null for non-numbers; its checks change with the code |
| jackson-core | `c4f79cef41` | AW002 | false positive | Two `verifyException` message checks merged into one that accepts either of two messages |

**Rate.** 9 of the 16 sampled commits that still block are false positives; they stand for the
26 unsampled commits of the first run that still block (about 15 wrong). The 6 commits that
newly blocked in the last run and still block are right, and 1 of the 5 above is wrong. That is
about 25 commits blocked wrongly, **about 2.8% of the 900** (roughly 2.4-3.1%), down from 4.3%,
and about 3.1% blocked rightly. Python stays at about 2.6%.

**Still open:** tests moved to another file and rewritten (13, 15, 24, 25), a stricter check in
another form (19), a rename with new cases (21), an input and its expected output extended
together (3), a test made parametrized with more values (2), and JUnit 3 tests through an
inherited `TestCase` (1), which needs another file to see.

## AW007 (swallowed checks)

**Replay** at the AW007 commit: no change. Python 133 and Java 53 blocking, the same commits
and findings as 6fedf88; AW007 fires on none of the 2,218 commits. The real commits that add
`catch (Throwable ...)` or `except Exception` around checks add them in new tests, which AW007
doesn't read (it compares a test with its base version). One of those is a broken new test:
commons-lang `9f89fd462` puts `fail(...)` inside a `try` whose `catch (Throwable expected)`
catches that failure too, so the test can't fail. Reporting that shape in new tests is a
possible later warning.
