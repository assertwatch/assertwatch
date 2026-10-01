from textwrap import dedent

import pytest

from assertwatch.extract.python import extract
from assertwatch.rules import all_rules, run_rules

PATH = "tests/test_x.py"


def run(before, after, path_map=None):
    """before/after: source for PATH, or a {path: source} dict for several files."""
    sides = []
    for side in (before, after):
        files = side if isinstance(side, dict) else {PATH: side}
        sides.append([extract(dedent(src), path) for path, src in files.items()])
    return run_rules(*sides, path_map=path_map)


def rules(before, after, path_map=None):
    return sorted(f.rule for f in run(before, after, path_map))


REFUND = """
def test_refund_rounding():
    order = make_order(total=10.05)
    refund = issue_refund(order, reason="damaged")
    assert refund.amount == 10.05
    assert refund.currency == "USD"
    assert order.status == "refunded"
"""


def test_rule_registry_is_complete_and_unique():
    ids = [r.id for r in all_rules()]
    assert ids == ["AW000", "AW001", "AW002", "AW003", "AW004", "AW008", "AW009", "AW011"]
    assert all(r.title and r.summary for r in all_rules())


def test_unchanged_and_added_tests_are_clean():
    assert rules(REFUND, REFUND) == []
    added = REFUND + "\ndef test_more():\n    assert refund_policy() is not None\n"
    assert rules(REFUND, added) == []
    more_asserts = REFUND + "    assert order.refunds == [refund]\n"
    assert rules(REFUND, more_asserts) == []


# --- AW001 ------------------------------------------------------------------------------


def test_aw001_removed_test():
    found = run(REFUND + "\ndef test_other():\n    assert other() == 1\n", REFUND)
    assert [(f.rule, f.test_id) for f in found] == [("AW001", f"{PATH}::test_other")]
    assert found[0].before == "assert other() == 1"


def test_aw001_failing_test_swapped_for_an_easier_one():
    easier = """
    def test_refund_basic():
        refund = issue_refund(make_order(total=10), reason="damaged")
        assert refund
    """
    assert rules(REFUND, easier) == ["AW001"]


def test_aw001_collection_disabled():
    disabled = "__test__ = False\n" + REFUND
    assert rules(REFUND, disabled) == ["AW001"]


@pytest.mark.parametrize(
    "after",
    [
        REFUND.replace("test_refund_rounding", "test_refund_rounds_to_cents"),
        # renamed and lightly edited
        REFUND.replace("test_refund_rounding", "test_rounding").replace("damaged", "broken"),
        # moved into a class
        "class TestRefunds:\n"
        + "\n".join("    " + line for line in dedent(REFUND).replace("():", "(self):").split("\n")),
    ],
    ids=["renamed", "renamed-and-edited", "moved-into-class"],
)
def test_aw001_rename_or_move_is_not_a_removal(after):
    assert rules(REFUND, after) == []


def test_aw001_move_to_another_file():
    assert rules({PATH: REFUND, "tests/test_y.py": ""}, {PATH: "", "tests/test_y.py": REFUND}) == []


def test_aw001_file_rename_keeps_ids():
    new_path = "tests/refunds/test_refund.py"
    assert run({PATH: REFUND}, {new_path: REFUND}, path_map={PATH: new_path}) == []
    # Rewritten beyond rename matching: the id still pairs it, so the drop is AW002.
    rewritten = "def test_refund_rounding():\n    assert refund_for(10.05) == 10.05\n"
    found = run({PATH: REFUND}, {new_path: rewritten}, path_map={PATH: new_path})
    assert [(f.rule, f.test_id) for f in found] == [("AW002", f"{new_path}::test_refund_rounding")]


def test_aw001_removing_an_empty_placeholder_is_fine():
    placeholder = '\ndef test_todo():\n    """Write me."""\n'
    assert rules(REFUND + placeholder, REFUND) == []


def test_aw001_split_into_smaller_tests():
    split = """
    def test_refund_amount():
        order = make_order(total=10.05)
        refund = issue_refund(order, reason="damaged")
        assert refund.amount == 10.05
        assert refund.currency == "USD"

    def test_refund_marks_order():
        order = make_order(total=10.05)
        issue_refund(order, reason="damaged")
        assert order.status == "refunded"
    """
    assert rules(REFUND, split) == []


