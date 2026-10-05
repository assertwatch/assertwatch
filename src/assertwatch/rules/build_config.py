"""Maven and Gradle settings that run fewer tests, ignore their failures or lower coverage.

Read by AW009 on both sides of a diff. Each function returns (message, needle) pairs, one per
loosening; anything that is the same, stricter or unreadable on both sides yields nothing.

- pom.xml: Surefire/Failsafe skips (`skipTests`, `skip`, `maven.test.skip`, `skipITs`),
  `testFailureIgnore`, `failIfNoTests=false`, excludes added, includes or `groups` narrowed,
  `excludedGroups` added; JaCoCo `skip`, `haltOnFailure=false`, a limit lowered or removed.
- build.gradle(.kts): on a test task, `ignoreFailures`, `enabled = false`, `exclude`,
  `excludeTestsMatching`, `excludeTags` (and JUnit 4/TestNG categories and groups), includes
  narrowed; JaCoCo `minimum` lowered or `failOnViolation = false`; `excludedTaskNames`.
- Command lines (CI workflows, .mvn/maven.config, .mvn/jvm.config): `-DskipTests` and the other
  skip or ignore properties above, `-x test` / `--exclude-task test`.

pom.xml is parsed with the stdlib's ElementTree after removing any DOCTYPE: with no DTD there
are no entity declarations to expand, and ElementTree never fetches external entities. A pom
that can't be read that way is reported, since Maven's own reader would read it.
"""

from __future__ import annotations

import re
import shlex
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

Loosening = tuple[str, str]  # message, needle (text to find the line by)

# Properties that skip tests or ignore their failures when set to this value.
_PROPERTIES = {
    "skipTests": "true",
    "maven.test.skip": "true",
    "skipITs": "true",
    "skipExec": "true",
    "maven.test.skip.exec": "true",
    "maven.test.failure.ignore": "true",
    "testFailureIgnore": "true",
    "failIfNoTests": "false",
    "failIfNoSpecifiedTests": "false",
    "surefire.failIfNoSpecifiedTests": "false",
    "it.failIfNoSpecifiedTests": "false",
    "jacoco.skip": "true",
    "jacoco.haltOnFailure": "false",
}
# Plugin parameters that do the same, and the plugins they belong to.
_PARAMETERS = {
    "skip": ("true", ("surefire", "failsafe", "jacoco")),
    "haltOnFailure": ("false", ("jacoco",)),
}
_RUNNERS = ("surefire", "failsafe")
# Each runner's includes when none are given.
_DEFAULT_INCLUDES = frozenset(
    {"**/Test*.java", "**/*Test.java", "**/*Tests.java", "**/*TestCase.java"}
)
_FAILSAFE_INCLUDES = frozenset({"**/IT*.java", "**/*IT.java", "**/*ITCase.java"})
# Custom properties that look like a test skip switch: skipUnitTests, skip.it, ignore.tests.
_SKIPPY = re.compile(r"(?i)skip|ignore|disable")
_TESTISH = re.compile(
    r"(?i)test|unit|integration|surefire|failsafe|jacoco|coverage|(^|[._-])its?($|[._-])|ITs"
)
# Larger build or CI files aren't read: they are reported as unchecked (fail closed).
MAX_CHARS = 1_000_000
# Longer Gradle statements aren't analysed; one in the new file is reported as unchecked.
MAX_STATEMENT = 8_000
_TOO_LARGE = ("The file is too large to check for loosened test settings", "")


# --- command lines ----------------------------------------------------------------------

_MAVEN = frozenset({"mvn", "mvnw", "mvnw.cmd", "mvn.cmd"})
_GRADLE = frozenset({"gradle", "gradlew", "gradlew.bat"})
_MAVEN_TEST_GOALS = frozenset(
    {"test", "integration-test", "verify", "install", "deploy", "package"}
)
_GRADLE_TEST_TASK = re.compile(r"(?i)^(\w*test\w*|check|build)$")
_SEGMENTS = re.compile(r"&&|\|\||[;|]")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Surefire and Failsafe user properties that select or exclude tests.
_SELECTING = frozenset(
    {
        "groups",
        "excludedGroups",
        "surefire.excludedGroups",
        "surefire.excludesFile",
        "surefire.includesFile",
        "failsafe.excludesFile",
        "failsafe.includesFile",
    }
)
# Options of one tool that build less: Maven's non-recursive build, project list and resume
# point leave modules out; Gradle's dry run skips every task.
_MAVEN_PARTIAL = frozenset({"-N", "--non-recursive"})
_MAVEN_PARTIAL_ARG = frozenset({"-pl", "--projects", "-rf", "--resume-from"})
_GRADLE_PARTIAL = frozenset({"-m", "--dry-run"})
# Environment variables whose options every Maven or Gradle run (or JVM) picks up.
_OPTION_VARS = re.compile(
    r"^\s*(?:-\s*)?(?:export\s+)?"
    r"(MAVEN_ARGS|MAVEN_OPTS|MAVEN_CONFIG|GRADLE_OPTS|JAVA_TOOL_OPTIONS|JDK_JAVA_OPTIONS"
    r"|_JAVA_OPTIONS)\s*[:=]\s*(.*)$"
)


