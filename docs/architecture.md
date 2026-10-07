# Architecture and operational behavior

## Registration and responsibilities

`.claude-plugin/plugin.json` describes the plugin and its `app_port` option.
The marketplace points to this plugin. `.mcp.json` launches the Node.js server
with the project root and application port; `hooks/hooks.json` registers Bash
launchers backed by a Python standard-library hook implementation. `.lsp.json`
registers an optional externally installed Java language server.

Skills provide task guidance. Agents restrict specialist work to review,
design or test-writing scopes. The MCP server provides six Spring-aware tools;
it delegates builds to the same runtime used by hooks.

## Project discovery and execution

`runtime/project_runtime.py` discovers Maven/Gradle projects, modules and
wrappers, validates supported tasks and constructs argument arrays. Wrappers
are preferred to global installations. Requested modules/profiles determine
execution scope.

Each project has a machine-local build lock and records in a private temporary
state directory. Plugin builds acquire that lock before executing. Timeouts,
cancellation and output limits terminate the build process tree; failed or
cancelled attempts invalidate reusable verification success.

`run_tests` snapshots report metadata and contents before and after execution
inside the lock. It returns summaries of fresh reports and structured test
failures, ignoring unchanged reports from earlier executions. The lock
coordinates plugin invocations; a separately launched build does not
participate in that coordination.

## Verification reuse

Post-edit hooks record relevant changes without launching a build for each
file. The Stop hook requests Maven `verify` or Gradle `check`. MCP and PR review
can share a completed equivalent verification gate.

Reuse requires successful full verification with unchanged inputs and matching
build tool, task, module, test filter and profile scope. Compilation or a test
run alone cannot satisfy that gate. Content fingerprints cover local files,
execution environment and runtime implementation. Prose outside source trees
and recognized output/state directories are excluded. Symlink, oversized or
unreadable inputs prevent reuse but still allow execution.

Force execution when external tools/services changed or the application uses
excluded artifacts as build inputs. Local records are an optimization; they
are not a replacement for CI.

## Inspection limits

Source inventories use literal configuration, dependency declarations,
annotations and SQL. Parent builds, catalogs, computed routes and runtime
configuration can provide information unavailable to static inspection.
Unknown evidence should stay unknown.

Runtime OpenAPI and Actuator inspection use explicit URLs and report access,
endpoint and application failures separately. Migration database status
requires an explicitly requested mode and the application's configured Flyway
plugin/database. Local migration lint does not prove database compatibility.

Secret and DDL hooks are heuristic configuration guards. Reviewers depend on
the caller for diffs and executable build evidence. See
[SECURITY.md](../SECURITY.md) for trust boundaries.

## Validation layers

- `tests/run-all.sh`: deterministic hook, MCP, runtime, packaging and evaluation
  harness regressions with local processes and fixtures.
- `tests/examples/check.sh`: extracted Java examples plus real local Pact and
  Gatling execution. Results describe those fixtures.
- `evals/`: model behavior cases, including fixture context and criterion-level
  judgments. Freeze a response before comparing judges; preserve failures and
  distinguish model disagreement from an executable defect.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for commands and
[evals/README.md](../evals/README.md) for evaluation provenance.
