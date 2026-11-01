#!/usr/bin/env python3
"""Pure SQL lint and the actual JSON bridge; no build or database access."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'runtime'))
from sql_lint import lint_hook, lint_migrations, migration_name


class SqlTests(unittest.TestCase):
    def test_long_versions_normalize_without_integer_conversion(self):
        version = '9' * 5000
        name = 'V000' + version + '_000__long.sql'
        self.assertEqual(migration_name(name, ascii_digits=True), {'type': 'V', 'version': version})
        self.assertEqual(lint_hook(name, 'SELECT 1;'), [])
        records = [{'file': name, 'name': name, 'location': '.', 'content': 'SELECT 1;'},
                   {'file': 'duplicate.sql', 'name': 'V' + version + '__duplicate.sql', 'location': '.', 'content': 'SELECT 1;'}]
        response = self.bridge(records)
        self.assertEqual(response['migrations'][0]['version'], version)
        self.assertEqual([e['code'] for e in response['errors']], ['duplicate-version'])

    def bridge(self, records):
        proc = subprocess.run([sys.executable, str(ROOT / 'runtime/project_runtime.py')],
                              input=json.dumps({'action': 'lint-migrations', 'migrations': records}),
                              text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        return json.loads(proc.stdout)

    def test_bridge_corpus_matches_pure_lint(self):
        cases = json.loads((Path(__file__).parent / 'sql-corpus.json').read_text())
        records = [{'file': c['name'], 'name': c['name'], 'location': c['id'], 'content': c['sql']} for c in cases]
        response = self.bridge(records)
        self.assertEqual(response, lint_migrations(records))
        for case, migration in zip(cases, response['migrations']):
            with self.subTest(case=case['id']):
                self.assertEqual(migration['version'], case['mcp']['version'])
                self.assertEqual(migration['type'], case['mcp']['type'])

    def test_invalid_records_report_validation_error(self):
        with self.assertRaisesRegex(ValueError, 'migrations must be a list'):
            lint_migrations([{'name': 'V1__bad.sql'}])


if __name__ == '__main__':
    unittest.main()