def test_aw001_split_that_loses_an_assertion():
    split = """
    def test_refund_amount():
        refund = issue_refund(make_order(total=10.05), reason="damaged")
        assert refund.amount == 10.05

    def test_refund_currency():
        refund = issue_refund(make_order(total=10.05), reason="damaged")
        assert refund.currency == "USD"
    """
    assert rules(REFUND, split) == ["AW001"]


PLAIN = """
def test_add_small():
    assert add(1, 2) == 3

def test_add_negative():
    assert add(-1, -2) == -3

def test_add_zero():
    assert add(0, 0) == 0
"""


def test_aw001_merged_into_parametrize():
    merged = """
    import pytest

    @pytest.mark.parametrize(("a", "b", "expected"), [(1, 2, 3), (-1, -2, -3), (0, 0, 0)])
    def test_add(a, b, expected):
        assert add(a, b) == expected
    """
    assert rules(PLAIN, merged) == []


def test_aw001_merged_into_parametrize_with_a_case_dropped():
    merged = """
    import pytest

    @pytest.mark.parametrize(("a", "b", "expected"), [(1, 2, 3), (0, 0, 0)])
    def test_add(a, b, expected):
        assert add(a, b) == expected
    """
    # test_add_negative's values are gone, so it can't be folded in
    found = run(PLAIN, merged)
    assert [(f.rule, f.test_id) for f in found] == [("AW001", f"{PATH}::test_add_negative")]


def test_aw001_merged_with_too_few_cases():
    merged = """
    import pytest

    @pytest.mark.parametrize(("a", "b", "expected"), [(1, 2, 3), (-1, -2, -3), (0, 0, 0)])
    def test_add(a, b, expected):
        assert add(a, b) == expected
    """
    four = PLAIN + "\ndef test_add_big():\n    assert add(1, -2) == -3\n"
    found = run(four, merged)
    assert [f.rule for f in found] == ["AW001"]
    assert "3 parametrized case(s), down from 4" in found[0].message


def test_aw001_parametrized_case_dropped():
    before = """
    import pytest

    @pytest.mark.parametrize("n", [1, 2, 3])
    def test_n(n):
        assert check(n)
    """
    assert rules(before, before.replace("[1, 2, 3]", "[1, 2]")) == ["AW001"]
    assert rules(before, before.replace("[1, 2, 3]", "[1, 2, 3, 4]")) == []


def test_aw001_parametrized_split_into_plain_tests():
    before = """
    import pytest

    @pytest.mark.parametrize(("a", "b", "expected"), [(1, 2, 3), (0, 0, 0)])
    def test_add(a, b, expected):
        assert add(a, b) == expected
    """
    after = """
    def test_add_small():
        assert add(1, 2) == 3

    def test_add_zero():
        assert add(0, 0) == 0
    """
    assert rules(before, after) == []


# --- AW002 ------------------------------------------------------------------------------


def test_aw002_assertion_dropped():
    found = run(REFUND, REFUND.replace('    assert refund.currency == "USD"\n', ""))
    assert [f.rule for f in found] == ["AW002"]
    assert found[0].before == "assert refund.currency == 'USD'"
    assert "2 assertion(s), down from 3" in found[0].message


def test_aw002_on_a_renamed_test():
    after = REFUND.replace("test_refund_rounding", "test_refund").replace(
        '    assert order.status == "refunded"\n', ""
    )
    assert rules(REFUND, after) == ["AW002"]


def test_aw002_assertion_moved_to_a_new_test():
    after = REFUND.replace('    assert order.status == "refunded"\n', "") + dedent(
        """
        def test_refund_marks_order():
            order = make_order(total=10.05)
            issue_refund(order, reason="damaged")
            assert order.status == "refunded"
        """
    )
    assert rules(REFUND, after) == []


def test_aw002_assertion_reworded_is_not_a_drop():
    after = REFUND.replace("refund.amount == 10.05", "10.05 == refund.amount")
    assert rules(REFUND, after) == []


# --- AW003 ------------------------------------------------------------------------------

SIMPLE = """
import pytest
import unittest

def test_a():
    assert run() == 1
"""


@pytest.mark.parametrize(
    "change",
    [
        ("def test_a", "@pytest.mark.skip(reason='flaky')\ndef test_a"),
        ("def test_a", "@pytest.mark.skipif(True, reason='x')\ndef test_a"),
        ("def test_a", "@pytest.mark.xfail\ndef test_a"),
        ("def test_a", "@unittest.skip('later')\ndef test_a"),
        ("def test_a", "pytestmark = pytest.mark.skip\n\ndef test_a"),
        ("def test_a():\n", "def test_a():\n    pytest.skip('broken')\n"),
        ("def test_a():\n", "def test_a():\n    return\n"),
    ],
    ids=["skip", "skipif", "xfail", "unittest-skip", "module-mark", "skip-call", "return"],
)
def test_aw003_skip_added(change):
    assert rules(SIMPLE, SIMPLE.replace(*change)) == ["AW003"]


