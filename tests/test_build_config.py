"""AW009 on Maven and Gradle: build files and CI runs that skip, exclude or stop enforcing tests."""

from __future__ import annotations

import pytest

from assertwatch.paths import is_guard_path
from assertwatch.rules import FileChange, run_rules


def file_rules(path, before, after):
    found = run_rules([], [], files=[FileChange(path, path, before, after)])
    return [f.message for f in found if f.rule == "AW009"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("pom.xml", True),
        ("core/pom.xml", True),
        ("build.gradle", True),
        ("app/build.gradle.kts", True),
        (".mvn/maven.config", True),
        ("sub/.mvn/jvm.config", True),
        ("settings.gradle", True),
        ("buildSrc/src/main/kotlin/java-conventions.gradle.kts", True),
        (".mvn/settings.xml", True),
        ("Jenkinsfile", True),
        ("ci/build.sh", True),
        ("docs/ci.txt", False),
        ("maven.config", False),
    ],
)
def test_build_guard_paths(path, expected):
    assert is_guard_path(path) is expected


# --- pom.xml ----------------------------------------------------------------------------

POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <properties>
    <java.version>17</java.version>
  </properties>
  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
        <version>3.2.5</version>
        <configuration>
          <excludes>
            <exclude>**/*Benchmark.java</exclude>
          </excludes>
        </configuration>
      </plugin>
      <plugin>
        <groupId>org.jacoco</groupId>
        <artifactId>jacoco-maven-plugin</artifactId>
        <executions>
          <execution>
            <id>check</id>
            <goals><goal>check</goal></goals>
            <configuration>
              <rules>
                <rule>
                  <element>BUNDLE</element>
                  <limits>
                    <limit>
                      <counter>LINE</counter>
                      <value>COVEREDRATIO</value>
                      <minimum>0.80</minimum>
                    </limit>
                  </limits>
                </rule>
              </rules>
            </configuration>
          </execution>
        </executions>
      </plugin>
    </plugins>
  </build>
</project>
"""
SF = "<version>3.2.5</version>\n        <configuration>\n"
PROPS = "    <java.version>17</java.version>\n"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ((PROPS, PROPS + "    <skipTests>true</skipTests>\n"), "`skipTests` (a property)"),
        ((PROPS, PROPS + "    <maven.test.skip>true</maven.test.skip>\n"), "`maven.test.skip`"),
        (
            (SF, SF + "          <testFailureIgnore>true</testFailureIgnore>\n"),
            "`testFailureIgnore` (maven-surefire-plugin)",
        ),
        ((SF, SF + "          <skip>true</skip>\n"), "`skip`"),
        (
            ("**/*Benchmark.java</exclude>\n", "**/*Benchmark.java</exclude>\n"
             "            <exclude>**/CartTest.java</exclude>\n"),
            "excludes `**/CartTest.java`",
        ),
        (("**/*Benchmark.java", "**/*.java"), "excludes `**/*.java`"),
        (
            (SF, SF + "          <excludedGroups>slow</excludedGroups>\n"),
            "test group `slow`",
        ),
        (
            (SF, SF + "          <groups>fast</groups>\n"),
            "`groups` now runs fewer tests",
        ),
        (
            (SF, SF + "          <test>CartTest</test>\n"),
            "no longer includes",
        ),
        (("<minimum>0.80</minimum>", "<minimum>0.50</minimum>"), "lowered to 0.5 (was 0.8)"),
        (("<minimum>0.80</minimum>", "<minimum>50%</minimum>"), "lowered to 0.5 (was 0.8)"),
        (("<minimum>0.80</minimum>", ""), "minimum was removed"),
        (("<counter>LINE</counter>", "<counter>CLASS</counter>"), "LINE COVEREDRATIO minimum"),
        (("<id>check</id>", "<id>check</id><configuration><haltOnFailure>false</haltOnFailure>"
          "</configuration>"), "`haltOnFailure`"),
        ((PROPS, PROPS + "    <jacoco.skip>true</jacoco.skip>\n"), "`jacoco.skip`"),
    ],
)  # fmt: skip
def test_pom_loosened(change, message):
    found = file_rules("pom.xml", POM, POM.replace(*change))
    assert len(found) == 1 and message in found[0], found


