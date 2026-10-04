from textwrap import dedent

import pytest

from assertwatch.extract.java import extract, join, tokenize, unicode_escapes
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


@pytest.mark.parametrize(
    ("member", "runs"),
    [
        ("@Test void a() { }", True),
        ("@Test public void a() { }", True),
        ("@Test private void a() { }", False),  # JUnit 5 doesn't run it
        ("@Test static void a() { }", False),
        ("@Test int a() { return 1; }", False),
        ("@TestFactory Stream<DynamicTest> a() { return Stream.empty(); }", True),
        ("@ParameterizedTest @ValueSource(ints = 1) String a(int n) { return null; }", False),
    ],
)
def test_only_methods_junit_runs_are_tests(member, runs):
    assert bool(extract(f"class ATest {{\n    {member}\n}}\n", PATH).tests) is runs


@pytest.mark.parametrize(
    ("inner", "runs"),
    [
        ("@Nested class Inner", True),
        ("class Inner", False),  # JUnit 5 runs inner classes only with @Nested
        ("@Nested static class Inner", False),
        ("static class Inner", False),  # Surefire skips nested classes by default
    ],
)
def test_only_nested_classes_junit_runs(inner, runs):
    source = f"class ATest {{\n    {inner} {{\n        @Test void a() {{ }}\n    }}\n}}\n"
    assert bool(extract(source, PATH).tests) is runs


def test_junit3_methods_must_be_public_void():
    source = """
    public class LegacyTest extends TestCase {
        public void testA() { }
        void testB() { }
        public int testC() { return 1; }
        public static void testD() { }
    }
    """
    assert [t.name for t in extract(dedent(source), PATH).tests] == ["testA"]


@pytest.mark.parametrize(
    ("text", "early", "skips"),
    [
        ("if (skip) return;\nassertTrue(ok());", True, []),
        ("assertTrue(ok());\nreturn;", False, []),
        ("Runnable r = () -> { return; };\nassertTrue(ok());", False, []),
        (
            "Comparator<String> c = new Comparator<>() {\n"
            "    public int compare(String a, String b) { return 0; }\n};\nassertTrue(ok());",
            False,
            [],
        ),
        (
            "if (ci) throw new TestAbortedException();\nassertTrue(ok());",
            False,
            ["throw new TestAbortedException"],
        ),
        (
            'throw new AssumptionViolatedException("x");',
            False,
            ["throw new AssumptionViolatedException"],
        ),
        ("assertThrows(X.class, () -> { throw new IllegalStateException(); });", False, []),
        ('if (bad) throw new IllegalStateException("bad");\nassertTrue(ok());', False, []),
    ],
)
def test_early_returns_and_aborts(text, early, skips):
    [case] = collect(body(text)).values()
    assert (case.returns_first, case.skip_calls) == (early, skips)


@pytest.mark.parametrize(
    ("source", "translated"),
    [
        ("a\\u0041", "aA"),
        ("a\\\\u0041", "a\\\\u0041"),  # an escaped backslash, then plain text
        ("a\\\\\\u0041", "a\\\\A"),
        ("\\u005cu0041", "\\u0041"),  # a translated backslash starts no second escape
        ("\\uuuu0041", "A"),
        ("\\u00", "\\u00"),  # not an escape: left for the tokenizer to reject
        ("\\0041\\u0041", "\\0041A"),  # no `u` before the first: not an escape
        ("\\uzzzz", "\\uzzzz"),  # not hex
        ("\\uD83D\\uDE00", "\U0001f600"),  # a surrogate pair is one character
        ("\\uD83Dx\\uDE00", "\\uD83Dx\\uDE00"),  # lone surrogates keep their escape text
    ],
)
def test_unicode_escapes_like_javac(source, translated):
    assert unicode_escapes(source) == translated


def test_unicode_escape_that_ends_a_string_is_read_like_javac():
    # For javac this is one string, `x\"; assertTrue(ok()); //`: there is no call.
    source = body('String s = "x\\u005c"; assertTrue(ok()); //";')
    [case] = collect(source).values()
    assert case.assertions == []


def test_only_top_level_classes_surefire_selects_by_name():
    source = """
    class CartTest { @Test void a() { } }
    class Helper { @Test void b() { } }
    class HelperTests { @Test void c() { } }
    """
    assert [t.name for t in extract(dedent(source), PATH).tests] == ["a", "c"]


@pytest.mark.parametrize(
    ("text", "counted"),
    [
        ("Runnable r = () -> assertTrue(ok());", 0),  # stored, maybe never run
        ("Runnable r = () -> { assertTrue(ok()); };", 0),
        ("if (false) assertTrue(ok());", 0),
        ("if (false) { assertTrue(ok()); }", 0),
        ("Runnable r = new Runnable() { public void run() { assertTrue(ok()); } };", 0),
        ("class Local { void c() { assertTrue(ok()); } }", 0),
        ("items.forEach(x -> assertTrue(x.ok()));", 1),  # passed to a call: runs
        ("run(1, () -> assertTrue(ok()));", 1),
        ("assertAll(() -> assertTrue(a()), () -> assertTrue(b()));", 1),
        ("if (ok) assertTrue(ok());", 1),
        ("Runnable r = () -> f();\nassertTrue(ok());", 1),
        ("Class<?> c = String.class;\nassertTrue(ok());", 1),
        ("Class<?> c = String.class;\nif (ok()) { assertTrue(ok()); }", 1),
    ],
)
def test_checks_in_code_that_may_never_run_dont_count(text, counted):
    [case] = collect(body(text)).values()
    assert len(case.assertions) == counted


