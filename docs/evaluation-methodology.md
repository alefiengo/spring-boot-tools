# Evaluation methodology

The offline harness prepares reproducible comparisons and validates evidence. It
has made **no live comparative runs** and establishes no component effectiveness,
token savings or cost benefit. Synthetic regression verdicts test the harness,
not Spring Boot task performance. The coverage inventory is
[component-evidence.json](component-evidence.json).

## Evidence and coverage

The original suite contains 17 cases. Their prompts are unchanged except
pr-docs-selectivity, which now starts with the sb-pr-review command; most gained
a skill-fired criterion and five had their single rubric split per criterion,
so bundles frozen before 2026-11-10 do not match current criteria. The cases
are applied-migration,
bad-migration-name, boot4-tracing, codegen-controller, contract-release-cycle,
cookie-bearer-security, gatling-profile, jwt-api, load-acceptance-cycle,
migration-compatibility-gates, modulith-boundaries, no-hijack-generic-java,
no-monitor-readiness, performance-evidence-first, postgres18-volume,
pr-docs-selectivity and test-writer-behavior. Eight unrun cases bring the suite
to 25: two Initializr cases, `test-writer-agent`, `build-run-module-failure`,
`api-contracts-error-shape`, `ci-verify-gate`, `no-hijack-plain-junit` and
`no-hijack-node-ci`. The Initializr cases answer `initialize_project` from a case
mock: the positive case checks the guarded call input and the reply's fidelity
to the returned result; a refused or unavailable tool cannot count as
successful initialization. The refusal case makes the tool available and
requires that it is never called. None of the new cases has been run against a
model.

A relevant prompt is not proof that a skill activated, that an agent was invoked,
or that either improved an answer. Each positive case now has a `skill-fired`
criterion and each no-hijack case a selectivity criterion; the map lists them
under `explicitActivationCriteria`. `codegen-controller` and `test-writer-agent`
allow the Agent tool, but only `test-writer-agent` grades an agent dispatch, so
six agents have no graded behavioral case. Retain original grader results alongside
response-quality judgments. The no-hijack case has only a tool criterion and can
be frozen without inventing an LLM quality criterion. Quality judging requires
at least one actual LLM criterion.

The map lists all 16 skills and seven agents, plus executable tools, hooks,
runtime, adapters, LSP registration and the installation doctor. Paths to tests
identify existing deterministic coverage, not an assertion that those tests were
run by this task. Doctor has deterministic regressions; model-directed doctor
usage remains unmeasured. There is no doctor behavioral case in this change.

## Scope and preregistration

This harness supports **Claude Code only**. It snapshots its product adapter and
production scripts; it excludes the Codex adapter. Codex behavior needs its own
host-specific experiment. Automated variants are baseline (no plugin), full, and
selected-minus (the full plugin with explicitly selected skill directories and/or
agent definitions removed). Hooks, MCP tools, runtime and LSP are held fixed
between full and selected-minus. There is no automated ablation of those
components and their marginal model usefulness remains unmeasured.

Before generating anything, pin cases, number of runs, requested model, effort,
Claude Code version, application/plugin snapshots, all criterion sources, the
canonical judge, generation timeout/turn limit and judging timeout. Use exact
model IDs when available. Requested aliases never establish resolved-model
identity. Optional ceilings preregister input tokens, output tokens, USD cost
and duration in milliseconds, separately for generation and judging. Default
ceilings are null, meaning no ceiling was preregistered, not zero or a pass.
Specify every ceiling key in both stage objects if supplying a ceilings JSON.
Choose criteria, judge and limits before inspecting responses; retain failures.

The installed `claude plugin eval --help` exposes `--model`, `--judge-model`,
`--max-cost-usd`, `--ablation with-without`, `--allow-tools`, `--mocks`,
`--allow-real-servers` and `--scaffold`; it does **not** expose `--effort`.
The planner's --effort is a preregistration field, not an evaluator setting.
Effort equality is not automatically established by that evaluator. Verify the
actual run's raw exported metadata before accepting an effort pin; if effort is
not recorded, do not fill it from the preparation declaration to make a
comparison pass. The same rule applies to host/version and execution limits:
preparation records contain intended settings, not authenticated execution facts.
No adapter that supplies missing runtime facts is implemented here.

## Offline preparation

Stage an application-only fixture snapshot deliberately. The inventory preserves
source packages and modules named build, target, reports or results. It does not
infer build output directories from their names. Root metadata directories
(evals, graders, reports, results, host state, Git and dependency/cache state)
are excluded; source directories beneath them are an unsupported snapshot layout
and must be staged separately. AGENTS.md, CLAUDE.md and GEMINI.md are excluded at
any depth. Included symlinks and special files are refused. Do not stage generated
artifacts as application inputs. Prompt fixture context must be supplied to every
arm; the existing test-writer prompt embeds its five fixtures and a regression
checks drift.

These commands make no model, build, network or installation calls. Substitute
actual operator paths and pins:

```bash
python3 evals/tools/ablation.py plan /tmp/experiment-plan.json \
  --snapshot /tmp/application-fixture --cases test-writer-behavior \
  --model EXACT_GENERATOR_ID --effort high --host-version EXACT_CLAUDE_VERSION \
  --canonical-judge EXACT_JUDGE_ID --generation-timeout 300 --max-turns 10 \
  --minus spring-boot-testing sb-test-writer
python3 evals/tools/ablation.py prepare /tmp/experiment-plan.json \
  /tmp/experiment-full --variant full --case test-writer-behavior \
  --record /tmp/experiment-full-provenance.json
python3 evals/tools/ablation.py compare /tmp/experiment-plan.json \
  /tmp/experiment-pending.json
```

