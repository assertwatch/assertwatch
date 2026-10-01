from textwrap import dedent

import pytest

from assertwatch.extract.python import extract
from assertwatch.paths import is_test_path


def collect(source: str, path: str = "tests/test_x.py"):
    return extract(dedent(source), path).by_id()


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_app.py", True),
        ("pkg/app_test.py", True),
        ("test_app.py", True),
        ("tests/conftest.py", False),
        ("tests/helpers.py", False),
        ("src/testing.py", False),
    ],
)
def test_is_test_path(path, expected):
    assert is_test_path(path) is expected


def test_collects_module_functions_classes_and_nesting():
    found = collect(
        """
        def test_a(): assert 1
        async def test_async(): assert 1
        def helper(): assert 1
        def testCamel(): assert 1

        class TestGroup:
            def test_m(self): assert 1
            def helper(self): assert 1

            class TestInner:
                def test_deep(self): assert 1

        class TestWithInit:
            def __init__(self): pass
            def test_ignored(self): assert 1

        class NotATestClass:
            def test_ignored(self): assert 1
        """
    )
    assert sorted(found) == [
        "tests/test_x.py::TestGroup::TestInner::test_deep",
        "tests/test_x.py::TestGroup::test_m",
        "tests/test_x.py::testCamel",
        "tests/test_x.py::test_a",
        "tests/test_x.py::test_async",
    ]


def test_collects_unittest_testcase_subclasses_of_any_name():
    found = collect(
        """
        import unittest

        class RefundCase(unittest.TestCase):
            def test_rounding(self): self.assertEqual(1, 1)
            def setUp(self): pass

        class Derived(RefundCase):
            def test_more(self): self.assertTrue(True)
        """
    )
    assert sorted(found) == [
        "tests/test_x.py::Derived::test_more",
        "tests/test_x.py::Derived::test_rounding",  # inherited, collected again by pytest
        "tests/test_x.py::RefundCase::test_rounding",
    ]


def test_inherited_tests_overrides_and_nested_classes_in_unittest():
    found = collect(
        """
        import unittest

        class TestBase:
            def test_shared(self): assert 1
            def test_overridden(self): assert 1

        class TestChild(TestBase):
            def test_overridden(self): assert 2

        class Case(unittest.TestCase):
            def test_a(self): self.assertTrue(1)
            class TestNested:
                def test_not_collected(self): assert 1
        """
    )
    assert sorted(found) == [
        "tests/test_x.py::Case::test_a",
        "tests/test_x.py::TestBase::test_overridden",
        "tests/test_x.py::TestBase::test_shared",
        "tests/test_x.py::TestChild::test_overridden",
        "tests/test_x.py::TestChild::test_shared",
    ]
    assert found["tests/test_x.py::TestChild::test_overridden"].body_text == "assert 2"


def test_dunder_test_flag_disables_collection_and_is_inherited():
    assert collect("__test__ = False\ndef test_a(): assert 1\n") == {}
    assert collect("class TestA:\n    __test__ = False\n    def test_a(self): assert 1\n") == {}
    found = collect(
        """
        class TestAbstract:
            __test__ = False
            def test_shared(self): assert 1

        class TestOff(TestAbstract):
            pass

        class TestOn(TestAbstract):
            __test__ = True
        """
    )
    assert sorted(found) == ["tests/test_x.py::TestOn::test_shared"]


def test_assertion_kinds_and_text():
    [case] = collect(
        """
        import pytest

        def test_refund(calc, mock_gateway):
            \"\"\"Refund rounds to cents.\"\"\"
            total = calc.refund(10.049)
            assert total == pytest.approx(10.05)
            assert   total>0   # formatting is normalized
            with pytest.raises(ValueError, match="negative"):
                calc.refund(-1)
            mock_gateway.assert_called_once_with(10.05)
            assert_frame_equal(a, b)
        """
    ).values()
    assert [(a.kind, a.name, a.text) for a in case.assertions] == [
        ("assert", "", "assert total == pytest.approx(10.05)"),
        ("assert", "", "assert total > 0"),
        ("raises", "raises", "pytest.raises(ValueError, match='negative')"),
        ("call", "assert_called_once_with", "mock_gateway.assert_called_once_with(10.05)"),
        ("call", "assert_frame_equal", "assert_frame_equal(a, b)"),
    ]
    assert [a.line for a in case.assertions] == [7, 8, 9, 11, 12]
    # calc.refund twice (body + raises block) and pytest.approx
    assert case.call_count == 3


def test_unittest_assertions_including_assert_raises_context():
    [case] = collect(
        """
        import unittest

        class T(unittest.TestCase):
            def test_x(self):
                self.assertEqual(f(), 3)
                with self.assertRaises(KeyError):
                    g()
                self.assertIn("a", h())
        """
    ).values()
    assert [(a.kind, a.name) for a in case.assertions] == [
        ("call", "assertEqual"),
        ("call", "assertRaises"),
        ("call", "assertIn"),
    ]