@pytest.mark.parametrize(
    "change",
    [
        ("<version>3.2.5</version>", "<version>3.5.0</version>"),  # plugin bump
        (PROPS, "    <java.version>21</java.version>\n"),
        (PROPS, PROPS + "    <skipTests>false</skipTests>\n"),
        (("            <exclude>**/*Benchmark.java</exclude>\n", "")),  # runs more
        (("<minimum>0.80</minimum>", "<minimum>0.85</minimum>")),
        (SF, SF + "          <skipTests>${skipUnit}</skipTests>\n"),
        (SF, SF + "          <includes><include>**/*Test.java</include>"
         "<include>**/Test*.java</include><include>**/*Tests.java</include>"
         "<include>**/*TestCase.java</include><include>**/*Spec.java</include></includes>\n"),
        (("<plugins>", "<pluginManagement><plugins>"),
         ("</plugins>", "</plugins></pluginManagement>")),
    ],
)  # fmt: skip
def test_pom_tightened_or_unrelated(change):
    after = POM
    for pair in change if isinstance(change[0], tuple) else (change,):
        after = after.replace(*pair)
    assert file_rules("pom.xml", POM, after) == []


def test_pom_with_a_dtd_or_broken_xml_fails_closed():
    dtd = '<!DOCTYPE project [<!ENTITY t "true">]>\n'
    after = POM.replace('<?xml version="1.0" encoding="UTF-8"?>\n', dtd).replace(
        PROPS, PROPS + "    <skipTests>&t;</skipTests>\n"
    )
    assert ["can't be read" in m for m in file_rules("pom.xml", POM, after)] == [True]
    assert ["can't be read" in m for m in file_rules("pom.xml", POM, "<project>")] == [True]
    assert file_rules("pom.xml", "<project>", "<project>") == []  # unreadable before too
    assert file_rules("pom.xml", POM, None) == []  # deleted


def test_new_pom_with_skip_is_reported():
    assert file_rules("api/pom.xml", None, POM.replace(PROPS, PROPS + "<skipITs>true</skipITs>"))


# --- Gradle -----------------------------------------------------------------------------

GROOVY = """\
plugins {
    id 'java'
    id 'jacoco'
}

dependencies {
    testImplementation('org.junit.jupiter:junit-jupiter:5.10.2') {
        exclude group: 'org.hamcrest'
    }
}

test {
    useJUnitPlatform()
    // exclude '**/Old*'
}

jacocoTestCoverageVerification {
    violationRules {
        rule {
            limit {
                minimum = 0.8
            }
        }
    }
}
"""
KOTLIN = """\
plugins {
    java
    jacoco
}

dependencies {
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.2") {
        exclude(group = "org.hamcrest")
    }
}

tasks.test {
    useJUnitPlatform()
}

tasks.jacocoTestCoverageVerification {
    violationRules {
        rule {
            limit {
                minimum = "0.8".toBigDecimal()
            }
        }
    }
}
"""
JUNIT = "    useJUnitPlatform()\n"


@pytest.mark.parametrize("build", [GROOVY, KOTLIN], ids=["groovy", "kotlin"])
@pytest.mark.parametrize(
    ("added", "message"),
    [
        ("    ignoreFailures = true\n", "ignoreFailures"),
        ("    ignoreFailures true\n", "ignoreFailures"),
        ("    isIgnoreFailures = true\n", "ignoreFailures"),
        ("    enabled = false\n", "enabled = false"),
        ("    onlyIf { false }\n", "onlyIf"),
        ("    exclude('**/CartTest.class')\n", "excludes `**/CartTest.class`"),
        ("    exclude '**/CartTest.class'\n", "excludes `**/CartTest.class`"),
        ('    filter { excludeTestsMatching("*.CartTest") }\n', "`*.CartTest`"),
        ('    useJUnitPlatform { excludeTags("slow") }\n', "`slow` (excludeTags)"),
        ('    filter { includeTestsMatching("*.FastTest") }\n', "runs fewer tests"),
    ],
)
def test_gradle_loosened(build, added, message):
    found = file_rules("build.gradle", build, build.replace(JUNIT, JUNIT + added))
    assert len(found) == 1 and message in found[0], found


