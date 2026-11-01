# Changelog

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