def flags(tokens: list[str]) -> Counter[str]:
    """Loosening options in a command's tokens: `-DskipTests`, `-x test`, ..."""
    found: Counter[str] = Counter()
    for i, token in enumerate(tokens):
        if token in ("-D", "--define") and i + 1 < len(tokens):  # `-D skipTests`
            token = "-D" + tokens[i + 1]
        elif token.startswith("--define="):
            token = "-D" + token.removeprefix("--define=")
        elif _ENV_ASSIGN.match(token):  # MAVEN_ARGS=-DskipTests mvn verify
            found += flags(_tokens(token.partition("=")[2]))
            continue
        if token in ("-fn", "--fail-never"):
            found[token] += 1
        elif token.startswith("-D"):
            name, eq, value = token[2:].partition("=")
            if name in _PROPERTIES and (value if eq else "true").lower() == _PROPERTIES[name]:
                found[token] += 1
            elif name in ("test", "it.test") and value:  # runs only these, or `!` excludes
                found[token] += 1
            elif name in _SELECTING and value:  # runs only some tests, or reads a list to skip
                found[token] += 1
            elif "$" in name or (name in _PROPERTIES and "$" in value):
                found[token] += 1  # set from a variable: may well skip tests
        elif token in ("-x", "--exclude-task") and i + 1 < len(tokens):
            if _GRADLE_TEST_TASK.match(tokens[i + 1].rsplit(":", 1)[-1]):
                found[f"{token} {tokens[i + 1]}"] += 1
        elif token.startswith("--exclude-task="):
            if _GRADLE_TEST_TASK.match(token.partition("=")[2].rsplit(":", 1)[-1]):
                found[token] += 1
        elif token == "--tests" and i + 1 < len(tokens) or token.startswith("--tests="):
            found[token if "=" in token else f"{token} {tokens[i + 1]}"] += 1  # only these
        elif token.startswith("-x") and len(token) > 2:  # Gradle reads `-xtest` as `-x test`
            if _GRADLE_TEST_TASK.match(token[2:].rsplit(":", 1)[-1]):
                found[token] += 1
    return found


def _partial(tool: str, args: list[str]) -> Counter[str]:
    """Options that make this Maven or Gradle run build only part of the project."""
    found: Counter[str] = Counter()
    for i, arg in enumerate(args):
        option = arg.partition("=")[0]
        if tool in _MAVEN and arg in _MAVEN_PARTIAL:
            found[arg] += 1
        elif tool in _MAVEN and option in _MAVEN_PARTIAL_ARG:
            found[arg if "=" in arg or i + 1 >= len(args) else f"{arg} {args[i + 1]}"] += 1
        elif tool in _GRADLE and arg in _GRADLE_PARTIAL:
            found[arg] += 1
    return found


def _tokens(text: str) -> list[str]:
    try:
        return shlex.split(text, comments=False)
    except ValueError:
        return text.split()


@dataclass
class _Commands:
    runs: int = 0  # Maven/Gradle runs of any kind
    enforcing: int = 0  # Maven/Gradle runs that run tests and fail on failures
    inside: Counter[str] = field(default_factory=Counter)  # loosening flags on those runs
    outside: Counter[str] = field(default_factory=Counter)  # anywhere else (env, MAVEN_OPTS)


def _commands(lines: list[str]) -> _Commands:
    result = _Commands()
    joined = "\n".join(lines).replace("\\\n", " ")
    for line in joined.splitlines():
        option_var = _OPTION_VARS.match(line)
        if option_var and "$" in option_var[2]:
            result.outside[f"{option_var[1]} set from a variable"] += 1
        for segment in _SEGMENTS.split(line):
            tokens = _tokens(segment)
            if _tool(tokens) is None:  # a command inside a string: sh './mvnw verify'
                tokens = re.findall(r"[^\s'\"]+", segment)
            found = flags(tokens)
            tool = _tool(tokens)
            if tool is None:
                result.outside += found
                continue
            for arg in tokens[tool + 1 :]:
                if arg.startswith("$") or "`" in arg or "$(" in arg:
                    found[f"{arg} (arguments from a variable)"] += 1
            found += _partial(tokens[tool].rsplit("/", 1)[-1], tokens[tool + 1 :])
            result.inside += found
            result.runs += 1
            if _runs_tests(tokens[tool].rsplit("/", 1)[-1], tokens[tool + 1 :]) and not found:
                result.enforcing += 1
    return result