@pytest.mark.parametrize("build", [GROOVY, KOTLIN], ids=["groovy", "kotlin"])
def test_gradle_jacoco_loosened(build):
    lowered = build.replace("0.8", "0.5")
    assert [
        "lowered or removed: 0.5 (was 0.8)" in m for m in file_rules("build.gradle", build, lowered)
    ] == [True]
    assert file_rules("build.gradle", build, build.replace("0.8", "0.9")) == []
    rule_off = build.replace("        rule {\n", "        rule {\n            enabled = false\n")
    assert len(file_rules("build.gradle", build, rule_off)) == 1
    no_fail = build.replace(
        "    violationRules {\n", "    violationRules {\n        failOnViolation = false\n"
    )
    assert len(file_rules("build.gradle", build, no_fail)) == 1


@pytest.mark.parametrize(
    "after",
    [
        GROOVY.replace("5.10.2", "5.11.0"),
        GROOVY.replace("exclude group: 'org.hamcrest'", "exclude group: 'org.mockito'"),
        GROOVY.replace(JUNIT, JUNIT + "    maxParallelForks = 4\n    enabled = true\n"),
        GROOVY + "\njar {\n    exclude '**/*.txt'\n}\n",
        GROOVY + "\nsourceSets { test { resources { exclude '**/*.tmp' } } }\n",
        GROOVY + "\ntasks.register('integrationTest', Test) {\n"
        "    useJUnitPlatform { includeTags 'integration' }\n    exclude '**/Unit*'\n}\n",
    ],
    ids=["dep-bump", "dep-exclude", "parallel", "jar-exclude", "resources", "new-task"],
)
def test_gradle_unrelated(after):
    assert file_rules("build.gradle", GROOVY, after) == []


def test_gradle_groovy_converted_to_kotlin_keeps_its_settings():
    old = GROOVY.replace(JUNIT, JUNIT + "    exclude '**/Slow*'\n")
    same = KOTLIN.replace(JUNIT, JUNIT + '    exclude("**/Slow*")\n')
    deleted = FileChange("build.gradle", None, old, None)
    added = FileChange(None, "build.gradle.kts", None, same)
    assert run_rules([], [], files=[deleted, added]) == []
    worse = same.replace('exclude("**/Slow*")', 'exclude("**/Slow*", "**/Cart*")')
    found = run_rules([], [], files=[deleted, FileChange(None, "build.gradle.kts", None, worse)])
    assert [f.message for f in found] == ["Gradle task `test` now excludes `**/Cart*` (exclude)."]


# --- command lines ----------------------------------------------------------------------

WORKFLOW = """\
name: ci
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: ./mvnw -B verify
"""


@pytest.mark.parametrize(
    ("after", "message"),
    [
        ("./mvnw -B verify -DskipTests", "`-DskipTests`"),
        ("./mvnw -B verify -Dmaven.test.skip=true", "`-Dmaven.test.skip=true`"),
        ("./mvnw -B verify -Dmaven.test.failure.ignore=true", "failure.ignore"),
        ("./mvnw -B verify -DtestFailureIgnore", "`-DtestFailureIgnore`"),
        ("./mvnw -B verify -Djacoco.skip=true", "`-Djacoco.skip=true`"),
        ("./mvnw -B verify -DfailIfNoTests=false", "failIfNoTests"),
        ("./gradlew build -x test", "`-x test`"),
        ("./gradlew build --exclude-task :core:test", "`--exclude-task :core:test`"),
        ("cd core && ./mvnw -B \\\n          verify -DskipTests", "`-DskipTests`"),
    ],
)
def test_ci_run_loosened(after, message):
    found = file_rules(
        ".github/workflows/ci.yml", WORKFLOW, WORKFLOW.replace("./mvnw -B verify", after)
    )
    assert len(found) == 1 and message in found[0], found


def test_ci_skip_flag_in_environment():
    env = WORKFLOW.replace("    steps:", "    env:\n      MAVEN_ARGS: -DskipTests\n    steps:")
    assert len(file_rules(".github/workflows/ci.yml", WORKFLOW, env)) == 1


@pytest.mark.parametrize(
    "after",
    [
        WORKFLOW.replace("-B verify", "-B -ntp verify"),
        WORKFLOW.replace("-B verify", "-B verify -DskipTests=false"),
        WORKFLOW + "  deploy:\n    needs: test\n    steps:\n"
        "      - run: ./mvnw -B deploy -DskipTests\n",
        WORKFLOW + "      - run: ./mvnw -B javadoc:javadoc -DskipTests\n",
        WORKFLOW.replace("./mvnw -B verify", "./gradlew build -x javadoc"),
    ],
    ids=["option", "explicit-false", "deploy-job", "docs-step", "gradle-other-task"],
)  # fmt: skip
def test_ci_runs_unaffected(after):
    assert file_rules(".github/workflows/ci.yml", WORKFLOW, after) == []


