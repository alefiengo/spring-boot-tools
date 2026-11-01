# Changelog

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
