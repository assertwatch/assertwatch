# Blocked real-world commits, labelled

The corpus in `cases/` was written by the same people as the rules. This file is the check
that wasn't: blocking findings on commits from other people's projects, labelled one by one.

**Method.** The last 300 non-merge commits touching `*test*.py` in click, more-itertools,
attrs, dateutil, markupsafe and itsdangerous (1,318 commits), each checked as `sha^..sha`.
At commit 29d183d, 145 of them got a blocking finding. 30 were drawn at random
(`random.seed(20261001)`) and labelled by reading the diff:

- **intended**: a deliberate change a reviewer should see and approve: a feature removed with
  its tests, a platform skip, changed behavior. Blocking is right; the human says yes.
- **weakening**: the test checks less than before and nothing replaced it. Blocking is right.
- **false positive**: nothing was lost; AssertWatch misread a restructure.

**Result.** 12 intended, 4 weakenings, 14 false positives. Four of those 14 were fixed after
labelling (marked *fixed*), and the fixes removed 9 blocks across the full 1,318 (all
refactors, checked) and added none. Now 136 commits block. 26 of the sample still do, and 10
of those are false positives (95% interval about 22-58%), so about 4% of these test-touching
commits are blocked wrongly (between roughly 2% and 6%), and about 6% are blocked rightly.

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
| 10 | click | `44ce9b8e4c` | AW002 | false positive | `Path(a) == Path(b)` rewritten as a plain string comparison |
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
compare (10, 19), dead version branches (2), equality tests checking reflexivity (14, 26), an
xfail case that started passing (20), and a unittest-to-pytest conversion that moved
assertions into new skipped tests (22). Each is a block a reviewer clears in seconds, but it
is still a block.
