"""Rules on Java tests: the same findings as for Python, from JUnit, AssertJ and Hamcrest."""

import pytest

from assertwatch.extract.java import extract
from assertwatch.rules import run_rules
from assertwatch.rules.java_checks import ALWAYS, SWAPPED, class_check, classify, shape, tautology

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
        (swap("    void emptyCheckoutThrows", "    private void emptyCheckoutThrows"), ["AW001"]),
        (
            swap(
                "    @Test\n    void emptyCheckoutThrows() {",
                "    @Test\n    int emptyCheckoutThrows() {",
            ).replace("checkout());\n    }\n}", "checkout());\n        return 0;\n    }\n}"),
            ["AW001"],
        ),
        (
            swap(
                "    @Test\n    void emptyCheckoutThrows",
                "    class Later {\n    @Test\n    void emptyCheckoutThrows",
            ).replace("checkout());\n    }\n}", "checkout());\n    }\n    }\n}"),
            ["AW001"],
        ),
        (swap("assertEquals(3, cart.count());", "assertThat(cart.count());"), ["AW011"]),
        (swap("assertTrue(Cart.valid(n));", "assertTrue(Cart.valid(n) || true);"), ["AW011"]),
        (
            swap(
                "        Cart cart = new Cart();",
                "        if (cart == null) return;\n        Cart cart = new Cart();",
            ),
            ["AW003"],
        ),
        (
            swap(
                "        Cart cart = new Cart();",
                "        if (true) throw new TestAbortedException();\n"
                "        Cart cart = new Cart();",
            ),
            ["AW003"],
        ),
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
        "made-private",
        "made-non-void",
        "moved-into-inner-class-without-nested",
        "assertj-without-check",
        "or-true",
        "early-return",
        "test-aborted",
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
        ("assertTrue(x.equals(x));", SWAPPED),
        ('assertTrue("reflexive", a.b().equals(a.b()));', SWAPPED),
        ("assertTrue(Objects.equals(x, x));", SWAPPED),
        ("assertThat(x.equals(x)).isTrue();", SWAPPED),
        ("assert x.equals(x);", SWAPPED),
        ("assertTrue(x.equals(y));", None),
        ("assertTrue(x.equals(x) && ok());", None),
        ("assertTrue(Objects.equals(x, y));", None),
        ("assertEquals(1, 2);", None),
        ("assertEquals(1, 1L);", ALWAYS),
        ("assertEquals(true, 1);", None),  # Boolean vs Integer: fails in Java
        ("assertTrue(x);", None),
        ("assertNotNull(null);", None),
        ("assertThat(x, is(not(x)));", None),
        ("assertThat(x);", ALWAYS),
        ('assertThat(x).as("d");', ALWAYS),
        ("assertTrue(ok() || true);", ALWAYS),
        ("assertTrue((true) || ok());", ALWAYS),
        ("assertFalse(ok() && false);", ALWAYS),
        ("assertTrue(ok() && true);", None),
        ("assert ok() || true;", ALWAYS),
        ("verify(repo, atLeast(0)).save(o);", ALWAYS),
        ("verify(repo, atLeast(1)).save(o);", None),
        ("assertThatThrownBy(() -> f());", None),
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


@pytest.mark.parametrize("escape", ["\\uD83D\\uDE00", "\\uD83D"])
def test_surrogate_escapes_in_a_removed_test(escape):
    # jsoup's emoji tests: a removed test with a surrogate escape once crashed the check.
    gone = f"""
    @Test
    void emoji() {{
        assertEquals("{escape}", Text.of("x"));
    }}
"""
    before = BASE.replace("\n}\n", gone + "}\n")
    assert rules(BASE, before) == ["AW001"]


def test_a_removed_test_is_no_copy_of_one_that_differs_only_in_quotes():
    # 'a' is a char and "a" a String: different checks, though Python would read them alike.
    def check(name, literal):
        return f"""
    @Test
    void {name}() {{
        assertEquals({literal}, Text.first("abc"));
    }}
"""

    both = check("firstIsA", '"a"') + check("firstCharIsA", "'a'")
    before = BASE.replace("\n}\n", both + "}\n")
    after = BASE.replace("\n}\n", check("firstCharIsA", "'a'") + "}\n")
    assert rules(after, before) == ["AW001"]


JUNIT4 = """
import org.junit.Test;
import static org.junit.Assert.*;

public class CartTest {
    @Test(expected = IllegalArgumentException.class)
    public void testNegativeQuantity() {
        new Cart().add("apple", -1, 0.5);
    }

    @Test
    public void testTotal() {
        Cart cart = new Cart();
        cart.add("apple", 3, 0.5);
        assertEquals(1.5, cart.total(), 1e-9);
    }
}
"""

JUNIT5 = """
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class CartTest {
    @Test
    void negativeQuantity() {
        assertThrows(IllegalArgumentException.class, () -> {
            new Cart().add("apple", -1, 0.5);
        });
    }

    @Test
    void total() {
        Cart cart = new Cart();
        cart.add("apple", 3, 0.5);
        assertEquals(1.5, cart.total(), 1e-9);
    }
}
"""