def _tool(tokens: list[str]) -> int | None:
    return next((i for i, t in enumerate(tokens) if t.rsplit("/", 1)[-1] in _MAVEN | _GRADLE), None)


def _runs_tests(tool: str, args: list[str]) -> bool:
    words = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
        elif arg.startswith("-"):
            skip_next = arg in ("-x", "--exclude-task", "-p", "-f", "--file", "-pl", "-P", "-s")
        else:
            words.append(arg)
    if tool in _MAVEN:
        return any(
            w in _MAVEN_TEST_GOALS or w.endswith((":test", ":integration-test")) for w in words
        )
    return any(_GRADLE_TEST_TASK.match(w.rsplit(":", 1)[-1]) for w in words)


def workflow(before: list[str], after: list[str]) -> Iterator[Loosening]:
    """CI lines: a skip flag that takes a test run away, or one set outside any run; or a
    workflow that still builds with Maven/Gradle but no longer runs any tests."""
    if sum(map(len, after)) > MAX_CHARS:
        yield _TOO_LARGE
        return
    old, new = _commands(before), _commands(after)
    added = (new.inside + new.outside) - (old.inside + old.outside)
    if added and (new.enforcing < old.enforcing or new.outside - old.outside):
        shown = ", ".join(f"`{f}`" for f in sorted(added))
        yield (f"A CI build now skips tests or ignores their failures: {shown}", next(iter(added)))
    elif old.enforcing and not new.enforcing and new.runs:
        yield ("The CI workflow still builds with Maven or Gradle but no longer runs tests", "")


def config_flags(before: str | None, after: str | None) -> Iterator[Loosening]:
    """.mvn/maven.config and .mvn/jvm.config apply to every Maven run."""
    if after is not None and len(after) > MAX_CHARS:
        yield _TOO_LARGE
        return
    added = flags(_tokens(after or "")) - flags(_tokens(before or ""))
    for flag in sorted(added):
        yield (f"Every Maven run now gets `{flag}`", flag)


# --- pom.xml ----------------------------------------------------------------------------


@dataclass
class _Pom:
    switches: set[tuple[str, str]] = field(default_factory=set)  # (owner, setting)
    excludes: set[tuple[str, str]] = field(default_factory=set)  # (plugin, pattern)
    includes: dict[str, set[str]] = field(default_factory=dict)  # plugin -> patterns
    groups: dict[str, set[str]] = field(default_factory=dict)  # owner -> included tags
    excluded_groups: set[tuple[str, str]] = field(default_factory=set)
    limits: dict[tuple[str, str, str, str], float] = field(default_factory=dict)
    modules: set[tuple[str, str]] = field(default_factory=set)  # (profile or "", module)
    properties: dict[str, str] = field(default_factory=dict)
    sources: set[tuple[str, str]] = field(default_factory=set)  # test source/class folders
    runners: set[str] = field(default_factory=set)  # Surefire/Failsafe plugins declared


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text(element: ET.Element | None) -> str:
    return (element.text or "").strip() if element is not None else ""


def _child(element: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in element if _local(c.tag) == name), None)


def _children(element: ET.Element | None) -> list[ET.Element]:
    return list(element) if element is not None else []


def _ratio(text: str) -> float | None:
    try:
        return float(text[:-1]) / 100 if text.endswith("%") else float(text)
    except ValueError:
        return None


# Properties every build machine (or CI runner) has: activating on one isn't asking for it.
_AMBIENT_PROPERTY = re.compile(r"^(env|java|os|user|file|line|path)\.")


def _active(profile: ET.Element) -> bool:
    """A profile Maven may turn on without being asked: activeByDefault, a missing property,
    an environment variable (CI sets its own) or system property, and any other condition
    (jdk, os, file, Maven 4's packaging or condition): assumed to hold, so fail closed. Only a
    user property someone must pass (`-Dquick`) keeps a profile off."""
    activation = _child(profile, "activation")
    if activation is None:
        return False
    for condition in activation:
        kind = _local(condition.tag)
        if kind == "activeByDefault":
            if _text(condition).lower() == "true":
                return True
        elif kind == "property":
            name = _text(_child(condition, "name"))
            if name.startswith("!") or _AMBIENT_PROPERTY.match(name):
                return True
        elif kind:
            return True
    return False


