# Contributing

Keep this plugin focused on Spring Boot development. New components should
have a distinct responsibility and work with the application's existing
conventions and build configuration.

## Components

- Skills belong in `skills/<name>/SKILL.md`. Match the directory and frontmatter
  names, describe when the skill should activate, and write actionable English
  instructions. Adapt examples to the target application; verify version
  claims against primary sources.
- Agents belong in `agents/<name>.md`. State their tools and output scope.
  Reviewers remain read-only; design and test writers stay within their roles.
- Hooks belong in `hooks/hooks.json` and `hooks/scripts/`. Keep pre/post-edit
  work inexpensive. Use valid Claude hook JSON, visible dependency diagnostics
  and functional regressions for changed behavior.
- Project discovery, command construction, locks and verification records
  belong in `runtime/project_runtime.py`. MCP and hooks share that runtime.
- MCP tools belong in `mcp-server/index.js`. Validate arguments, use argument
  arrays for execution, bound output/time and distinguish static evidence from
  runtime inspection.

Update the README catalog and registrations when adding, renaming or removing
components. Keep plugin, marketplace, MCP package/lockfile and server versions
aligned. Preserve dependency lockfiles and the license.

## Local validation

Install the MCP dependencies and run deterministic checks:

```bash
npm ci --prefix mcp-server
node --check mcp-server/index.js
python3 -m py_compile runtime/project_runtime.py hooks/scripts/hook-runtime.py
bash tests/run-all.sh
claude plugin validate .claude-plugin/plugin.json --strict
claude plugin validate .claude-plugin/marketplace.json --strict
claude plugin validate skills --strict
claude plugin validate agents --strict
git diff --check
```

`tests/run-all.sh` runs the hook, MCP, runtime, packaging and evaluation-harness
regressions. MCP tests use local processes and HTTP listeners. Build tests use
controlled wrappers and fixtures rather than an application's external services.

Validate Java examples separately with a compatible JDK, Maven and access to
the configured dependency registry:

```bash
bash tests/examples/check.sh
```

That check extracts code directly from skills, compiles it and runs smoke,
Pact and local Gatling checks. These fixtures exercise examples and workflow;
they do not certify a production provider, broker or performance target.

The CI workflow runs deterministic tests on Linux/macOS and validates the
examples in a Java job. It does not make behavioral model calls.

## Behavioral evaluations

Evaluations use a logged-in Claude Code account, make paid model calls and
may read plugin content. Run them explicitly and keep reports local:

```bash
claude plugin eval . --trust-plugin --case test-writer-behavior \
  --runs 1 --ablation none --threshold 1 --no-publish
claude plugin eval . --trust-plugin --runs 1 --ablation none \
  --threshold 1 --no-publish
```

Require every release criterion to pass. Compare judges against one frozen
response, choose the canonical judge before examining results and retain
failures alongside diagnostics. See [evals/README.md](evals/README.md) for the
freeze/judge commands. Model behavior checks supplement executable regressions.

## Reviewing changes

Explain the trigger, resulting behavior and validation evidence. Include a
regression when correcting an executable defect. Shared runtime changes should
cover relevant input freshness, task/module/profile scope, failure/cancellation
or cross-process coordination. Keep generated logs and evaluation reports out
of commits.

Use Conventional Commits and a title that describes the final change. Mark
breaking public tool/configuration changes explicitly. Test the plugin from a
Spring application with `claude --plugin-dir /absolute/path/to/this/repo` when
interactive verification is needed.
