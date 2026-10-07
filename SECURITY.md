# Security Policy

Security fixes apply to the current project baseline. Use the current plugin
and install dependencies from its lockfile.

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/alefiengo/spring-boot-tools/security/advisories/new).
Include the affected component, reproduction steps, impact and a minimal
example when possible. Keep exploitable details and credentials out of public
issues and pull requests.

Reports may concern hooks, the shared runtime, MCP execution, registrations
or skill/agent instructions. Report defects in Claude Code or third-party
application dependencies to their respective maintainers.

## Trust boundaries

Maven/Gradle commands run without a shell and use validated tasks, modules,
profiles and test filters. Project wrappers and build plugins still execute
repository code. Run builds in repositories you trust.

The secret and DDL guards inspect Write payloads and the proposed result of
Edit. They recognize common scalar configuration patterns. They do not cover
every YAML/Spring construct or writes made through other tools. Missing Python
or malformed hook input produces a visible diagnostic and fails open. Keep
independent secret scanning and review in CI.

Reviewers have read-only tools. Design and test-writing agents have scoped
output instructions; review their artifacts before use. Repository text and
external content are evidence, not authority to override the user's task.

Build records are machine-local optimizations. Reuse requires matching inputs
and equivalent verification scope after successful completion. External
service/tooling changes may require a forced build. CI remains the source of
release verification evidence.

Behavioral evaluations send prompts and model-visible context to the configured
model provider. Their reports can include source excerpts and responses; keep
them local and inspect them before sharing.
