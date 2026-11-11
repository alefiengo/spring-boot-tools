#!/usr/bin/env python3
"""Offline ablation plan/prepare/compare. This tool never executes a model or build."""
import argparse
import json
import re
import shutil
from pathlib import Path

from frozen_judge import body, definitions, digest, metrics, seal, validate, verify, write_new

# Product-only snapshot: evals, documentation, tests and operator instructions are never shipped to generation.
PRODUCT = {'.claude-plugin', '.mcp.json', '.lsp.json', 'agents', 'skills', 'hooks', 'runtime', 'mcp-server', 'scripts'}
EXCLUDED = {'.git', '.claude', '.codex', '.agents', 'node_modules', '__pycache__', 'evals',
            'graders', 'reports', 'results'}
INSTRUCTIONS = {'agents.md', 'claude.md', 'gemini.md'}


def safe_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', value):
        raise ValueError('Unsafe case/component identifier')
    return value


def inventory(root, product=False):
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Snapshot root must be a directory')
    files = {}
    def walk(directory):
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root)
            if ((len(relative.parts) == 1 and path.name in EXCLUDED)
                    or (product and path.name in {'node_modules', '__pycache__', '.git'})
                    or path.name.lower() in INSTRUCTIONS or path.suffix == '.pyc'):
                continue
            if product and relative.parts[0] not in PRODUCT:
                continue
            if path.is_symlink():
                raise ValueError('Snapshot symlinks are forbidden: ' + str(relative))
            if path.is_dir():
                walk(path)
            elif path.is_file():
                import hashlib
                files[relative.as_posix()] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                             'executable': bool(path.stat().st_mode & 0o111)}
            else:
                raise ValueError('Snapshot contains special file')
    walk(root)
    return files


CEILING_METRICS = ('inputTokens', 'outputTokens', 'costUSD', 'durationMs')


def ceilings(value=None):
    import math
    if value is None:
        value = {stage: {name: None for name in CEILING_METRICS} for stage in ('generation', 'judging')}
    if not isinstance(value, dict) or set(value) != {'generation', 'judging'}:
        raise ValueError('Ceilings require generation and judging objects')
    for stage in value.values():
        if not isinstance(stage, dict) or set(stage) != set(CEILING_METRICS):
            raise ValueError('Specify every ceiling, using null when not preregistered')
        if any(v is not None and (type(v) not in (int, float) or not math.isfinite(v) or v < 0)
               for v in stage.values()):
            raise ValueError('Invalid metric ceiling')
    return value


def plan(repo, snapshot, cases, model, effort, canonical_judge, minus, runs=1, *,
         host_version, generation_timeout, max_turns, judge_timeout=300, metric_ceilings=None):
    repo, snapshot = repo.resolve(strict=True), snapshot.resolve(strict=True)
    if not all(isinstance(v, str) and v.strip() for v in (model, effort, canonical_judge)):
        raise ValueError('Model, effort and canonical judge must be explicitly pinned')
    if not isinstance(host_version, str) or not host_version.strip():
        raise ValueError('Pin the Claude Code host version')
    if any(type(v) is not int or v < 1 for v in (generation_timeout, max_turns, judge_timeout)):
        raise ValueError('Execution limits must be positive integers')
    if type(runs) is not int or runs < 1:
        raise ValueError('Runs must be positive')
    components = {p.parent.name: 'skills/' + p.parent.name for p in (repo / 'skills').glob('*/SKILL.md')}
    components.update({p.stem: 'agents/' + p.name for p in (repo / 'agents').glob('*.md')})
    if not minus or len(set(minus)) != len(minus) or any(m not in components for m in minus):
        raise ValueError('Choose distinct existing skills/agents for selected-minus')
    if not cases or len(set(cases)) != len(cases):
        raise ValueError('Cases must be nonempty and unique')
    contexts = {}
    for name in cases:
        safe_name(name)
        case_dir = repo / 'evals' / name
        if case_dir.is_symlink() or (case_dir / 'prompt.md').is_symlink() or (case_dir / 'graders').is_symlink():
            raise ValueError('Symlinked case input')
        contexts[name] = {'prompt': body(case_dir / 'prompt.md'), 'criterionDefinitions': definitions(case_dir)}
    project_files, plugin_files = inventory(snapshot), inventory(repo, product=True)
    if not project_files:
        raise ValueError('Application snapshot must contain explicit fixture files')
    # Do not silently use the plugin checkout as the application snapshot.
    if snapshot == repo or 'skills' in {Path(p).parts[0] for p in project_files}:
        raise ValueError('Use a separate application-only snapshot, not the plugin checkout')
    return seal({'schemaVersion': 1, 'kind': 'ablation-plan', 'host': 'claude-code', 'hostVersion': host_version,
                 'executionLimits': {'generation': {'timeoutSeconds': generation_timeout, 'maxTurns': max_turns},
                                     'judging': {'timeoutSeconds': judge_timeout}},
                 'metricCeilings': ceilings(metric_ceilings), 'modelRequested': model,
                 'effort': effort, 'canonicalJudge': canonical_judge, 'runs': runs,
                 'cases': contexts, 'variants': ['baseline', 'full', 'selected-minus'],
                 'minus': {m: components[m] for m in minus},
                 'projectSource': str(snapshot), 'pluginSource': str(repo),
                 'projectFiles': project_files, 'pluginFiles': plugin_files,
                 'projectSnapshotSha256': digest(project_files), 'pluginSnapshotSha256': digest(plugin_files),
                 'limitations': ['Prepared inputs are not live effectiveness evidence.',
                    'An external runner must enforce filesystem/tool isolation and record actual model/effort.',
                    'Host/plugin loading and ambient account settings require independent runtime verification.']})


