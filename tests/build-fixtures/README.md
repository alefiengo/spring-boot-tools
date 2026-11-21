These opt-in fixtures run real Maven 3.10.0 and Gradle 9.8.1 with JDK 25,
Python 3 and the repository's installed MCP dependencies. Java sources target
release 17. JUnit and Maven lifecycle plugin versions are pinned in the builds.
No Spring Boot application, wrapper distribution or external service is needed.

Run `bash tests/build-fixtures/check.sh`, or pass `maven` or `gradle` to select
one tool. The script copies projects to temporary directories, uses installed
tools and removes fixture outputs and private verification records afterward.
It does not install tools or alter global configuration. On an empty dependency
cache the explicit real build needs registry access; it is intentionally absent
from `test-basic.sh` and the default deterministic suites.

After checking the installed version of each selected tool, the runner executes
its regression from `tests/project-runtime/real-test-filters.py`. The Maven case
filters a test class in the second reactor module, both from the root and with
`-pl` and `-am`, and checks the diagnostic for a missing class. The Gradle case
runs a filtered test in its subproject and checks the diagnostic when the
module is omitted.

Set `SB_REAL_BUILD_KEEP_FAILURE=1` to preserve the MCP/Stop fixture and its
verification records on failure; their paths are printed for inspection. Keep
the plugin runtime and adapters unchanged during a run: the verification
fingerprint includes the runtime implementation as well as fixture inputs.

The runner exercises real runtime verification, equivalent MCP/Stop reuse,
compile and module scope invalidation, filtered JUnit runs with fresh XML,
ignored unchanged reports, structured failures, Maven profile activation and
scope, Gradle profile rejection, and external edits caught by Stop without a
post-edit marker. Failures print the failing assertion and available build
diagnostics. These checks certify the tiny fixtures, not an application's build.