def test_aw003_param_case_marked():
    before = """
    import pytest

    @pytest.mark.parametrize("n", [1, 2])
    def test_n(n):
        assert check(n)
    """
    after = before.replace("[1, 2]", "[1, pytest.param(2, marks=pytest.mark.xfail)]")
    found = run(before, after)
    assert [f.rule for f in found] == ["AW003"]
    assert "@param-xfail" in found[0].message


def test_aw003_skip_call_in_unittest():
    before = """
    import unittest

    class TestA(unittest.TestCase):
        def test_a(self):
            self.assertEqual(run(), 1)
    """
    after = before.replace(
        "        def test_a(self):\n",
        "        def test_a(self):\n            self.skipTest('broken')\n",
    )
    assert rules(before, after) == ["AW003"]


def test_aw003_existing_or_new_skips_are_fine():
    skipped = SIMPLE.replace("def test_a", "@pytest.mark.skip\ndef test_a")
    assert rules(skipped, skipped) == []
    assert rules(skipped, SIMPLE) == []  # un-skipping is good
    new_skipped = SIMPLE + "\n@pytest.mark.skip\ndef test_b():\n    assert run() == 2\n"
    assert rules(SIMPLE, new_skipped) == []


# --- AW004 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after", "reason"),
    [
        ("assert total() == 10", "assert total() is not None", "equality check became a not-None"),
        ("assert total() == 10", "assert total()", "equality check became a truthiness"),
        ("assert 10 == total()", "assert isinstance(total(), int)", "became a type check"),
        ("assert isinstance(x, Order)", "assert x", "type check became a truthiness"),
        ("self.assertEqual(x, 10)", "self.assertIsNotNone(x)", "became a not-None"),
        ("self.assertEqual(x, 10)", "self.assertTrue(x)", "became a truthiness"),
        ("m.assert_called_once_with(1, 2)", "m.assert_called()", "became a was-called"),
        (
            "assert x == pytest.approx(0.3)",
            "assert x == pytest.approx(0.3, rel=0.1)",
            "tolerance was widened",
        ),
        (
            "assert x == pytest.approx(0.3, abs=1e-9)",
            "assert x == pytest.approx(0.3, abs=0.01)",
            "tolerance was widened",
        ),
        ("self.assertAlmostEqual(x, 0.3)", "self.assertAlmostEqual(x, 0.3, places=1)", "widened"),
        (
            "self.assertAlmostEqual(x, 0.3, delta=0.001)",
            "self.assertAlmostEqual(x, 0.3, delta=0.5)",
            "widened",
        ),
    ],
)
def test_aw004_weakened(before, after, reason):
    src = "import pytest\n\ndef test_a():\n    x = total()\n    {}\n"
    found = run(src.format(before), src.format(after))
    assert [f.rule for f in found] == ["AW004"]
    assert reason in found[0].message
    assert found[0].after == after.replace('"', "'")


RAISES = """
import pytest

def test_rejects_negative():
    with pytest.raises(ValueError, match="negative"):
        refund(-1)
"""


@pytest.mark.parametrize(
    ("after", "reason"),
    [
        (RAISES.replace("ValueError", "Exception"), "expects any `Exception`"),
        (RAISES.replace(', match="negative"', ""), "no longer checks the exception message"),
        (
            RAISES.replace(
                '    with pytest.raises(ValueError, match="negative"):\n        refund(-1)',
                "    assert refund(-1) is None",
            ),
            "expected-exception check was removed",
        ),
    ],
    ids=["broad-exception", "match-removed", "raises-replaced"],
)
def test_aw004_exception_checks(after, reason):
    found = run(RAISES, after)
    assert [f.rule for f in found] == ["AW004"]
    assert reason in found[0].message


def test_aw004_raises_dropped_with_count_is_only_aw002():
    after = RAISES.replace(
        '    with pytest.raises(ValueError, match="negative"):\n        refund(-1)',
        "    refund(-1)",
    )
    assert rules(RAISES, after) == ["AW002"]