def _effective(element: ET.Element, settings: bool) -> Iterator[ET.Element]:
    """Elements Maven applies on a plain build: profiles that must be asked for are skipped
    (in a settings file every profile counts: activeProfiles turns them on)."""
    yield element
    for child in element:
        if _local(child.tag) == "profile" and not settings and not _active(child):
            continue
        yield from _effective(child, settings)


def _pom(text: str | None, settings: bool = False) -> _Pom | None:
    if text is None:
        return _Pom()
    text = text.removeprefix("\ufeff")
    if "<!doctype" in text.lower() or "<!entity" in text.lower():
        return None  # no pom needs a DTD; never let the parser expand one
    try:
        root = ET.fromstring(text)
        pom = _Pom()
        for properties in (e for e in _effective(root, settings) if _local(e.tag) == "properties"):
            pom.properties |= {_local(p.tag): _text(p) for p in properties}
        _walk(root, "", "", pom, settings)
    except (ET.ParseError, RecursionError, ValueError):
        return None  # unreadable, or nested past what a real pom needs
    profiles = _child(root, "profiles")
    owners = [(root, "")] + [(p, _text(_child(p, "id"))) for p in _children(profiles)]
    for profile, owner in owners:
        for module in _children(_child(profile, "modules")):
            if _local(module.tag) == "module":
                pom.modules.add((owner, _text(module).rstrip("/")))
    return pom


def _skip_switch(name: str) -> bool:
    return bool(_SKIPPY.search(name) and _TESTISH.search(name))


def _resolve(value: str, properties: dict[str, str]) -> str:
    """Each `${name}` replaced by the pom's own property, a few levels deep, as Maven does
    (`${a}${b}` included)."""
    for _ in range(5):
        expanded = _REFERENCE.sub(lambda m: properties.get(m[1], m[0]), value)
        if expanded == value:
            break
        value = expanded[:1000]
    return value


_REFERENCE = re.compile(r"\$\{([^${}]{1,200})\}")


def _walk(element: ET.Element, plugin: str, parent: str, pom: _Pom, settings: bool) -> None:
    tag = _local(element.tag)
    if tag == "profile" and not settings and not _active(element):
        return
    if tag == "plugin":
        plugin = _resolve(_text(_child(element, "artifactId")), pom.properties)
        if any(r in plugin for r in _RUNNERS):
            pom.runners.add(plugin)
    owner = "properties" if parent == "properties" else plugin
    value = _resolve(_text(element), pom.properties).lower()
    runner = any(r in plugin for r in _RUNNERS)
    if (parent == "properties" or plugin) and _PROPERTIES.get(tag) == value:
        pom.switches.add((owner, tag))
    elif parent == "properties" and value == "true" and _skip_switch(tag):
        pom.switches.add((owner, tag))  # e.g. skipUnitTests, read by a module's plugin config
    elif tag in _PARAMETERS and plugin:
        wanted, plugins = _PARAMETERS[tag]
        if value == wanted and any(p in plugin for p in plugins):
            pom.switches.add((owner, tag))
    if runner and tag == "exclude" and parent == "excludes":
        pom.excludes.add((plugin, _text(element)))
    elif runner and tag == "excludesFile":
        pom.excludes.add((plugin, f"excludesFile {_text(element)}"))
    elif runner and (tag == "include" and parent == "includes" or tag == "test"):
        pom.includes.setdefault(plugin, set()).add(_text(element))
    elif runner and tag == "excludeJUnit5Engines":
        pom.excludes |= {(plugin, f"engine {e.strip()}") for e in _text(element).split(",")}
    elif runner and tag == "includeJUnit5Engines":
        engines = {e.strip() for e in _text(element).split(",") if e.strip()}
        pom.groups.setdefault(f"{plugin} engines", set()).update(engines)
    if tag in ("testSourceDirectory", "testClassesDirectory"):
        pom.sources.add((tag, _text(element)))
    if tag in ("groups", "excludedGroups") and (runner or parent == "properties"):
        tags = {t.strip() for t in _text(element).split(",") if t.strip()}
        if tag == "groups":
            pom.groups.setdefault(owner, set()).update(tags)
        else:
            pom.excluded_groups |= {(owner, t) for t in tags}
    if tag == "rule" and "jacoco" in plugin:
        scope = _text(_child(element, "element")) or "BUNDLE"
        for limit in element.iter():
            if _local(limit.tag) != "limit":
                continue
            counter = _text(_child(limit, "counter")) or "INSTRUCTION"
            kind = _text(_child(limit, "value")) or "COVEREDRATIO"
            for bound in ("minimum", "maximum"):
                number = _ratio(_resolve(_text(_child(limit, bound)), pom.properties))
                if number is None:
                    continue
                # The same limit declared twice: keep the looser one, whichever Maven uses.
                key = (scope, counter, kind, bound)
                pick = min if bound == "minimum" else max
                pom.limits[key] = pick(number, pom.limits.get(key, number))
    for child in element:
        _walk(child, plugin, tag, pom, settings)


