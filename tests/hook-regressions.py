#!/usr/bin/env python3
"""Run real hooks with JSON and isolated projects; builds use mock wrappers only."""
import hashlib
import json
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

ROOT = Path(__file__).resolve().parents[1]
ACTION = sys.argv.pop(1)
SQL_MESSAGES = {
    'naming': 'naming: expected V<version>__<description>.sql, U<version>__<description>.sql or R__<description>.sql',
    'loop': 'BEGIN/COMMIT with LOOP: verify transaction handling is intentional',
    'destructive': 'Destructive SQL: IF EXISTS does not protect data. Review backups, dependencies and rollback before applying. Never edit an already applied migration; add a new migration.',
}

def state_path(root):
    key = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / '.cache/spring-boot-tools' / f'build-{key}'


class HookTests(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory()
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name)
        self.addCleanup(shutil.rmtree, state_path(self.root), ignore_errors=True)

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

    def deny(self, output, decision='deny'):
        self.assertIsNotNone(output)
        self.assertEqual(output['hookSpecificOutput']['hookEventName'], 'PreToolUse')
        self.assertEqual(output['hookSpecificOutput']['permissionDecision'], decision)

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
        self.deny(self.invoke(self.root/'.env.production', 'DB_PASSWORD=hunter2\nOTHER=${ENV}'))
        self.deny(self.invoke(self.root/'.env', 'PASSWORD=dummy,real-secret'))
        self.assertIsNone(self.invoke(self.root/'Service.java', 'password: hunter2'))

    def test_secret_only_flags_introduced_literals(self):
        if ACTION != 'guard-secrets': self.skipTest('other hook')
        path = self.root / 'application.properties'
        path.write_text('server.port=8080\nspring.datasource.password=legacy-literal\n')
        self.assertIsNone(self.invoke(path, tool_input={'old_string':'8080', 'new_string':'8081'}))
        self.assertIsNone(self.invoke(path, path.read_text().replace('8080', '8081')))
        self.deny(self.invoke(path, tool_input={'old_string':'server.port=8080', 'new_string':'server.port=8080\nspring.mail.password=new-literal'}))
        self.deny(self.invoke(path, tool_input={'old_string':'legacy-literal', 'new_string':'other-literal'}))
        # A second copy of an existing literal is still new.
        self.deny(self.invoke(path, path.read_text() + 'spring.datasource.password=legacy-literal\n'))
        path = self.root / 'application.yml'
        path.write_text('password: safe\napi_key: ${API_KEY}')
        self.assertIsNone(self.invoke(path, tool_input={'old_string':'${API_KEY}', 'new_string':'dummy'}))

    def test_secret_test_configuration_asks(self):
        if ACTION != 'guard-secrets': self.skipTest('other hook')
        for name in ('src/test/resources/application.yml', 'application-test.yml', 'config/application-it-test.properties'):
            with self.subTest(name=name):
                self.deny(self.invoke(self.root / name, 'spring:\n  datasource:\n    password: test'), decision='ask')
        self.assertIsNone(self.invoke(self.root / 'src/test/resources/application.yml', 'password: ${DB_PASSWORD}'))
        self.deny(self.invoke(self.root / 'src/main/resources/application.yml', 'password: test'))

    def test_secret_names_files_and_separators(self):
        if ACTION != 'guard-secrets': self.skipTest('other hook')
        cases = (('application.yml', 'spring:\n  cloud:\n    aws:\n      credentials:\n        secret-key: wJalrXUtnFEMI/K7MDENG'),
                 ('application.yml', 'jwt.signing-key: c2VjcmV0LXNpZ25pbmc'),
                 ('application.properties', 'spring.datasource.password hunter2'),
                 ('application.properties', '  app.api-key\tliteral-key'),
                 ('bootstrap.yml', 'spring.cloud.config.password: hunter2'),
                 ('application.yml', 'password: ' + '${A:' * 2000 + 'hunter2' + '}' * 2000))
        for name, text in cases:
            with self.subTest(text=text[:40]):
                self.deny(self.invoke(self.root / name, text))
        for name, text in (('application.properties', 'spring.datasource.url jdbc:h2:mem:test'),
                           ('application.properties', 'app.note rotate the password monthly'),
                           ('application.yml', 'jwt.signing-key: classpath:jwt.key'),
                           ('application.yml', 'password: ${A:${B:${C}}}')):
            with self.subTest(text=text):
                self.assertIsNone(self.invoke(self.root / name, text))

    def test_guard_input_cost_is_linear(self):
        if ACTION not in ('guard-secrets', 'guard-ddl'): self.skipTest('other hook')
        for text in ('\n' * 200000 + 'x', ' ' + 'password' * 20000, 'password: x ' * 20000, 'ddl-auto: none ' * 20000):
            start = time.monotonic()
            self.invoke(self.root / 'application.yml', text)
            self.assertLess(time.monotonic() - start, 5, text[:20])

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

    def test_ddl_documents_keys_and_values(self):
        if ACTION != 'guard-ddl': self.skipTest('other hook')
        yaml, properties = self.root / 'application.yml', self.root / 'application.properties'
        for path, text in (
                (yaml, 'spring.config.activate.on-profile: dev\n--- # prod\nspring.jpa.hibernate.ddl-auto: update'),
                (yaml, 'spring:\n  config:\n    activate:\n      on-profile: dev\n--- # prod\nspring:\n  jpa:\n    hibernate:\n      ddl-auto: create'),
                (yaml, 'spring.config.activate.on-profile: dev\n...\nddl-auto: update'),
                (yaml, 'spring.config.activate.on-profile: dev\n  ---\nddl-auto: update'),
                (yaml, 'spring:\n  jpa:\n    properties:\n      hibernate:\n        hbm2ddl:\n          auto: create'),
                (properties, 'spring.jpa.properties.hibernate.hbm2ddl.auto=create-drop'),
                (properties, 'spring.jpa.properties.jakarta.persistence.schema-generation.database.action=drop-and-create'),
                (properties, 'spring.jpa.hibernate.ddl-auto=drop'),
                (properties, 'spring.jpa.hibernate.ddl-auto=create-only'),
                (properties, 'spring.jpa.hibernate.ddl-auto=truncate'),
                (properties, 'spring.jpa.hibernate.ddlAuto=update'),
                (properties, 'spring.jpa.hibernate.ddl-auto create'),
                (properties, 'spring.config.activate.on-profile=dev\n---\nspring.jpa.hibernate.ddl-auto=update')):
            with self.subTest(text=text):
                self.deny(self.invoke(path, text))
        for path, text in ((yaml, 'spring.config.activate.on-profile: dev\n# ---\nddl-auto: update'),
                           (properties, 'spring.config.activate.on-profile=dev\nspring.jpa.hibernate.ddl-auto=create-drop\n#---\nspring.jpa.hibernate.ddl-auto=validate'),
                           (properties, 'spring.jpa.properties.jakarta.persistence.schema-generation.database.action=none')):
            with self.subTest(text=text):
                self.assertIsNone(self.invoke(path, text))

    def test_test_paths_are_relative_to_the_project(self):
        if ACTION not in ('guard-ddl', 'guard-secrets'): self.skipTest('other hook')
        project = self.root / 'src/test/shop'  # checkout under a directory named src/test
        (project / 'src/main/resources').mkdir(parents=True)
        (project / 'pom.xml').write_text('<project/>')
        text = 'spring:\n  jpa:\n    hibernate:\n      ddl-auto: create-drop\n  datasource:\n    password: hunter2\n'
        self.deny(self.invoke(project / 'src/main/resources/application.yml', text))
        output = self.invoke(project / 'src/test/resources/application.yml', text)
        if ACTION == 'guard-ddl':
            self.assertIsNone(output)
        else:
            self.deny(output, decision='ask')
        loose = self.root / 'src/test/notes/application.yml'  # no build file: relative to CLAUDE_PROJECT_DIR
        output = self.invoke(loose, text)
        if ACTION == 'guard-ddl':
            self.assertIsNone(output)
        else:
            self.deny(output, decision='ask')

    def test_ddl_test_configuration_and_unrelated_edits(self):
        if ACTION != 'guard-ddl': self.skipTest('other hook')
        for name in ('src/test/resources/application.yml', 'application-test.properties', 'src/main/resources/application-test-h2.yml'):
            with self.subTest(name=name):
                self.assertIsNone(self.invoke(self.root / name, 'spring.jpa.hibernate.ddl-auto: create-drop'))
        path = self.root / 'application.properties'
        path.write_text('server.port=8080\nspring.jpa.hibernate.ddl-auto=update\n')
        self.assertIsNone(self.invoke(path, tool_input={'old_string':'8080', 'new_string':'8081'}))
        self.deny(self.invoke(path, tool_input={'old_string':'update', 'new_string':'create'}))

    def test_stop_batches_edits_without_pending_markers(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        self.wrapper()
        file = self.root/'src/main/java/com/example/App.java'
        file.parent.mkdir(parents=True)
        for index in range(3):
            file.write_text('class App { int value = ' + str(index) + '; }')
        self.assertFalse((self.root/'target/calls').exists())
        self.assertIsNone(self.invoke())
        self.assertEqual((self.root/'target/calls').read_text().splitlines(), ['-B -ntp verify'])
        self.assertIsNone(self.invoke())
        self.assertEqual(len((self.root/'target/calls').read_text().splitlines()), 1)
        file.write_text('class App { int changed; }')
        self.assertIsNone(self.invoke())
        self.assertEqual(len((self.root/'target/calls').read_text().splitlines()), 2)

    def test_verify(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        self.assertIsNone(self.invoke())
        wrapper = self.wrapper(gradle=True, code=4)
        output = self.invoke()
        self.assertEqual(output['decision'], 'block')
        self.assertIn('check (exit 4)', output['reason'])
        self.assertEqual(len((self.root/'target/calls').read_text().splitlines()), 1)
        # After a Stop continuation, verify again but report without blocking (no loop).
        output = self.invoke(stop_hook_active=True)
        self.assertNotIn('decision', output)
        self.assertIn('check (exit 4)', output['systemMessage'])
        self.assertEqual(len((self.root/'target/calls').read_text().splitlines()), 2)
        # A failed check is never reusable.
        self.assertEqual(self.invoke()['decision'], 'block')
        self.assertEqual(len((self.root/'target/calls').read_text().splitlines()), 3)
        wrapper.write_text('#!/bin/sh\necho warning\nexit 0\n')
        self.assertIsNone(self.invoke(stop_hook_active=True))
        self.assertIsNone(self.invoke())

    def test_stop_reports_skipped_discovery(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        self.wrapper(code=1)
        (self.root/'pom.xml').write_text('<!DOCTYPE project><project/>')
        self.assertIn('Unsafe or oversized POM', self.invoke()['systemMessage'])
        self.assertFalse((self.root/'target/calls').exists())

    def test_stop_never_runs_untrusted_ancestor_wrapper(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        shared = self.root / 'shared'
        victim = shared / 'victim'
        victim.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, state_path(victim), ignore_errors=True)
        sentinel = self.root / 'planted'
        (shared / 'pom.xml').write_text('<project><modules><module>victim</module></modules></project>')
        (shared / 'mvnw').write_text(f'#!/bin/sh\ntouch "{sentinel}"\n')
        shared.chmod(0o1777)
        self.addCleanup(shared.chmod, 0o755)
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(victim))
        run = lambda: subprocess.run(['bash', str(ROOT / 'hooks/scripts/verify-on-stop.sh')], input='{}',
                                     capture_output=True, text=True, env=env)
        self.assertIn('untrusted ancestor', json.loads(run().stdout)['systemMessage'])
        (victim / 'pom.xml').write_text('<project/>')
        (victim / 'mvnw').write_text('#!/bin/sh\nexit 0\n')
        (victim / 'mvnw').chmod(0o755)
        # Verified alone, and the refused reactor is reported rather than skipped silently.
        warning = json.loads(run().stdout)['systemMessage']
        self.assertIn(f'Ignored enclosing Maven reactor {shared}', warning)
        self.assertIn('chmod go-w', warning)
        self.assertFalse(sentinel.exists())

    def test_stop_hangup_and_kill_never_orphan_build(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        signals = [signal.SIGHUP] + ([signal.SIGKILL] if sys.platform.startswith('linux') else [])
        wrapper = self.wrapper()
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root))
        for number in signals:
            with self.subTest(signal=number):
                (self.root/'target/calls').unlink(missing_ok=True)
                (self.root/'input.txt').write_text(str(number))  # defeat reuse
                wrapper.write_text('#!/bin/sh\necho started >> target/calls\nsleep 1\necho survived > target/orphan\n')
                proc = subprocess.Popen(['bash', str(ROOT / 'hooks/scripts/verify-on-stop.sh')], stdin=subprocess.PIPE,
                                        stdout=subprocess.DEVNULL, env=env, start_new_session=True)
                proc.stdin.write(b'{}')
                proc.stdin.close()
                deadline = time.monotonic() + 5
                while not (self.root/'target/calls').exists():
                    if time.monotonic() >= deadline: self.fail('hook build did not start')
                    time.sleep(.02)
                os.killpg(proc.pid, number)
                proc.wait(timeout=5)
                time.sleep(1.5)
                self.assertFalse((self.root/'target/orphan').exists())

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

    def test_sql_policy_corpus(self):
        if ACTION != 'lint-migration': self.skipTest('other hook')
        directory = self.root/'db/migration'
        directory.mkdir(parents=True)
        corpus = json.loads((ROOT/'tests/project-runtime/sql-corpus.json').read_text())
        for case in corpus:
            with self.subTest(case=case['id']):
                path = directory/case['name']
                path.write_text(case['sql'])
                output = self.invoke(path)
                if case['hook']:
                    message = self.context(output)
                    expected = [SQL_MESSAGES[code] for code in case['hook']]
                    self.assertEqual(message, path.name + ': ' + '\n'.join(expected))
                else:
                    self.assertIsNone(output)

if __name__ == '__main__':
    methods = {
        'guard-secrets': ['test_secret', 'test_secret_only_flags_introduced_literals', 'test_secret_test_configuration_asks',
                          'test_secret_names_files_and_separators', 'test_guard_input_cost_is_linear',
                          'test_test_paths_are_relative_to_the_project'],
        'guard-ddl': ['test_ddl', 'test_ddl_documents_keys_and_values', 'test_ddl_test_configuration_and_unrelated_edits',
                      'test_guard_input_cost_is_linear', 'test_test_paths_are_relative_to_the_project'],
        'verify-on-stop': ['test_stop_batches_edits_without_pending_markers', 'test_verify', 'test_verify_maven', 'test_mcp_verification_reused_by_stop', 'test_source_packages_named_build_target_docs', 'test_stop_sigterm_kills_child_session',
                           'test_stop_reports_skipped_discovery', 'test_stop_never_runs_untrusted_ancestor_wrapper', 'test_stop_hangup_and_kill_never_orphan_build'],
        'lint-migration': ['test_lint', 'test_sql_policy_corpus'],
    }
    result = unittest.TextTestRunner().run(unittest.TestSuite(HookTests(name) for name in ['test_diagnostics', *methods[ACTION]]))
    sys.exit(not result.wasSuccessful())