def test_junit4_expected_exception_is_a_check():
    [case] = [t for t in extract(JUNIT4, PATH).tests if t.name == "testNegativeQuantity"]
    assert [a.text for a in case.assertions] == ["@Test(expected = IllegalArgumentException.class)"]
    assert classify(case.assertions[0].text)["exception"] == "IllegalArgumentException"
    broader = JUNIT4.replace("expected = IllegalArgumentException", "expected = Exception")
    assert rules(broader, JUNIT4) == ["AW004"]
    assert rules(JUNIT4.replace("(expected = IllegalArgumentException.class)", ""), JUNIT4) == [
        "AW002"
    ]


def test_junit4_to_5_rename_dropping_the_test_prefix_is_quiet():
    assert rules(JUNIT5, JUNIT4) == []


def test_a_test_renamed_in_place_and_gutted_still_blocks():
    gutted = JUNIT5.replace("assertEquals(1.5, cart.total(), 1e-9);", "")
    assert rules(gutted, JUNIT4) == ["AW002"]


def test_removing_a_test_that_never_ran_is_quiet():
    disabled = swap(
        "    @Test\n    void emptyCheckoutThrows",
        "    @Test @Disabled\n    void emptyCheckoutThrows",
    )
    removed = swap("\n    @Test\n    void emptyCheckoutThrows() {\n" + THROWS + "    }\n", "\n")
    assert rules(removed, disabled) == []
    on_windows = disabled.replace("@Disabled", "@DisabledOnOs(OS.WINDOWS)")
    assert rules(removed, on_windows) == ["AW001"]  # it ran everywhere else


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('assertEquals("shop.Cart", cart.getClass().getName());', ("cart", "Cart")),
        ('assertEquals("Cart", cart.getClass().getSimpleName());', ("cart", "Cart")),
        ('assertEquals("shop.Outer$Cart", cart.getClass().getName());', ("cart", "Cart")),
        ("assertEquals(Cart.class, cart.getClass());", ("cart", "Cart")),
        ("assertInstanceOf(shop.Cart.class, cart);", ("cart", "Cart")),
        ("assertThat(cart).isInstanceOf(Cart.class);", ("cart", "Cart")),
        ('assertThat(cart.getClass().getName()).isEqualTo("shop.Cart");', ("cart", "Cart")),
        ("assertThat(cart, instanceOf(Cart.class));", ("cart", "Cart")),
        ('assertEquals("Cart", cart.getName());', None),
        ("assertNotNull(cart);", None),
    ],
)
def test_class_check(text, expected):
    assert class_check(text) == expected


CLASS_NAME = """
import static org.junit.jupiter.api.Assertions.*;
import org.junit.jupiter.api.Test;

class CartTest {
    @Test
    void defaultCart() {
        Object cart = Carts.create();
        assertEquals("shop.Cart", cart.getClass().getName());
    }
}
"""


def test_class_name_check_made_as_instance_of_the_same_class_is_quiet():
    same = 'assertEquals("shop.Cart", cart.getClass().getName());'
    assert rules(CLASS_NAME.replace(same, "assertInstanceOf(Cart.class, cart);"), CLASS_NAME) == []
    other = CLASS_NAME.replace(same, "assertInstanceOf(Basket.class, cart);")
    assert rules(other, CLASS_NAME) == ["AW011"]
    assert rules(CLASS_NAME.replace(same, "assertNotNull(cart);"), CLASS_NAME) == ["AW011"]


HELPER_BASE = """
import static org.junit.jupiter.api.Assertions.*;
import org.junit.jupiter.api.Test;

class CartTest {
    @Test
    void totals() {
        checkTotal(new Cart(), 0.0);
        checkTotal(Carts.of("apple"), 0.5);
    }

    private static void checkTotal(Cart cart, double total) {
        assertEquals(total, cart.total(), 1e-9);
        assertTrue(cart.count() >= 0);
    }
}
"""


def test_helper_moved_to_another_file_is_quiet():
    # checkTotal now comes from a base class or a static import: its checks can't be seen
    moved = HELPER_BASE.replace(
        HELPER_BASE[HELPER_BASE.index("    private static void") : HELPER_BASE.rindex("}")], ""
    )
    assert "checkTotal(new Cart()" in moved and "private static" not in moved
    assert rules(moved, HELPER_BASE) == []


def test_helper_gutted_in_place_still_blocks():
    gutted = HELPER_BASE.replace("        assertEquals(total, cart.total(), 1e-9);\n", "")
    assert rules(gutted, HELPER_BASE) == ["AW002"]


def test_helper_turned_into_a_parametrized_test_is_quiet():
    after = """
import static org.junit.jupiter.api.Assertions.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

class CartTest {
    @Test
    void totals() {
    }

    @ParameterizedTest
    @CsvSource({"'', 0.0", "apple, 0.5"})
    void checkTotal(String items, double total) {
        Cart cart = Carts.of(items);
        assertEquals(total, cart.total(), 1e-9);
        assertTrue(cart.count() >= 0);
    }
}
"""
    assert "AW002" not in rules(after, HELPER_BASE)
