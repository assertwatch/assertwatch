from textwrap import dedent

import pytest

from assertwatch.extract.java import extract, join, tokenize
from assertwatch.paths import is_test_path

PATH = "src/test/java/shop/CartTest.java"


def collect(source: str, path: str = PATH):
    result = extract(dedent(source), path)
    assert result.parse_error is None, result.parse_error
    return {t.id.split("::", 1)[1]: t for t in result.tests}


def body(text: str, decorators: str = "@Test", params: str = "") -> str:
    return f"class CartTest {{\n    {decorators}\n    void t({params}) {{\n{text}\n    }}\n}}\n"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/test/java/shop/CartTest.java", True),
        ("src/test/java/shop/CartTests.java", True),
        ("src/test/java/shop/TestCart.java", True),
        ("src/test/java/shop/CartIT.java", True),
        ("src/test/java/shop/CartTestCase.java", True),
        ("src/main/java/shop/Cart.java", False),
        ("src/test/java/shop/CartFixtures.java", False),
    ],
)
def test_java_test_paths(path, expected):
    assert is_test_path(path) is expected


def test_tokens_skip_comments_and_keep_literals_whole():
    source = 'a("}{", \'}\', """\n  x { y\n  """) // }\n/* { */ b;'
    texts = [t.text for t in tokenize(source)]
    assert texts == ["a", "(", '"}{"', ",", "'}'", ",", '"""\n  x { y\n  """', ")", "b", ";"]
    assert [t.text for t in tokenize("x = 1e-9 + 0x1F + .5f + 1_000L;")][2:] == [
        "1e-9", "+", "0x1F", "+", ".5f", "+", "1_000L", ";",
    ]  # fmt: skip


def test_block_comments_end_at_the_first_close():
    assert [t.text for t in tokenize("/* a */ x(); /* b */")] == ["x", "(", ")", ";"]


def test_join_is_the_same_for_any_formatting():
    a = join(tokenize("assertEquals( 1.5,cart.total() , 1e-9 );"))
    b = join(tokenize("assertEquals(\n    1.5,\n    cart.total(),\n    1e-9);"))
    assert a == b == "assertEquals(1.5, cart.total(), 1e-9);"


def test_tests_in_classes_nested_classes_and_junit3():
    tests = collect(
        """
        package shop;

        import static org.junit.jupiter.api.Assertions.*;
        import org.junit.jupiter.api.*;

        @Tag("fast")
        class CartTest {
            private final Comparator<String> byLength = new Comparator<>() {
                public int compare(String a, String b) { return a.length() - b.length(); }
            };
            static { System.setProperty("x", "y"); }

            @BeforeEach void setUp() { }

            @Test
            void total() { assertEquals(2, add(1, 1)); }

            <T extends Comparable<T>> T helper(T value) { return value; }

            @Nested
            class WhenEmpty {
                @org.junit.jupiter.api.Test
                public void checkoutFails() throws Exception { fail("no"); }
            }

            record Item(String name, int qty) { }
            enum Kind { A, B }
        }

        abstract class BaseTest {
            @Test void inherited() { assertTrue(true); }
        }

        @Disabled
        public final class SkippedTest {
            @Test void off() { assertTrue(ok()); }
        }

        public class LegacyTest extends junit.framework.TestCase {
            public void testOld() { assertEquals(1, 1); }
            public void helperNotATest() { }
            public void testWithArgument(int x) { assertEquals(x, x); }
        }
        """
    )
    assert list(tests) == [
        "CartTest::total",
        "CartTest::WhenEmpty::checkoutFails",
        "SkippedTest::off",
        "LegacyTest::testOld",
    ]
    assert tests["SkippedTest::off"].markers == frozenset({"skip"})
    assert tests["CartTest::total"].markers == frozenset({'tag:"fast"'})
    assert tests["CartTest::WhenEmpty::checkoutFails"].markers == frozenset({'tag:"fast"'})


def test_imports():
    result = extract(
        "import static org.junit.Assert.*;\nimport java.util.List;\nclass ATest {}\n", PATH
    )
    assert result.imports == frozenset({"org.junit.Assert", "java.util.List"})


@pytest.mark.parametrize(
    ("annotations", "markers"),
    [
        ("@Test @Disabled", {"skip"}),
        ('@Test @Disabled("flaky")', {"skip"}),
        ("@Test @Ignore", {"skip"}),
        ("@Test @DisabledOnOs(OS.WINDOWS)", {"skipif"}),
        ('@Test @EnabledIfEnvironmentVariable(named = "CI", matches = "true")', {"skipif"}),
        ("@Test @DisabledForJreRange(min = JRE.JAVA_21)", {"skipif"}),
        ("@Test @Timeout(5)", set()),
    ],
)
def test_markers(annotations, markers):
    [case] = collect(body("assertTrue(ok());", annotations)).values()
    assert case.markers == frozenset(markers)


