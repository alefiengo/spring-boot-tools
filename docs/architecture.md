# Architecture and operational behavior

## Registration and responsibilities

`.claude-plugin/plugin.json` describes the plugin and its `app_port` option.
The marketplace points to this plugin. `.mcp.json` launches the Node.js server
with the project root (`PROJECT_ROOT`, set from `CLAUDE_PROJECT_DIR`) and the
application port; it sets no `cwd`, because Claude Code resolves `${...}` only
in a stdio server's `command`, `args` and `env`. The server's dependencies are
declared in the root `package.json`/`package-lock.json`, where Claude Code's
automatic dependency installer reads them; `mcp-server/package.json` only marks
the directory as an ES module scope; `hooks/hooks.json` registers Bash
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
must leave existing user files intact. A destination whose path contains a
symbolic link (any component, final or intermediate) is refused with an error
that names the link; on macOS `/tmp` and `/var` are links, so pass the resolved
path. Targets are opened through the parent directory descriptor with
`O_NOFOLLOW`.

## Project discovery and execution

`runtime/project_runtime.py` discovers Maven/Gradle projects, modules and
wrappers, validates supported tasks and constructs argument arrays. Wrappers
are preferred to global installations. Requested modules/profiles determine
execution scope.

A test filter (`testClass`, optionally with a method) is scoped per build tool.
Maven adds `-Dtest=<class>` together with `-Dsurefire.failIfNoSpecifiedTests=false`,
so reactor modules that do not contain the class do not fail the build. Because
that flag also lets a filter that matches nothing exit 0, the runtime checks the
fresh XML reports: when none in the requested scope executed a test (an empty or
all-skipped suite does not count), it keeps the real exit code but returns
`ok: false` with a `No tests matched testClass ...` diagnostic. Gradle rejects a
filter that matches no test in a task, so in a build with discovered subprojects
a filter without `module` is refused during planning and the diagnostic says to
select the subproject; the targeted run keeps `:module:test --rerun-tasks --tests
<filter>`. Tests that live in the root project of such a build can be targeted
manually with `:test`.

Each project has a machine-local build lock and records in a private per-user
state directory, `~/.cache/spring-boot-tools/build-<sha256 of the project
root>`. The home directory comes from the user database, not from `TMPDIR`,
`HOME` or `XDG_*`, so hooks, MCP and Codex agree on one lock even when their
environments differ. The state directories must be owned by the current
user with mode 0700 (and `~/.cache` owned by the user and not world-writable)
or the runtime refuses them. Plugin builds acquire that lock before executing. Timeouts, cancellation and output limits terminate the
build process tree: SIGTERM first, 750 ms for the build's own cleanup, then
SIGKILL for survivors (the hook ends the process group before it computes the
post-build fingerprint, so files written by TERM handlers are counted). Failed
or cancelled attempts invalidate reusable verification success. When the MCP server is stopped (SIGINT, SIGTERM or
SIGHUP) it terminates the builds in progress before exiting. On Linux a hook's
build also receives SIGTERM if the hook process dies (`PR_SET_PDEATHSIG`);
macOS has no equivalent, so a build started by a killed hook can outlive it
until its own timeout.

Project root discovery walks up from the starting directory. The starting
directory is the caller's choice; an ancestor build directory is used only when
the current user owns it and its build files and wrappers and no other user can
write them, otherwise discovery refuses it with an explicit error. An enclosing
Maven reactor or Gradle settings directory becomes the root only under the same
condition and only when it declares the project: a Maven reactor must list it
as a module, and Gradle settings must include it when the project has its own
`gradlew` (without one, Gradle itself uses the enclosing settings). An enclosing
build refused for its permissions is reported in `discoveryWarnings` and the
nearer project is built alone. The trust check covers build and settings files,
wrappers, `gradle.properties`, `.mvn/**` and `gradle/wrapper/*`. The
installation doctor warns with `discovered_root` when the intended project is
discovered inside a larger root.

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
fingerprints. No post-edit marker or build is required. Two plugin options
shape it: `stop_verification` (`verify`, the default; `compile`, Maven `compile`
or Gradle `classes`, which never satisfies the full verification gate; or `off`)
and `stop_timeout_seconds` (default 300, 30 to 600, passed to the runtime as
`timeoutMs`; the hook registration allows 660 s to cover the build and both
fingerprints). Claude Code 2.1.330 applies no `default` to an option that was
never configured: `${user_config.KEY}` fails and `CLAUDE_PLUGIN_OPTION_<KEY>` is
not exported, and hook registrations cannot use `${user_config.*}`. The hook
therefore reads those environment variables, treats an absent or empty value as
the default and, for an invalid value, emits a notice and uses the default. The
output is always a single JSON object, with notices in `systemMessage`.

The `session-baseline` SessionStart hook (matcher `startup|clear`) calls the
runtime's `discover` and `fingerprint` and stores the result atomically in the
project's private state directory under `sessions/<sha256 of session id>.json`
(mode 0700, at most 64 files per project, oldest pruned, never overwriting an
existing baseline). Stop reads it with `O_NOFOLLOW` and a 4 KiB limit and skips
silently when the current fingerprint matches. `resume`, `fork` and `compact`
record no baseline, since they continue a conversation that may hold unverified
edits; without a baseline, or if the fingerprint cannot be computed, Stop
verifies as before. Consequently a tree that was already broken at session start
is not verified until something changes. Recording is silent on every error,
and a very large project can make it take up to the hook's 30 s limit, in which
case no baseline exists. When a build changes its own inputs, the Stop message
lists up to 10 changed paths from `changedInputs` and suggests `.gitignore`.

