# Evals — plugin behavior test suite

Run from the plugin root: `claude plugin eval .`

- Each subdirectory is one case: a realistic prompt (`prompt.md`) plus graders
  (`graders/*.md`) that pass/fail on what Claude produced.
- Runs are real model calls (with-plugin arm and no-plugin baseline arm by
  default). Iterate cheaply with:
  `claude plugin eval . --case <name> --runs 1 --ablation none`
- CI invocation and options: see CONTRIBUTING.md and
  `claude plugin eval --help`.

`results/` is gitignored — it is run output, not suite content.

Cases also cover applied migration history, PostgreSQL 18 volume persistence, Modulith verification, Gatling session/injection profiles, Boot 4 OTLP/servlet APIs and JWT encoding with refresh-cookie CSRF protection. Run one iteration with `claude plugin eval . --trust-plugin --runs 1 --ablation none --no-publish`. These evals assess generated behavior; deterministic MCP/hook tests and Java-example compilation are separate checks and must also pass.

## Workflow coverage

Cases validate evidence-first performance, mixed cookie/bearer CSRF, actual log-source selection and readiness, complete contract release gates, load acceptance and staged migration compatibility. Run the cases in `contract-release-cycle`, `performance-evidence-first`, `cookie-bearer-security`, `no-monitor-readiness`, `load-acceptance-cycle` and `migration-compatibility-gates` with the documented evaluation command. These are model behavior checks; deterministic Java/Pact/Gatling execution lives in `tests/examples/check.sh`.

`pr-docs-selectivity` checks PR scope avoids unnecessary builds/reviewers; `test-writer-behavior` checks risk-driven observable coverage.

## Reproducible judge comparisons

`test-writer-behavior` supplies versioned source, SQL and JSON fixtures directly
in its prompt; the model receives the fixture context even when its evaluation directory is
empty. Its five graders and the five
`contract-release-cycle` graders identify failures separately. The fixture
context is checked for drift by `tests/evaluations/test-basic.sh`.

To compare judges, generate **once**, then freeze that run's response and the
current criteria. The freezer rejects a report whose prompt differs from the
current case. For example:

```bash
python3 evals/tools/frozen_judge.py freeze \
  evals/results/run/result.json evals/contract-release-cycle \
  evals/results/run/contract-frozen.json
python3 evals/tools/frozen_judge.py judge \
  evals/results/run/contract-frozen.json \
  evals/results/run/contract-judged.json --models haiku sonnet
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
