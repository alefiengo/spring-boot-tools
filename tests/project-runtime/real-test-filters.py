"""Opt-in integration tests. Run with Maven 3.10.0 and Gradle 9.8.1 on PATH."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'runtime'))
from project_runtime import run_build, state_dir


class RealTestFilters(unittest.TestCase):
    def fixture(self, tool):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        shutil.copytree(ROOT / 'tests/build-fixtures' / tool, root, dirs_exist_ok=True)
        self.addCleanup(shutil.rmtree, state_dir(root))
        return root

    def filtered(self, root, test_class, **scope):
        return run_build(dict(projectRoot=str(root), task='test', testClass=test_class,
                              collectTestReports=True, **scope), own_process_group=True)

    def assert_profile_test(self, result):
        self.assertTrue(result['ok'], result['stdout'] + result['stderr'])
        cases = [case.get('classname') for report in result['testReports']
                 for case in ET.fromstring(report['content']).iter('testcase')]
        self.assertEqual(cases, ['fixture.ProfileTest'])

    def test_maven_reactor(self):
        root = self.fixture('maven')
        # Exercise normal Surefire defaults, including core in extra's -am build.
        pom = root / 'pom.xml'
        pom.write_text(pom.read_text().replace('<failIfNoSpecifiedTests>false</failIfNoSpecifiedTests>', ''))
        extra = root / 'extra/pom.xml'
        extra.write_text(extra.read_text().replace('<dependencies>', '''<dependencies>
          <dependency><groupId>fixture</groupId><artifactId>core</artifactId><version>1.0.0</version></dependency>'''))
        for scope in ({}, {'module': 'extra'}):
            with self.subTest(scope=scope):
                self.assert_profile_test(self.filtered(root, 'fixture.ProfileTest', profiles=['extra'], **scope))
        result = self.filtered(root, 'fixture.Missing', profiles=['extra'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['exitCode'], 0, result['stdout'] + result['stderr'])
        self.assertIn('No tests matched testClass fixture.Missing', result['diagnostic'])

    def test_gradle_subproject(self):
        root = self.fixture('gradle')
        self.assert_profile_test(self.filtered(root, 'fixture.ProfileTest', module='extra'))
        with self.assertRaisesRegex(ValueError, 'specify the module'):
            self.filtered(root, 'fixture.ProfileTest')


if __name__ == '__main__':
    unittest.main()
