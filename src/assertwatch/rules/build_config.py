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
# Surefire's includes when none are given.
_DEFAULT_INCLUDES = frozenset(
    {"**/Test*.java", "**/*Test.java", "**/*Tests.java", "**/*TestCase.java"}
)
_DOCTYPE = re.compile(r"<!DOCTYPE[^\[>]*(\[.*?\])?\s*>", re.S | re.I)


# --- command lines ----------------------------------------------------------------------

_MAVEN = frozenset({"mvn", "mvnw", "mvnw.cmd", "mvn.cmd"})
_GRADLE = frozenset({"gradle", "gradlew", "gradlew.bat"})
_MAVEN_TEST_GOALS = frozenset(
    {"test", "integration-test", "verify", "install", "deploy", "package"}
)
_GRADLE_TEST_TASK = re.compile(r"(?i)^(\w*test\w*|check|build)$")
_SEGMENTS = re.compile(r"&&|\|\||[;|]")


def flags(tokens: list[str]) -> Counter[str]:
    """Loosening options in a command's tokens: `-DskipTests`, `-x test`, ..."""
    found: Counter[str] = Counter()
    for i, token in enumerate(tokens):
        if token == "-D" and i + 1 < len(tokens):  # `-D skipTests`, as Maven also accepts
            token = "-D" + tokens[i + 1]
        if token.startswith("-D"):
            name, eq, value = token[2:].partition("=")
            if name in _PROPERTIES and (value if eq else "true").lower() == _PROPERTIES[name]:
                found[token] += 1
            elif name in ("test", "it.test") and "!" in value:  # `-Dtest='!CartTest'`
                found[token] += 1
            elif name in ("excludedGroups", "surefire.excludedGroups") and value:
                found[token] += 1
        elif token in ("-x", "--exclude-task") and i + 1 < len(tokens):
            if _GRADLE_TEST_TASK.match(tokens[i + 1].rsplit(":", 1)[-1]):
                found[f"{token} {tokens[i + 1]}"] += 1
        elif token.startswith("--exclude-task="):
            if _GRADLE_TEST_TASK.match(token.partition("=")[2].rsplit(":", 1)[-1]):
                found[token] += 1
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
        for segment in _SEGMENTS.split(line):
            tokens = _tokens(segment)
            found = flags(tokens)
            tool = next(
                (i for i, t in enumerate(tokens) if t.rsplit("/", 1)[-1] in _MAVEN | _GRADLE),
                None,
            )
            if tool is None:
                result.outside += found
                continue
            result.inside += found
            result.runs += 1
            if _runs_tests(tokens[tool].rsplit("/", 1)[-1], tokens[tool + 1 :]) and not found:
                result.enforcing += 1
    return result


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
    old, new = _commands(before), _commands(after)
    added = (new.inside + new.outside) - (old.inside + old.outside)
    if added and (new.enforcing < old.enforcing or new.outside - old.outside):
        shown = ", ".join(f"`{f}`" for f in sorted(added))
        yield (f"A CI build now skips tests or ignores their failures: {shown}", next(iter(added)))
    elif old.enforcing and not new.enforcing and new.runs:
        yield ("The CI workflow still builds with Maven or Gradle but no longer runs tests", "")


def config_flags(before: str | None, after: str | None) -> Iterator[Loosening]:
    """.mvn/maven.config and .mvn/jvm.config apply to every Maven run."""
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


def _pom(text: str | None) -> _Pom | None:
    if text is None:
        return _Pom()
    text = _DOCTYPE.sub("", text)
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.I):
        return None  # a DTD the pattern above didn't remove: don't let the parser see it
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    pom = _Pom()
    for properties in (e for e in root.iter() if _local(e.tag) == "properties"):
        pom.properties |= {_local(p.tag): _text(p) for p in properties}
    _walk(root, "", "", pom)
    profiles = _child(root, "profiles")
    owners = [(root, "")] + [(p, _text(_child(p, "id"))) for p in _children(profiles)]
    for profile, owner in owners:
        for module in _children(_child(profile, "modules")):
            if _local(module.tag) == "module":
                pom.modules.add((owner, _text(module).rstrip("/")))
    return pom


def _resolve(value: str, properties: dict[str, str]) -> str:
    """`${name}` replaced by the pom's own property, a few levels deep (Maven does the same)."""
    for _ in range(5):
        ref = re.fullmatch(r"\$\{([^}]+)\}", value)
        if not ref or ref[1] not in properties:
            break
        value = properties[ref[1]]
    return value


def _walk(element: ET.Element, plugin: str, parent: str, pom: _Pom) -> None:
    tag = _local(element.tag)
    if tag == "plugin":
        plugin = _text(_child(element, "artifactId"))
    owner = "properties" if parent == "properties" else plugin
    value = _resolve(_text(element), pom.properties).lower()
    runner = any(r in plugin for r in _RUNNERS)
    if (parent == "properties" or plugin) and _PROPERTIES.get(tag) == value:
        pom.switches.add((owner, tag))
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
                number = _ratio(_text(_child(limit, bound)))
                if number is not None:
                    pom.limits[(scope, counter, kind, bound)] = number
    for child in element:
        _walk(child, plugin, tag, pom)