def test_maven_config_flags():
    assert file_rules(".mvn/maven.config", "-B\n", "-B\n-DskipTests\n") == [
        "Every Maven run now gets `-DskipTests`."
    ]
    assert len(file_rules(".mvn/jvm.config", None, "-Dmaven.test.failure.ignore=true")) == 1
    assert file_rules(".mvn/maven.config", "-B\n", "-B\n-T 1C\n") == []
    assert file_rules(".mvn/maven.config", "-DskipTests\n", "-B\n") == []


PARENT = """\
<project>
  <modules>
    <module>core</module>
    <module>web</module>
  </modules>
  <profiles>
    <profile><id>it</id><modules><module>it-tests</module></modules></profile>
  </profiles>
</project>
"""


def test_pom_module_dropped():
    def found(after, *others):
        files = [FileChange("pom.xml", "pom.xml", PARENT, after), *others]
        return [f.message for f in run_rules([], [], files=files)]

    dropped = PARENT.replace("    <module>web</module>\n", "")
    assert found(dropped) == ["pom.xml no longer builds module `web`, so its tests don't run."]
    into_profile = dropped.replace("<module>it-tests", "<module>web</module><module>it-tests")
    assert "only in profile `it`" in found(into_profile)[0]
    # The module itself deleted or moved in the same change: nothing is hidden.
    web = "<project><artifactId>web</artifactId></project>"
    assert found(dropped, FileChange("web/pom.xml", None, web, None)) == []
    assert found(dropped, FileChange("web/pom.xml", "apps/web/pom.xml", web, web)) == []
    out_of_profile = PARENT.replace("<module>it-tests</module>", "").replace(
        "<module>web</module>", "<module>web</module><module>it-tests</module>"
    )
    assert found(out_of_profile) == []


def test_pom_switch_through_a_property():
    via = POM.replace(PROPS, PROPS + "    <skip.unit>true</skip.unit>\n").replace(
        SF, SF + "          <skipTests>${skip.unit}</skipTests>\n"
    )
    assert "`skipTests` (maven-surefire-plugin)" in " ".join(file_rules("pom.xml", POM, via))


@pytest.mark.parametrize(
    "after",
    [
        "./mvnw -B -D skipTests verify",
        "./mvnw -B verify -Dtest='!CartTest'",
        "./mvnw -B verify -DexcludedGroups=slow",
    ],
)
def test_ci_run_loosened_other_spellings(after):
    changed = WORKFLOW.replace("./mvnw -B verify", after)
    assert len(file_rules(".github/workflows/ci.yml", WORKFLOW, changed)) == 1


def test_ci_build_no_longer_runs_tests():
    compiled = WORKFLOW.replace("-B verify", "-B compile")
    assert [
        "no longer runs tests" in m
        for m in file_rules(".github/workflows/ci.yml", WORKFLOW, compiled)
    ] == [True]
    gradle = WORKFLOW.replace("./mvnw -B verify", "./gradlew build")
    assembled = gradle.replace("build", "assemble")
    assert len(file_rules(".github/workflows/ci.yml", gradle, assembled)) == 1
    # The step removed with its whole workflow, or no Maven/Gradle left: not this check's call.
    assert (
        file_rules(
            ".github/workflows/ci.yml", WORKFLOW, WORKFLOW.replace("./mvnw -B verify", "make")
        )
        == []
    )


def test_gradle_only_if_false_on_task_property():
    after = GROOVY + "\ntest.onlyIf { false }\n"
    assert len(file_rules("build.gradle", GROOVY, after)) == 1


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("./mvnw -B verify", "./mvnw -B -pl test compile"),  # `test` names a module here
        ("./mvnw -B surefire:test", "./mvnw -B compile"),
        ("./mvnw -B \\\n          verify", "./mvnw -B \\\n          compile"),
        ("./gradlew build", "./gradlew build --exclude-task=test"),
    ],
)
def test_ci_runs_compared_as_the_tool_reads_them(before, after):
    old = WORKFLOW.replace("./mvnw -B verify", before)
    new = WORKFLOW.replace("./mvnw -B verify", after)
    assert len(file_rules(".github/workflows/ci.yml", old, new)) == 1


