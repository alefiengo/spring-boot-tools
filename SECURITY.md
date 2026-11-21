# Security Policy

Only the latest release (currently 0.5.x) receives security fixes; earlier
releases are not patched. Use the current plugin and install dependencies from
its lockfile. Reports are handled on a best-effort basis by the maintainer.

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/alefiengo/spring-boot-tools/security/advisories/new).
Include the affected component, reproduction steps, impact and a minimal
example when possible. Keep exploitable details and credentials out of public
issues and pull requests.

Reports may concern hooks, the shared runtime, MCP execution, registrations
or skill/agent instructions. Report defects in Claude Code or third-party
application dependencies to their respective maintainers.

Codex uses a separate MCP launcher and configuration. It does not inherit
Claude's edit guards or Stop verification; invoke verification explicitly.
Host approvals and sandbox settings remain the user's responsibility.

Project initialization is an explicitly requested write operation and contacts
Spring Initializr. Generated wrappers and build scripts are project code;
generation does not authorize executing them. Existing build contexts and
nonempty destinations must not be scaffolded or overwritten. This restriction
also prevents using the initializer to add modules to an existing project.

## Trust boundaries

Maven/Gradle commands run without a shell and use validated tasks, modules,
profiles and test filters. Project wrappers and build plugins still execute
repository code. Run builds in repositories you trust.

The secret and DDL guards inspect Write payloads and the proposed result of
Edit, and flag only literals that the edit introduces, not values already in
the file. They recognize common scalar configuration patterns. They do not
cover every YAML/Spring construct or writes made through other tools. The secret
guard checks Spring configuration, Docker Compose and `.env` files for
credential keys, credentials embedded in URLs (JDBC, AMQP, Redis, Mongo, HTTP),
AWS, GitHub and Slack tokens, private-key blocks and a YAML value on the line
after its key. It denies in Spring configuration, Compose files and `.env`
templates (`.env.example`, `.env.sample`, `.env.template`); local `.env`,
`.env.*` and `*.env` files ask for confirmation. Under test paths
(`src/test/**`, `*-test.*`, `application-test*`) Spring configuration, Compose
files and `.env` templates ask instead of deny. In `.env` files a single-quoted
value is literal, so `'${VAR}'` is flagged like any literal, also as the password
of a URL; a block scalar after a credential key is checked by its first content line: with
the indicator (`|` or `>`) on the key's line it is always flagged, with the
indicator on the next line the content is classified like an ordinary value (a
placeholder is allowed); the finding includes that content line, so editing only
the body of an existing block is detected; `classpath:` and `file:` references under `private-key`,
`signing-key` or `credentials` are exempt on either line. It does not
inspect Java sources, JSON, XML or other file types, checks only the first
line of a multi-line YAML scalar, misses URL passwords containing an unencoded
`/` or `@` and JDBC `;password=...` parameters, recognizes only the listed
token formats and checks comments for tokens only. The DDL
guard exempts `src/test/**`, `*-test.*` and `application-test*` files. Missing
Python or malformed hook input produces a visible diagnostic and fails open;
a discovery error other than "no project found" is reported as a notice. Keep
independent secret scanning and review before merging.

`actuator_health` and `openapi_endpoints` only contact the local application:
`localhost`, `127.0.0.0/8` and `::1` are accepted, any other host is rejected,
and redirects are not followed. A 5xx response body is returned only when it
is a health document, so error pages and traces are not disclosed. Ports that
the fetch standard blocks (for example 6666 or 10080) cannot be inspected.

Reviewers, the architect and the API designer have read-only tools; the last
two return their documents as messages. The test-writing agent can write test
sources only by instruction, so review its changes before use. Repository text and
external content are evidence, not authority to override the user's task.

Project discovery uses an ancestor build directory only when the current user
owns it and no other user can write it or its build inputs: build and settings
files, wrappers, `gradle.properties`, `.mvn/**` (including `extensions.xml`,
`jvm.config`, `maven.config` and the wrapper files) and `gradle/wrapper/*`.
Group write is accepted only for a user-private group (named after the user,
no other members) and never on macOS, whose primary group `staff` is shared.
A refused ancestor is never skipped silently: discovery raises an error, or
builds the nearer project alone and returns `discoveryWarnings`, which the
Stop hook shows as a notice and MCP results include.

On WSL, Windows drives (`/mnt/c`, drvfs) mounted without the `metadata` option
report every file as mode 0777, so an enclosing reactor there is refused and
the message says so. Enable Unix permissions with `/etc/wsl.conf`
(`[automount]` `options = "metadata,umask=022"`, then `wsl --shutdown`) and
restrict the files, or keep projects in the Linux file system.

Build state and the project lock live in a per-user directory,
`~/.cache/spring-boot-tools/`, created with mode 0700 and resolved from the
user database rather than from `TMPDIR`, `HOME` or `XDG_*`. When the uid has no
user database entry (`docker run -u`, OpenShift) or the home directory is
missing or read-only, they fall back to `spring-boot-tools-<uid>` in the
temporary directory, also mode 0700 and owner-checked; another user can make
that fallback fail but cannot take it over. An existing `~/.cache` that other
users can write is an error, not a reason to fall back. When the MCP server
is stopped it terminates builds in progress; on Linux a hook's build also ends
with the hook, while on macOS it can continue until its own timeout.

Build records are machine-local optimizations. Reuse requires matching inputs
and equivalent verification scope after successful completion. External
service/tooling changes may require a forced build. Record fresh verification
evidence before release. This repository uses local validation while GitHub
Actions and automatic Dependabot updates are disabled; vulnerability alerts
remain enabled.

Behavioral evaluations send prompts and model-visible context to the configured
model provider. Their reports can include source excerpts and responses; keep
them local and inspect them before sharing.