def test_long_backslash_runs_are_linear():
    import time

    start = time.perf_counter()
    unicode_escapes("\\" * 200_000 + "u")
    assert time.perf_counter() - start < 1.0


@pytest.mark.parametrize(
    ("text", "counted"),
    [
        ("IllegalStateException e = assertThrows(IllegalStateException.class, () -> f());", 1),
        ("final var e = assertThrows(X.class, () -> f());", 1),
        ("e = assertThrows(X.class, () -> f());", 1),
        ("this.e = assertThrows(X.class, () -> f());", 1),
        ("Map<String, List<X>> m = assertDoesNotThrow(() -> f());\nassertTrue(ok());", 2),
        ("int[] a = assertDoesNotThrow(() -> g());", 1),
        ("boolean b = check(x);", 0),
        ("Runnable r = () -> assertTrue(ok());", 0),  # stored, not run
    ],
)
def test_checks_whose_result_is_kept_count(text, counted):
    [case] = collect(body(text)).values()
    assert len(case.assertions) == counted


def test_a_kept_check_is_read_from_the_call():
    # the comma in the declared type doesn't end the statement
    [case] = collect(body("Map<K, V> m = assertDoesNotThrow(() -> f());")).values()
    assert [a.text for a in case.assertions] == ["assertDoesNotThrow(() -> f());"]


HELPERS = """
class CartTest {
    @Test
    void %s

    private void checkTotal(Cart cart, double total) {
        assertEquals(total, cart.total(), 1e-9);
        assertTrue(cart.count() > 0);
    }

    private void checkTotal(Cart cart) {
        checkTotal(cart, 0.0);
        fail("unreachable");
    }

    static void checkEmpty(Cart cart) { assertEquals(0, cart.count()); }

    void recurse(int n) { recurse(n - 1); assertTrue(n > 0); }

    private int noChecks(Cart cart) { return cart.count(); }

    @Nested
    class Inner {
        @Test
        void nested() { checkEmpty(new Cart()); }
    }
}
"""


@pytest.mark.parametrize(
    ("text", "counted"),
    [
        ("t() { checkTotal(cart, 1.5); }", 2),
        ("t() { this.checkTotal(cart, 1.5); }", 2),
        ("t() { checkTotal(cart); }", 3),  # the one-argument overload, through the other
        ("t() { for (Cart c : carts) checkEmpty(c); }", 1),
        ("t() { carts.forEach(c -> checkEmpty(c)); }", 1),
        ("t() { int n = noChecks(cart); }", 0),
        ("t() { recurse(3); }", 1),
        ("t() { other.checkTotal(cart, 1.5); }", 0),  # another object's method
        ("t() { Runnable r = () -> checkEmpty(cart); }", 0),  # stored, not run
    ],
)
def test_checks_in_helper_methods_of_the_class_count(text, counted):
    tests = collect(HELPERS % text)
    assert len(tests["CartTest::t"].assertions) == counted
    assert [a.line for a in tests["CartTest::t"].assertions] == [4] * counted


def test_nested_tests_use_the_outer_class_helpers():
    tests = collect(HELPERS % "t() {}")
    assert [a.text for a in tests["CartTest::Inner::nested"].assertions] == [
        "assertEquals(0, cart.count());"
    ]


@pytest.mark.parametrize(
    ("text", "counted"),
    [
        ("mocked.verify(() -> Util.accept(any(), eq(5)));", 1),
        ("mocked.verify(Util::reset, times(2));", 1),
        ("signature.verify(data);", 0),  # an ordinary method that happens to be named verify
        ("mocked.when(() -> Util.zero(any())).thenCallRealMethod();", 0),
    ],
)
def test_mocked_static_verification_counts(text, counted):
    [case] = collect(body(text)).values()
    assert len(case.assertions) == counted


def test_overloads_with_the_same_arity_are_told_apart_by_visible_types():
    source = """
    class CartTest {
        @Test
        void t(final Locale locale) {
            String id = "UTC";
            check(locale, id);
            check("en", "x");
        }

        private void check(Locale locale, String id) { fail("for " + id); }

        private void check(String tag, String id) {}
    }
    """
    [case] = collect(source).values()
    assert [(a.text, a.via) for a in case.assertions] == [('fail("for " + id);', "check")]


@pytest.mark.parametrize(
    ("text", "counted"),
    [
        ('verifyException(e, "too long");', 1),  # a project helper named like a check
        ("verifying(x);", 0),
        ("verifyCart(cart);", 2),  # the class's own helper: its body counts
    ],
)
def test_calls_named_verify_count_as_checks(text, counted):
    source = body(text).replace(
        "\n}\n",
        "\n    void verifyCart(Cart c) { assertEquals(1, c.count()); assertTrue(c.ok()); }\n}\n",
    )
    [case] = [t for t in collect(source).values() if t.name == "t"]
    assert len(case.assertions) == counted


def test_calls_to_functions_outside_the_file_are_recorded():
    [case] = [t for t in collect(HELPERS % "t() { checkTotal(cart); load(x); }").values()
              if t.name == "t"]  # fmt: skip
    assert "load" in case.external_calls
    assert "checkTotal" not in case.external_calls