def test_pom_properties_from_every_block():
    profile = "<profiles><profile><id>ci</id><properties><x>1</x></properties></profile></profiles>"
    base = POM.replace(PROPS, PROPS + "    <skip.unit>true</skip.unit>\n").replace(
        "  <build>", profile + "\n  <build>"
    )
    via = base.replace(SF, SF + "          <skipTests>${skip.unit}</skipTests>\n")
    assert len(file_rules("pom.xml", base, via)) == 1


JAVADOC = "      <plugin>\n        <artifactId>maven-javadoc-plugin</artifactId>\n"


@pytest.mark.parametrize(
    "extra",
    [
        "<configuration><skip>true</skip></configuration>",
        "<configuration><groups><group><title>API</title></group></groups></configuration>",
    ],
)
def test_pom_other_plugins_settings_are_not_test_settings(extra):
    after = POM.replace(
        "    <plugins>\n", "    <plugins>\n" + JAVADOC + extra + "\n      </plugin>\n"
    )
    assert file_rules("pom.xml", POM, after) == []


def test_pom_more_shapes():
    excludes_file = POM.replace(SF, SF + "          <excludesFile>skip.txt</excludesFile>\n")
    assert len(file_rules("pom.xml", POM, excludes_file)) == 1
    grouped = POM.replace(SF, SF + "          <groups>fast</groups>\n")
    assert file_rules("pom.xml", grouped, POM) == []  # filter removed: runs everything
    explicit = POM.replace("<counter>LINE</counter>", "")
    same = explicit.replace("<value>", "<counter>INSTRUCTION</counter><value>")
    assert file_rules("pom.xml", explicit, same) == []
    maximum = POM.replace("<minimum>0.80</minimum>", "<maximum>10</maximum>").replace(
        "COVEREDRATIO", "MISSEDCOUNT"
    )
    assert file_rules("pom.xml", maximum, maximum.replace(">10<", ">5<")) == []
    assert "raised to 20" in file_rules("pom.xml", maximum, maximum.replace(">10<", ">20<"))[0]


def test_gradle_more_shapes():
    every = GROOVY + "\ntasks.withType(Test) {\n    exclude '**/Cart*'\n}\n"
    assert len(file_rules("build.gradle", GROOVY, every)) == 1
    assert len(file_rules("build.gradle", GROOVY, GROOVY + "\ntest.ignoreFailures = true\n")) == 1
    assert file_rules("build.gradle", GROOVY, GROOVY + "\ntest.onlyIf { true }\n") == []
    no_min = GROOVY.replace("                minimum = 0.8\n", "")
    assert len(file_rules("build.gradle", GROOVY, no_min)) == 1


def test_gradle_conversion_only_pairs_files_in_one_folder():
    old = GROOVY.replace(JUNIT, JUNIT + "    ignoreFailures = true\n")
    new = KOTLIN.replace(JUNIT, JUNIT + "    ignoreFailures = true\n")
    files = [
        FileChange("a/build.gradle", None, old, None),
        FileChange(None, "b/build.gradle.kts", None, new),
    ]
    assert len(run_rules([], [], files=files)) == 1


def test_pom_module_edited_in_place_is_not_gone():
    web = "<project><artifactId>web</artifactId></project>"
    dropped = PARENT.replace("    <module>web</module>\n", "")
    files = [
        FileChange("pom.xml", "pom.xml", PARENT, dropped),
        FileChange("web/pom.xml", "web/pom.xml", web, web.replace("web<", "web2<")),
    ]
    assert len(run_rules([], [], files=files)) == 1


# --- adversarial forms (security review of the first AW009 build slice) -----------------

PROFILE = (
    "<profiles><profile><id>quick</id>{}<properties><skipTests>true</skipTests>"
    "</properties></profile></profiles>\n  <build>"
)


@pytest.mark.parametrize(
    "activation",
    [
        "<activation><activeByDefault>true</activeByDefault></activation>",
        "<activation><jdk>[17,)</jdk></activation>",
        "<activation><property><name>!fast</name></property></activation>",
    ],
)
def test_pom_profile_made_active(activation):
    manual = POM.replace("  <build>", PROFILE.format(""), 1)
    active = POM.replace("  <build>", PROFILE.format(activation), 1)
    assert file_rules("pom.xml", POM, manual) == []  # asked for by name: not a default build
    assert len(file_rules("pom.xml", manual, active)) == 1


