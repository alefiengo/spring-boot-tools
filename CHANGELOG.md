# Changelog

## [0.4.0](https://github.com/alefiengo/spring-boot-tools/compare/v0.3.0...v0.4.0)

- Move `package.json` and `package-lock.json` from `mcp-server/` to the plugin
  root so Claude Code installs the MCP dependencies automatically when it
  copies the plugin from a marketplace. A checkout loaded with `--plugin-dir`
  (or a marketplace added from a local path) still needs `npm ci` in the root;
  remove a leftover `mcp-server/node_modules`, which the doctor now flags.
  `.mcp.json` no longer sets `cwd`; the project root travels in `PROJECT_ROOT`.
- Update `fast-xml-parser` to 5.12.0 (GHSA-474x-hrwr-fgx4); `npm audit` is clean.
- Restrict `actuator_health` and `openapi_endpoints` to loopback targets
  (`localhost`, `127.0.0.0/8`, `::1`). Breaking for callers that inspected a
  remote host. Non-health 5xx bodies are no longer returned.
- Keep build state and the project lock in `~/.cache/spring-boot-tools/`
  (resolved from the user database, mode 0700) instead of a temporary
  directory. The MCP server terminates builds in progress when it is stopped,
  and on Linux a hook's build ends with the hook. Project discovery ignores an
  ancestor build directory that another user can write (including `.mvn/**`,
  `gradle/wrapper/*` and `gradle.properties`) and reports it in
  `discoveryWarnings` instead of falling back silently. On WSL Windows drives
  (`/mnt/c`) without the drvfs `metadata` mount option every file is mode 0777,
  so an enclosing reactor there is refused: enable `metadata` in
  `/etc/wsl.conf` or keep projects in the Linux file system. Group-writable
  files are trusted only for a user-private group, never on macOS. A uid
  without a user database entry or a missing/read-only home falls back to a
  private `spring-boot-tools-<uid>` directory in the temporary directory.
- Make the edit guards compare against the current file so only introduced
  literals are flagged; `guard-secrets` asks for confirmation in test paths and
  `guard-ddl` exempts `src/test/**` (relative to the project, not the absolute
  path), `*-test.*` and `application-test*`. The Stop hook reports without
  blocking again when `stop_hook_active` is set, and discovery errors other
  than "no project found" are reported.
- Remove `Write` and `Edit` from `sb-architect` and `sb-api-designer`; they
  return their document as the final message and the caller saves it.
- Forward JDK, Maven, Gradle and proxy variables to the Codex MCP process
  (Codex already passes `TMPDIR`, `LC_ALL` and `TZ`); registry credentials
  passed by variable must be added to `env_vars`. Codex catalog entries now
  carry a `category`.
- Correct skill guidance verified against current sources: Spring Cloud
  Contract matchers, the CORS bean name, API versioning defaults, Boot 3 to 4
  starter renames (including Flyway/Liquibase starters), Jackson 3 behavior,
  support dates, CI action and image versions, and container user ordering.
  Narrow skill descriptions so generic Java requests do not trigger them.
- Add six behavioral cases (test-writer-agent, build-run-module-failure,
  api-contracts-error-shape, ci-verify-gate, no-hijack-plain-junit,
  no-hijack-node-ci), more graders and plugin-eval report checks; none has
  been run against a model.
- Add `license`, `repository` and `homepage` to the plugin manifest.

Compatibility: breaking for callers of `actuator_health` or `openapi_endpoints`
against non-loopback hosts. A `--plugin-dir` checkout needs `npm ci` in the
plugin root. Build state moves to `~/.cache/spring-boot-tools/`; earlier
temporary state is ignored. Design agents no longer write files.

## [0.3.0](https://github.com/alefiengo/spring-boot-tools/compare/v0.2.0...v0.3.0)

- Remove repository GitHub Actions workflows, the CI badge and automatic
  Dependabot updates. Use documented local validation and dependency audits;
  `main` no longer requires hosted status checks.
- Remove the unused post-edit pending-marker hook; Stop verification still
  fingerprints current inputs and the legacy `mark` action returns a
  deprecation diagnostic without writing state.
- Share migration lint heuristics between hook and MCP adapters, preserving
  their characterized warning/error policies.
- Add an offline installation doctor and a host capability/evidence matrix.
- Add real Maven/Gradle workflow fixtures separate from fast deterministic
  regressions.
- Prepare reproducible baseline/full/component-ablation inputs and comparisons
  without automatic model calls; record missing measurements explicitly.
- Refine skill and agent instructions to respect project conventions and use
  specialist delegation only when task scope warrants it.

Live comparative model results are not included. Component effectiveness remains
unmeasured until explicitly budgeted runs are completed.

Compatibility: the former `post-edit-compile` hook is no longer registered.
Stop still detects external edits through current fingerprints; the legacy
`mark` runtime action is deprecated and writes no state. Codex verification
remains explicit. The MCP tool catalog and host prerequisites are unchanged.

## [0.2.0](https://github.com/alefiengo/spring-boot-tools/compare/v0.1.0...v0.2.0)

- Add explicit Spring Initializr project creation through a new skill and seventh
  MCP tool, with existing-project protection, bounded archive validation and
  atomic publication into an absent destination.
- Add Codex CLI support through its own manifest, MCP configuration and launcher;
  share 16 skills and seven tools while keeping Claude hooks and agents separate.
- Harden process cancellation, report collection/transport, module discovery,
  source scanning and Actuator document validation.
- Fix JWT audience and CI permission examples, coordinate finite migration
  builds, and align testing guidance with observable behavior.
- Update Zod to 4.6.7 to address GHSA-mrhx-c7xc-m3w5.
- Add deterministic regressions, an isolated Codex protocol smoke check, audit
  findings and host-specific installation/verification documentation.

Compatibility: Linux/macOS/WSL with Node.js 20.3+, Python 3.11+ and Bash.
Initialization requires a nonexistent target with an existing parent; even an
existing empty directory is preserved and refused. Codex requires the documented
launcher and explicit verification; Claude hooks, agents and LSP are not
registered there. Codex CLI 0.166.0 was checked; other hosts/versions need their
own validation. Oversized test reports/results fail with bounded diagnostics.

## [0.1.0](https://github.com/alefiengo/spring-boot-tools/releases/tag/v0.1.0)

- Initial public version: 15 skills, seven Claude specialist agents, five hooks,
  six MCP tools and a shared Maven/Gradle build runtime.
- Include CI, deterministic regressions, Java/Pact/Gatling examples and behavioral
  evaluation fixtures.