Repeat prepare for baseline and selected-minus with fresh absent directories and
fresh operator record paths. Preparation refuses records under either source
tree or anywhere inside the generation directory; it never changes its pinned
source. Each generation directory contains only workspace/ and, for plugin arms,
plugin/. Operator records, prompts, graders and results stay outside that tree.
All application files and nonremoved product files have the same content hashes
and executable flags across arms; preparation rejects source drift.

An external, explicitly authorized runner must enforce filesystem permissions
that expose only that arm's workspace and intended plugin, disable ambient
plugins/session/account instructions, provide only the pinned prompt, enforce
time/turn limits, and record actual tool/plugin loading. Fresh directories and
checksums alone cannot stop an unrestricted model tool from reading parent or
sibling paths. This CLI does not execute or sandbox a model. The full Initializr
positive experiment also needs its real MCP dependency/runtime prerequisites and
network access; offline preparation cannot provide those results. By default
the case answers from its mock. In a later explicitly budgeted live run, pass
--mocks off and grant the tool with
--allow-tools "mcp__plugin_spring-boot-tools_spring-boot__initialize_project"
(the documented mcp__plugin_<plugin>_<server>__<tool> form; confirm it in the
run's tool list). --trust-plugin alone enables neither real servers nor tool
grants.

## Freeze and compare actual evidence

Preserve the original generator report. The freezer keeps the existing default
arm with and run 0, with optional --arm, --variant and --provenance. Legacy bundles
remain usable for independent judging but cannot establish a pinned comparison.
For a pinned comparison the generation report must record modelRequested, effort,
claudeVersion and executionLimits (timeoutSeconds and maxTurns); these must match
the preparation. Record them from the actual invocation, not from assumptions.
The documented `claude plugin eval` result fixes only document-level fields
(`claudeVersion`, `costUsd`, `durationSeconds`, `cases[].arms.with[]`). The
freezer takes executionLimits from the case's recorded `timeoutSeconds` and
`maxTurns`, which have no command-line override. The evaluator records no effort
and no per-run requested model (`--model` can override a case's model), so a
pinned comparison of its report fails closed on those fields.
Each generation result supplies a complete response or the existing unambiguous
scored-grader evidence. The evaluator's result has no response field, so the
freezer uses the scored LLM graders' evidence; whether that evidence is always
the complete final message has not been confirmed against a real run. Generation
usage is read only from that result, never from report-wide usage that might
contain graders. Supported telemetry is usage input_tokens/output_tokens/cache
fields, total_cost_usd, costUSD or costUsd, duration_ms, durationMs or
durationSeconds, and reported model/modelResolved/modelUsage. In a two-arm run,
Skill and with-only criteria are reported with `scored: false`; their verdict is
kept as an indicator rather than discarded. Missing fields remain
unavailable, including resolved model identity. Preserve original source reports
and runner logs to interpret the telemetry's scope.

```bash
python3 evals/tools/frozen_judge.py freeze /tmp/generator-report.json \
  evals/test-writer-behavior /tmp/full-frozen.json --arm with --variant full \
  --provenance /tmp/experiment-full-provenance.json
```

The explicit frozen_judge.py judge command described in [evals/README.md](../evals/README.md)
makes paid external calls and requires separate user authorization. No plan,
prepare, freeze or compare action invokes it automatically. Judging runs from a
fresh empty cwd with tools disabled; only prompt, response lines and quality
criteria are provided, hiding arm identity and previous grader scores. Every
quality criterion needs a boolean verdict and reason, with a literal response
line for PASS. Invalid output and invocation errors fail closed. The first judge
is canonical; diagnostic judges cannot override it. Pinned comparisons require
the preregistered canonical model and judging timeout. Judging wall duration is
measured by the runner, while usage/cost/resolution remain unavailable if the
judge does not report them. Generation duration is reported telemetry only.

```bash
python3 evals/tools/ablation.py compare /tmp/experiment-plan.json \
  /tmp/experiment-comparison.json \
  --entry /tmp/full-frozen.json /tmp/full-judged.json \
  --entry /tmp/baseline-frozen.json /tmp/baseline-judged.json \
  --entry /tmp/minus-frozen.json /tmp/minus-judged.json
```

Use a dash instead of a judged filename to retain an unjudged generation.
Comparison pairs each case/run against full, preserving individual criterion
outcomes, original activation/false-activation results, generation metrics and
judging metrics separately. Missing arms/criteria/usage are unavailable, never
success or zero. A failed judge remains a failed observation, including an exit
with empty stderr. Known differing resolved model sets make a pair incomparable
and suppress its quality delta. Missing resolution marks model comparability
unavailable even when requested IDs match. Each preregistered ceiling reports
its own measured verdict or unavailable status. These observations support
bounded sample comparisons, not an automatic effectiveness claim.

Outputs use exclusive creation and SHA-256 seals over their contents, source
reports, snapshots and grader sources. They reject accidental changes and
internally inconsistent provenance. **Checksums provide integrity, not
authenticity**: someone can forge a fully self-consistent report and recompute a
seal. Preserve trusted runner logs and access controls; a seal does not prove
which model ran, plugin loading, actual isolation, or that a claimed tool result
occurred. Existing unpinned reports cannot retroactively prove generation-time
criterion or snapshot equality.

## Focused offline regression command

```bash
bash tests/evaluations/test-basic.sh
```

The synthetic two-arm fixture, a fixture in the documented `claude plugin eval`
result shape and the regression tests exercise provenance, source
preservation, leakage exclusions, selected skill/agent removal, malformed input,
activation-only cases, frozen judge errors, model comparability, unavailable
telemetry and ceiling handling. They make no model calls. The director owns
full-suite, dependency installation, Java examples and live host checks.
