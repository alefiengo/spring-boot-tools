"""The freezer reads the claude plugin eval result shape, not only the legacy synthetic one."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'evals/tools'))
import ablation as h
import frozen_judge as j


class PluginEvalReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.case = Path(temp.name) / 'documented'
        (self.case / 'graders').mkdir(parents=True)
        (self.case / 'prompt.md').write_text('---\nmax_turns: 10\n---\n\nExplain the requested behavior.\n')
        (self.case / 'graders/quality.md').write_text('---\ntype: llm\n---\n\nRequired behavior is present.\n')
        (self.case / 'graders/skill-fired.md').write_text(
            "---\ntype: tool_used\ntool: Skill\ninput_match: '\"skill\"'\n---\n")
        self.report = json.loads((ROOT / 'tests/evaluations/fixtures/plugin-eval-report.json').read_text())

    def test_response_metrics_and_limits_come_from_documented_fields(self):
        bundle = j.freeze(self.report, 'documented', self.case)
        self.assertEqual(bundle['response'], 'Required behavior is present.')
        generation = bundle['generation']
        self.assertEqual(generation['claudeVersion'], '2.1.322')
        self.assertEqual(generation['executionLimits'], {'timeoutSeconds': 300, 'maxTurns': 10})
        self.assertEqual(generation['metrics']['costUSD'], {'status': 'measured', 'value': 0.12})
        self.assertEqual(generation['metrics']['durationMs']['value'], 42000)
        self.assertEqual(generation['metrics']['inputTokens']['status'], 'unavailable')
        # The evaluator records neither effort nor a requested-model override per run.
        self.assertIsNone(generation['effort'])
        self.assertIsNone(generation['modelRequested'])

    def test_two_arm_skill_indicator_is_retained(self):
        bundle = j.freeze(self.report, 'documented', self.case)
        [item] = bundle['activationResults']
        self.assertTrue(item['indicatorOnly'])
        [activation] = h.activation(bundle)
        self.assertEqual((activation['passed'], activation['status']), (True, 'measured'))
        self.assertTrue(activation['indicatorOnly'])

    def test_unscored_grader_without_indicator_rule_stays_unavailable(self):
        (self.case / 'graders/skill-fired.md').write_text(
            "---\ntype: tool_used\ntool: Skill\narm: both\n---\n")
        report = copy.deepcopy(self.report)
        report['cases'][0]['arms']['with'][0]['graders'][1]['withOnly'] = False
        [activation] = h.activation(j.freeze(report, 'documented', self.case))
        self.assertEqual(activation['status'], 'unavailable')

    def test_missing_prompt_fails_closed(self):
        report = copy.deepcopy(self.report)
        del report['cases'][0]['promptMarkdown']
        with self.assertRaisesRegex(ValueError, 'promptMarkdown'):
            j.freeze(report, 'documented', self.case)

    def test_invalid_duration_seconds_fails_closed(self):
        with self.assertRaises(ValueError):
            j.metrics({'durationSeconds': -1})


if __name__ == '__main__':
    unittest.main()
