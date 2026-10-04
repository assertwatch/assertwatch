"""Rules on Java tests: the same findings as for Python, from JUnit, AssertJ and Hamcrest."""

import pytest

from assertwatch.extract.java import extract
from assertwatch.rules import run_rules
from assertwatch.rules.java_checks import ALWAYS, SWAPPED, classify, shape, tautology

PATH = "src/test/java/shop/CartTest.java"

BASE = """
import static org.junit.jupiter.api.Assertions.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.*;

class CartTest {
    @Test
    void totalSumsItems() {
        Cart cart = new Cart();
        cart.add("apple", 3, 0.5);
        assertEquals(1.5, cart.total());
        assertEquals(3, cart.count());
    }

    @ParameterizedTest
    @ValueSource(ints = {1, 2, 3})
    void positive(int n) {
        assertTrue(Cart.valid(n));
    }

    @ParameterizedTest
    @CsvSource({"apple, 0.5", "pear, 1.25"})
    void prices(String name, double price) {
        assertEquals(price, Prices.of(name));
    }

    @Test
    void emptyCheckoutThrows() {
        assertThrows(IllegalStateException.class, () -> new Cart().checkout());
    }
}
"""


def rules(after: str, before: str = BASE) -> list[str]:
    found = run_rules([extract(before, PATH)], [extract(after, PATH)])
    return sorted(f.rule for f in found)


def swap(old: str, new: str) -> str:
    assert old in BASE, old
    return BASE.replace(old, new)


THROWS = "        assertThrows(IllegalStateException.class, () -> new Cart().checkout());\n"


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        (
            swap(THROWS.join(["    @Test\n    void emptyCheckoutThrows() {\n", "    }\n"]), ""),
            ["AW001"],
        ),
        (swap("        assertEquals(3, cart.count());\n", ""), ["AW002"]),
        (
            swap("    void emptyCheckoutThrows", "    @Disabled\n    void emptyCheckoutThrows"),
            ["AW003"],
        ),
        (
            swap(
                "        Cart cart = new Cart();",
                "        assumeTrue(false);\n        Cart cart = new Cart();",
            ),
            ["AW003"],
        ),
        (swap(THROWS, ""), ["AW008"]),
        (swap("assertEquals(1.5, cart.total());", "assertNotNull(cart.total());"), ["AW004"]),
        (swap("assertEquals(1.5, cart.total());", "assertEquals(1.6, cart.total());"), ["AW005"]),
        (swap("{1, 2, 3}", "{1, 2}"), ["AW001"]),
        (swap('"pear, 1.25"', '"pear, 1.5"'), ["AW005"]),
        (
            swap("assertEquals(3, cart.count());", "assertEquals(cart.count(), cart.count());"),
            ["AW011"],
        ),
        (swap("IllegalStateException.class", "RuntimeException.class"), ["AW004"]),
        (swap("assertEquals(3, cart.count());", "assertTrue(true);"), ["AW011"]),
    ],
    ids=[
        "test-deleted",
        "assertion-dropped",
        "disabled",
        "assumption-skips",
        "emptied",
        "equals-to-not-null",
        "expected-value-changed",
        "case-dropped",
        "csv-case-value-changed",
        "self-comparison",
        "exception-broadened",
        "always-true",
    ],
)
def test_cheats(after, expected):
    assert rules(after) == expected


@pytest.mark.parametrize(
    "after",
    [
        swap("totalSumsItems", "totalAddsUpItems"),
        swap(
            "assertEquals(1.5, cart.total());",
            "assertEquals(\n            1.5,\n            cart.total());",
        ),
        swap("assertEquals(1.5, cart.total());", "Assertions.assertEquals(1.5, cart.total());"),
        swap("assertEquals(1.5, cart.total());", 'assertEquals(1.5, cart.total(), "total");'),
        swap("assertEquals(3, cart.count());", "assertThat(cart.count()).isEqualTo(3);"),
        swap("assertEquals(3, cart.count());", "assertThat(cart.count(), is(3));"),
        swap("{1, 2, 3}", "{1, 2, 3, 4}"),
        swap("assertTrue(Cart.valid(n));", 'assertTrue(Cart.valid(n), () -> "n=" + n);'),
    ],
    ids=[
        "renamed",
        "reformatted",
        "qualified",
        "message-added",
        "junit-to-assertj",
        "junit-to-hamcrest",
        "case-added",
        "lazy-message-added",
    ],
)
def test_refactors_are_quiet(after):
    assert rules(after) == []


def test_junit4_to_junit5_argument_order_is_quiet():
    before = BASE.replace(
        "assertEquals(1.5, cart.total());", 'assertEquals("total", 1.5, cart.total(), 1e-9);'
    )
    after = BASE.replace(
        "assertEquals(1.5, cart.total());", 'assertEquals(1.5, cart.total(), 1e-9, "total");'
    )
    assert rules(after, before) == []
    wider = after.replace("1e-9", "0.1")
    assert rules(wider, before) == ["AW004"]  # the tolerance was widened