def maven(
    before: str | None, after: str | None, gone: Callable[[str], bool] = lambda module: False
) -> Iterator[Loosening]:
    """gone(module): the change deletes or moves that module's pom.xml."""
    if after is None:
        return  # the module is gone; its tests' removal is reported on the test files
    old, new = _pom(before), _pom(after)
    if new is None:
        if old is not None:
            yield ("pom.xml can't be read, so its test settings can't be compared", "")
        return
    if old is None:
        return
    for owner, setting in sorted(new.switches - old.switches):
        where = "a property" if owner == "properties" else owner
        yield (f"pom.xml now sets `{setting}` ({where}), so tests are skipped or not "
               "enforced", setting)  # fmt: skip
    for plugin, pattern in sorted(new.excludes - old.excludes):
        yield (f"{plugin} in pom.xml now excludes `{pattern}`", pattern)
    for plugin in sorted(set(old.includes) | set(new.includes)):
        lost = _narrowed(
            old.includes.get(plugin) or _DEFAULT_INCLUDES,
            new.includes.get(plugin) or _DEFAULT_INCLUDES,
        )
        if lost:
            yield (f"{plugin} in pom.xml no longer includes {_list(lost)}", "include")
    for owner in sorted(set(old.groups) | set(new.groups)):
        lost = _narrowed(old.groups.get(owner, set()), new.groups.get(owner, set()))
        if lost:
            yield (f"pom.xml `groups` now runs fewer tests: {_list(lost)} left out", "groups")
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
_COMMENT_OR_STRING = re.compile(_STRING.pattern + r"|//[^\n]*|/\*.*?\*/", re.S)
_TEST_HEADER = re.compile(r"(?i)test")
_ASSIGN = re.compile(r"^(?:[\w.]+\.)?(is)?([A-Za-z]\w*)\s*(?:=|\s)\s*(\w+)\s*$")
_EXCLUDE_LISTS = ("excludeTestsMatching", "excludeTags", "excludeCategories", "excludeGroups")
_INCLUDE_LISTS = ("includeTestsMatching", "includeTags", "includeCategories", "includeGroups")
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
    """Each statement with the headers of the blocks around it, outermost first."""
    code = _COMMENT_OR_STRING.sub(lambda m: m[0] if m[0][0] in "'\"" else " ", text)
    stack: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(code):
        string = _STRING.match(code, i)
        if string:
            current.append(string[0])
            i = string.end()
            continue
        char = code[i]
        if char == "{":
            stack.append("".join(current).strip().rsplit("\n", 1)[-1])
            current = []
        elif char in "};\n":
            statement = "".join(current).strip()
            if statement:
                yield statement, tuple(stack)
            current = []
            if char == "}" and stack:
                stack.pop()
        else:
            current.append(char)
        i += 1
    if "".join(current).strip():
        yield "".join(current).strip(), tuple(stack)


def _gradle(text: str | None) -> _Gradle:
    facts = _Gradle()
    for statement, stack in _statements(text or ""):
        headers = " ".join(stack)
        in_test = bool(_TEST_HEADER.search(headers)) and not _NOT_TEST_BLOCKS.search(headers)
        task = _task(stack) if in_test else _ALL_TEST_TASKS
        if in_test:
            facts.tasks.add(task)
        word = statement.split("(", 1)[0].split()[0] if statement.split() else ""
        strings = [s[1:-1] for s in _STRING.findall(statement)]
        assign = _ASSIGN.match(statement)
        if assign:
            prefix = statement.split(assign[2], 1)[0]
            name, value = assign[2][0].lower() + assign[2][1:], assign[3]
            test_task = in_test or bool(_TEST_HEADER.search(prefix))
            if name == "ignoreFailures" and value == "true" and test_task:
                facts.switches["ignoreFailures = true"] += 1
            elif name == "enabled" and value == "false" and "violationRules" in headers:
                facts.switches["a JaCoCo rule disabled"] += 1
            elif name == "enabled" and value == "false" and test_task:
                facts.switches["a test task disabled (`enabled = false`)"] += 1
            elif name == "failOnViolation" and value == "false":
                facts.switches["failOnViolation = false"] += 1
        if "excludedTaskNames" in statement and any(_TEST_HEADER.search(s) for s in strings):
            facts.switches["test tasks excluded (`excludedTaskNames`)"] += 1
        if stack and stack[-1].endswith("onlyIf") and statement == "false" and in_test:
            facts.switches["a test task disabled (`onlyIf { false }`)"] += 1
        bare = word.rsplit(".", 1)[-1]
        if bare in _EXCLUDE_LISTS or (bare == "exclude" and in_test):
            facts.excludes |= {(task, bare, s) for s in strings}
        elif bare in _INCLUDE_LISTS or (bare == "include" and in_test):
            facts.includes.setdefault((task, bare), set()).update(strings)
        elif bare == "minimum" and (number := _NUMBER.search(statement.split("minimum", 1)[1])):
            facts.minimums.append(float(number[0]))
    return facts


def gradle(before: str | None, after: str | None) -> Iterator[Loosening]:
    if after is None:
        return
    old, new = _gradle(before), _gradle(after)
    for switch in sorted(new.switches - old.switches):
        yield (f"The Gradle build now has {switch}", switch.split()[0].strip("`"))
    # A test task the change adds runs tests that didn't run before, whatever it selects.
    existed = old.tasks | {_ALL_TEST_TASKS}
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