def variant_files(plan_data, variant):
    if variant not in plan_data['variants']:
        raise ValueError('Unknown variant')
    if variant == 'baseline':
        return {}
    removed = plan_data['minus'].values() if variant == 'selected-minus' else []
    return {p: h for p, h in plan_data['pluginFiles'].items()
            if not any(p == r or p.startswith(r + '/') for r in removed)}


def preparation(plan_data, variant, case, run):
    verify(plan_data)
    if (case not in plan_data['cases'] or type(run) is not int
            or not 0 <= run < plan_data['runs']):
        raise ValueError('Unknown case/run')
    plugin_files = variant_files(plan_data, variant)
    return seal({'schemaVersion': 1, 'kind': 'ablation-preparation',
                 'planSha256': plan_data['inputSha256'], 'variant': variant, 'case': case, 'run': run,
                 'host': plan_data['host'], 'hostVersion': plan_data['hostVersion'],
                 'executionLimits': plan_data['executionLimits'], 'metricCeilings': plan_data['metricCeilings'],
                 'modelRequested': plan_data['modelRequested'], 'effort': plan_data['effort'],
                 'canonicalJudge': plan_data['canonicalJudge'], **plan_data['cases'][case],
                 'projectSnapshotSha256': plan_data['projectSnapshotSha256'],
                 'pluginSnapshotSha256': plan_data['pluginSnapshotSha256'],
                 'variantSnapshotSha256': digest(plugin_files)})


def copy_files(source, destination, files):
    import hashlib
    for relative, metadata in files.items():
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Unsafe snapshot path')
        origin = source / path
        # Recheck on copy as well as before it; never follow a newly introduced link.
        if any(p.is_symlink() for p in [origin, *origin.parents] if p != source.parent):
            raise ValueError('Snapshot symlink introduced during preparation')
        data = origin.read_bytes()
        if hashlib.sha256(data).hexdigest() != metadata['sha256']:
            raise ValueError('Snapshot changed during preparation')
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755 if metadata['executable'] else 0o644)


def prepare(plan_data, variant, case, run, destination, record_path):
    record = preparation(plan_data, variant, case, run)
    source, plugin = Path(plan_data['projectSource']), Path(plan_data['pluginSource'])
    destination = destination.absolute()
    record_path = record_path.absolute()
    if (any(record_path.resolve().is_relative_to(p.resolve()) for p in (destination, source, plugin))
            or record_path.exists()):
        raise ValueError('Use a fresh operator record path outside generation and both source trees')
    # Outside BOTH source trees; each invocation gets a fresh absent directory.
    if any(destination.resolve().is_relative_to(p.resolve()) for p in (source, plugin)):
        raise ValueError('Preparation must be outside snapshot/plugin source trees')
    if inventory(source) != plan_data['projectFiles'] or inventory(plugin, True) != plan_data['pluginFiles']:
        raise ValueError('Pinned snapshot changed; create a new plan')
    destination.mkdir(parents=False, exist_ok=False)
    try:
        workspace, product = destination / 'workspace', destination / 'plugin'
        workspace.mkdir()
        copy_files(source, workspace, plan_data['projectFiles'])
        files = variant_files(plan_data, variant)
        if files:
            product.mkdir()
            copy_files(plugin, product, files)
        # No prompt, grader, report or provenance file exists anywhere in the generation tree.
        write_new(record_path, record)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return record