def maven(
    before: str | None,
    after: str | None,
    gone: Callable[[str], bool] = lambda module: False,
    *,
    settings: bool = False,
) -> Iterator[Loosening]:
    """gone(module): the module is deleted or moved with its tests, not just left out."""
    if after is None:
        return  # a deleted root pom breaks the build; a module's is checked through gone()
    if len(after) > MAX_CHARS:
        yield _TOO_LARGE
        return
    old, new = _pom(before, settings), _pom(after, settings)
    if new is None:
        yield ("The file can't be read, so its test settings can't be compared", "")
        return
    if old is None:
        old = _Pom()  # the old file unreadable: everything the new one loosens counts
    for owner, setting in sorted(new.switches - old.switches):
        where = "a property" if owner == "properties" else owner
        yield (f"pom.xml now sets `{setting}` ({where}), so tests are skipped or not "
               "enforced", setting)  # fmt: skip
    for plugin, pattern in sorted(new.excludes - old.excludes):
        yield (f"{plugin} in pom.xml now excludes `{pattern}`", pattern)
    for plugin in sorted(set(old.includes) | set(new.includes)):
        if "failsafe" in plugin and plugin not in old.runners:
            continue  # Failsafe runs only once declared: a new one adds tests, it can't narrow
        defaults = _FAILSAFE_INCLUDES if "failsafe" in plugin else _DEFAULT_INCLUDES
        lost = _narrowed(old.includes.get(plugin) or defaults, new.includes.get(plugin) or defaults)
        if lost:
            yield (f"{plugin} in pom.xml no longer includes {_list(lost)}", "include")
    for owner in sorted(set(old.groups) | set(new.groups)):
        lost = _narrowed(old.groups.get(owner, set()), new.groups.get(owner, set()))
        if lost:
            label = "engines" if owner.endswith(" engines") else "groups"
            yield (f"pom.xml `{label}` now runs fewer tests: {_list(lost)} left out",
                   "groups")  # fmt: skip
    for tag, folder in sorted(new.sources - old.sources):
        yield (f"pom.xml now reads tests from another folder: `{tag}` is `{folder}`", tag)
    for _owner, tag in sorted(new.excluded_groups - old.excluded_groups):
        yield (f"pom.xml now excludes the test group `{tag}`", "excludedGroups")
    yield from _limits(old.limits, new.limits)

    def built(pom: _Pom, module: str, profile: str | None = "") -> bool:
        return any(m == module and (profile is None or p == profile) for p, m in pom.modules)

    # A module dropped from the default build, or from every profile that built it.
    lost = {m for p, m in old.modules if not built(new, m, None if p else "")}
    for module in sorted(lost):
        if not gone(module):
            where = f" (now only in profile `{p}`)" if (p := _profile_of(module, new)) else ""
            yield (f"pom.xml no longer builds module `{module}`{where}, so its tests don't "
                   "run", "<modules>")  # fmt: skip


def _profile_of(module: str, pom: _Pom) -> str:
    return next((p for p, m in sorted(pom.modules) if m == module and p), "")


def _narrowed(before: set[str], after: set[str]) -> set[str]:
    """What an include filter no longer selects; an empty filter selects everything."""
    if not after:
        return set()
    if not before:
        return {"everything else"}
    return before - after


def _list(items: set[str]) -> str:
    return ", ".join(f"`{i}`" if i != "everything else" else i for i in sorted(items))


def _limits(
    before: dict[tuple[str, str, str, str], float], after: dict[tuple[str, str, str, str], float]
) -> Iterator[Loosening]:
    for key, was in sorted(before.items()):
        scope, counter, kind, bound = key
        now = after.get(key)
        name = f"JaCoCo {counter} {kind} {bound}"
        if now is None:
            yield (f"{name} was removed (was {was:g})", "jacoco")
        elif now < was if bound == "minimum" else now > was:
            yield (f"{name} was {'lowered' if bound == 'minimum' else 'raised'} to {now:g} "
                   f"(was {was:g})", bound)  # fmt: skip