@pytest.mark.parametrize(
    ("change", "flagged"),
    [
        ((PROPS, PROPS + "    <skipUnitTests>true</skipUnitTests>\n"), True),
        ((PROPS, PROPS + "    <skip.it>true</skip.it>\n"), True),
        ((PROPS, PROPS + "    <maven.javadoc.skip>true</maven.javadoc.skip>\n"), False),
        ((PROPS, PROPS + "    <gpg.skip>true</gpg.skip>\n"), False),
        (("  <build>\n", "  <build>\n    <testSourceDirectory>x</testSourceDirectory>\n"), True),
        ((SF, SF + "          <includeJUnit5Engines>junit-vintage</includeJUnit5Engines>\n"), True),
    ],
)
def test_pom_other_loosenings(change, flagged):
    assert bool(file_rules("pom.xml", POM, POM.replace(*change))) is flagged


def test_maven_settings_profiles_all_count():
    settings = (
        "<settings><profiles><profile><id>ci</id><properties>{}</properties></profile>"
        "</profiles></settings>"
    )
    before, after = settings.format(""), settings.format("<skipTests>true</skipTests>")
    assert len(file_rules(".mvn/settings.xml", before, after)) == 1


@pytest.mark.parametrize(
    "added",
    [
        '\ntasks.getByName("test").enabled = false\n',
        "\ntest.setEnabled(false)\n",
        "\ntasks.test { ignoreFailures.set(true) }\n",
        '\ntest { enabled = System.getenv("CI") == null }\n',
        "\ntest { onlyIf { !isCi } }\n",
        "\ntest { exclude { it.name.contains('Cart') } }\n",
        "\ncheck.dependsOn.remove(test)\n",
        "\ntasks.named('check') { setDependsOn([]) }\n",
        "\nsourceSets.test.java.srcDirs = []\n",
        "\ntest { testClassesDirs = files() }\n",
        "\ntest { useJUnitPlatform { excludeEngines 'junit-jupiter' } }\n",
    ],
)
def test_gradle_other_spellings(added):
    assert len(file_rules("build.gradle", GROOVY, GROOVY + added)) == 1


@pytest.mark.parametrize(
    "added",
    [
        "\ntest { reports.html.enabled = false }\n",
        "\ntest { onlyIf { true } }\n",
        "\nsourceSets { integrationTest { java { srcDirs = ['src/it/java'] } } }\n",
        "\nsourceSets.test.java.srcDirs += 'src/extra/java'\n",
        "\ncheck.dependsOn integrationTest\n",
    ],
)
def test_gradle_other_spellings_unrelated(added):
    assert file_rules("build.gradle", GROOVY, GROOVY + added) == []


def test_gradle_statements_over_several_lines():
    split = GROOVY.replace(JUNIT, JUNIT + '    exclude(\n        "**/Cart*"\n    )\n')
    assert len(file_rules("build.gradle", GROOVY, split)) == 1


def test_gradle_any_script_and_existing_test_task():
    script = "test {\n    exclude '**/Cart*'\n}\n"
    assert len(file_rules("gradle/filters.gradle", None, script)) == 1
    assert len(file_rules("buildSrc/src/main/kotlin/conv.gradle.kts", "", script)) == 1


def test_gradle_settings_project_dropped():
    def found(after, *others, tests_left=True):
        files = [FileChange("settings.gradle", "settings.gradle", SETTINGS, after), *others]
        found = run_rules([], [], files=files, tests_under=lambda folder: tests_left)
        return [f.message for f in found]

    dropped = SETTINGS.replace(", 'web'", "")
    assert found(dropped) == [
        "Gradle settings no longer include project `web`, so its tests don't run."
    ]
    build = FileChange("web/build.gradle", None, "plugins { id 'java' }\n", None)
    assert found(dropped, build, tests_left=False) == []  # deleted with its tests
    assert len(found(dropped, build, tests_left=True)) == 1  # build file gone, tests left
    assert found(SETTINGS.replace("'core', 'web'", "'core',\n        'web'")) == []


SETTINGS = "rootProject.name = 'shop'\ninclude 'core', 'web'\n"