def test_assertions_in_nested_functions_are_not_counted():
    [case] = collect(
        """
        def test_x():
            def check(v):
                assert v == 1
            fn = lambda: (_ for _ in ()).throw(AssertionError)
            check(1)
        """
    ).values()
    assert case.assertions == []


def test_markers_from_decorators_class_module_and_params():
    found = collect(
        """
        import pytest, unittest
        from unittest import skipIf

        pytestmark = [pytest.mark.slow]

        @pytest.mark.skip(reason="flaky")
        def test_skip(): assert 1

        @pytest.mark.skipif(True, reason="x")
        def test_skipif(): assert 1

        @pytest.mark.xfail
        def test_xfail(): assert 1

        @unittest.skip("no")
        def test_ut_skip(): assert 1

        @skipIf(True, "no")
        def test_ut_skipif(): assert 1

        @pytest.mark.parametrize("n", [1, pytest.param(2, marks=pytest.mark.xfail)])
        def test_param(n): assert n

        @pytest.fixture
        def not_a_test(): pass

        @pytest.mark.usefixtures("db")
        class TestC:
            pytestmark = pytest.mark.skip
            def test_in_class(self): assert 1
        """
    )
    markers = {k.split("::", 1)[1]: set(v.markers) for k, v in found.items()}
    assert markers == {
        "test_skip": {"slow", "skip"},
        "test_skipif": {"slow", "skipif"},
        "test_xfail": {"slow", "xfail"},
        "test_ut_skip": {"slow", "skip"},
        "test_ut_skipif": {"slow", "skipif"},
        "test_param": {"slow", "parametrize", "param-xfail"},
        "TestC::test_in_class": {"slow", "usefixtures", "skip"},
    }


def test_mock_targets_and_http_patch_is_not_a_mock():
    [case] = collect(
        """
        from unittest import mock
        from unittest.mock import patch

        @patch("app.refund.calculate")
        @mock.patch.object(Gateway, "charge")
        def test_x(m1, m2, mocker, monkeypatch, client):
            mocker.patch("app.refund.tax_rate", return_value=0)
            mocker.patch.object(Ledger, "post")
            monkeypatch.setattr("app.config.CURRENCY", "EUR")
            monkeypatch.setattr(Clock, "now", lambda: 0)
            with patch.dict("os.environ", {"X": "1"}):
                pass
            client.patch("/items/1", json={})
            assert 1
        """
    ).values()
    assert case.mocks == [
        "app.refund.calculate",
        "Gateway.charge",
        "app.refund.tax_rate",
        "Ledger.post",
        "app.config.CURRENCY",
        "Clock.now",
        "os.environ",
    ]
    # Only client.patch counts: patch.dict is a mock and the lambda body isn't walked.
    assert case.call_count == 1


def test_skip_calls():
    [case] = collect(
        """
        import pytest

        def test_x():
            pytest.skip("later")
            pytest.importorskip("numpy")
            assert 1
        """
    ).values()
    assert case.skip_calls == ["pytest.skip", "pytest.importorskip"]


def test_try_blocks_record_swallowed_assertions():
    [case] = collect(
        """
        def test_x():
            try:
                assert compute() == 3
            except AssertionError:
                pass
            try:
                risky()
            except (KeyError, ValueError) as e:
                log(e)
            try:
                assert 1
            except:
                ...
            try:
                assert 2
            finally:
                cleanup()
            try:
                assert 3
            except KeyError:
                pass
            except ValueError:
                raise
        """
    ).values()
    assert [(t.handlers, t.wraps_assertion, t.handler_is_noop) for t in case.try_blocks][3:] == [
        ((), True, False),  # try/finally swallows nothing
        (("KeyError", "ValueError"), True, False),  # one real handler is enough
    ]
    assert [(t.handlers, t.wraps_assertion, t.handler_is_noop) for t in case.try_blocks][:3] == [
        (("AssertionError",), True, True),
        (("(KeyError, ValueError)",), False, False),
        (("",), True, True),
    ]


@pytest.mark.parametrize(
    ("body", "is_empty", "returns_first"),
    [
        ("pass", True, False),
        ("...", True, False),
        ('"""Only a docstring."""', True, False),
        ('"""Doc."""\n    pass', True, False),
        ("return\n    assert f() == 1", False, True),
        ("assert f() == 1", False, False),
    ],
)
def test_empty_and_early_return(body, is_empty, returns_first):
    [case] = collect(f"def test_x():\n    {body}\n").values()
    assert (case.is_empty, case.returns_first) == (is_empty, returns_first)


