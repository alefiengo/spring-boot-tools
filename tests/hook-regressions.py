#!/usr/bin/env python3
"""Run real hooks with JSON and isolated projects; builds use mock wrappers only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ACTION = sys.argv.pop(1)

class HookTests(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory()
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name)

    def invoke(self, file=None, content=None, **extra):
        action = extra.pop('action', ACTION)
        args = {}
        if file is not None:
            args['file_path'] = str(file)
        if content is not None:
            args['content'] = content
        args.update(extra.pop('tool_input', {}))
        data = {'tool_input': args, **extra}
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root))
        result = subprocess.run(['bash', str(ROOT / 'hooks/scripts' / (action+'.sh'))],
                                input=json.dumps(data), capture_output=True, text=True, env=env, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout else None

    def deny(self, output):
        self.assertEqual(output['hookSpecificOutput']['hookEventName'], 'PreToolUse')
        self.assertEqual(output['hookSpecificOutput']['permissionDecision'], 'deny')

    def context(self, output):
        self.assertEqual(output['hookSpecificOutput']['hookEventName'], 'PostToolUse')
        return output['hookSpecificOutput']['additionalContext']

    def wrapper(self, gradle=False, code=0):
        (self.root / 'target').mkdir(exist_ok=True)
        (self.root / ('build.gradle.kts' if gradle else 'pom.xml')).write_text('')
        wrapper = self.root / ('gradlew' if gradle else 'mvnw')
        wrapper.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> target/calls\necho "ordinary build warning"\nexit '+str(code)+'\n')
        wrapper.chmod(0o755)
        return wrapper

    def test_diagnostics(self):
        helper = str(ROOT / 'hooks/scripts/hook-runtime.py')
        result = subprocess.run([sys.executable, helper, ACTION], input='{broken', text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('invalid JSON', json.loads(result.stdout)['systemMessage'])
        result = subprocess.run(['/bin/bash', str(ROOT / 'hooks/scripts' / (ACTION+'.sh'))],
                                input='{}', text=True, capture_output=True, env={'PATH': '/nonexistent'})
        self.assertEqual(result.returncode, 0)
        self.assertIn('Python 3 is required', json.loads(result.stdout)['systemMessage'])
        if ACTION == 'verify-on-stop':
            (self.root/'pom.xml').write_text('')
            data = {'tool_input': {'file_path': str(self.root/'src/main/java/App.java')}}
            result = subprocess.run([sys.executable, helper, ACTION], input=json.dumps(data), text=True,
                                    capture_output=True, env={'PATH': '/nonexistent', 'CLAUDE_PROJECT_DIR': str(self.root)})
            output = json.loads(result.stdout)
            message = output.get('systemMessage') or output['hookSpecificOutput']['additionalContext']
            self.assertIn('wrapper are unavailable', message)

    def test_secret(self):
        if ACTION != 'guard-secrets': self.skipTest('other hook')
        path = self.root / 'application.yml'
        for text in ('password: hunter2\nurl: ${DB_URL}', 'name: "app"\npassword: hunter2',
                     'spring.datasource.password=hunter2', 'password: "unsafe \\"quote\\""',
                     'secret=real\napi_key=dummy', 'password: dummy-real', 'JWT_TOKEN: literal-token', 'password: ${DB_PASSWORD:${FALLBACK_PASSWORD:literal}}', 'spring: {password: ${DB_PASSWORD}, api_key: literal-secret}', 'spring: {password: dummy, api_key: \"literal,secret\"}', '"password": hunter2', 'password: ${DB_PASSWORD:real-secret}'):
            self.deny(self.invoke(path, text))
        for text in ('password: ${DB_PASSWORD}', 'password: ${DB_PASSWORD:${FALLBACK_PASSWORD}}', 'password: "${DB_PASSWORD}"',
                     'password: changeme', 'api_key: <your-key>', '# password: secret', 'password: ""', 'spring: {password: ${DB_PASSWORD}, api_key: dummy}', 'spring: {password: "${DB_PASSWORD}", api_key: dummy}', 'password-policy: strict', 'access-token-uri: https://issuer/token', 'private-key: classpath:private.pem', 'password: {policy: strict}'):
            self.assertIsNone(self.invoke(path, text))
        path.write_text('name: "app"\npassword: ${DB_PASSWORD}\n')
        self.deny(self.invoke(path, tool_input={'old_string':'${DB_PASSWORD}', 'new_string':'hunter2'}))
        path.write_text('password: safe\napi_key: ${API_KEY}')
        self.deny(self.invoke(path, tool_input={'old_string':'${API_KEY}', 'new_string':'dummy'}))
        self.deny(self.invoke(self.root/'.env.production', 'DB_PASSWORD=hunter2\nOTHER=${ENV}'))
        self.deny(self.invoke(self.root/'.env', 'PASSWORD=dummy,real-secret'))
        self.assertIsNone(self.invoke(self.root/'Service.java', 'password: hunter2'))

    def test_ddl(self):
        if ACTION != 'guard-ddl': self.skipTest('other hook')
        path = self.root/'application.yml'
        for text in ('ddl-auto: update', 'name: "app"\nddl-auto: create',
                     'spring.profiles.active=dev\nspring.jpa.hibernate.ddl-auto=create-drop',
                     'ddl-auto: "update"', '"ddl-auto": update', 'ddl-auto: ${DDL_AUTO:update}', 'hibernate: {ddl-auto: ${DDL_AUTO:update}}', 'hibernate: {ddl-auto: update}', 'hibernate: {ddl-auto: none, ddl-auto: create}', 'hibernate: {"ddl-auto": "create-drop"}'):
            self.deny(self.invoke(path, text))
        for text in ('ddl-auto: none', 'ddl-auto: validate', '# ddl-auto: update'):
            self.assertIsNone(self.invoke(path, text))
        self.assertIsNone(self.invoke(self.root/'application-dev.yml', 'ddl-auto: update'))
        self.deny(self.invoke(self.root/'application-prod.yml', 'spring.profiles.active=dev\nddl-auto: update'))
        self.assertIsNone(self.invoke(path, 'spring:\n  config:\n    activate:\n      on-profile: dev\nddl-auto: update'))
        self.assertIsNone(self.invoke(path, 'spring.config.activate.on-profile=local\nspring.jpa.hibernate.ddl-auto=create-drop'))
        for text in ('spring.config.activate.on-profile: dev|prod\nddl-auto: update', 'unrelated.on-profile: dev\nddl-auto: update', 'spring.config.activate.on-profile: dev\nddl-auto: update\n---\nddl-auto: update', 'spring.config.activate.on-profile: !prod\nddl-auto: update'):
            self.deny(self.invoke(path, text))
        path.write_text('ddl-auto: none')
        self.deny(self.invoke(path, tool_input={'old_string':'none', 'new_string':'update'}))

    def test_compile(self):
        if ACTION != 'post-edit-compile': self.skipTest('other hook')
        self.wrapper()
        file = self.root/'src/main/java/com/example/App.java'
        file.parent.mkdir(parents=True)
        file.write_text('class App {}')
        for _ in range(3):
            self.assertIsNone(self.invoke(file))
        self.assertFalse((self.root/'target/calls').exists())
        self.assertIsNone(self.invoke(action='verify-on-stop'))
        self.assertEqual((self.root/'target/calls').read_text().splitlines(), ['-B -ntp verify'])
        self.assertIsNone(self.invoke(file))
        self.assertIsNone(self.invoke(action='verify-on-stop'))
        self.assertEqual(len((self.root/'target/calls').read_text().splitlines()), 1)
        file.write_text('class App { int changed; }')
        self.assertIsNone(self.invoke(file))
        self.assertIsNone(self.invoke(action='verify-on-stop'))
        self.assertEqual(len((self.root/'target/calls').read_text().splitlines()), 2)

    def test_compile_gradle(self):
        if ACTION != 'post-edit-compile': self.skipTest('other hook')
        self.wrapper(gradle=True)
        self.assertIsNone(self.invoke(self.root/'src/test/java/com/demo/Test.java'))
        self.assertFalse((self.root/'target/calls').exists())
        self.assertIsNone(self.invoke(action='verify-on-stop'))
        self.assertEqual((self.root/'target/calls').read_text(), '--console=plain --no-daemon check\n')

    def test_verify(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        self.assertIsNone(self.invoke())
        wrapper = self.wrapper(gradle=True, code=4)
        output = self.invoke()
        self.assertEqual(output['decision'], 'block')
        self.assertIn('check (exit 4)', output['reason'])
        self.assertEqual(len((self.root/'target/calls').read_text().splitlines()), 1)
        self.assertIsNone(self.invoke(stop_hook_active=True))
        self.assertEqual(len((self.root/'target/calls').read_text().splitlines()), 1)
        # A failed check is never reusable.
        self.assertEqual(self.invoke()['decision'], 'block')
        self.assertEqual(len((self.root/'target/calls').read_text().splitlines()), 2)
        wrapper.write_text('#!/bin/sh\necho warning\nexit 0\n')
        self.assertIsNone(self.invoke())

    def test_verify_maven(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        self.wrapper()
        self.assertIsNone(self.invoke())
        self.assertEqual((self.root/'target/calls').read_text(), '-B -ntp verify\n')
        self.assertIsNone(self.invoke())
        self.assertEqual(len((self.root/'target/calls').read_text().splitlines()), 1)
        (self.root/'pom.xml').write_text('<project><!-- external edit --></project>')
        self.assertIsNone(self.invoke())
        self.assertEqual(len((self.root/'target/calls').read_text().splitlines()), 2)

    def test_stop_sigterm_kills_child_session(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        wrapper = self.wrapper()
        wrapper.write_text('#!/bin/sh\necho started >> target/calls\n(sleep 1; echo survived > target/orphan) & wait\n')
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root))
        proc = subprocess.Popen([sys.executable, str(ROOT / 'hooks/scripts/hook-runtime.py'), 'verify-on-stop'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        proc.stdin.write('{}')
        proc.stdin.close()
        proc.stdin = None
        import time
        deadline = time.monotonic() + 5
        while not (self.root/'target/calls').exists():
            if time.monotonic() >= deadline: self.fail('hook build did not start')
            time.sleep(.02)
        proc.terminate()
        proc.communicate(timeout=5)
        time.sleep(1.1)
        self.assertFalse((self.root/'target/orphan').exists())
        wrapper.write_text('#!/bin/sh\necho verified >> target/calls\nexit 0\n')
        self.assertIsNone(self.invoke())
        self.assertEqual((self.root/'target/calls').read_text().splitlines(), ['started', 'verified'])

    def test_source_packages_named_build_target_docs(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        wrapper = self.wrapper()
        wrapper.write_text('#!' + sys.executable + '\nimport sys\nfrom pathlib import Path\nwith open("target/calls", "a") as out: print(" ".join(sys.argv[1:]), file=out)\nif any("invalid Java syntax" in p.read_text() for p in Path("src").rglob("*.java")):\n print("invalid-source"); sys.exit(9)\n')
        for name in ('build', 'target', 'docs'):
            source = self.root / 'src/main/java/example' / name / 'App.java'
            source.parent.mkdir(parents=True)
            source.write_text('class App {}')
            self.assertIsNone(self.invoke())
            before = len((self.root/'target/calls').read_text().splitlines())
            source.write_text('invalid Java syntax')
            result = self.invoke()
            self.assertEqual(result['decision'], 'block')
            self.assertIn('invalid-source', result['reason'])
            self.assertEqual(len((self.root/'target/calls').read_text().splitlines()), before + 1)
            source.write_text('class App {}')


    def test_mcp_verification_reused_by_stop(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        self.wrapper()
        runtime = ROOT / 'runtime/project_runtime.py'
        result = subprocess.run([sys.executable, str(runtime)], input=json.dumps({'action':'run', 'projectRoot':str(self.root), 'task':'verify'}), capture_output=True, text=True)
        self.assertTrue(json.loads(result.stdout)['ok'])
        self.assertIsNone(self.invoke())
        self.assertEqual(len((self.root/'target/calls').read_text().splitlines()), 1)

    def test_lint(self):
        if ACTION != 'lint-migration': self.skipTest('other hook')
        directory = self.root/'src/main/resources/db/migration'
        directory.mkdir(parents=True)
        for name in ('V1.2__users.sql', 'V2026_01_01__users.sql', 'R__refresh_views.sql', 'U1__undo.sql'):
            path = directory/name
            path.write_text('CREATE TABLE users (id bigint);')
            self.assertIsNone(self.invoke(path))
        path = directory/'bad.sql'
        path.write_text('DROP TABLE IF EXISTS users;')
        message = self.context(self.invoke(path))
        self.assertIn('naming:', message)
        self.assertIn('IF EXISTS does not protect data', message)
        self.assertIn('Never edit an already applied migration', message)
        path.write_text('-- DROP TABLE users;\nCREATE TABLE users (id bigint);')
        self.assertNotIn('Destructive SQL', self.context(self.invoke(path)))
        self.assertIsNone(self.invoke(self.root/'missing.sql'))
        other = self.root/'db/migration-other'
        other.mkdir(parents=True)
        (other/'bad.sql').write_text('DROP TABLE users;')
        self.assertIsNone(self.invoke(other/'bad.sql'))

if __name__ == '__main__':
    methods = {
        'guard-secrets': ['test_secret'],
        'guard-ddl': ['test_ddl'],
        'post-edit-compile': ['test_compile', 'test_compile_gradle'],
        'verify-on-stop': ['test_verify', 'test_verify_maven', 'test_mcp_verification_reused_by_stop', 'test_source_packages_named_build_target_docs', 'test_stop_sigterm_kills_child_session'],
        'lint-migration': ['test_lint'],
    }
    result = unittest.TextTestRunner().run(unittest.TestSuite(HookTests(name) for name in ['test_diagnostics', *methods[ACTION]]))
    sys.exit(not result.wasSuccessful())