def activation(bundle):
    output = []
    for item in bundle.get('activationResults', []):
        raw = item.get('result')
        # A two-arm run reports Skill/with-only graders with scored: false as
        # pass/fail indicators; keep that verdict instead of discarding it.
        indicator = isinstance(raw, dict) and raw.get('scored') is False and (
            item.get('indicatorOnly') is True or raw.get('withOnly') is True)
        usable = isinstance(raw, dict) and (raw.get('scored') is True or indicator)
        passed = raw.get('passed') if usable else None
        if passed is not None and type(passed) is not bool:
            raise ValueError('Malformed activation verdict')
        output.append({'id': item['id'], 'falseActivationCriterion': item['falseActivationCriterion'],
                       'indicatorOnly': bool(indicator),
                       'passed': passed, 'falseActivation': (not passed) if passed is not None
                       and item['falseActivationCriterion'] else None,
                       'status': 'measured' if passed is not None else 'unavailable'})
    return output


def compare(plan_data, entries):
    verify(plan_data)
    observed = {}
    ceilings(plan_data['metricCeilings'])
    for bundle, judged in entries:
        verify(bundle)
        provenance = bundle.get('provenance')
        if not isinstance(provenance, dict):
            raise ValueError('Unpinned legacy bundle cannot establish a paired comparison')
        expected = preparation(plan_data, bundle['variant'], bundle['case'], bundle['generation']['run'])
        if provenance != expected:
            raise ValueError('Bundle provenance differs from pinned plan')
        if (bundle['prompt'] != expected['prompt'] or bundle['criterionDefinitions'] != expected['criterionDefinitions']
                or bundle['criteria'] != [{'id': d['id'], 'criterion': d['criterion']}
                    for d in expected['criterionDefinitions'] if d['type'] == 'llm']
                or bundle['generation']['claudeVersion'] != expected['hostVersion']
                or bundle['generation']['executionLimits'] != expected['executionLimits']['generation']
                or any(bundle['generation'][k] != expected[k] for k in ('modelRequested', 'effort'))):
            raise ValueError('Bundle inputs differ from pinned preparation')
        key = (bundle['case'], bundle['generation']['run'], bundle['variant'])
        if key in observed:
            raise ValueError('Duplicate case/run/variant')
        quality, judge_metrics, judge_error = {}, metrics({}), None
        if judged is not None:
            verify(judged, 'reportSha256')
            if judged.get('case') != bundle['case'] or judged.get('canonicalModel') != plan_data['canonicalJudge']:
                raise ValueError('Canonical judge differs from plan')
            canonical = judged['results'][0]
            if canonical.get('inputSha256') != bundle['inputSha256'] or canonical.get('modelRequested') != plan_data['canonicalJudge']:
                raise ValueError('Judge input/model mismatch')
            if canonical.get('executionLimits') != expected['executionLimits']['judging']:
                raise ValueError('Judge execution limits differ from plan')
            judge_error = canonical.get('error')
            if 'error' in canonical or canonical.get('exitCode', 0) != 0:
                judge_error = judge_error or f"Judge exited with code {canonical.get('exitCode', 'unknown')}"
                if canonical.get('passed') is not False:
                    raise ValueError('Judge error cannot pass')
            else:
                passed = validate(bundle, canonical.get('judgments'))
                if canonical.get('passed') is not passed or judged.get('passed') is not passed:
                    raise ValueError('Inconsistent judge verdict')
                quality = {j['id']: j['passed'] for j in canonical['judgments']}
            judge_metrics = canonical.get('metrics', metrics({}))
            reported = metrics(canonical.get('raw', {}))
            if any(judge_metrics.get(k) != v for k, v in reported.items() if k != 'durationMs'):
                raise ValueError('Judge telemetry differs from raw record')
        ceiling_results = {}
        for stage, telemetry in (('generation', bundle['generation']['metrics']), ('judging', judge_metrics)):
            ceiling_results[stage] = {}
            for name, ceiling in plan_data['metricCeilings'][stage].items():
                measurement = telemetry[name]
                available = measurement['status'] == 'measured'
                ceiling_results[stage][name] = {'ceiling': ceiling,
                    'status': 'not-preregistered' if ceiling is None else 'measured' if available else 'unavailable',
                    'passed': measurement['value'] <= ceiling if ceiling is not None and available else None}
        observed[key] = {'ceilingOutcomes': ceiling_results, 'frozenSha256': bundle['inputSha256'], 'criteria': quality,
                         'activation': activation(bundle), 'generationMetrics': bundle['generation']['metrics'],
                         'judgingMetrics': judge_metrics, 'judgeError': judge_error}
    pairs = []
    for case, context in plan_data['cases'].items():
        for run in range(plan_data['runs']):
            for reference in ('baseline', 'selected-minus'):
                arms = {v: observed.get((case, run, v)) for v in (reference, 'full')}
                model_comparability = {}
                for stage, field in (('generation', 'generationMetrics'), ('judging', 'judgingMetrics')):
                    models = {v: a[field]['resolvedModels']['value'] if a else None for v, a in arms.items()}
                    known = all(value is not None for value in models.values())
                    model_comparability[stage] = {'resolvedModels': models,
                        'status': ('matched' if models[reference] == models['full'] else 'incomparable') if known else 'unavailable'}
                statuses = [value['status'] for value in model_comparability.values()]
                model_comparability['status'] = ('incomparable' if 'incomparable' in statuses else
                                                  'unavailable' if 'unavailable' in statuses else 'matched')
                incomparable = model_comparability['status'] == 'incomparable'
                outcomes = []
                for definition in context['criterionDefinitions']:
                    if definition['type'] != 'llm':
                        continue
                    values = {v: a['criteria'].get(definition['id']) if a else None for v, a in arms.items()}
                    available = all(type(v) is bool for v in values.values())
                    outcomes.append({'id': definition['id'], 'outcomes': values,
                        'status': 'incomparable' if incomparable else 'measured' if available else 'unavailable',
                        'fullMinusReference': int(values['full']) - int(values[reference]) if available and not incomparable else None})
                pairs.append({'case': case, 'run': run, 'reference': reference, 'arms': arms,
                              'modelComparability': model_comparability, 'pairedCriteria': outcomes})
    expected_count = len(plan_data['cases']) * plan_data['runs'] * len(plan_data['variants'])
    return seal({'schemaVersion': 1, 'kind': 'ablation-comparison', 'planSha256': plan_data['inputSha256'],
                 'observedGenerations': len(observed), 'expectedGenerations': expected_count,
                 'missingGenerations': expected_count - len(observed), 'pairs': pairs,
                 'effectivenessClaim': None,
                 'interpretation': 'Paired observed samples only; unavailable data is never zero or success. '
                    'Synthetic/offline fixtures establish harness behavior, not component effectiveness.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('plan')
    p.add_argument('output', type=Path)
    p.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    p.add_argument('--snapshot', type=Path, required=True)
    p.add_argument('--cases', nargs='+', required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--effort', required=True)
    p.add_argument('--canonical-judge', required=True)
    p.add_argument('--minus', nargs='+', required=True)
    p.add_argument('--runs', type=int, default=1)
    p.add_argument('--host-version', required=True)
    p.add_argument('--generation-timeout', type=int, required=True)
    p.add_argument('--max-turns', type=int, required=True)
    p.add_argument('--judge-timeout', type=int, default=300)
    p.add_argument('--ceilings', type=Path)
    p = sub.add_parser('prepare')
    p.add_argument('plan', type=Path)
    p.add_argument('destination', type=Path)
    p.add_argument('--variant', choices=['baseline', 'full', 'selected-minus'], required=True)
    p.add_argument('--case', required=True)
    p.add_argument('--record', type=Path, required=True)
    p.add_argument('--run', type=int, default=0)
    p = sub.add_parser('compare')
    p.add_argument('plan', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--entry', nargs=2, action='append', default=[], metavar=('FROZEN', 'JUDGED_OR_DASH'))
    args = parser.parse_args()
    if args.action == 'plan':
        result = plan(args.repo, args.snapshot, args.cases, args.model, args.effort,
                      args.canonical_judge, args.minus, args.runs, host_version=args.host_version,
                      generation_timeout=args.generation_timeout, max_turns=args.max_turns,
                      judge_timeout=args.judge_timeout,
                      metric_ceilings=json.loads(args.ceilings.read_text()) if args.ceilings else None)
    elif args.action == 'prepare':
        prepare(json.loads(args.plan.read_text()), args.variant, args.case, args.run, args.destination, args.record)
        return
    else:
        entries = [(json.loads(Path(b).read_text()), None if j == '-' else json.loads(Path(j).read_text()))
                   for b, j in args.entry]
        result = compare(json.loads(args.plan.read_text()), entries)
    write_new(args.output, result)


if __name__ == '__main__':
    main()