# --- Gradle -----------------------------------------------------------------------------

_STRING = re.compile(r"'''.*?'''|\"\"\".*?\"\"\"|'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"", re.S)
# One pass over a script: strings and comments, each allowed to run to the end of the
# input (or line) unterminated, so the scan never backtracks; everything else in chunks.
_LEX = re.compile(
    r"'''.*?(?:'''|\Z)|\"\"\".*?(?:\"\"\"|\Z)|'(?:\\.|[^'\\\n])*'?|\"(?:\\.|[^\"\\\n])*\"?"
    r"|//[^\n]*|/\*.*?(?:\*/|\Z)|[^'\"/]+|/",
    re.S,
)
_UNICODE_ESCAPE = re.compile(r"\\u+([0-9a-fA-F]{4})")
_TEST_HEADER = re.compile(r"(?i)test")
# `x = v`, `x v` (Groovy), after any receiver: `tasks.getByName("test").enabled = false`.
_SETTER = re.compile(r"\bset([A-Z]\w{0,100})\s*\(")  # setEnabled(false)
_PROPERTY_SET = re.compile(r"\b(\w{1,100})\s*\.\s*set\s*\(")  # enabled.set(false)
# setProperty('enabled', false): Groovy's dynamic setter, by name
_SET_PROPERTY = re.compile(r"\bsetProperty\s*\(\s*(['\"])(\w{1,100})\1\s*,(.*)\)\s*$", re.S)
# test['enabled'] and test.'enabled': a property named by a string, read as test.enabled
_NAMED_BY_STRING = re.compile(r"(?:\[\s*(['\"])(\w{1,100})\1\s*\]|\.\s*(['\"])(\w{1,100})\3)\s*$")
_NOT_ASSIGN = "=!<>+-*/?:~%&|^"  # `==`, `!=`, `+=`, `?=`, ... are not plain assignments
_EXCLUDE_LISTS = (
    "excludeTestsMatching",
    "excludeTags",
    "excludeCategories",
    "excludeGroups",
    "excludeEngines",
)
_INCLUDE_LISTS = (
    "includeTestsMatching",
    "includeTags",
    "includeCategories",
    "includeGroups",
    "includeEngines",
)
_SOURCES = re.compile(r"\b(srcDirs|setSrcDirs|testClassesDirs|setTestClassesDirs|classpath)\b")
_DEPENDS_REMOVED = re.compile(
    r"dependsOn\b.*\b(remove|removeAll|clear)\b|setDependsOn|dependsOn\s*="
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


# Blocks whose include/exclude calls select files or dependencies, not tests.
_NOT_TEST_BLOCKS = re.compile(r"dependencies|jacoco|resources|\bjar\b|javadoc|copy|zip", re.I)
_ALL_TEST_TASKS = "*Test"


@dataclass
class _Gradle:
    switches: Counter[str] = field(default_factory=Counter)
    excludes: set[tuple[str, str, str]] = field(default_factory=set)  # (task, kind, pattern)
    includes: dict[tuple[str, str], set[str]] = field(default_factory=dict)  # (task, kind)
    tasks: set[str] = field(default_factory=set)  # test tasks configured
    minimums: list[float] = field(default_factory=list)
    sources: set[tuple[str, str]] = field(default_factory=set)  # (task, assignment)
    projects: set[str] = field(default_factory=set)  # settings.gradle includes
    unreadable: bool = False  # a statement too long to analyse


def _task(headers: tuple[str, ...]) -> str:
    """The test task a block configures, the same in Groovy and Kotlin: `test {`,
    `tasks.test {`, `tasks.named<Test>("test") {` and `tasks.named('test') {` are all `test`."""
    header = next((h for h in headers if _TEST_HEADER.search(h)), "")
    if re.search(r"withType\s*[(<]\s*Test\b", header):
        return _ALL_TEST_TASKS
    quoted = _STRING.search(header)
    if quoted:
        return quoted[0].strip("'\"")
    header = re.sub(r"^(tasks\.|task\s+)", "", header.strip())
    name = re.match(r"\w+", header)
    return name[0] if name else header


def _statements(text: str) -> Iterator[tuple[str, tuple[str, ...]]]:
    """Each statement with the headers of the blocks around it, outermost first. Linear in
    the size of the script."""
    text = _UNICODE_ESCAPE.sub(lambda m: chr(int(m[1], 16)), text.removeprefix("\ufeff"))
    stack: list[str] = []
    depth = 0  # open ( and [ in the current statement
    current: list[str] = []

    def flush() -> Iterator[tuple[str, tuple[str, ...]]]:
        statement = "".join(current).strip()
        current.clear()
        if statement:
            yield statement, tuple(stack)

    for token in _LEX.finditer(text):
        chunk = token[0]
        if chunk[0] in "'\"":
            current.append(chunk)
            continue
        if chunk.startswith(("//", "/*")):
            current.append("\n" if "\n" in chunk else " ")
            if chunk.startswith("//"):
                continue
            chunk = ""
        for char in chunk:
            if char in "([":
                depth += 1
            elif char in ")]":
                depth = max(depth - 1, 0)
            if char == "\n" and (depth or _continues(current)):
                char = " "  # the statement goes on: `include 'a',\n 'b'`, `exclude(\n "x"\n)`
            if char == "{":
                stack.append("".join(current).strip().rsplit("\n", 1)[-1][-MAX_STATEMENT:])
                current.clear()
                depth = 0
            elif char in "};\n":
                yield from flush()
                depth = 0
                if char == "}" and stack:
                    stack.pop()
            else:
                current.append(char)
    yield from flush()


def _continues(current: list[str]) -> bool:
    """The statement so far ends in a way that carries it onto the next line."""
    for part in reversed(current):
        tail = part.rstrip()
        if tail:
            return tail.endswith((",", "=", "+", "(", "&&", "||"))
    return False


def _assignment(statement: str) -> tuple[str, str, str] | None:
    """(receiver, property, value) of an assignment in any of its spellings, or None:
    `x = v`, Groovy's `x v`, `setX(v)`, `x.set(v)`, `setProperty('x', v)`, `t['x'] = v` and
    `t.'x' = v`, after any receiver."""
    by_name = _SET_PROPERTY.search(statement)
    if by_name:
        return _named(statement[: by_name.start()], by_name[2], by_name[3])
    setter = _SETTER.search(statement) or _PROPERTY_SET.search(statement)
    if setter and statement.rstrip().endswith(")"):
        name = setter[1]
        prefix = statement[: setter.start()]
        value = statement[setter.end() : statement.rstrip().rfind(")")]
        return _named(prefix, name, value)
    equals = _equals_sign(statement)
    if equals is None:
        parts = statement.split()
        if len(parts) != 2 or not all(c.isalnum() or c in "._" for c in parts[0]):
            return None
        left, value = parts
    else:
        left, value = statement[:equals], statement[equals + 1 :]
    left = left.rstrip()
    quoted = _NAMED_BY_STRING.search(left)
    if quoted:
        left = f"{left[: quoted.start()]}.{quoted[2] or quoted[4]}"
    start = len(left)
    while start and (left[start - 1].isalnum() or left[start - 1] == "_"):
        start -= 1
    return _named(left[:start], left[start:], value)


def _named(prefix: str, name: str, value: str) -> tuple[str, str, str] | None:
    if not name or not name[0].isalpha():
        return None
    if len(name) > 2 and name.startswith("is") and name[2].isupper():
        name = name[2:]  # Kotlin's isEnabled, isIgnoreFailures
    return prefix, name[0].lower() + name[1:], value.strip()


def _equals_sign(statement: str) -> int | None:
    """Index of the first plain `=` outside strings, or None."""
    quote = ""
    escaped = False
    for i, char in enumerate(statement):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "=":
            before = statement[i - 1] if i else ""
            after = statement[i + 1] if i + 1 < len(statement) else ""
            if before not in _NOT_ASSIGN and after not in "=~>":
                return i
    return None


def _gradle(text: str | None, settings: bool = False) -> _Gradle:
    facts = _Gradle()
    for statement, stack in _statements(text or ""):
        if len(statement) > MAX_STATEMENT:
            facts.unreadable = True
            continue
        headers = " ".join(stack)
        in_test = bool(_TEST_HEADER.search(headers)) and not _NOT_TEST_BLOCKS.search(headers)
        task = _task(stack) if in_test else _ALL_TEST_TASKS
        if in_test:
            facts.tasks.add(task)
        word = statement.split("(", 1)[0].split()[0] if statement.split() else ""
        strings = [s[1:-1] for s in _STRING.findall(statement)]
        bare = word.rsplit(".", 1)[-1]
        if settings:
            if bare == "include" and not stack:
                facts.projects |= set(strings)
            continue
        assign = _assignment(statement)
        if assign:
            prefix, name, value = assign
            test_task = in_test or bool(_TEST_HEADER.search(prefix))
            report = re.search(r"(?i)report", prefix + headers)
            if name == "ignoreFailures" and value != "false" and test_task:
                facts.switches[f"ignoreFailures = {value}"] += 1
            elif name == "enabled" and value != "true" and "violationRules" in headers:
                facts.switches["a JaCoCo rule disabled"] += 1
            elif name == "enabled" and value != "true" and test_task and not report:
                facts.switches[f"a test task disabled (`enabled = {value}`)"] += 1
            elif name == "failOnViolation" and value != "true":
                facts.switches[f"failOnViolation = {value}"] += 1
        if "excludedTaskNames" in statement and any(_TEST_HEADER.search(s) for s in strings):
            facts.switches["test tasks excluded (`excludedTaskNames`)"] += 1
        conditional = stack and re.search(r"\bonlyIf\b", stack[-1])
        if in_test and (conditional or bare == "onlyIf") and statement != "true":
            facts.switches["a test task made conditional (`onlyIf`)"] += 1
        if in_test and stack and re.search(r"(^|\.)\s*exclude\s*$", stack[-1]):
            facts.switches["tests excluded by a closure (`exclude { ... }`)"] += 1
        if _DEPENDS_REMOVED.search(statement) and re.search(
            r"\b(check|build)\b", statement + " " + headers
        ):
            facts.switches["`check` or `build` no longer depending on tests"] += 1
        if (in_test or "test" in statement.lower()) and _SOURCES.search(statement):
            if "+=" not in statement and (assign or "set" in statement):
                facts.sources.add((task, re.sub(r"\s+", "", statement)))
        if bare in _EXCLUDE_LISTS or (bare == "exclude" and in_test):
            facts.excludes |= {(task, bare, s) for s in strings}
        elif bare in _INCLUDE_LISTS or (bare == "include" and in_test):
            facts.includes.setdefault((task, bare), set()).update(strings)
        elif bare == "minimum" and (number := _NUMBER.search(statement.split("minimum", 1)[1])):
            facts.minimums.append(float(number[0]))
    return facts


def gradle(
    before: str | None,
    after: str | None,
    name: str = "build.gradle",
    gone: Callable[[str], bool] = lambda project: False,
) -> Iterator[Loosening]:
    """gone(folder): the subproject is deleted or moved with its tests, not just left out."""
    if after is None:
        return
    if len(after) > MAX_CHARS:
        yield _TOO_LARGE
        return
    settings = name.startswith("settings.")
    old, new = _gradle(before, settings), _gradle(after, settings)
    if new.unreadable:
        yield (f"A statement longer than {MAX_STATEMENT} characters can't be checked", "")
    for project in sorted(old.projects - new.projects):
        if not gone(project.strip(":").replace(":", "/")):
            yield (f"Gradle settings no longer include project `{project}`, so its tests "
                   "don't run", project)  # fmt: skip
    for switch in sorted(new.switches - old.switches):
        yield (f"The Gradle build now has {switch}", switch.split()[0].strip("`"))
    # A test task the change adds runs tests that didn't run before, whatever it selects.
    # `test` exists in every Java build, even when this file didn't configure it before.
    existed = old.tasks | {_ALL_TEST_TASKS, "test"}
    for task, assignment in sorted(new.sources - old.sources):
        if task in existed:
            yield (f"Gradle task `{task}` now reads tests from elsewhere: `{assignment}`",
                   assignment.split("=")[0])  # fmt: skip
    for task, kind, pattern in sorted(new.excludes - old.excludes):
        if task in existed:
            yield (f"Gradle task `{task}` now excludes `{pattern}` ({kind})", pattern)
    for task, kind in sorted(set(old.includes) | set(new.includes)):
        key = (task, kind)
        lost = _narrowed(old.includes.get(key, set()), new.includes.get(key, set()))
        if lost and task in existed:
            yield (f"Gradle task `{task}` {kind} now runs fewer tests: {_list(lost)} left "
                   "out", kind)  # fmt: skip
    was, now = sorted(old.minimums), sorted(new.minimums)
    if len(now) < len(was) or any(n < w for n, w in zip(now, was, strict=False)):
        shown = ", ".join(f"{m:g}" for m in now) or "none"
        yield (f"JaCoCo minimum lowered or removed: {shown} (was "
               f"{', '.join(f'{m:g}' for m in was)})", "minimum")  # fmt: skip
