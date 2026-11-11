"""Focused offline tests; no dependency installation, hosts or model calls."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('install_doctor', ROOT / 'scripts/install-doctor.py')
DOCTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOCTOR)


@unittest.skipUnless(sys.platform in ('linux', 'darwin'), 'POSIX runtime only')
class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='sbt doctor ')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.bundle = self.base / 'relocated plugin with spaces ; $bundle'
        self.bundle.mkdir()
        self.app = self.base / 'application with spaces'
        self.app.mkdir()
        (self.app / 'pom.xml').write_text('<project/>')
        for name in ('.claude-plugin/plugin.json', '.claude-plugin/marketplace.json',
                     '.codex-plugin/plugin.json', '.mcp.json', 'codex/mcp.json',
                     'codex/run.mjs', 'codex/launch.mjs', 'mcp-server/index.js',
                     'package.json', 'package-lock.json',
                     'runtime/project_runtime.py', 'runtime/sql_lint.py',
                     'runtime/project_initializer.py', 'scripts/install-doctor.py'):
            target = self.bundle / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        (self.bundle / 'skills').mkdir()
        # Resolution fixtures deliberately throw if imported. A doctor must only
        # resolve entrypoints and must never start this mock dependency's code.
        package = json.loads((self.bundle / 'package.json').read_text())
        for name in package['dependencies']:
            dep = self.bundle / 'node_modules' / name
            dep.mkdir(parents=True)
            for target in ('index.cjs', 'index.mjs', 'stdio.mjs'):
                (dep / target).write_text('throw new Error("must not import dependencies");')
            (dep / 'package.json').write_text(json.dumps({
                'name': name, 'main': 'index.cjs', 'exports': {
                    '.': {'import': './index.mjs', 'require': './index.cjs'},
                    './stdio': {'import': './stdio.mjs', 'require': './index.cjs'},
                }}))
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ('SBT_PLUGIN_ROOT', 'PROJECT_ROOT', 'NODE_OPTIONS', 'NODE_PATH')}
        self.env.update(HOME=str(self.base / 'home'), CODEX_HOME=str(self.base / 'codex-home'),
                        XDG_CACHE_HOME=str(self.base / 'cache'),
                        PYTHONDONTWRITEBYTECODE='1')

    def run_doctor(self, *args, env=None, expected=0):
        child = subprocess.run([sys.executable, '-B', str(self.bundle / 'scripts/install-doctor.py'),
                                '--json', '--workspace', str(self.app), '--python', sys.executable,
                                *args], env=env or self.env, cwd=self.app,
                               capture_output=True, text=True, timeout=20)
        self.assertEqual(child.returncode, expected, child.stdout + child.stderr)
        self.assertEqual(child.stderr, '', child.stderr)
        data = json.loads(child.stdout)
        self.assertEqual(data['schemaVersion'], 1)
        self.assertEqual(data['exitCode'], expected)
        self.assertEqual(data['ok'], expected == 0)
        return {check['id']: check for check in data['checks']}

    def needs_node(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Supported Node required for successful resolution fixture')
        version = subprocess.check_output([node, '--version'], text=True)
        if tuple(map(int, version.strip().lstrip('v').split('.'))) < (20, 3, 0):
            self.skipTest('Node >=20.3 required for successful resolution fixture')

    def mutate_json(self, relative, change):
        path = self.bundle / relative
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))

    def test_relocation_and_explicit_discovery(self):
        self.needs_node()
        result = self.run_doctor('--project', str(self.app))
        self.assertEqual(result['project_discovery']['discovery']['root'], str(self.app))
        self.assertEqual(result['dependencies']['status'], 'ok')
        self.assertNotIn('discovered_root', result)

    def test_dependencies_resolve_from_plugin_root_only(self):
        self.needs_node()
        self.assertFalse((self.bundle / 'mcp-server/node_modules').exists())
        self.assertEqual(self.run_doctor()['dependencies']['status'], 'ok')
        shutil.copytree(self.bundle / 'node_modules', self.bundle / 'mcp-server/node_modules')
        self.assertEqual(self.run_doctor()['stale_dependencies']['status'], 'warning')
        shutil.rmtree(self.bundle / 'node_modules')
        self.assertIn('Missing MCP dependencies', self.run_doctor(expected=1)['dependencies']['message'])

    def test_warns_when_project_is_discovered_inside_another_root(self):
        self.needs_node()
        module = self.app / 'module'
        module.mkdir()
        (module / 'pom.xml').write_text('<project/>')
        (self.app / 'pom.xml').write_text('<project><modules><module>module</module></modules></project>')
        result = self.run_doctor('--workspace', str(module), '--project', str(module))
        self.assertEqual(result['discovered_root']['status'], 'warning')
        self.assertIn(str(self.app), result['discovered_root']['message'])

    def test_missing_node_and_python(self):
        result = self.run_doctor('--node', '/missing/node', '--python', '/missing/python', expected=1)
        for identifier in ('node', 'python', 'dependencies'):
            self.assertEqual(result[identifier]['status'], 'error')
        self.assertEqual(result['project_discovery']['status'], 'skipped')

    def test_old_node_and_python(self):
        for name, version in (('node', 'v20.2.9'), ('python', 'Python 3.10.9')):
            args = type('Args', (), dict(plugin_root=str(self.bundle), workspace=str(self.app),
                                         node=sys.executable, python=sys.executable, project=None))()
            with patch.object(DOCTOR, 'probe', return_value=version):
                result = DOCTOR.diagnose(args)
            checks = {check['id']: check for check in result['checks']}
            self.assertIn('too old', checks[name]['message'])

    def test_missing_dependency(self):
        self.needs_node()
        shutil.rmtree(self.bundle / 'node_modules/yaml')
        result = self.run_doctor(expected=1)
        self.assertIn('yaml', result['dependencies']['message'])

    def test_missing_esm_target_despite_valid_cjs_entrypoint(self):
        self.needs_node()
        dependency = self.bundle / 'node_modules/@modelcontextprotocol/server'
        for target, specifier in (('index.mjs', '@modelcontextprotocol/server'),
                                  ('stdio.mjs', '@modelcontextprotocol/server/stdio')):
            with self.subTest(target=target):
                path = dependency / target
                original = path.read_text()
                try:
                    path.unlink()
                    result = self.run_doctor(expected=1)
                    self.assertIn(specifier, result['dependencies']['message'])
                    self.assertTrue((dependency / 'index.cjs').is_file())
                finally:
                    path.write_text(original)

    def test_missing_esm_subpath_export(self):
        self.needs_node()
        self.mutate_json('node_modules/@modelcontextprotocol/server/package.json',
                         lambda data: data['exports'].pop('./stdio'))
        result = self.run_doctor(expected=1)
        self.assertIn('@modelcontextprotocol/server/stdio', result['dependencies']['message'])

    def test_malformed_registration(self):
        self.mutate_json('.codex-plugin/plugin.json', lambda d: d.update(hooks={}))
        self.assertEqual(self.run_doctor(expected=1)['registrations']['status'], 'error')
        (self.bundle / 'codex/mcp.json').write_text('{invalid')
        self.assertEqual(self.run_doctor(expected=1)['json:codex/mcp.json']['status'], 'error')

    def test_bad_json_shape(self):
        (self.bundle / '.codex-plugin/plugin.json').write_text('[]')
        self.assertEqual(self.run_doctor(expected=1)['json:.codex-plugin/plugin.json']['status'], 'error')

    def test_malformed_nested_json_reports_errors_without_traceback(self):
        cases = [
            ('.claude-plugin/marketplace.json', {'plugins': [None]}, 'versions'),
            ('.claude-plugin/marketplace.json', {'plugins': {}}, 'versions'),
            ('.claude-plugin/marketplace.json', {'plugins': [{'name': []}]}, 'versions'),
            ('.codex-plugin/plugin.json', {'skills': None}, 'registrations'),
            ('codex/mcp.json', {'mcpServers': None}, 'registrations'),
            ('codex/mcp.json', {'mcpServers': {'spring-boot': None}}, 'registrations'),
        ]
        original_server = json.loads((self.bundle / 'codex/mcp.json').read_text())['mcpServers']['spring-boot']
        for field in ('args', 'env_vars'):
            for value in (None, 42, {}, 'SBT_PLUGIN_ROOT', [None]):
                cases.append(('codex/mcp.json', {'mcpServers': {'spring-boot': {
                    **original_server, field: value}}}, 'registrations'))
        for path, replacement, identifier in cases:
            with self.subTest(path=path, replacement=replacement):
                target = self.bundle / path
                original = target.read_text()
                try:
                    target.write_text(json.dumps(replacement))
                    self.assertEqual(self.run_doctor(expected=1)[identifier]['status'], 'error')
                finally:
                    target.write_text(original)

    def test_registration_path_escape_and_missing_launcher(self):
        self.mutate_json('.codex-plugin/plugin.json', lambda d: d.update(skills='../'))
        self.assertIn('escapes', self.run_doctor(expected=1)['registrations']['message'])
        self.mutate_json('.codex-plugin/plugin.json', lambda d: d.update(skills='./skills/'))
        (self.bundle / 'codex/launch.mjs').unlink()
        self.assertIn('launch.mjs', self.run_doctor(expected=1)['registrations']['message'])

    def test_wrong_loader_filename_is_rejected_without_execution(self):
        self.mutate_json('codex/mcp.json', lambda data: data['mcpServers']['spring-boot'].update(
            args=['-e', DOCTOR.CODEX_LOADER.replace("'launch.mjs'", "'missing-launch.mjs'")]))
        self.assertIn('loader template', self.run_doctor(expected=1)['registrations']['message'])

    def test_required_runtime_support_files(self):
        for name in ('sql_lint.py', 'project_initializer.py'):
            with self.subTest(name=name):
                path = self.bundle / 'runtime' / name
                original = path.read_bytes()
                try:
                    path.unlink()
                    self.assertIn(name, self.run_doctor(expected=1)['registrations']['message'])
                finally:
                    path.write_bytes(original)

    def test_version_mismatch(self):
        self.mutate_json('.codex-plugin/plugin.json', lambda d: d.update(version='99.0.0'))
        self.assertIn('disagree', self.run_doctor(expected=1)['versions']['message'])

    def test_sbt_plugin_root(self):
        self.needs_node()
        self.run_doctor(env={**self.env, 'SBT_PLUGIN_ROOT': str(self.bundle)})
        for value in ('relative', str(self.app)):
            result = self.run_doctor(env={**self.env, 'SBT_PLUGIN_ROOT': value}, expected=1)
            self.assertEqual(result['sbt_plugin_root']['status'], 'error')

    def test_incorrect_workspace(self):
        self.needs_node()
        other = self.base / 'other project'
        other.mkdir()
        (other / 'build.gradle').touch()
        result = self.run_doctor('--project', str(other), expected=1)
        self.assertIn('Workspace discovers', result['project_discovery']['message'])
        result = self.run_doctor('--workspace', str(self.bundle), expected=1)
        self.assertEqual(result['workspace']['status'], 'error')
        result = self.run_doctor('--workspace', str(self.base / 'absent'), expected=1)
        self.assertEqual(result['workspace']['status'], 'error')

    def test_nested_workspace_and_stale_inherited_root(self):
        self.needs_node()
        nested = self.app / 'src/main'
        nested.mkdir(parents=True)
        result = self.run_doctor('--workspace', str(nested), '--project', str(self.app),
                                 env={**self.env, 'PROJECT_ROOT': str(self.base / 'stale')})
        self.assertEqual(result['project_discovery']['discovery']['root'], str(self.app))
        self.assertEqual(result['inherited_project_root']['status'], 'warning')

    def test_runtime_refusal_without_build_project(self):
        self.needs_node()
        (self.app / 'pom.xml').unlink()
        result = self.run_doctor('--project', str(self.app), expected=1)
        self.assertEqual(result['project_discovery']['status'], 'error')

    def test_no_files_changed_no_wrappers_or_hosts_launched(self):
        self.needs_node()
        tripwire = self.base / 'SIDE_EFFECT'
        for name in ('mvnw', 'gradlew', 'codex', 'claude', 'npm', 'java', 'jdtls'):
            path = self.app / name
            path.write_text(f'#!/bin/sh\ntouch "{tripwire}"\nexit 99\n')
        def snapshot():
            return {str(path.relative_to(self.base)): (
                path.stat().st_mode, path.stat().st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
                    for path in self.base.rglob('*')}
        before = snapshot()
        self.run_doctor('--project', str(self.app),
                        env={**self.env, 'PATH': str(self.app) + os.pathsep + self.env['PATH'],
                             'NODE_V8_COVERAGE': str(self.base / 'coverage-output'),
                             'NODE_COMPILE_CACHE': str(self.base / 'compile-cache'),
                             'NODE_COMPILE_CACHE_PORTABLE': '1',
                             'NODE_REDIRECT_WARNINGS': str(self.base / 'warnings.log'),
                             'NODE_REPL_HISTORY': str(self.base / 'node-history'),
                             'PYTHONPYCACHEPREFIX': str(self.base / 'python-cache'),
                             'PYTHONHISTFILE': str(self.base / 'python-history')})
        self.assertEqual(snapshot(), before)
        self.assertFalse(tripwire.exists())

    def test_probe_does_not_forward_output_environment(self):
        variables = ('NODE_V8_COVERAGE', 'NODE_COMPILE_CACHE', 'NODE_COMPILE_CACHE_PORTABLE',
                     'NODE_REDIRECT_WARNINGS', 'NODE_REPL_HISTORY',
                     'PYTHONPYCACHEPREFIX', 'PYTHONHISTFILE')
        with patch.dict(os.environ, {key: str(self.base / key) for key in variables}):
            output = DOCTOR.probe([sys.executable, '-I', '-B', '-c',
                                  'import json, os; print(json.dumps(dict(os.environ)))'], self.base)
        self.assertFalse(set(variables) & set(json.loads(output)))

    def test_text_and_usage_exit_codes(self):
        command = [sys.executable, '-B', str(self.bundle / 'scripts/install-doctor.py')]
        result = subprocess.run(command + ['--node', '/missing/node'], env=self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Install doctor: FAIL', result.stdout)
        result = subprocess.run(command + ['--nonexistent'], env=self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2)

    def test_probe_timeout_and_output_limit(self):
        with patch.object(DOCTOR, 'TIMEOUT', 0.1):
            with self.assertRaisesRegex(ValueError, 'second limit'):
                DOCTOR.probe([sys.executable, '-c', 'import time; time.sleep(5)'], self.base)
        with self.assertRaisesRegex(ValueError, 'output limit'):
            DOCTOR.probe([sys.executable, '-c', 'print("x" * 70000)'], self.base)

    def test_unsupported_os(self):
        args = type('Args', (), {'plugin_root': str(self.bundle), 'workspace': str(self.app)})()
        with patch.object(DOCTOR.platform, 'system', return_value='Windows'):
            result = DOCTOR.diagnose(args)
        self.assertEqual(result['exitCode'], 1)
        self.assertEqual(len(result['checks']), 1)


if __name__ == '__main__':
    unittest.main()
