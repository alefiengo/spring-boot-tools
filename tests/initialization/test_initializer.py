"""Deterministic stdlib regressions; no network or build tools required."""
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.parse
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'runtime'))
import project_initializer as init

METADATA = (Path(__file__).parent / 'metadata.json').read_bytes()


def archive(extra=(), build='pom.xml'):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as output:
        output.writestr(build, '<project><parent><groupId>org.springframework.boot</groupId></parent></project>')
        wrapper = zipfile.ZipInfo('mvnw' if build == 'pom.xml' else 'gradlew')
        wrapper.external_attr = (stat.S_IFREG | 0o755) << 16
        output.writestr(wrapper, '#!/bin/sh\ntouch BUILD_EXECUTED\n')
        output.writestr('src/main/java/com/example/App.java', 'class App {}')
        for name, content in extra:
            output.writestr(name, content)
    return stream.getvalue()


class InitializerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sbt-initializer-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.request = {'projectRoot': str(self.root), 'targetDirectory': 'app', 'userRequested': True}
        self.calls = []
        self.data = archive()

    def fetch(self, url, accept, limit, deadline):
        self.calls.append(url)
        return METADATA if url.endswith('/metadata/client') else self.data

    def run_init(self, **changes):
        with patch.object(init, 'download', self.fetch):
            return init.initialize_project(dict(self.request, **changes))

    def assert_clean(self):
        self.assertFalse((self.root / 'app').exists())
        self.assertFalse(list(self.root.glob('.sbt-initializr-*')))

    def test_success_metadata_defaults_wrapper_and_no_build(self):
        result = self.run_init(dependencies=['web'], name='Example app')
        self.assertTrue(result['ok'])
        self.assertFalse(result['buildExecuted'])
        self.assertEqual(result['parameters']['bootVersion'], '9.8.7')
        self.assertEqual(result['parameters']['javaVersion'], '29')
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.calls[1]).query)
        self.assertEqual(params['name'], ['Example app'])
        self.assertEqual(params['dependencies'], ['web'])
        self.assertEqual((self.root / 'app/mvnw').stat().st_mode & 0o777, 0o755)
        self.assertFalse((self.root / 'app/BUILD_EXECUTED').exists())
        self.assertFalse(list(self.root.glob('.sbt-initializr-*')))

    def test_gradle_kotlin_explicit_choices(self):
        self.data = archive(build='build.gradle.kts')
        result = self.run_init(type='gradle-project-kotlin', language='kotlin', bootVersion='9.8.6', javaVersion='25')
        self.assertEqual(result['parameters']['bootVersion'], '9.8.6')
        self.assertTrue(os.access(self.root / 'app/gradlew', os.X_OK))

    def test_default_package_is_left_to_initializr(self):
        result = self.run_init()
        self.assertEqual(result['parameters']['groupId'], 'com.example')
        self.assertEqual(result['parameters']['artifactId'], 'demo')
        self.assertNotIn('packageName', result['parameters'])
        self.assertNotIn('packageName', urllib.parse.parse_qs(urllib.parse.urlsplit(self.calls[1]).query))

    def test_custom_coordinates_do_not_send_metadata_example_package(self):
        result = self.run_init(groupId='org.acme', artifactId='order-service')
        self.assertEqual(result['parameters']['groupId'], 'org.acme')
        self.assertEqual(result['parameters']['artifactId'], 'order-service')
        self.assertNotIn('packageName', result['parameters'])
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.calls[1]).query)
        self.assertNotIn('packageName', params)

    def test_explicit_package_override_is_preserved(self):
        result = self.run_init(groupId='org.acme', artifactId='order-service', packageName='net.custom.orders')
        self.assertEqual(result['parameters']['packageName'], 'net.custom.orders')
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.calls[1]).query)
        self.assertEqual(params['packageName'], ['net.custom.orders'])

    def test_explicit_request_required(self):
        for value in (False, None, 'true', 1):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'explicit user'):
                self.run_init(userRequested=value)
        self.assertEqual(self.calls, [])
        self.assert_clean()

    def test_existing_nonempty_target_preserved(self):
        target = self.root / 'app'
        target.mkdir()
        (target / 'keep').write_text('original')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_init()
        self.assertEqual((target / 'keep').read_text(), 'original')
        self.assertEqual(self.calls, [])

    def test_existing_empty_target_refused_and_preserved(self):
        target = self.root / 'app'
        target.mkdir()
        identity = target.stat().st_ino
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_init()
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(target.stat().st_ino, identity)

    def test_existing_boot_project_and_ancestor_module_context(self):
        (self.root / 'pom.xml').write_text('<project><parent><groupId>org.springframework.boot</groupId></parent><modules><module>module</module></modules></project>')
        (self.root / 'module').mkdir()
        (self.root / 'module/pom.xml').write_text('<project/>')
        (self.root / 'module/deep').mkdir()
        for target in ('.', 'app', 'module/deep/new'):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'Existing project context'):
                self.run_init(targetDirectory=target)
        with tempfile.TemporaryDirectory() as outside:
            with self.assertRaisesRegex(ValueError, 'Existing project context'):
                self.run_init(projectRoot=str(self.root / 'module/deep'), targetDirectory=str(Path(outside) / 'new'))
        self.assertEqual(self.calls, [])

    def test_destination_ancestor_project_even_from_unrelated_context(self):
        build = self.root / 'existing'
        build.mkdir()
        (build / 'settings.gradle.kts').write_text('include(":module")')
        (build / 'module').mkdir()
        with self.assertRaisesRegex(ValueError, 'Existing project context'):
            self.run_init(targetDirectory=str(build / 'module/new'))
        self.assertEqual(self.calls, [])

    def test_symlink_target_and_ancestor_and_traversal(self):
        (self.root / 'real').mkdir()
        (self.root / 'link').symlink_to(self.root / 'real', target_is_directory=True)
        for target in ('../escape', 'real/../escape', 'link/new', 'link'):
            with self.subTest(target=target), self.assertRaises((OSError, ValueError)):
                self.run_init(targetDirectory=target)
        self.assertEqual(list((self.root / 'real').iterdir()), [])
        self.assertEqual(self.calls, [])

    def test_unknown_metadata_choices_and_build_only_type(self):
        for args in ({'bootVersion': 'latest'}, {'dependencies': ['unknown']}, {'type': 'maven-build'}, {'artifactId': '../escape'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.run_init(**args)
        self.assert_clean()

    def test_malformed_traversal_duplicate_symlink_special_and_privileged_archives(self):
        invalid = [b'not a zip', archive([('aXescape', 'payload')]).replace(b'aXescape', b'a\x00escape')]
        for name in ('../escape', '/escape', 'a/../../escape', 'a\\escape', 'C:/escape', './escape', 'a//escape', 'a/escape.', 'POM.XML'):
            invalid.append(archive([(name, 'payload')]))
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o644, stat.S_IFREG | 0o4755):
            entry = zipfile.ZipInfo('unsafe')
            entry.create_system = 3
            entry.external_attr = mode << 16
            invalid.append(archive([(entry, '../outside')]))
        for payload in invalid:
            with self.subTest(payload=payload[:30]):
                self.data = payload
                with self.assertRaises((ValueError, OSError)):
                    self.run_init()
                self.assert_clean()
        self.assertFalse((self.root.parent / 'escape').exists())

    def test_archive_expansion_count_file_and_download_limits(self):
        self.data = archive([('bomb', 'x' * 100000)])
        with self.assertRaisesRegex(ValueError, 'expansion limit'):
            self.run_init()
        for setting, limit in (('MAX_ENTRIES', 2), ('MAX_FILE', 10), ('MAX_EXPANDED', 20), ('MAX_DOWNLOAD', 10)):
            self.data = archive()
            with self.subTest(setting=setting), patch.object(init, setting, limit), self.assertRaises(ValueError):
                self.run_init()
            self.assert_clean()

    def test_bad_crc_or_missing_build_no_partial_files(self):
        self.data = archive().replace(b'#!/bin/sh', b'#!/bin/xx')
        with self.assertRaisesRegex(ValueError, 'Invalid Initializr archive'):
            self.run_init()
        self.data = archive(build='unrecognized')
        with self.assertRaisesRegex(ValueError, 'no root project'):
            self.run_init()
        self.assert_clean()

    def test_network_metadata_and_publication_failures_leave_no_partial_files(self):
        for failure in (TimeoutError('timeout'), OSError('offline'), ValueError('HTTP error')):
            with patch.object(init, 'download', side_effect=failure), self.assertRaises(type(failure)):
                init.initialize_project(self.request)
            self.assert_clean()
        with patch.object(init, 'download', return_value=b'{}'), self.assertRaisesRegex(ValueError, 'Malformed Initializr metadata'):
            init.initialize_project(self.request)
        with patch.object(init, 'publish', side_effect=OSError('publication failed')), self.assertRaises(OSError):
            self.run_init()
        self.assert_clean()

    def test_racing_destination_is_never_overwritten(self):
        original = init.publish
        def race(parent, stage, name):
            (self.root / 'app').mkdir()
            (self.root / 'app/racer').write_text('keep')
            original(parent, stage, name)
        with patch.object(init, 'publish', side_effect=race), self.assertRaisesRegex(ValueError, 'appeared'):
            self.run_init()
        self.assertEqual((self.root / 'app/racer').read_text(), 'keep')
        self.assertEqual([p.name for p in (self.root / 'app').iterdir()], ['racer'])
        self.assertFalse(list(self.root.glob('.sbt-initializr-*')))

    def test_parent_symlink_swap_before_publication(self):
        parent = self.root / 'parent'
        parent.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        extract = init.extract_archive
        def swap(data, stage):
            files = extract(data, stage)
            parent.rename(self.root / 'moved')
            parent.symlink_to(outside, target_is_directory=True)
            return files
        with patch.object(init, 'extract_archive', side_effect=swap), self.assertRaises(OSError):
            self.run_init(targetDirectory='parent/app')
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(list((self.root / 'moved').iterdir()), [])

    def test_runtime_cli_validation_and_signal_cleanup(self):
        # Inject only the transport in a child; execute the production runtime CLI.
        script = '''import runpy, sys, time
sys.path.insert(0, sys.argv[1])
import project_initializer as init
init.download = lambda url, *args: open(sys.argv[2] if url.endswith('/metadata/client') else sys.argv[3], 'rb').read()
extract = init.extract_archive
def delayed(data, stage):
    files = extract(data, stage)
    print('ready', file=sys.stderr, flush=True)
    time.sleep(30)
    return files
init.extract_archive = delayed
runpy.run_path(sys.argv[1] + '/project_runtime.py', run_name='__main__')
'''
        payload = self.root / 'fixture.zip'
        payload.write_bytes(self.data)
        child = subprocess.Popen([sys.executable, '-c', script, str(ROOT / 'runtime'), str(Path(__file__).parent / 'metadata.json'), str(payload)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            child.stdin.write(json.dumps(dict(self.request, action='initialize')))
            child.stdin.close()
            self.assertEqual(child.stderr.readline().strip(), 'ready')
            child.send_signal(signal.SIGTERM)
            self.assertEqual(child.wait(timeout=5), 1)
            self.assertIn('cancelled', json.loads(child.stdout.read())['error'])
            self.assert_clean()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            child.stdout.close()
            child.stderr.close()


class DownloadTests(unittest.TestCase):
    def test_transport_errors_redirect_size_and_deadline(self):
        response = io.BytesIO(b'ok')
        response.status = 200
        response.headers = {'Content-Length': '2'}
        with patch.object(init.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(init.download(init.ORIGIN + '/ok', 'application/zip', 10, time.monotonic() + 2), b'ok')
            self.assertLessEqual(opener.return_value.open.call_args.kwargs['timeout'], 2)
        for size, body in (('999', b'ok'), ('3', b'ok'), (None, b'oversized body')):
            response = io.BytesIO(body)
            response.status = 200
            response.headers = {} if size is None else {'Content-Length': size}
            with self.subTest(size=size), patch.object(init.urllib.request, 'build_opener') as opener:
                opener.return_value.open.return_value = response
                with self.assertRaises(ValueError):
                    init.download(init.ORIGIN + '/zip', 'application/zip', 10, time.monotonic() + 2)
        with self.assertRaises(ValueError):
            init.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.example')
        with self.assertRaises(TimeoutError):
            init.download(init.ORIGIN + '/ok', 'application/zip', 10, time.monotonic() - 1)
        with self.assertRaises(ValueError):
            init.download('http://untrusted.example/file', 'application/zip', 10, time.monotonic() + 2)
        with patch.object(init.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = OSError('HTTP transport failed')
            with self.assertRaises(OSError):
                init.download(init.ORIGIN + '/error', 'application/zip', 10, time.monotonic() + 2)


if __name__ == '__main__':
    unittest.main()
