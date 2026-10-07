#!/usr/bin/env python3
"""Shared runtime regression tests: real OS processes, isolated fake wrappers."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / 'runtime/project_runtime.py'
sys.path.insert(0, str(RUNTIME.parent))
from project_runtime import discover, build_plan, run_build


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory()
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name)
        (self.root / 'target').mkdir()
        (self.root / 'pom.xml').write_text('<project/>')
        (self.root / 'src/main/java').mkdir(parents=True)
        self.source = self.root / 'src/main/java/App.java'
        self.source.write_text('class App {}')
        self.wrapper = self.root / 'mvnw'
        self.wrapper.write_text('#!/bin/sh\necho "$*" >> target/calls\nexit 0\n')
        self.wrapper.chmod(0o755)

    def request(self, **extra):
        return {'action': 'run', 'projectRoot': str(self.root), 'task': 'verify', **extra}

    def invoke(self, **extra):
        proc = subprocess.run([sys.executable, str(RUNTIME)], input=json.dumps(self.request(**extra)), capture_output=True, text=True)
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        return json.loads(proc.stdout)

    def launch(self, **extra):
        proc = subprocess.Popen([sys.executable, str(RUNTIME)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        proc.stdin.write(json.dumps(self.request(**extra)))
        proc.stdin.close()
        proc.stdin = None
        return proc

    def calls(self):
        return (self.root / 'target/calls').read_text().splitlines()

    def wait_for_calls(self):
        deadline = time.monotonic() + 5
        while not (self.root / 'target/calls').exists():
            if time.monotonic() >= deadline:
                self.fail('wrapper did not start')
            time.sleep(.02)

    def test_discovery_and_module_plan(self):
        child = self.root / 'services/api'
        child.mkdir(parents=True)
        (child / 'pom.xml').write_text('<project/>')
        (self.root / 'pom.xml').write_text('<project xmlns="http://maven.apache.org/POM/4.0.0"><modules><module>services/api</module></modules></project>')
        info = discover(child / 'src/main/java/App.java')
        self.assertEqual(info['root'], str(self.root))
        self.assertEqual(info['modules'][0]['path'], 'services/api')
        plan = build_plan(self.request(task='test', module='services/api', profiles=['integration'], testClass='app.Test#example'))
        self.assertIn('-Pintegration', plan['command'])
        self.assertEqual(plan['command'][-4:], ['-pl', 'services/api', '-am', '-Dtest=app.Test#example'])
        for bad in ({'tasks':['verify; touch bad']}, {'profiles':['-Dexec=bad']}, {'module':'../outside'}, {'testClass':'X; echo bad','task':'test'}):
            with self.assertRaises(ValueError):
                build_plan(self.request(**bad))
        self.wrapper.chmod(0o644)
        self.assertEqual(build_plan(self.request())['command'][:2], ['sh', str(self.wrapper)])

    def test_gradle_settings_without_root_build(self):
        (self.root / 'pom.xml').unlink()
        (self.root / 'settings.gradle.kts').write_text('include(\n  ":api",\n  ":services:users"\n)')
        (self.root / 'api').mkdir()
        (self.root / 'api/build.gradle.kts').write_text('')
        (self.root / 'gradlew').write_text('#!/bin/sh\nexit 0\n')
        info = discover(self.root)
        self.assertEqual(info['buildTool'], 'gradle')
        self.assertIsNone(info['buildFile'])
        self.assertEqual([m['path'] for m in info['modules']], ['api', 'services/users'])
        plan = build_plan(self.request(task='verify', module='services/users'))
        self.assertEqual(plan['command'][-1], ':services:users:check')
        self.assertEqual(discover(self.root / 'api')['root'], str(self.root))

    def test_reuse_and_changes_outside_hooks(self):
        self.assertFalse(self.invoke()['reused'])
        self.assertTrue(self.invoke()['reused'])
        for path, text in ((self.source, 'class App { int x; }'), (self.root / 'application.yml', 'server.port: 8082'), (self.root / '.mvn/maven.config', '-Pchecks')):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            self.assertFalse(self.invoke()['reused'])
            self.assertTrue(self.invoke()['reused'])
        (self.root / 'README.md').write_text('documentation only')
        self.assertTrue(self.invoke()['reused'])
        self.source.unlink()
        self.assertFalse(self.invoke()['reused'])
        self.assertEqual(len(self.calls()), 5)

    def test_arbitrary_build_inputs_remain_inputs(self):
        for name in ('compile-mode', 'config.txt', 'data.csv', 'docs/config.json', 'docs/data.yml', 'assets/build/source.txt', 'assets/target/source.csv'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('first value')
            self.assertFalse(self.invoke()['reused'])
            self.assertTrue(self.invoke()['reused'])
            path.write_text('changed value')
            self.assertFalse(self.invoke()['reused'])
        self.assertEqual(len(self.calls()), 14)

    def test_source_packages_named_like_outputs_remain_inputs(self):
        for name in ('build', 'target', 'docs', 'node_modules'):
            source = self.source.parent / 'example' / name / 'Other.java'
            source.parent.mkdir(parents=True)
            source.write_text('class Other {}')
            self.assertFalse(self.invoke()['reused'])
            self.assertTrue(self.invoke()['reused'])
            source.write_text('invalid Java syntax')
            self.assertFalse(self.invoke()['reused'])
        output = self.root / 'target/generated.java'
        output.parent.mkdir(exist_ok=True)
        output.write_text('generated output')
        self.assertTrue(self.invoke()['reused'])
        self.assertEqual(len(self.calls()), 8)

    def test_compile_test_never_verify(self):
        self.assertTrue(self.invoke(task='compile')['ok'])
        self.assertFalse(self.invoke()['reused'])
        self.assertTrue(self.invoke(task='test')['ok'])
        self.assertFalse(self.invoke()['reused'])
        self.assertEqual(len(self.calls()), 4)

    def test_exact_scope_only(self):
        self.assertFalse(self.invoke(profiles=['integration'])['reused'])
        self.assertTrue(self.invoke(profiles=['integration'])['reused'])
        self.assertFalse(self.invoke()['reused'])
        self.assertFalse(self.invoke(reuse=False)['reused'])
        self.assertEqual(len(self.calls()), 3)

    def test_failed_attempt_invalidates_prior_success(self):
        self.wrapper.write_text('#!/bin/sh\necho "$*" >> target/calls\ncase "$*" in *compile*) exit 7;; esac\nexit 0\n')
        self.assertTrue(self.invoke()['ok'])
        failed = self.invoke(task='compile')
        self.assertFalse(failed['ok'])
        self.assertEqual(failed['exitCode'], 7)
        self.assertFalse(self.invoke()['reused'])
        self.assertEqual(len(self.calls()), 3)

    def test_cross_process_lock_deduplicates(self):
        self.wrapper.write_text('#!/bin/sh\necho "$*" >> target/calls\nsleep .3\nexit 0\n')
        first, second = self.launch(), self.launch()
        a = json.loads(first.communicate(timeout=10)[0])
        b = json.loads(second.communicate(timeout=10)[0])
        self.assertTrue(a['ok'] and b['ok'])
        self.assertEqual(sorted([a['reused'], b['reused']]), [False, True])
        self.assertEqual(len(self.calls()), 1)

    def test_inputs_changed_during_build_no_stamp(self):
        self.wrapper.write_text('#!/bin/sh\necho "$*" >> target/calls\nsleep .3\nexit 0\n')
        proc = self.launch()
        self.wait_for_calls()
        self.source.write_text('class App { int changed; }')
        result = json.loads(proc.communicate(timeout=10)[0])
        self.assertTrue(result['inputsChanged'])
        self.assertFalse(self.invoke()['reused'])

    def test_cancellation_kills_group_and_invalidates_stamp(self):
        self.wrapper.write_text('#!/bin/sh\necho "$*" >> target/calls\ncase "$*" in *compile*) (sleep 1; echo survivor > orphan) & wait;; esac\nexit 0\n')
        self.assertTrue(self.invoke()['ok'])
        (self.root / 'target/calls').unlink()
        proc = self.launch(task='compile')
        self.wait_for_calls()
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate(timeout=5)
        time.sleep(1.1)
        self.assertFalse((self.root / 'orphan').exists())
        self.assertFalse(self.invoke()['reused'])

    def test_timeout_and_output_limit(self):
        self.wrapper.write_text('#!/bin/sh\necho started\n(sleep 1; echo survivor > orphan) & wait\n')
        result = self.invoke(timeoutMs=100)
        self.assertTrue(result['timedOut'])
        self.assertFalse(result['ok'])
        time.sleep(1.1)
        self.assertFalse((self.root / 'orphan').exists())
        self.wrapper.write_text('#!/bin/sh\necho too-much-output\nsleep 1\n')
        result = self.invoke(maxOutputBytes=4)
        self.assertTrue(result['outputLimitExceeded'])
        self.assertEqual(result['stdout'], 'too-')
        self.assertFalse(result['ok'])

    def test_environment_and_executable_changes_invalidate(self):
        self.assertTrue(self.invoke()['ok'])
        self.wrapper.chmod(0o644)
        self.assertFalse(self.invoke()['reused'])
        env = dict(os.environ, CUSTOM_BUILD_MODE='changed')
        result = subprocess.run([sys.executable, str(RUNTIME)], input=json.dumps(self.request()), env=env, capture_output=True, text=True)
        self.assertFalse(json.loads(result.stdout)['reused'])

    def test_symlink_directory_and_oversized_inputs_never_stamp(self):
        external = self.root / 'external'
        external.mkdir()
        (external / 'Other.java').write_text('class Other {}')
        (self.source.parent / 'linked').symlink_to(external, target_is_directory=True)
        result = self.invoke()
        self.assertTrue(result['ok'])
        self.assertFalse(result['cacheable'])
        self.assertIn('symlinked build directory', result['cacheDiagnostic'])
        self.assertFalse(self.invoke()['reused'])
        (self.source.parent / 'linked').unlink()
        huge = self.source.parent / 'Huge.java'
        with huge.open('wb') as output:
            output.truncate(128 * 1024 * 1024 + 1)
        result = self.invoke()
        self.assertTrue(result['ok'])
        self.assertFalse(result['cacheable'])
        self.assertIn('safety limit', result['cacheDiagnostic'])
        self.assertFalse(self.invoke()['reused'])

    def test_symlink_inputs_fail_safe(self):
        self.source.unlink()
        outside = self.root / 'outside.txt'
        outside.write_text('class External {}')
        self.source.symlink_to(outside)
        result = self.invoke()
        self.assertTrue(result['ok'])
        self.assertFalse(result['cacheable'])
        self.assertIn('symlinked build input', result['cacheDiagnostic'])
        self.assertFalse(self.invoke()['reused'])
        self.assertEqual(len(self.calls()), 2)


if __name__ == '__main__':
    unittest.main()
