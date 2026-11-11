# Evals — plugin behavior test suite

Run from the plugin root: `claude plugin eval .`

- Each subdirectory is one case: a realistic prompt (`prompt.md`) plus graders
  (`graders/*.md`) that pass/fail on what Claude produced.
- Runs are real model calls (with-plugin arm and no-plugin baseline arm by
  default). Iterate cheaply with:
  `claude plugin eval . --case <name> --runs 1 --ablation none`
- Release invocation: see "Behavioral evaluations" in CONTRIBUTING.md and
  `claude plugin eval --help`. This repository has no hosted CI; evaluations
  run only when explicitly requested.

`results/` is gitignored — it is run output, not suite content.

Cases also cover applied migration history, PostgreSQL 18 volume persistence, Modulith verification, Gatling session/injection profiles, Boot 4 OTLP/servlet APIs and JWT encoding with refresh-cookie CSRF protection. Run one iteration with `claude plugin eval . --trust-plugin --runs 1 --ablation none --no-publish`. These evals assess generated behavior; deterministic MCP/hook tests and Java-example compilation are separate checks and must also pass.

## Workflow coverage

Cases validate evidence-first performance, mixed cookie/bearer CSRF, actual log-source selection and readiness, complete contract release gates, load acceptance and staged migration compatibility. Run the cases in `contract-release-cycle`, `performance-evidence-first`, `cookie-bearer-security`, `no-monitor-readiness`, `load-acceptance-cycle` and `migration-compatibility-gates` with the documented evaluation command. These are model behavior checks; deterministic Java/Pact/Gatling execution lives in `tests/examples/check.sh`.

`pr-docs-selectivity` checks PR scope avoids unnecessary builds/reviewers; `test-writer-behavior` checks risk-driven observable coverage.

## Reproducible judge comparisons

`test-writer-behavior` supplies versioned source, SQL and JSON fixtures directly
in its prompt; the model receives the fixture context even when its evaluation directory is
empty. Multi-criterion cases use one grader file per criterion so a failure
identifies the criterion. The fixture
context is checked for drift by `tests/evaluations/test-basic.sh`.

To compare judges, generate **once**, then freeze that run's response and the
current criteria. The freezer rejects a report whose prompt differs from the
current case. For example:

```bash
python3 evals/tools/frozen_judge.py freeze \
  evals/results/<timestamp>/aggregate-result.json evals/contract-release-cycle \
  evals/results/<timestamp>/contract-frozen.json
python3 evals/tools/frozen_judge.py judge \
  evals/results/<timestamp>/contract-frozen.json \
  evals/results/<timestamp>/contract-judged.json --models haiku sonnet
```

Judging makes external model calls. The first model is the canonical judge,
chosen before results; later models are diagnostic and cannot override its
failure. Every judge receives the same checksummed input and reports each
criterion, a reason, and a literal response excerpt. Missing, duplicated or
malformed verdicts, invented excerpts and invocation errors fail closed.
Reports retain raw output, requested model, resolved model usage and input /
judge-prompt hashes. Existing reports are never overwritten. Keep the frozen
bundle when comparing models later; do not freeze against changed criteria.
Model aliases may resolve differently over time: use full model IDs when
pinning a release gate. A favorable generated sample is not proof of all
possible model behavior, and these checks do not replace executable tests.

## Offline usefulness evidence

See [evaluation methodology](../docs/evaluation-methodology.md) and the
[component coverage map](../docs/component-evidence.json) for the offline
`ablation.py plan`, `prepare` and `compare` commands. They never execute models.
The original 17 cases plus eight unrun cases (two Initializr, an explicit
test-writer agent request, build-run, API-contract and CI positives, and two
no-hijack negatives) map task coverage without claiming effectiveness. Only
skills/agents support automated selected-minus preparation; hooks, MCP and LSP
marginal value remain unmeasured.

The freezer accepts `--arm`, `--variant` and `--provenance` while retaining
`with` / run 0 defaults. It preserves original grader and activation results
separately from independent quality judgments. Plans pin the Claude host/version,
intended effort, limits and nullable metric ceilings; exported actual-run
metadata must establish those facts. The installed evaluator has no `--effort`
flag. The freezer reads the documented `aggregate-result.json` fields and
accepts both the documented and the earlier synthetic spellings for per-run
cost and duration, which the documentation does not fix. The evaluator
records no effort and no per-run requested model, so a pinned comparison
cannot be established from its report alone. No live comparative evidence is
included in this change.

## Case limitations

- Each positive case has a `skill-fired` grader. In a two-arm run it is a
  plugin-fired indicator, not part of the score; under `--ablation none` it is
  scored, so a correct answer produced without the expected skill fails.
- The Initializr cases answer `initialize_project` from a case mock
  (`mocks/spring-boot/initialize_project.md`); `claude plugin eval` does not
  start the real MCP server by default, and an unmocked tool is unavailable.
  The positive mock checks the call input and returns a fixture result; it
  does not contact Spring Initializr. For a live run, use
  `--mocks off --allow-tools "mcp__plugin_spring-boot-tools_spring-boot__initialize_project"`
  and accept that the real server runs outside the evaluator sandbox.
- `sb-pr-review` is user-invoked only (`disable-model-invocation: true`), so
  `pr-docs-selectivity` starts with `/spring-boot-tools:sb-pr-review`. The
  no-plugin baseline has no such command, so that case's delta is not a
  comparison of equal prompts.
- `codegen-controller` and `test-writer-agent` allow the `Agent` tool, but
  only `test-writer-agent` grades whether an agent (`sb-test-writer`) was
  dispatched. The other six agents have no graded behavioral case.
- The MCP tool names in graders follow the documented
  `mcp__plugin_<plugin>_<server>__<tool>` pattern; they have not yet been
  confirmed by a run.
