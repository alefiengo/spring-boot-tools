#!/usr/bin/env python3
"""Shared runtime regression tests: real OS processes, isolated fake wrappers."""
import json
import hashlib
import os
from pathlib import Path
import pwd
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / 'runtime/project_runtime.py'
sys.path.insert(0, str(RUNTIME.parent))
import project_runtime
from project_runtime import discover, build_plan, run_build, state_dir, test_report_snapshot


def state_path(root):
    """Expected location: per-user cache from the password database, never TMPDIR/HOME."""
    key = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / '.cache/spring-boot-tools' / f'build-{key}'


class RuntimeTests(unittest.TestCase):
    def test_legacy_mark_discovers_without_writing_state(self):
        folder = state_path(self.root)
        self.assertFalse(folder.exists())
        result = self.invoke(action='mark')
        self.assertEqual(result['root'], str(self.root))
        self.assertIn('deprecated', result['diagnostic'])
        self.assertFalse(folder.exists())
        self.assertFalse((self.root / 'target/calls').exists())

    def test_legacy_pending_file_has_no_consumer(self):
        folder = state_dir(self.root)
        pending = folder / 'pending.json'
        pending.write_text('{broken legacy marker')
        self.assertFalse(self.invoke()['reused'])
        self.assertTrue(self.invoke()['reused'])
        self.source.write_text('class App { int changed; }')
        self.assertFalse(self.invoke()['reused'])
        self.assertEqual(pending.read_text(), '{broken legacy marker')
        self.assertEqual(len(self.calls()), 2)

    def test_profile_module_uses_reactor_root(self):
        (self.root / 'pom.xml').write_text('<project><profiles><profile><id>integration</id><modules><module>integration</module></modules></profile></profiles></project>')
        child = self.root / 'integration'
        child.mkdir()
        (child / 'pom.xml').write_text('<project/>')
        self.assertEqual(discover(child)['root'], str(self.root))
        self.assertEqual(build_plan(self.request(module='integration', profiles=['integration']))['module'], 'integration')

    def test_report_root_symlink_refused(self):
        with tempfile.TemporaryDirectory() as external:
            (Path(external) / 'test.xml').write_text('<testsuite tests="1"/>')
            (self.root / 'target/surefire-reports').symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'Unsafe test report directory'):
                test_report_snapshot(self.root)

    def test_module_named_build_keeps_test_reports(self):
        module = self.root / 'build'
        reports = module / 'target/surefire-reports'
        reports.mkdir(parents=True)
        (module / 'pom.xml').write_text('<project/>')
        report = reports / 'tests.xml'
        report.write_text('<testsuite tests="1"/>')
        self.assertIn(str(report), test_report_snapshot(self.root))

    def test_gradle_module_named_build_without_local_build_script(self):
        (self.root / 'pom.xml').unlink()
        (self.root / 'settings.gradle').write_text('include(":build")')
        reports = self.root / 'build/build/test-results/test'
        reports.mkdir(parents=True)
        report = reports / 'tests.xml'
        report.write_text('<testsuite tests="1"/>')
        self.assertIn(str(report), test_report_snapshot(self.root))

    def test_module_report_budget_ignores_unrelated_reports(self):
        (self.root / 'pom.xml').write_text('<project><modules><module>selected</module></modules></project>')
        module = self.root / 'selected'
        module.mkdir()
        (module / 'pom.xml').write_text('<project/>')
        reports = self.root / 'target/surefire-reports'
        reports.mkdir()
        for index in range(3):
            (reports / f'{index}.xml').write_bytes(b' ' * (3 * 1024 * 1024))
        result = self.invoke(task='test', module='selected', collectTestReports=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['reportScope'], str(module))
        self.assertEqual(result['testReports'], [])

    def test_aggregate_report_budget_fails_with_diagnostic(self):
        reports = self.root / 'target/surefire-reports'
        reports.mkdir()
        for index in range(3):
            (reports / f'{index}.xml').write_bytes(b' ' * (3 * 1024 * 1024))
        with self.assertRaisesRegex(ValueError, 'aggregate exceeds'):
            test_report_snapshot(self.root)

    def test_escaped_result_exceeds_transport_with_explicit_diagnostic(self):
        import shlex
        self.wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) +
                                ' -c "import sys; sys.stdout.write(chr(1) * 5 * 1024 * 1024)"\n')
        result = self.invoke(maxOutputBytes=8 * 1024 * 1024)
        self.assertFalse(result['ok'])
        self.assertEqual(result['buildExitCode'], 0)
        self.assertTrue(result['outputOmitted'])
        self.assertIn('serialized limit', result['error'])

    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory()
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name).resolve()
        self.addCleanup(shutil.rmtree, state_path(self.root), ignore_errors=True)
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
        proc = subprocess.run([sys.executable, str(RUNTIME)], input=json.dumps(self.request(**extra)), capture_output=True, text=True, timeout=60)
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

    def ancestor_fixture(self, shared_mode):
        shared = self.root / 'shared'
        victim = shared / 'victim'
        victim.mkdir(parents=True)
        (victim / 'pom.xml').write_text('<project/>')
        wrapper = victim / 'mvnw'
        wrapper.write_text('#!/bin/sh\necho own >> ../../target/calls\nexit 0\n')
        wrapper.chmod(0o755)
        (shared / 'pom.xml').write_text('<project><modules><module>victim</module></modules></project>')
        (shared / 'mvnw').write_text('#!/bin/sh\necho planted >> ../target/calls\nexit 0\n')
        self.addCleanup(shutil.rmtree, state_path(victim), ignore_errors=True)
        self.addCleanup(shutil.rmtree, state_path(shared), ignore_errors=True)
        shared.chmod(shared_mode)
        self.addCleanup(shared.chmod, 0o755)
        return shared, victim

    def test_untrusted_ancestor_reactor_and_wrapper_are_never_used(self):
        shared, victim = self.ancestor_fixture(0o1777)  # like /tmp: other users can plant files
        info = discover(victim)
        self.assertEqual(info['root'], str(victim))
        self.assertIn(f'Ignored enclosing Maven reactor {shared}', info['discoveryWarnings'][0])
        self.assertIn('writable by all users', info['discoveryWarnings'][0])
        result = self.invoke(projectRoot=str(victim))
        self.assertTrue(result['ok'])
        self.assertIn('Ignored enclosing Maven reactor', result['discoveryWarnings'][0])
        self.assertEqual(self.calls(), ['own'])
        notes = shared / 'notes'
        notes.mkdir()
        with self.assertRaisesRegex(ValueError, 'untrusted ancestor'):
            discover(notes)
        self.assertIn('untrusted ancestor', self.invoke(projectRoot=str(notes))['error'])
        self.assertEqual(self.calls(), ['own'])
        shared.chmod(0o755)
        (shared / 'mvnw').chmod(0o666)  # world-writable wrapper
        with self.assertRaisesRegex(ValueError, 'untrusted ancestor'):
            discover(notes)
        self.assertEqual(discover(victim)['root'], str(victim))

    def test_trusted_ancestor_reactor_is_still_shared(self):
        shared, victim = self.ancestor_fixture(0o755)
        info = discover(victim)
        self.assertEqual(info['root'], str(shared))
        self.assertNotIn('discoveryWarnings', info)

    def test_build_configuration_directories_are_trust_inputs(self):
        shared, victim = self.ancestor_fixture(0o755)
        for relative in ('.mvn/extensions.xml', '.mvn/jvm.config', '.mvn/wrapper/maven-wrapper.properties',
                         'gradle/wrapper/gradle-wrapper.properties', 'gradle.properties'):
            with self.subTest(relative=relative):
                path = shared / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('')
                path.chmod(0o666)
                info = discover(victim)
                self.assertEqual(info['root'], str(victim))
                self.assertIn(relative.split('/')[-1], info['discoveryWarnings'][0])
                path.chmod(0o644)
                self.assertEqual(discover(victim)['root'], str(shared))
        (shared / '.mvn').chmod(0o777)
        self.addCleanup((shared / '.mvn').chmod, 0o755)
        self.assertEqual(discover(victim)['root'], str(victim))

    def test_wsl_drive_advice(self):
        mounts = ('C:\\ /mnt/c 9p rw,noatime,aname=drvfs;path=C:\\;uid=1000;gid=1000 0 0\n'
                  'D:\\ /mnt/d 9p rw,aname=drvfs;path=D:\\;metadata;uid=1000 0 0\n'
                  'E: /mnt/e drvfs rw,metadata 0 0\n'
                  '/dev/sdc / ext4 rw,relatime 0 0\n')
        self.assertTrue(project_runtime.drvfs('/mnt/c/Users/me/app', mounts))
        self.assertFalse(project_runtime.drvfs('/home/me/app', mounts))
        self.assertFalse(project_runtime.drvfs('/mnt/cache/app', mounts))
        self.assertFalse(project_runtime.drvfs('/mnt/d/app', mounts))
        self.assertFalse(project_runtime.drvfs('/mnt/e/app', mounts))
        shared, victim = self.ancestor_fixture(0o777)
        with patch.object(project_runtime, 'drvfs', return_value=True):
            warning = discover(victim)['discoveryWarnings'][0]
        self.assertIn('"metadata" option', warning)
        self.assertIn('/etc/wsl.conf', warning)
        self.assertNotIn('metadata', discover(victim)['discoveryWarnings'][0])

    def test_group_write_only_for_user_private_group(self):
        path = self.root / 'pom.xml'
        path.chmod(0o664)
        project_runtime.private_group.cache_clear()
        self.addCleanup(project_runtime.private_group.cache_clear)
        user = pwd.getpwuid(os.getuid()).pw_name
        for platform, members, name, trusted in (('linux', [], user, True), ('linux', [user], user, True),
                                                  ('linux', ['other'], user, False), ('linux', [], 'staff', False),
                                                  ('darwin', [], user, False)):
            with self.subTest(platform=platform, members=members, name=name):
                project_runtime.private_group.cache_clear()
                group = type('Group', (), {'gr_name': name, 'gr_mem': members})()
                with patch.object(project_runtime.sys, 'platform', platform), \
                        patch.object(project_runtime.grp, 'getgrgid', return_value=group):
                    self.assertEqual(project_runtime.trusted(path), trusted)

    def run_with_user_database(self, code, home=None):
        """Run the runtime CLI in a child whose user lookup fails or points elsewhere."""
        script = '''import io, json, pwd, runpy, sys
mode, home = sys.argv[1], sys.argv[2]
real = pwd.getpwuid
def lookup(uid):
    if mode == 'missing':
        raise KeyError(f'getpwuid(): uid not found: {uid}')
    return type('Entry', (), {'pw_dir': home, 'pw_name': real(uid).pw_name})()
pwd.getpwuid = lookup
runpy.run_path(sys.argv[3], run_name='__main__')
'''
        # Outside the project: state inside it would change the input fingerprint.
        tmp = Path(self.outside.name) / 'tmp'
        tmp.mkdir(exist_ok=True)
        proc = subprocess.run([sys.executable, '-c', script, code, str(home or ''), str(RUNTIME)],
                              input=json.dumps(self.request()), capture_output=True, text=True, timeout=60,
                              env=dict(os.environ, TMPDIR=str(tmp)))
        return json.loads(proc.stdout), tmp / f'spring-boot-tools-{os.getuid()}'

    def test_state_without_user_database_or_home_falls_back_privately(self):
        self.outside = tempfile.TemporaryDirectory()
        self.addCleanup(self.outside.cleanup)
        result, fallback = self.run_with_user_database('missing')
        self.assertTrue(result['ok'], result)
        self.assertEqual(fallback.stat().st_mode & 0o777, 0o700)
        self.assertTrue(any(fallback.iterdir()))
        result, _ = self.run_with_user_database('home', Path(self.outside.name) / 'absent-home')
        self.assertTrue(result['ok'], result)
        self.assertTrue(self.run_with_user_database('missing')[0]['reused'])
        unsafe = Path(self.outside.name) / 'unsafe-home'
        (unsafe / '.cache').mkdir(parents=True)
        (unsafe / '.cache').chmod(0o777)
        result, _ = self.run_with_user_database('home', unsafe)
        self.assertIn('Unsafe build state cache directory', result['error'])

    def test_gradle_ancestor_settings_require_trust_and_membership(self):
        (self.root / 'pom.xml').unlink()
        shared = self.root / 'shared'
        app = shared / 'app'
        app.mkdir(parents=True)
        (app / 'build.gradle').write_text('')
        (shared / 'settings.gradle').write_text('include(":other")')
        self.assertEqual(discover(app)['root'], str(shared))  # no own wrapper: Gradle uses the ancestor
        (app / 'gradlew').write_text('#!/bin/sh\n')
        self.assertEqual(discover(app)['root'], str(app))  # own wrapper and not an included project
        (shared / 'settings.gradle').write_text('include(":app")')
        self.assertEqual(discover(app)['root'], str(shared))
        shared.chmod(0o1777)
        self.addCleanup(shared.chmod, 0o755)
        self.assertEqual(discover(app)['root'], str(app))

    def test_utf16_doctype_pom_refused(self):
        (self.root / 'pom.xml').write_bytes('<?xml version="1.0" encoding="UTF-16"?>\n<!DOCTYPE project [<!ENTITY m "evil">]><project><modules><module>&m;</module></modules></project>'.encode('utf-16'))
        with self.assertRaisesRegex(ValueError, 'Unsafe or oversized POM'):
            discover(self.root)

    def test_lock_is_shared_across_tmpdir_values(self):
        self.wrapper.write_text('#!/bin/sh\necho start >> target/calls\nsleep .3\necho end >> target/calls\n')
        processes = []
        for name in ('a', 'b'):
            tmp = self.root / ('tmp-' + name)
            tmp.mkdir()
            proc = subprocess.Popen([sys.executable, str(RUNTIME)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    text=True, env=dict(os.environ, TMPDIR=str(tmp)))
            proc.stdin.write(json.dumps(self.request(task='compile')))
            proc.stdin.close()
            proc.stdin = None
            processes.append(proc)
        for proc in processes:
            self.assertTrue(json.loads(proc.communicate(timeout=10)[0])['ok'])
        self.assertEqual(self.calls(), ['start', 'end', 'start', 'end'])
        self.assertEqual(state_dir(self.root), state_path(self.root))

    def test_unsafe_state_directory_refused(self):
        folder = state_dir(self.root)
        folder.chmod(0o755)
        result = self.invoke()
        self.assertIn('Unsafe shared build state directory', result['error'])
        self.assertFalse((self.root / 'target/calls').exists())

    def test_special_report_files_never_block(self):
        reports = self.root / 'target/surefire-reports'
        reports.mkdir()
        os.mkfifo(reports / 'TEST-blocking.xml')
        (reports / 'TEST-real.xml').write_text('<testsuite tests="1"/>')
        start = time.monotonic()
        result = self.invoke(task='test', collectTestReports=True, timeoutMs=5000)
        self.assertLess(time.monotonic() - start, 4)
        self.assertTrue(result['ok'], result)
        self.wrapper.write_text('#!/bin/sh\nmkfifo target/surefire-reports/TEST-new.xml\necho "<testsuite/>" > target/surefire-reports/TEST-fresh.xml\n')
        result = self.invoke(task='test', collectTestReports=True, timeoutMs=5000)
        self.assertEqual([Path(report['file']).name for report in result['testReports']], ['TEST-fresh.xml'])

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
