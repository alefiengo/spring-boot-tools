import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('frozen_judge', ROOT / 'evals/tools/frozen_judge.py')
judge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(judge)


class FrozenJudgeTests(unittest.TestCase):
    def bundle(self):
        data = {'response': 'Real transaction rolls back the insert.',
                'criteria': [{'id': 'rollback', 'criterion': 'Real rollback required'}]}
        data['inputSha256'] = judge.digest(data)
        return data

    def test_fail_closed_and_literal_evidence(self):
        b = self.bundle()
        good = {'id': 'rollback', 'passed': True, 'reason': 'Database assertion',
                'evidence': 'transaction rolls back'}
        self.assertTrue(judge.validate(b, [good]))
        for bad in [[], [good, good], [{**good, 'id': 'other'}],
                    [{**good, 'passed': 'true'}], [{**good, 'reason': ''}],
                    [{**good, 'evidence': 'invented assertion'}], [{**good, 'evidence': ''}]]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                judge.validate(b, bad)
        self.assertFalse(judge.validate(b, [{**good, 'passed': False, 'evidence': ''}]))
        with self.assertRaises(ValueError):
            judge.validate({**b, 'criteria': []}, [])
        with self.assertRaises(ValueError):
            judge.validate({**b, 'criteria': b['criteria'] * 2}, [good])

    def test_models_receive_identical_frozen_input(self):
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs['input']))
            result = {'structured_output': {'judgments': [{'id': 'rollback', 'passed': False,
                       'reason': 'No persisted row assertion', 'evidenceLine': 0}]}}
            return type('Process', (), {'returncode': 0, 'stdout': json.dumps(result)})()
        with patch.object(judge.subprocess, 'run', side_effect=run):
            a = judge.judge(self.bundle(), 'model-a')
            b = judge.judge(self.bundle(), 'model-b')
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertEqual(a['judgePromptSha256'], b['judgePromptSha256'])
        self.assertFalse(a['passed'])

    def test_nonexistent_evidence_lines_fail_closed(self):
        raw = {'structured_output': {'judgments': [{'id': 'rollback', 'passed': True,
               'reason': 'Claims success', 'evidenceLine': 5}]}}
        proc = type('Process', (), {'returncode': 0, 'stdout': json.dumps(raw)})()
        with patch.object(judge.subprocess, 'run', return_value=proc):
            result = judge.judge(self.bundle(), 'model')
        self.assertFalse(result['passed'])
        self.assertIn('out of bounds', result['error'])

    def test_malformed_external_output_fails_closed(self):
        for raw in [[], {'structured_output': []}, {'structured_output': {'judgments': None}}]:
            proc = type('Process', (), {'returncode': 0, 'stdout': json.dumps(raw)})()
            with self.subTest(raw=raw), patch.object(judge.subprocess, 'run', return_value=proc):
                result = judge.judge(self.bundle(), 'model')
                self.assertFalse(result['passed'])
                self.assertIn('error', result)

    def test_timeout_is_failed_report(self):
        with patch.object(judge.subprocess, 'run', side_effect=judge.subprocess.TimeoutExpired('claude', 300)):
            result = judge.judge(self.bundle(), 'model')
        self.assertFalse(result['passed'])
        self.assertIn('error', result)

    def test_tampering_rejected_before_external_call(self):
        bundle = self.bundle()
        bundle['response'] = 'Changed response'
        with patch.object(judge.subprocess, 'run') as call, self.assertRaises(ValueError):
            judge.judge(bundle, 'model')
        call.assert_not_called()

    def test_changed_prompt_cannot_rejudge_old_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory) / 'case'
            (case / 'graders').mkdir(parents=True)
            (case / 'prompt.md').write_text('New prompt')
            (case / 'graders/a.md').write_text('---\ntype: llm\n---\ncriterion')
            report = {'cases': [{'name': 'case', 'promptMarkdown': 'Old prompt',
                'arms': {'with': [{'graders': [{'scored': True, 'evidence': 'Answer'}]}]}}]}
            with self.assertRaisesRegex(ValueError, 'Prompt changed'):
                judge.freeze(report, 'case', case)


if __name__ == '__main__':
    unittest.main()
