"""Keep evidence references aligned with the shipped catalog, without scoring usefulness."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ComponentInventoryTests(unittest.TestCase):
    def test_catalog_and_evidence_references_resolve(self):
        data = json.loads((ROOT / 'docs/component-evidence.json').read_text())
        components = data['components']
        identities = [(entry['kind'], entry['id']) for entry in components]
        self.assertEqual(len(identities), len(set(identities)))
        for kind, actual in (
            ('skill', {p.parent.name for p in (ROOT / 'skills').glob('*/SKILL.md')}),
            ('agent', {p.stem for p in (ROOT / 'agents').glob('*.md')}),
            ('mcp-tool', set(re.findall(r'register\("([a-z_]+)"', (ROOT / 'mcp-server/index.js').read_text()))),
        ):
            self.assertEqual({entry['id'] for entry in components if entry['kind'] == kind}, actual)
        hooks = json.loads((ROOT / 'hooks/hooks.json').read_text())['hooks']
        hook_names = {re.search(r'/([\w-]+)\.sh', hook['command']).group(1)
                      for entries in hooks.values() for entry in entries for hook in entry['hooks']}
        self.assertEqual({entry['id'] for entry in components if entry['kind'] == 'hook'}, hook_names)
        cases = {p.parent.name for p in (ROOT / 'evals').glob('*/prompt.md')}
        self.assertEqual(set(data['originalCases'] + data['newUnrunCases']), cases)
        for entry in components:
            for relative in [entry['path'], *entry['deterministicEvidencePaths']]:
                path = (ROOT / relative).resolve()
                self.assertTrue(path.is_relative_to(ROOT), relative)
                self.assertTrue(path.exists(), relative)
            for names in entry['behavioralCases'].values():
                self.assertTrue(set(names) <= cases, entry['id'])


if __name__ == '__main__':
    unittest.main()
