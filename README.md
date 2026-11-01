# Spring Boot Tools for Claude Code and Codex

[![Release](https://img.shields.io/github/v/release/alefiengo/spring-boot-tools)](https://github.com/alefiengo/spring-boot-tools/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A plugin for developing and reviewing Spring Boot applications with Claude Code and Codex.
It provides 16 shared skills and seven MCP tools. Claude Code additionally loads seven specialist agents and four hooks.
Build execution and verification share one runtime so hooks and tools can
coordinate their work.

## Requirements

- Node.js 20.3 or newer and npm for the MCP server.
- Python 3.11 or newer and Bash on Linux, macOS or WSL.
- A JDK compatible with the application and its Maven or Gradle wrapper.
  A global build-tool installation is used when no wrapper is available.
- Optional Java diagnostics: an installed Eclipse JDT Language Server with
  its `jdtls` launcher on `PATH`.

Skills target Spring Boot 4 applications and include migration guidance for
Boot 3 projects. Inspect the application's build before selecting dependency
versions or adapting examples.

For Codex installation, configuration and host differences, see
[Codex support](docs/codex.md). Both hosts use the same MCP implementation;
Codex verification is explicit and does not depend on Claude's Stop hook.

Check a local installation without changing it:

```bash
python3 /absolute/path/to/spring-boot-tools/scripts/install-doctor.py \
  --workspace /absolute/path/to/application --project /absolute/path/to/application
```

Add `--json` for structured diagnostics. The doctor checks local prerequisites,
registrations and workspace selection; it does not install packages, change
host configuration, contact services or execute builds. See the
[capability matrix](docs/capability-matrix.md) for host differences and the
distinction between functional tests and measured usefulness.

The [component evidence inventory](docs/component-evidence.json) distinguishes
functional coverage from measured model benefit. The
[evaluation methodology](docs/evaluation-methodology.md) explains offline
baseline/full/component preparation, provenance and separate generation/judging
metrics. No live comparative effectiveness or cost-saving result is claimed.

To create a project, explicitly ask, for example:

```text
Initialize a new Spring Boot project in /path/to/new-service with Maven,
Java and the web dependency. Use currently available Initializr options.
```

The initializer checks the active workspace and destination for an existing
Maven/Gradle project context before contacting Spring Initializr. It never
re-scaffolds an existing application, overwrites nonempty destinations or
runs generated builds. See the initialization skill for supported targets.

## Install and use

For local use, clone the plugin and install its server dependencies:

```bash
git clone https://github.com/alefiengo/spring-boot-tools ~/dev/spring-boot-tools
npm ci --prefix ~/dev/spring-boot-tools/mcp-server
cd /path/to/your/spring-project
claude --plugin-dir ~/dev/spring-boot-tools
```

Once loaded, try a request such as:

```text
Inspect this Spring Boot project, identify its build tool and modules,
then run its tests and summarize any failures.
```

For a focused review, use `/sb-pr-review`. Claude selects the relevant
reviewers and coordinates a verification gate.

The plugin registers its MCP server, hooks and optional Java language server
from `.mcp.json`, `hooks/hooks.json` and `.lsp.json`.

For marketplace installation:

```bash
claude plugin marketplace add https://github.com/alefiengo/spring-boot-tools
claude plugin install spring-boot-tools@spring-boot-tools-marketplace
```

Install the MCP dependencies with `npm ci` in the installed plugin's
`mcp-server/` directory. A team can enable the plugin in its application's
`.claude/settings.json`:

```json
{
  "enabledPlugins": {
    "spring-boot-tools@spring-boot-tools-marketplace": true
  }
}
```

Configure `app_port` through the plugin configuration. It defaults to 8080
and supplies the default application port for Actuator inspection. Project
build files determine the build tool and dependency versions.

## Skills

Claude selects skills from their descriptions and the request context. Each
skill lives in `skills/<name>/SKILL.md`.

| Skill | Purpose |
|---|---|
| `spring-boot-initializr` | Initialize an explicitly requested new project without overwriting existing work |
| `spring-boot-api-contracts` | REST contracts, error responses and OpenAPI |
| `spring-boot-build-run` | Build, startup, profiles and troubleshooting |
| `spring-boot-ci` | Build gates, scans and image publication in CI |
| `spring-boot-codegen` | Controllers, services, entities, repositories and DTOs |
| `spring-boot-contract-testing` | Pact and Spring Cloud Contract workflows |
| `spring-boot-database` | Flyway, persistence mappings and SQL |
| `spring-boot-docker` | Images, Compose, persistence and container startup |
| `spring-boot-load-testing` | Gatling, k6 and measurable acceptance criteria |
| `spring-boot-modulith` | Module boundaries and architecture verification |
| `spring-boot-observability` | Logs, metrics, traces and Actuator diagnostics |
| `spring-boot-performance` | Query behavior, pools, caching and measurement |
| `spring-boot-security-hardening` | Authentication, authorization, cookies and CSRF |
| `spring-boot-testing` | Unit, slice and integration tests |
| `spring-boot-3-to-4-migration` | Staged migration from Spring Boot 3 to 4 |
| `sb-pr-review` | Relevant reviewers, one verification gate and a shared verdict |

For example, ask Claude to design an API, investigate slow queries or write
tests for a specific behavior. Use `/sb-pr-review` for a review of the current
changes.

## Specialist agents

| Agent | Responsibility | Output permissions |
|---|---|---|
| `sb-architect` | Domain design, boundaries and implementation planning | Design documents |
| `sb-api-designer` | Routes, payloads, errors and API contracts | Contract documents |
| `sb-code-reviewer` | Correctness, security and maintainability | Read-only |
| `sb-runtime-reviewer` | Transactions, query paths, concurrency and N+1 | Read-only |
| `sb-migration-reviewer` | Migration history, compatibility and locking | Read-only |
| `sb-docker-reviewer` | Packaging, persistence, startup and CI | Read-only |
| `sb-test-writer` | Tests tied to observable behavior and risks | Test sources and fixtures |

Ask for an agent by name when a specific role is useful. Design before
implementation; select reviewers from the changed behavior. The caller
supplies reviewers with diffs and build results. Changes confined to prose
need no application build or application reviewers.

## MCP tools

| Tool | Responsibility |
|---|---|
| `initialize_project` | Generate a project through Spring Initializr into an absent target; refuse existing project contexts and targets |
| `analyze_project` | Discover projects/modules, declared dependencies, configuration and persistence mappings |
| `run_build` | Execute controlled Maven/Gradle tasks with module/profile scope |
| `run_tests` | Run tests and summarize fresh XML reports and failures |
| `check_migrations` | Inspect migration names, versions, ordering and SQL risks; explicitly request database status |
| `openapi_endpoints` | Inspect static mappings or runtime OpenAPI and compare them |
| `actuator_health` | Inspect health, readiness and liveness with access diagnostics |

Static inventories describe available source evidence. Runtime endpoints and
configured database inspection provide additional evidence when requested.

## Automatic hooks

| Hook | Event | Behavior |
|---|---|---|
| `guard-secrets` | Before Write/Edit | Inspect proposed configuration for hard-coded credentials |
| `guard-ddl` | Before Write/Edit | Reject destructive Hibernate schema settings outside recognized development configuration |
| `lint-migration` | After Write/Edit | Check migration names and flag destructive SQL |
| `verify-on-stop` | Stop | Run or reuse an equivalent successful Maven verify / Gradle check |

Verification reuse requires matching project inputs, task, module and
profiles. Failed/cancelled builds and compile/test-only results cannot satisfy
a full verification gate. Explicit reruns can force execution. The shared
runtime serializes plugin builds for a project and captures test reports
inside that lock.

The former `post-edit-compile` marker hook has been removed: Stop and MCP
verification inspect current fingerprints directly, including edits made
outside the host's Write/Edit tools. No compilation runs after every edit.

## Repository validation

GitHub Actions and automatic Dependabot updates are disabled for this
repository. Changes are validated locally before merging; see
[CONTRIBUTING.md](CONTRIBUTING.md) for deterministic checks, Java examples,
real Maven/Gradle fixtures and dependency audits. No hosted check is required
to merge. The shipped CI skill remains available for application projects.

## Repository layout

Versioned top-level directories and configuration files:

| Path | Contents |
|---|---|
| `.claude-plugin/` | Plugin and marketplace manifests |
| `.codex-plugin/` | Codex compatibility manifest |
| `codex/` | Codex MCP configuration and workspace-preserving launchers |
| `.github/` | Issue forms and PR template |
| `agents/` | Specialist design, review and test-writing roles |
| `docs/` | Architecture, shared runtime behavior and operational limits |
| `evals/` | Behavioral prompts, graders, fixtures and frozen-response judge tooling |
| `hooks/` | Event registration, Bash launchers and Python hook implementation |
| `mcp-server/` | Node.js MCP server, package metadata and dependency lockfile |
| `runtime/` | Shared project discovery, build execution and local gate records |
| `scripts/` | Offline installation diagnostics |
| `skills/` | Task guidance in one `SKILL.md` per skill |
| `tests/` | Deterministic regression suites and executable Java/Pact/Gatling examples |
| `.gitignore` | Exclusions for local state, dependencies and generated artifacts |
| `.lsp.json` | Optional Java language-server registration |
| `.mcp.json` | MCP server launch configuration and project/user settings |

The root documentation consists of this README, `CONTRIBUTING.md`,
`SECURITY.md`, `CHANGELOG.md`, `AGENTS.md` and `LICENSE`. Generated dependencies, caches and evaluation
reports are excluded from version control.

See [CONTRIBUTING.md](CONTRIBUTING.md) for validation and development,
[evals/README.md](evals/README.md) for model evaluations, and
[SECURITY.md](SECURITY.md) for security reporting and trust boundaries.
The project is licensed under the [MIT license](LICENSE).

See the [changelog](CHANGELOG.md) for release changes and compatibility notes,
and the [audit report](docs/audit-2026-10-31.md) for findings, validation and
remaining coverage limits.