def test_body_text_ignores_docstring_and_formatting():
    a = collect('def test_x():\n    """Doc."""\n    x = f( 1 )\n    assert x==1\n')
    b = collect("def test_x():\n    x = f(1)\n    assert x == 1  # same\n")
    assert a["tests/test_x.py::test_x"].body_text == b["tests/test_x.py::test_x"].body_text
    assert a["tests/test_x.py::test_x"].body_text == "x = f(1)\nassert x == 1"


def test_imports_and_lines():
    tf = extract(
        dedent(
            """
            import os, app.refund
            from app.billing import Invoice
            from . import local_helper

            @decorated
            def test_x():
                assert 1
                assert 2
            """
        ),
        "tests/test_x.py",
    )
    assert tf.imports == {"os", "app.refund", "app.billing", "app.billing.Invoice"}
    [case] = tf.tests
    assert (case.line, case.end_line) == (7, 9)


def test_syntax_error_is_reported_not_raised():
    tf = extract("def test_x(:\n    assert 1\n", "tests/test_x.py")
    assert tf.tests == []
    assert tf.parse_error is not None and tf.parse_error.startswith("SyntaxError")


def test_null_bytes_are_reported_not_raised():
    tf = extract("x = 1\x00\n", "tests/test_x.py")
    assert tf.parse_error is not None


def test_parametrize_names_cases_and_literals():
    found = collect(
        """
        import pytest

        @pytest.mark.parametrize("a, b", [(1, -2), (3, 4)])
        @pytest.mark.parametrize(argnames=["mode"], argvalues=("x", "y", "z"))
        def test_grid(a, b, mode): assert a

        @pytest.mark.parametrize("n", load_cases())
        def test_dynamic(n): assert n

        def test_plain(): assert 1
        """
    )
    grid = found["tests/test_x.py::test_grid"]
    assert grid.param_names == {"a", "b", "mode"}
    assert grid.param_cases == 6
    assert grid.param_literals == {"1", "-2", "3", "4", "'x'", "'y'", "'z'"}
    assert found["tests/test_x.py::test_dynamic"].param_cases is None
    plain = found["tests/test_x.py::test_plain"]
    assert (plain.param_names, plain.param_cases, plain.param_literals) == (set(), None, set())
    assert [(r.names, r.values, r.line) for r in grid.param_rows] == [
        (("a", "b"), ("1", "-2"), 4),
        (("a", "b"), ("3", "4"), 4),
        (("mode",), ("'x'",), 5),
        (("mode",), ("'y'",), 5),
        (("mode",), ("'z'",), 5),
    ]
    assert found["tests/test_x.py::test_dynamic"].param_rows == ()


def test_parametrize_cases_from_pytest_param_and_module_lists():
    found = collect(
        """
        import pytest

        CASES = [
            (1, 2, 3),
            pytest.param(2, 2, 4, id="two", marks=pytest.mark.slow),
            (1, 2),
        ]

        @pytest.mark.parametrize(("a", "b", "total"), CASES)
        def test_add(a, b, total): assert add(a, b) == total

        @pytest.mark.parametrize("pair", [(1, 2), [3, 4]])
        def test_pair(pair): assert pair
        """
    )
    rows = found["tests/test_x.py::test_add"].param_rows
    # the malformed (1, 2) case is left out: its values don't line up with the names
    assert [(r.values, r.line) for r in rows] == [(("1", "2", "3"), 5), (("2", "2", "4"), 6)]
    pair = found["tests/test_x.py::test_pair"].param_rows
    assert [r.values for r in pair] == [("(1, 2)",), ("[3, 4]",)]


def test_helper_assertions_count_for_the_calling_test():
    found = collect(
        """
        import unittest


        def assert_cart(cart, total, count):
            assert cart.total() == total
            check_count(cart, count)


        def check_count(cart, count):
            assert cart.count() == count


        def assert_looks_fine(cart):
            return cart is not None  # named like an assertion, checks nothing


        def recurse(n):
            assert n
            recurse(n - 1)


        def test_cart():
            cart = make()
            assert_cart(cart, 4.0, 5)
            assert_looks_fine(cart)
            recurse(3)


        class CartTests(unittest.TestCase):
            def assertCartEqual(self, cart, total):
                self.assertEqual(cart.total(), total)

            def test_total(self):
                self.assertCartEqual(make(), 4.0)
        """
    )
    cart = found["tests/test_x.py::test_cart"]
    assert [(a.kind, a.text, a.line) for a in cart.assertions] == [
        ("assert", "assert cart.total() == total", 25),
        ("assert", "assert cart.count() == count", 25),
        ("assert", "assert n", 27),
    ]
    assert cart.call_count == 4  # make(), and each helper call
    total = found["tests/test_x.py::CartTests::test_total"]
    assert [a.text for a in total.assertions] == ["self.assertEqual(cart.total(), total)"]
