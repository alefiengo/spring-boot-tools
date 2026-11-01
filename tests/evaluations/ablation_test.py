"""Offline provenance and leakage regressions; synthetic evidence never proves usefulness."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'evals/tools'))
import ablation as h
import frozen_judge as j


class AblationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo, self.snapshot = self.root / 'repo', self.root / 'snapshot'
        self.case = self.repo / 'evals/paired'
        for relative, content in {
            'skills/one/SKILL.md': 'skill one', 'skills/one/references/detail.md': 'detail',
            'skills/two/SKILL.md': 'skill two', 'agents/reviewer.md': 'reviewer',
            '.claude-plugin/plugin.json': '{}', '.codex-plugin/plugin.json': 'not Claude',
            'codex/launcher.py': 'not Claude', 'scripts/install-doctor.py': 'production doctor', 'AGENTS.md': 'operator instructions',
            'evals/paired/prompt.md': 'Explain the requested behavior.',
            'evals/paired/graders/quality.md': '---\ntype: llm\n---\nRequired behavior is present.',
            'evals/paired/graders/no-activation.md': '---\ntype: tool_used\ntool: Skill\nmin: 0\nmax: 0\n---\n',
            'evals/results/leak.json': 'previous answer', 'tests/secret.md': 'test answer',
        }.items():
            self.put(self.repo / relative, content)
        for relative in ('pom.xml', 'src/main/java/com/example/build/Task.java',
                         'src/main/java/com/example/target/Target.java',
                         'src/main/java/com/example/reports/Report.java',
                         'src/main/java/com/example/results/Result.java', 'build/pom.xml',
                         'build/src/main/java/App.java', 'AGENTS.md', 'graders/secret.md',
                         'results/sibling.json', 'reports/result.json'):
            self.put(self.snapshot / relative, '<project/>')
        self.raw = json.loads((ROOT / 'tests/evaluations/fixtures/two-arm-report.json').read_text())
        self.plan = h.plan(self.repo, self.snapshot, ['paired'], 'fixture-generator', 'high',
                           'fixture-judge', ['one', 'reviewer'], host_version='fixture-host-1',
                           generation_timeout=60, max_turns=4)

    def put(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def bundle(self, variant='full', arm='with'):
        prep = h.preparation(self.plan, variant, 'paired', 0)
        return j.freeze(self.raw, 'paired', self.case, arm=arm, variant=variant, provenance=prep)

    def judged(self, bundle, passed=True, proc=None):
        raw = {'structured_output': {'judgments': [{'id': 'quality', 'passed': passed,
               'reason': 'Synthetic fixture verdict', 'evidenceLine': 1 if passed else 0}]}}
        proc = proc or type('Process', (), {'returncode': 0, 'stdout': json.dumps(raw), 'stderr': ''})()
        with patch.object(j.subprocess, 'run', return_value=proc):
            record = j.judge(bundle, 'fixture-judge')
        return j.seal({'case': 'paired', 'canonicalModel': 'fixture-judge',
                       'results': [record], 'passed': record['passed']}, 'reportSha256')

    def test_prepare_preserves_sources_and_declared_build_module_excludes_evidence(self):
        for variant in self.plan['variants']:
            destination = self.root / variant
            record = h.prepare(self.plan, variant, 'paired', 0, destination, self.root / (variant + '.json'))
            self.assertEqual(h.inventory(destination / 'workspace'), self.plan['projectFiles'])
            self.assertTrue((destination / 'workspace/build/pom.xml').exists())
            self.assertTrue((destination / 'workspace/src/main/java/com/example/build/Task.java').exists())
            self.assertTrue((destination / 'workspace/src/main/java/com/example/target/Target.java').exists())
            self.assertEqual(record['planSha256'], self.plan['inputSha256'])
            copied = [p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()]
            self.assertFalse(any(Path(p).name == 'AGENTS.md' or any(part in {'graders', 'results', 'reports'}
                                 for part in Path(p).parts[1:2]) for p in copied))
            self.assertTrue((destination / 'workspace/src/main/java/com/example/reports/Report.java').exists())
            self.assertTrue((destination / 'workspace/src/main/java/com/example/results/Result.java').exists())
            self.assertFalse(any('preparation.json' in p or 'prompt.txt' in p or 'codex' in p for p in copied))
            if variant == 'baseline':
                self.assertFalse((destination / 'plugin').exists())
            else:
                self.assertEqual((destination / 'plugin/skills/one').exists(), variant == 'full')
                self.assertEqual((destination / 'plugin/agents/reviewer.md').exists(), variant == 'full')
                self.assertTrue((destination / 'plugin/skills/two/SKILL.md').exists())
                self.assertTrue((destination / 'plugin/scripts/install-doctor.py').exists())

    def test_prepare_rejects_changed_snapshot_reused_directory_and_internal_records(self):
        destination = self.root / 'generation'
        h.prepare(self.plan, 'full', 'paired', 0, destination, self.root / 'record.json')
        with self.assertRaises(FileExistsError):
            h.prepare(self.plan, 'full', 'paired', 0, destination, self.root / 'other.json')
        with self.assertRaises(ValueError):
            h.prepare(self.plan, 'full', 'paired', 0, self.root / 'new', self.root / 'new/record.json')
        self.put(self.snapshot / 'pom.xml', 'changed')
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            h.prepare(self.plan, 'full', 'paired', 0, self.root / 'new', self.root / 'fresh.json')

    def test_operator_record_cannot_mutate_either_pinned_source(self):
        for source in (self.repo, self.snapshot):
            with self.subTest(source=source), self.assertRaises(ValueError):
                h.prepare(self.plan, 'full', 'paired', 0, self.root / 'generation', source / 'operator.json')
            self.assertFalse((source / 'operator.json').exists())
        self.assertFalse((self.root / 'generation').exists())

    def test_known_different_resolved_models_are_incomparable_missing_is_unavailable(self):
        full, baseline = self.bundle(), self.bundle('baseline', 'without')
        # Requested aliases match; only actual reported generation telemetry differs.
        baseline['generation']['metrics']['resolvedModels'] = {'status': 'measured', 'value': ['different-resolved']}
        j.seal(baseline)
        comparison = h.compare(self.plan, [(full, self.judged(full)), (baseline, self.judged(baseline, False))])
        pair = comparison['pairs'][0]
        self.assertEqual(pair['modelComparability']['generation']['status'], 'incomparable')
        self.assertEqual(pair['pairedCriteria'][0]['status'], 'incomparable')
        self.assertIsNone(pair['pairedCriteria'][0]['fullMinusReference'])
        baseline['generation']['metrics']['resolvedModels'] = {'status': 'unavailable', 'value': None}
        j.seal(baseline)
        comparison = h.compare(self.plan, [(full, None), (baseline, None)])
        self.assertEqual(comparison['pairs'][0]['modelComparability']['status'], 'unavailable')

    def test_symlinked_inputs_fail_closed(self):
        (self.snapshot / 'linked.java').symlink_to(self.repo / 'AGENTS.md')
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            h.inventory(self.snapshot)

    def test_arm_variant_and_activation_are_retained_separately(self):
        full, baseline = self.bundle(), self.bundle('baseline', 'without')
        self.assertNotEqual(full['response'], baseline['response'])
        self.assertEqual(full['generation']['arm'], 'with')
        self.assertEqual(baseline['generation']['arm'], 'without')
        self.assertEqual(len(full['criteria']), 1)
        self.assertTrue(h.activation(full)[0]['falseActivation'])
        self.assertFalse(h.activation(baseline)[0]['falseActivation'])
        self.assertEqual(full['originalGraderResults'], self.raw['cases'][0]['arms']['with'][0]['graders'])
        # Legacy positional defaults still freeze with/0.
        self.assertEqual(j.freeze(self.raw, 'paired', self.case)['response'], full['response'])

    def test_activation_only_case_can_freeze_without_inventing_quality(self):
        (self.case / 'graders/quality.md').unlink()
        bundle = j.freeze(self.raw, 'paired', self.case, arm='without')
        self.assertEqual(bundle['criteria'], [])
        with patch.object(j.subprocess, 'run') as call, self.assertRaises(ValueError):
            j.judge(bundle, 'fixture-judge')
        call.assert_not_called()

    def test_invalid_run_arm_generation_and_criteria_fail_closed(self):
        for args in ({'run': -1}, {'arm': 'missing'}, {'run': 1}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                j.freeze(self.raw, 'paired', self.case, **args)
        self.raw['cases'][0]['arms']['with'][0]['error'] = 'generation failed'
        with self.assertRaises(ValueError):
            self.bundle()

    def test_provenance_model_effort_host_and_limits_mismatches_rejected(self):
        for field, value in (('modelRequested', 'different'), ('effort', 'low'),
                             ('claudeVersion', 'different'), ('executionLimits', {'timeoutSeconds': 1})):
            raw = copy.deepcopy(self.raw)
            raw[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                j.freeze(raw, 'paired', self.case, variant='full',
                         provenance=h.preparation(self.plan, 'full', 'paired', 0))
        prep = h.preparation(self.plan, 'baseline', 'paired', 0)
        with self.assertRaises(ValueError):
            j.freeze(self.raw, 'paired', self.case, variant='full', provenance=prep)

    def test_plan_and_criteria_tampering_rejected(self):
        changed = copy.deepcopy(self.plan)
        changed['effort'] = 'low'
        with self.assertRaises(ValueError):
            h.compare(changed, [])
        self.put(self.case / 'graders/quality.md', '---\ntype: llm\n---\nChanged requirement.')
        with self.assertRaisesRegex(ValueError, 'criteria mismatch'):
            self.bundle()

    def test_paired_criteria_missing_data_and_separate_metrics(self):
        full, baseline, minus = self.bundle(), self.bundle('baseline', 'without'), self.bundle('selected-minus', 'minus')
        comparison = h.compare(self.plan, [(full, self.judged(full)), (baseline, self.judged(baseline, False)),
                                           (minus, self.judged(minus, False))])
        self.assertEqual(comparison['missingGenerations'], 0)
        self.assertIsNone(comparison['effectivenessClaim'])
        for pair in comparison['pairs']:
            self.assertEqual(pair['pairedCriteria'][0]['fullMinusReference'], 1)
            arm = pair['arms']['full']
            self.assertEqual(arm['generationMetrics']['inputTokens']['value'], 20)
            self.assertEqual(arm['generationMetrics']['durationMs']['value'], 12)
            self.assertEqual(arm['judgingMetrics']['inputTokens']['status'], 'unavailable')
            self.assertEqual(arm['judgingMetrics']['durationMs']['source'], 'runner_wall')
            self.assertEqual(arm['generationMetrics']['resolvedModels']['value'], ['fixture-generator-resolved'])
        missing = h.compare(self.plan, [])
        self.assertEqual(missing['missingGenerations'], 3)
        self.assertEqual(missing['pairs'][0]['pairedCriteria'][0]['status'], 'unavailable')
        self.assertIsNone(missing['pairs'][0]['pairedCriteria'][0]['fullMinusReference'])

    def test_nonzero_empty_stderr_retained_as_judge_failure_without_fabricated_usage(self):
        bundle = self.bundle()
        proc = type('Process', (), {'returncode': 9, 'stdout': '', 'stderr': ''})()
        judged = self.judged(bundle, proc=proc)
        self.assertEqual(judged['results'][0]['error'], 'Judge exited with code 9')
        comparison = h.compare(self.plan, [(bundle, judged)])
        full = comparison['pairs'][0]['arms']['full']
        self.assertEqual(full['judgeError'], 'Judge exited with code 9')
        self.assertEqual(full['criteria'], {})
        for name in ('inputTokens', 'outputTokens', 'costUSD', 'resolvedModels'):
            self.assertEqual(full['judgingMetrics'][name]['status'], 'unavailable')
        # Retain old empty-error failed reports too; explicit failure must not become a verdict.
        judged['results'][0]['error'] = ''
        j.seal(judged, 'reportSha256')
        self.assertIn('code 9', h.compare(self.plan, [(bundle, judged)])['pairs'][0]['arms']['full']['judgeError'])

    def test_duplicate_legacy_wrong_judge_and_resealed_inconsistent_variants_rejected(self):
        bundle = self.bundle()
        with self.assertRaises(ValueError):
            h.compare(self.plan, [(bundle, None), (bundle, None)])
        legacy = copy.deepcopy(bundle)
        del legacy['provenance']
        j.seal(legacy)
        with self.assertRaises(ValueError):
            h.compare(self.plan, [(legacy, None)])
        inconsistent = copy.deepcopy(bundle)
        inconsistent['variant'] = 'baseline'
        j.seal(inconsistent)
        with self.assertRaises(ValueError):
            h.compare(self.plan, [(inconsistent, None)])
        judged = self.judged(bundle)
        judged['canonicalModel'] = 'other'
        j.seal(judged, 'reportSha256')
        with self.assertRaises(ValueError):
            h.compare(self.plan, [(bundle, judged)])

    def test_telemetry_and_nullable_ceilings_are_not_estimates(self):
        empty = j.metrics({})
        self.assertEqual(empty['resolvedModels']['status'], 'unavailable')
        self.assertEqual(empty['costUSD']['value'], None)
        for raw in ({'duration_ms': -1}, {'usage': {'input_tokens': True}}, {'costUSD': float('inf')}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                j.metrics(raw)
        self.assertIsNone(self.plan['metricCeilings']['generation']['costUSD'])
        with self.assertRaises(ValueError):
            h.ceilings({'generation': {}})
        self.plan['metricCeilings']['generation']['durationMs'] = 10
        self.plan['metricCeilings']['generation']['costUSD'] = 1
        j.seal(self.plan)
        bundle = self.bundle()
        full = h.compare(self.plan, [(bundle, None)])['pairs'][0]['arms']['full']
        self.assertFalse(full['ceilingOutcomes']['generation']['durationMs']['passed'])
        self.assertEqual(full['ceilingOutcomes']['generation']['costUSD']['status'], 'unavailable')

    def test_offline_comparison_cli_never_needs_claude(self):
        plan_path, output = self.root / 'plan.json', self.root / 'comparison.json'
        j.write_new(plan_path, self.plan)
        proc = subprocess.run([sys.executable, str(ROOT / 'evals/tools/ablation.py'), 'compare',
                               str(plan_path), str(output)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(output.read_text())['missingGenerations'], 3)
        again = subprocess.run([sys.executable, str(ROOT / 'evals/tools/ablation.py'), 'compare',
                                str(plan_path), str(output)], capture_output=True, text=True)
        self.assertNotEqual(again.returncode, 0)


if __name__ == '__main__':
    unittest.main()
