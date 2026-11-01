# Architecture and operational behavior

## Registration and responsibilities

`.claude-plugin/plugin.json` describes the plugin and its `app_port` option.
The marketplace points to this plugin. `.mcp.json` launches the Node.js server
with the project root and application port; `hooks/hooks.json` registers Bash
launchers backed by a Python standard-library hook implementation. `.lsp.json`
registers an optional externally installed Java language server.

Skills provide task guidance. Agents restrict specialist work to review,
design or test-writing scopes. The MCP server provides seven Spring-aware tools;
it delegates builds to the same runtime used by hooks.

## Host adapters and initialization

Claude Code reads `.claude-plugin/`, `.mcp.json`, `agents/` and `hooks/`.
Codex reads `.codex-plugin/plugin.json` and the separate configuration and
launcher under `codex/`. Shared skills and MCP tools are reused; Codex does not
load the Claude specialist agents or edit/Stop hooks. Verification remains an
explicit tool invocation on that host. See [Codex support](codex.md).

`initialize_project` delegates to the Python runtime's initializer. It requires
an explicit user request and destination, rejects existing build contexts and
unsafe targets, obtains options from official Initializr metadata, validates
and stages the archive, and publishes without overwriting existing files.
Generation never executes wrappers or tests. Network and archive failures
must leave existing user files intact.

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

Report snapshots reject externally linked report roots and are bounded to
8 MiB and 1024 reports. Runtime JSON is bounded to 24 MiB to leave room under
the MCP transport budget; oversized results return an explicit diagnostic,
the build's actual exit code and omitted-output metadata. Narrow module/test
scope to recover bounded results. Profile-declared Maven modules participate
in root/lock discovery; discovery describes membership, not profile activation.
With a selected module, `reportScope` identifies the module subtree whose fresh
reports are collected. Maven `-am` can also execute upstream modules; their
failures still affect the build exit code, but their XML is outside that report
scope. Use a root test invocation to collect the whole reactor's reports.

## Verification reuse

The Stop hook requests Maven `verify` or Gradle `check` against current input
fingerprints. No post-edit marker or build is required. MCP and PR review
can share a completed equivalent verification gate.

Reuse requires successful full verification with unchanged inputs and matching
build tool, task, module, test filter and profile scope. Compilation or a test
run alone cannot satisfy that gate. Content fingerprints cover local files,
execution environment and runtime implementation. Prose outside source trees
and recognized output/state directories are excluded. Symlink, oversized or
unreadable inputs prevent reuse but still allow execution.

Force execution when external tools/services changed or the application uses
excluded artifacts as build inputs. Local records are an optimization; they
are not a replacement for independent verification before release.

## Inspection limits

Source inventories use literal configuration, dependency declarations,
annotations and SQL. Parent builds, catalogs, computed routes and runtime
configuration can provide information unavailable to static inspection.
Unknown evidence should stay unknown.

SQL heuristics live in `runtime/sql_lint.py`. The edit hook uses its immediate
warning policy; MCP uses its inventory/error/risk policy through the runtime
bridge. Their deliberately different DELETE and string-handling policies are
characterized with one shared corpus. Neither policy proves database safety.

The legacy runtime CLI action `mark` returns project discovery plus a
deprecation diagnostic without writing state. Old pending-marker files are
ignored; successful verification reuse depends only on scope and fingerprints.

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