def test_class_markers_are_inherited():
    tests = collect(
        """
        @Disabled
        class CartTest {
            @Test void a() { assertTrue(ok()); }
            @Nested class Inner { @Test void b() { assertTrue(ok()); } }
        }
        """
    )
    assert all("skip" in t.markers for t in tests.values())


def test_assertions_and_kinds():
    [case] = collect(
        body(
            """
        assertEquals(3, cart.count());
        Assertions.assertEquals(1.5, cart.total(), 1e-9);
        assertThat(cart.items()).hasSize(2).contains("apple");
        assertThat(total, is(equalTo(5)));
        assertThrows(IllegalStateException.class, () -> cart.checkout());
        assertThatThrownBy(() -> cart.checkout()).isInstanceOf(IllegalStateException.class);
        verify(repo).save(order);
        assert cart.total() > 0 : "positive";
        if (cart.isEmpty()) fail("empty");
        assertAll(() -> assertEquals(1, a), () -> assertEquals(2, b));
        String note = "assertEquals(1, 2)";
        helper.assertNothing = true;
        """
        )
    ).values()
    assert [(a.kind, a.name) for a in case.assertions] == [
        ("call", "assertEquals"),
        ("call", "assertEquals"),
        ("call", "assertThat"),
        ("call", "assertThat"),
        ("raises", "assertThrows"),
        ("raises", "assertThatThrownBy"),
        ("call", "verify"),
        ("assert", ""),
        ("call", "fail"),
        ("call", "assertAll"),
    ]
    assert all(a.lang == "java" for a in case.assertions)
    assert case.assertions[1].text == "Assertions.assertEquals(1.5, cart.total(), 1e-9);"
    assert case.assertions[0].line == 5


def test_skip_calls_empty_and_early_return():
    [case] = collect(body("Assumptions.assumeTrue(isLinux());\nassumeFalse(ci);\nrun();")).values()
    assert case.skip_calls == ["Assumptions.assumeTrue", "assumeFalse"]
    assert case.call_count == 2  # isLinux() and run(); skips aren't calls under test
    [empty] = collect(body("")).values()
    assert empty.is_empty and empty.cannot_fail
    [semi] = collect(body(";")).values()
    assert semi.is_empty
    [early] = collect(body("return;\nassertTrue(ok());")).values()
    assert early.returns_first and not early.is_empty


def test_try_blocks():
    [case] = collect(
        body(
            """
        try {
            assertEquals(1, f());
        } catch (AssertionError e) {
        }
        try (var in = open()) {
            g();
        } catch (IOException | RuntimeException e) {
            log(e);
        }
        try { assertTrue(ok()); } finally { close(); }
        """
        )
    ).values()
    assert [(t.handlers, t.wraps_assertion, t.handler_is_noop) for t in case.try_blocks] == [
        (("AssertionError",), True, True),
        (("IOException | RuntimeException",), False, False),
        ((), True, False),
    ]


@pytest.mark.parametrize(
    ("annotations", "cases", "rows"),
    [
        ("@ValueSource(ints = {1, 2, 3})", 3, [("1",), ("2",), ("3",)]),
        ('@ValueSource(strings = "a")', 1, [('"a"',)]),
        (
            '@CsvSource({"apple, 1", "\'pear, ripe\', 2"})',
            2,
            [('"apple"', "1"), ('"pear, ripe"', "2")],
        ),
        (
            '@CsvSource(textBlock = """\n    apple, 1\n    # note\n    pear, 2\n    """)',
            2,
            [('"apple"', "1"), ('"pear"', "2")],
        ),
        ("@ValueSource(ints = {1}) @NullAndEmptySource", 3, None),
        ("@NullSource @EmptySource", 2, None),
        ('@MethodSource("cases")', None, None),
        ("@EnumSource(Kind.class)", None, None),
        ("@CsvSource(value = {\"a; 1\"}, delimiter = ';')", None, None),
    ],
)
def test_parametrized_cases(annotations, cases, rows):
    source = body(
        "assertTrue(ok(name));", f"@ParameterizedTest {annotations}", "String name, int qty"
    )
    [case] = collect(source).values()
    assert "parametrize" in case.markers
    assert case.param_names == frozenset({"name", "qty"})
    assert case.param_cases == cases
    if rows is not None:
        assert [r.values for r in case.param_rows] == rows


@pytest.mark.parametrize(
    "source",
    [
        "class ATest { @Test void a() { assertEquals(1, f()); }",  # unclosed
        'class ATest { @Test void a() { String s = "unterminated; } }',
        "class ATest { @Test void a() { f()); } }",  # unbalanced
    ],
)
def test_unparsable_files_are_errors_not_empty(source):
    assert extract(source, PATH).parse_error is not None