def test_pom_module_deleted_but_tests_left():
    dropped = PARENT.replace("    <module>web</module>\n", "")
    files = [
        FileChange("pom.xml", "pom.xml", PARENT, dropped),
        FileChange("web/pom.xml", None, "<project/>", None),
    ]
    assert len(run_rules([], [], files=files, tests_under=lambda folder: True)) == 1
    assert run_rules([], [], files=files, tests_under=lambda folder: False) == []


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (WORKFLOW, WORKFLOW.replace("      - run:", "      - if: false\n        run:")),
        (WORKFLOW, WORKFLOW.replace("    runs-on:", "    if: ${{ false }}\n    runs-on:")),
        (WORKFLOW, WORKFLOW.replace("on: [push]", "on: [workflow_dispatch]")),
        (WORKFLOW, WORKFLOW.replace("on: [push]", "on:\n  schedule:\n    - cron: '0 0 * * 0'")),
        (WORKFLOW, WORKFLOW.replace("-B verify", "-B verify -D$SKIP")),
        (WORKFLOW, WORKFLOW.replace("-B verify", "-B verify -DskipTests=${{ vars.SKIP }}")),
    ],
)  # fmt: skip
def test_ci_other_loosenings(before, after):
    assert len(file_rules(".github/workflows/ci.yml", before, after)) == 1


def test_ci_quiet_changes():
    both = WORKFLOW.replace("on: [push]", "on: [push, pull_request]")
    assert file_rules(".github/workflows/ci.yml", both, WORKFLOW) == []  # still on push
    cond = WORKFLOW.replace(
        "      - run:", "      - if: matrix.os == 'ubuntu-latest'\n        run:"
    )
    assert file_rules(".github/workflows/ci.yml", WORKFLOW, cond) == []
    flag = WORKFLOW.replace("-B verify", "-B verify -Drevision=${{ github.sha }}")
    assert file_rules(".github/workflows/ci.yml", WORKFLOW, flag) == []


def test_ci_file_deleted():
    deleted = FileChange(".github/workflows/ci.yml", None, WORKFLOW, None)
    assert len(run_rules([], [], files=[deleted])) == 1
    moved_to = FileChange(None, ".github/workflows/build.yml", None, WORKFLOW)
    assert run_rules([], [], files=[deleted, moved_to]) == []
    docs = FileChange(
        ".github/workflows/docs.yml",
        None,
        "jobs:\n  d:\n    steps:\n      - run: mkdocs build\n",
        None,
    )
    assert run_rules([], [], files=[docs]) == []


@pytest.mark.parametrize(
    ("path", "before", "after"),
    [
        ("Jenkinsfile", "sh './mvnw -B verify'\n", "sh './mvnw -B verify -DskipTests'\n"),
        (".gitlab-ci.yml", "t:\n  script: gradle build\n", "t:\n  script: gradle build -x test\n"),
        ("ci/build.sh", "./mvnw -B verify\n", "./mvnw -B compile\n"),
        ("Makefile", "test:\n\tmvn -B verify\n", "test:\n\tmvn -B verify -Dmaven.test.skip\n"),
    ],
)  # fmt: skip
def test_other_ci_files(path, before, after):
    assert len(file_rules(path, before, after)) == 1


def test_more_expression_and_quiet_cases():
    lenient = GROOVY.replace(JUNIT, JUNIT + "    ignoreFailures = project.hasProperty('x')\n")
    assert len(file_rules("build.gradle", GROOVY, lenient)) == 1
    rules = "    violationRules {\n"
    soft = GROOVY.replace(rules, rules + "        failOnViolation = isCi\n")
    assert len(file_rules("build.gradle", GROOVY, soft)) == 1
    # A property set only in a profile that must be asked for doesn't apply to a plain build.
    manual = POM.replace(
        "  <build>",
        "<profiles><profile><id>q</id><properties><s>true</s></properties>"
        "</profile></profiles>\n  <build>",
        1,
    )
    reads = manual.replace(SF, SF + "          <skipTests>${s}</skipTests>\n")
    assert file_rules("pom.xml", manual, reads) == []
    # Deleting a shell script, or a docs workflow's triggers changing, hides no test run.
    script = FileChange("scripts/test.sh", None, "./mvnw -B verify\n", None)
    assert run_rules([], [], files=[script]) == []
    docs = "on: [push]\njobs:\n  d:\n    steps:\n      - run: mkdocs build\n"
    manual_docs = docs.replace("[push]", "[workflow_dispatch]")
    assert file_rules(".github/workflows/docs.yml", docs, manual_docs) == []