@pytest.mark.parametrize(
    ("text", "kind", "subject"),
    [
        ("assertEquals(5, cart.count());", "equal", "cart.count()"),
        ('assertEquals("msg", 5, cart.count());', "equal", "cart.count()"),
        ("assertSame(expected, actual);", "equal", "actual"),
        ("assertNotNull(cart.total());", "notnone", "cart.total()"),
        ('assertNotNull("msg", cart.total());', "notnone", "cart.total()"),
        ("assertTrue(cart.isEmpty());", "truthy", "cart.isEmpty()"),
        ("assertInstanceOf(Cart.class, made);", "type", "made"),
        ("assertThat(cart.count()).isEqualTo(3);", "equal", "cart.count()"),
        ("assertThat(cart.count()).isNotNull().isEqualTo(3);", "equal", "cart.count()"),
        ('assertThat(cart.count()).as("count").isNotNull();', "notnone", "cart.count()"),
        ("assertThat(made).isInstanceOf(Cart.class);", "type", "made"),
        ("assertThat(cart.isEmpty()).isTrue();", "truthy", "cart.isEmpty()"),
        ("assertThat(cart.items()).hasSize(2);", "equal", "cart.items().size()"),
        ("assertThat(total, is(5));", "equal", "total"),
        ("assertThat(total, is(equalTo(5)));", "equal", "total"),
        ("assertThat(total, notNullValue());", "notnone", "total"),
        ("assertThat(made, instanceOf(Cart.class));", "type", "made"),
        ("assert cart.count() == 3;", "equal", "cart.count()"),
        ('assert cart.total() != null : "no total";', "notnone", "cart.total()"),
        ("verify(repo).save(order);", "called_with", "repo.save"),
        ("verify(repo).save(any());", "called", "repo.save"),
        ("verify(repo, times(1)).save(any(Order.class));", "called", "repo.save"),
        ("assertThrows(IllegalStateException.class, () -> f());", "raises", "<raises>"),
    ],
)
def test_classify(text, kind, subject):
    found = classify(text)
    assert found is not None
    assert (found["kind"], found["subject"]) == (kind, subject)


@pytest.mark.parametrize(
    "text",
    ["assertTrue(true);", 'assertNotNull("x");', "assertThat(x).contains(1);", "foo(1);"],
)
def test_classify_unknown_or_trivial(text):
    assert classify(text) is None


def test_classify_exceptions_and_tolerances():
    assert (
        classify("assertThrows(java.lang.IllegalStateException.class, () -> f());")["exception"]
        == "IllegalStateException"
    )
    thrown = classify(
        'assertThatThrownBy(() -> f()).isInstanceOf(IOException.class).hasMessage("x");'
    )
    assert (thrown["exception"], thrown["matches_message"]) == ("IOException", True)
    of_type = classify("assertThatExceptionOfType(IOException.class).isThrownBy(() -> f());")
    assert (of_type["exception"], of_type.get("matches_message")) == ("IOException", False)
    assert classify("assertEquals(1.5, x, 1e-9);")["tolerance"] == (0.0, 1e-9)
    assert classify("assertThat(x).isCloseTo(1.5, within(0.01));")["tolerance"] == (0.0, 0.01)
    assert classify("assertThat(x, closeTo(1.5, 0.01));")["tolerance"] == (0.0, 0.01)


@pytest.mark.parametrize(
    ("text", "template", "values"),
    [
        ("assertEquals(5, cart.count());", "assertEquals(_, cart.count())", [5]),
        ('assertEquals(5, cart.count(), "msg");', "assertEquals(_, cart.count())", [5]),
        ('assertEquals("msg", 5, cart.count());', "assertEquals(_, cart.count())", [5]),
        ("assertEquals(-1.5, x, 1e-9);", "assertEquals(_, x)", [-1.5]),
        (
            'assertThat(name).as("n").isEqualTo("apple");',
            "assertThat(name).isEqualTo(_)",
            ["apple"],
        ),
        ('assert x == 3 : "three";', "assert x == _", [3]),
        ("assertTrue(ok());", None, None),
    ],
)
def test_shape(text, template, values):
    found = shape(text)
    if template is None:
        assert found is None
    else:
        assert found == (template, values)


@pytest.mark.parametrize(
    ("text", "verdict"),
    [
        ("assertTrue(true);", ALWAYS),
        ('assertTrue("msg", true);', ALWAYS),
        ("assertFalse(false);", ALWAYS),
        ('assertNotNull("x");', ALWAYS),
        ("assertEquals(1, 1);", ALWAYS),
        ("assertEquals(x, x);", SWAPPED),
        ("assertThat(x).isEqualTo(x);", SWAPPED),
        ("assertThat(true).isTrue();", ALWAYS),
        ("assertThat(x, anything());", ALWAYS),
        ("assertThat(x, is(x));", SWAPPED),
        ("assertInstanceOf(Object.class, x);", ALWAYS),
        ("assert true;", ALWAYS),
        ("assert x == x;", SWAPPED),
        ("assertEquals(1, 2);", None),
        ("assertEquals(1, 1L);", ALWAYS),
        ("assertEquals(true, 1);", None),  # Boolean vs Integer: fails in Java
        ("assertTrue(x);", None),
        ("assertNotNull(null);", None),
        ("assertThat(x, is(not(x)));", None),
    ],
)
def test_tautology(text, verdict):
    assert tautology(text) == verdict


def test_lost_checks_are_not_read_as_implied_python():
    # In Python, `n > 0` is implied by `n == 5`; the evaluator reads Python only, so for Java
    # the lost check is still reported.
    before = BASE.replace("assertTrue(Cart.valid(n));", "assert n == 5;\n        assert n > 0;")
    after = BASE.replace("assertTrue(Cart.valid(n));", "assert n == 5;")
    assert rules(after, before) == ["AW002"]