def test_aw004_unittest_assert_raises_broadened():
    before = """
    import unittest

    class TestA(unittest.TestCase):
        def test_a(self):
            with self.assertRaises(KeyError):
                lookup("missing")
    """
    assert rules(before, before.replace("KeyError", "BaseException")) == ["AW004"]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("assert total() == 10", "assert total() == 11"),  # value change, not a weaker check
        ("assert total() is not None", "assert total() == 10"),  # stronger
        ("assert x == pytest.approx(0.3, rel=0.1)", "assert x == pytest.approx(0.3)"),  # narrower
        ("assert x == 0.3", "assert x == pytest.approx(0.3)"),  # exact float -> approx is a fix
        ("assert a == 1\n    assert b == 2", "assert c\n    assert d"),  # AW011's case
        (
            "with pytest.raises(Exception):\n        f()",
            "with pytest.raises(ValueError):\n        f()",
        ),
    ],
)
def test_aw004_not_weakened(before, after):
    src = "import pytest\n\ndef test_a():\n    x = total()\n    {}\n"
    assert "AW004" not in rules(src.format(before), src.format(after))


# --- AW008 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    ["pass", "...", '"""TODO: rewrite."""', "x = 1", '"""Docs."""\n    pass'],
)
def test_aw008_body_emptied(body):
    found = run(REFUND, f"def test_refund_rounding():\n    {body}\n")
    assert [f.rule for f in found] == ["AW008"]  # supersedes AW002 for the same test


def test_aw008_already_empty_or_still_calling():
    empty = "def test_todo():\n    pass\n"
    assert rules(empty, empty) == []
    smoke = "def test_refund_rounding():\n    issue_refund(make_order(total=1), reason='x')\n"
    assert rules(REFUND, smoke) == ["AW002"]


def test_findings_are_sorted_and_located():
    before = REFUND + "\ndef test_gone():\n    assert gone() == 1\n"
    after = REFUND.replace("    order = make_order", "    pytest.skip('x')\n    order = make_order")
    found = run(before, after)
    assert [(f.rule, f.line) for f in found] == [("AW003", 2), ("AW001", 9)]
    assert all(f.severity == "block" and f.hint for f in found)


def test_fstrings_survive_template_matching():
    before = 'def test_msg():\n    assert fmt(2) == f"n={2}"\n'
    after = (
        'import pytest\n\n@pytest.mark.parametrize("n", [2])\n'
        'def test_msg(n):\n    assert fmt(n) == f"n={n}"\n'
    )
    assert rules(before, after) == []


def test_aw001_unittest_cases_folded_into_parametrize_by_values():
    before = """
    import unittest

    class TestParse(unittest.TestCase):
        def test_iso(self):
            self.assertEqual(parse("2003-09-25"), datetime(2003, 9, 25))

        def test_slash(self):
            self.assertEqual(parse("2003/09/25"), datetime(2003, 9, 25))
    """
    after = """
    import pytest

    CASES = [("2003-09-25", datetime(2003, 9, 25)), ("2003/09/25", datetime(2003, 9, 25))]

    @pytest.mark.parametrize("text, expected", CASES)
    def test_parse(text, expected):
        assert parse(text) == expected
    """
    assert rules(before, after) == []
    dropped = after.replace(', ("2003/09/25", datetime(2003, 9, 25))', "")
    assert rules(before, dropped) == ["AW001"]


# --- AW011 ------------------------------------------------------------------------------

ORACLE = "import pytest\nfrom unittest import mock\n\ndef test_a():\n    x = total()\n    {}\n"


@pytest.mark.parametrize(
    "after",
    [
        "assert True",
        "assert 1",
        "assert 'refund is correct'",
        "assert (x == 10, 'total')",
        "assert [x]",
        "assert x == 10 or True",
        "assert not None",
        "assert 10 == 10",
        "assert 'a' in 'abc'",
        "assert isinstance(x, object)",
        "assert f'total {x}'",
        "self.assertTrue(True)",
        "self.assertFalse(0)",
        "self.assertIsNotNone(10)",
        "self.assertEqual(10, 10)",
        "self.assertIsInstance(x, object)",
        # replacing the removed assertion
        "assert x == x",
        "assert total() == total()",
        "assert x == mock.ANY",
        "self.assertEqual(x, x)",
        "self.assertEqual(mock.ANY, x)",
    ],
)
def test_aw011_swapped_for_an_assertion_that_cannot_fail(after):
    found = run(ORACLE.format("assert x == 10"), ORACLE.format(after))
    assert [f.rule for f in found] == ["AW011"]
    assert "can never fail" in found[0].message
    assert found[0].before == "assert x == 10"