When Claude is already continuing because of a Stop hook (`stop_hook_active`),
the hook still verifies but reports the result without blocking again. Discovery errors other
than "no project found" are reported as a notice. MCP and PR review can share a completed equivalent verification gate.

Reuse requires successful full verification with unchanged inputs and matching
build tool, task, module, test filter and profile scope. Compilation or a test
run alone cannot satisfy that gate. Content fingerprints cover local files,
execution environment and runtime implementation. Prose outside source trees
and recognized output/state directories are excluded. Symlink, oversized or
unreadable inputs prevent reuse but still allow execution.

Two exclusions keep one session's identity and a build's own logs from defeating
reuse. The environment part of the fingerprint omits host and session identity:
variables with the `CLAUDE_`, `CODEX_` or `HERDR_` prefix, and explicit terminal,
window, multiplexer, SSH and VS Code IPC handles (for example `TERM_SESSION_ID`,
`TMUX`, `SSH_AUTH_SOCK`, `VSCODE_IPC_HOOK`). Every other variable (JVM and tool
options, locale, time zone, proxies, CI variables) still counts, and a project
file that references a host variable explicitly (such as
`${CLAUDE_PLUGIN_OPTION_APP_PORT}`) brings that value back in. Untracked files
that Git ignores are excluded from the file part, so a wrapper that appends to a
git-ignored `logs/app.log` does not invalidate its own verification; ignored
files that are build inputs must be tracked or the run forced. The runtime asks
Git for them from the project root with a 2-second, 8 MiB, 50 000-entry bound
and falls back to the conservative walk (ignored files count) when Git is
unavailable, errors or exceeds a bound; that fallback can cause extra builds.

When a fingerprint differs from the recorded one, the result includes
`changedInputs`, up to 20 sorted POSIX paths relative to the project (including
additions, deletions and mode changes), and `changedInputsTruncated` when more
changed. Both fields are absent when the fingerprint matches.

Force execution when external tools/services changed or the application uses
excluded artifacts as build inputs. Local records are an optimization; they
are not a replacement for independent verification before release.

## Inspection limits

Source inventories use literal configuration, dependency declarations,
annotations and SQL. An unparseable YAML document is skipped and reported in
`warnings` with its relative path; ports from other documents and files are
kept. A static endpoint's `version` is `null` unless its path has a literal
`/v<N>` segment, and a path with a `${...}` placeholder is kept with one warning
per file and placeholder. In OpenAPI comparison those static routes are listed
in `comparison.unresolved`, and runtime paths that could match by method and
literal fragments in `comparison.unresolvedRuntime`; exact comparison of literal
routes still uses method and path. `check_migrations` status on Gradle is
refused after discovery with an explanation. Parent builds, catalogs, computed routes and runtime
configuration can provide information unavailable to static inspection.
Unknown evidence should stay unknown.

SQL heuristics live in `runtime/sql_lint.py`. The edit hook uses its immediate
warning policy; MCP uses its inventory/error/risk policy through the runtime
bridge. Their deliberately different DELETE and string-handling policies are
characterized with one shared corpus. Neither policy proves database safety.

The legacy runtime CLI action `mark` returns project discovery plus a
deprecation diagnostic without writing state. Old pending-marker files are
ignored; successful verification reuse depends only on scope and fingerprints.

Runtime OpenAPI and Actuator inspection accept only loopback targets
(`localhost`, `127.0.0.0/8` or `::1`) and report access, endpoint and
application failures separately. A refused or reset local connection
(`ECONNREFUSED` or `ECONNRESET`) gets one diagnostic: check that the
application listens on the configured port. A 5xx body that is not a health document is
not returned, and ports blocked by the fetch standard cannot be inspected.
Migration database status requires an explicitly requested mode and the
application's configured Flyway plugin/database, and is supported only for
Maven (`flyway:info`): a Gradle project gets an error that explains this and
points to running its configured Flyway task manually. Local migration lint
does not prove database compatibility.

Secret and DDL hooks are heuristic configuration guards. They compare the
proposed content with the current file and flag only literals the edit
introduces. The secret guard covers Spring configuration, Compose
(`docker-compose*.yml`, `compose*.yaml`) and `.env` files: credential keys,
credentials in URLs (`scheme://user:password@host`, including JDBC), AWS
(`AKIA`/`ASIA`), GitHub and Slack tokens, private-key blocks and a YAML value on
the next line. Placeholders such as `${VAR}` are accepted, and Compose
interpolation in Compose and `.env` files. It denies in Spring configuration,
Compose files and `.env` templates, and asks for confirmation for local `.env`,
`.env.*` and `*.env` files and in test paths. Java sources, JSON, XML and other
file types are not inspected; only the first line of a multi-line YAML scalar is
checked; URL passwords with an unencoded `/` or `@` and JDBC `;password=...`
parameters are missed; only the listed token formats are recognized; comments
are checked for tokens only. The DDL guard exempts `src/test/**`, `*-test.*` and
`application-test*` files. Reviewers depend on the caller for diffs and
executable build evidence. See
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