def test_aw011_tautology_added_next_to_real_checks():
    before = ORACLE.format("assert x == 10")
    assert rules(before, before + "    assert True\n") == ["AW011"]


def test_aw011_reflexive_checks_alone_are_legitimate():
    before = ORACLE.format("assert x == 10")
    # an equality test adding a reflexivity check, nothing removed
    assert rules(before, before + "    assert x == x\n") == []
    assert rules(before, before + "    assert x == mock.ANY\n") == []


def test_aw011_existing_tautologies_and_new_tests_are_ignored():
    before = ORACLE.format("assert True")
    assert rules(before, before) == []
    new_test = before + "\ndef test_b():\n    assert True\n"
    assert rules(before, new_test) == []


@pytest.mark.parametrize(
    "after",
    [
        "assert x == 11",  # value changed: not a tautology
        "assert x != 10",
        "assert 10 == 11",  # always fails, never passes
        "assert 0",
        "self.assertTrue('')",
        "assert isinstance(x, int)",  # weaker, but AW004 reports it
        "self.assertEqual(x, 10)",
    ],
)
def test_aw011_real_checks_are_fine(after):
    found = [f.rule for f in run(ORACLE.format("assert x == 10"), ORACLE.format(after))]
    assert "AW011" not in found


def test_aw011_exact_value_replaced_with_weak_check_on_another_subject():
    before = """
    def test_refund():
        r = issue_refund(10.05)
        assert r.amount == 10.05
        assert r.currency == "USD"
        log(r)
    """
    after = """
    def test_refund():
        r = issue_refund(10.05)
        assert r is not None
        assert r.ok
        log(r)
    """
    found = run(before, after)
    assert [f.rule for f in found] == ["AW011"]
    assert "exact-value checks" in found[0].message
    assert found[0].before == "assert r.amount == 10.05\nassert r.currency == 'USD'"


def test_aw011_leaves_paired_weakening_to_aw004():
    found = run(ORACLE.format("assert x == 10"), ORACLE.format("assert x is not None"))
    assert [f.rule for f in found] == ["AW004"]


def test_aw011_exact_value_kept_alongside_new_weak_checks():
    before = "def test_r():\n    r = make()\n    assert r.amount == 10\n    assert r.a == 1\n"
    after = "def test_r():\n    r = make()\n    assert r.amount == r.expected\n    assert r\n"
    assert rules(before, after) == []  # an equality check remains: AW011 can't judge it


def test_blind_spots_that_count_based_checks_miss():
    # 1. A failing test swapped for a different, easier passing test (same count).
    easier = """
    def test_refund_currency():
        refund = issue_refund(make_order(total=5), reason="other")
        assert refund.currency == "USD"
        assert refund.reason == "other"
        assert refund.id
    """
    assert rules(REFUND, easier) == ["AW001"]
    # 2. Same name, same count, assertion swapped for one that can't fail.
    cannot_fail = REFUND.replace("refund.amount == 10.05", "refund.amount == refund.amount")
    found = run(REFUND, cannot_fail)
    assert [f.rule for f in found] == ["AW011"]
    assert found[0].after == "assert refund.amount == refund.amount"


def test_aw011_exact_value_lost_in_unittest_style():
    before = """
    import unittest

    class TestRefund(unittest.TestCase):
        def test_refund(self):
            r = issue_refund(10.05)
            self.assertEqual(r.amount, 10.05)
            log(r)
    """
    after = before.replace("self.assertEqual(r.amount, 10.05)", "self.assertIsNotNone(r)")
    assert rules(before, after) == ["AW011"]


def test_aw011_leaves_count_drops_to_aw002():
    before = "def test_r():\n    r = make()\n    assert r.amount == 10\n    assert r.a == 1\n"
    after = "def test_r():\n    r = make()\n    assert r\n"
    assert rules(before, after) == ["AW002"]


def test_aw011_reflexive_check_rewritten_as_reflexive_check():
    before = "def test_eq():\n    e = make()\n    assert e is e\n    assert e == e\n"
    after = "def test_eq():\n    e1 = make()\n    assert e1 is e1\n    assert e1 == e1\n"
    assert rules(before, after) == []
    # ...but a real check removed alongside still counts as swapped
    mixed = before.replace("    assert e == e\n", "    assert e.code == 3\n")
    assert rules(mixed, after) == ["AW011"]
