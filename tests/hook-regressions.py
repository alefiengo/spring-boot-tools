#!/usr/bin/env python3
"""Run real hooks with JSON and isolated projects; builds use mock wrappers only."""
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ACTION = sys.argv.pop(1)
SQL_MESSAGES = {
    'naming': 'naming: expected V<version>__<description>.sql, U<version>__<description>.sql or R__<description>.sql',
    'loop': 'BEGIN/COMMIT with LOOP: verify transaction handling is intentional',
    'destructive': 'Destructive SQL: IF EXISTS does not protect data. Review backups, dependencies and rollback before applying. Never edit an already applied migration; add a new migration.',
}

def load_hook_runtime():
    sys.dont_write_bytecode = True  # keep the shipped hooks directory free of caches
    spec = importlib.util.spec_from_file_location('hook_runtime', ROOT / 'hooks/scripts/hook-runtime.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        options = extra.pop('env', {})
        args = {}
        if file is not None:
            args['file_path'] = str(file)
        if content is not None:
            args['content'] = content
        args.update(extra.pop('tool_input', {}))
        data = {'tool_input': args, **extra}
        env = {key: value for key, value in os.environ.items() if not key.startswith('CLAUDE_PLUGIN_OPTION_') and key != 'CLAUDE_ENV_FILE'}
        env.update(options, CLAUDE_PROJECT_DIR=str(self.root))
        result = subprocess.run(['bash', str(ROOT / 'hooks/scripts' / (action+'.sh'))],
                                input=json.dumps(data), capture_output=True, text=True, env=env, cwd=self.root, timeout=120)
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
        self.deny(self.invoke(self.root/'.env.production', 'DB_PASSWORD=hunter2\nOTHER=${ENV}'), decision='ask')
        self.deny(self.invoke(self.root/'.env', 'PASSWORD=dummy,real-secret'), decision='ask')
        self.assertIsNone(self.invoke(self.root/'Service.java', 'password: hunter2'))

    def test_secret_local_env_files_ask(self):
        if ACTION != 'guard-secrets': self.skipTest('other hook')
        # The exact .env the spring-boot-docker skill recommends creating (gitignored, local-only).
        skill_env = 'POSTGRES_USER=dev\nPOSTGRES_PASSWORD=dev-password\n'
        for name in ('.env', '.env.local', '.env.production', 'db.env'):
            with self.subTest(name=name):
                output = self.invoke(self.root / name, skill_env)
                self.deny(output, decision='ask')
                self.assertIn('gitignored', output['hookSpecificOutput']['permissionDecisionReason'])
        # Committed templates and other committed files keep deny for real-looking values.
        for name in ('.env.example', '.env.sample', '.env.template', '.env.local.example', 'app.env.example'):
            with self.subTest(name=name):
                self.deny(self.invoke(self.root / name, skill_env))
        self.deny(self.invoke(self.root / '.envrc', 'export DB_PASSWORD=hunter2'))
        self.deny(self.invoke(self.root / 'src/main/resources/application.yml', 'password: dev-password'))
        for name in ('.env', '.env.example'):
            with self.subTest(name=name, placeholder=True):
                self.assertIsNone(self.invoke(self.root / name, 'POSTGRES_USER=\nPOSTGRES_PASSWORD=\nAPI_TOKEN=<your-token>\n'))

    def test_secret_quotes_blocks_and_references(self):
        if ACTION != 'guard-secrets': self.skipTest('other hook')
        # dotenv keeps single-quoted values literally, so '${VAR}' is not a placeholder there.
        for name, decision in (('.env', 'ask'), ('.env.example', 'deny'), ('db.env', 'ask')):
            with self.subTest(name=name):
                self.deny(self.invoke(self.root / name, "DB_PASSWORD='${DB_PASSWORD}'"), decision=decision)
                self.deny(self.invoke(self.root / name, "DB_PASSWORD='${DB_PASSWORD:-}' # literal"), decision=decision)
        for name, text in (('.env', 'DB_PASSWORD="${DB_PASSWORD}"'), ('.env', 'DB_PASSWORD=${DB_PASSWORD}'),
                           ('.env', "DB_PASSWORD=''"), ('.env', "API_KEY='<your-key>'"),
                           ('docker-compose.yml', "services:\n  db:\n    environment:\n      POSTGRES_PASSWORD: '${POSTGRES_PASSWORD}'"),
                           ('application.yml', "password: '${DB_PASSWORD}'")):
            with self.subTest(text=text):
                self.assertIsNone(self.invoke(self.root / name, text))
        yaml = self.root / 'application.yml'
        # A block scalar indicator on the line after the key is flagged like 'password: |'.
        for text in ('password:\n  |\n    hunter2', 'spring:\n  password:\n    >-\n      hunter2', 'secret: |\n  hunter2'):
            with self.subTest(text=text):
                self.deny(self.invoke(yaml, text))
        # Resource references are exempt on the next line as on the same line.
        for text in ('credentials: classpath:creds.json', 'credentials:\n  classpath:creds.json',
                     'jwt:\n  private-key:\n    file:/etc/keys/jwt.pem', 'signing-key:\n  "classpath:jwt.key"'):
            with self.subTest(text=text):
                self.assertIsNone(self.invoke(yaml, text))
        self.deny(self.invoke(yaml, 'credentials:\n  literal-credential'))

    def test_secret_urls_tokens_and_compose(self):
        if ACTION != 'guard-secrets': self.skipTest('other hook')
        yaml, properties = self.root / 'application.yml', self.root / 'application.properties'
        aws = 'AKIA' + 'IOSFODNN7EXAMPLE'  # split so the source itself holds no token literal
        github = 'ghp_' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8'
        denied = (
            (properties, 'spring.datasource.url=jdbc:postgresql://admin:S3cr3tPass@db:5432/app'),
            (yaml, 'spring:\n  rabbitmq:\n    addresses: amqp://app:rabbit-pass@broker:5672'),
            (yaml, 'spring.data.redis.url: redis://:redis-pass@cache:6379'),
            (yaml, 'spring.data.mongodb.uri: "mongodb+srv://app:mongo-pass@cluster0.example.net/db"'),
            (properties, 'app.remote=https://deploy:http-pass@repo.example.com/path'),
            (properties, 'spring.datasource.url=jdbc:postgresql://${DB_USER:app}:literal-pass@db/app'),
            (yaml, 'cloud:\n  aws:\n    access-key-id: ' + aws),
            (properties, 'app.github=' + github),
            (properties, 'app.github=github_pat_' + '11ABCDEFG0123456789_abcdefghijklmnopqrstuv'),
            (yaml, 'slack:\n  bot: xoxb-' + '123456789012-abcdefghijklmnop'),
            (yaml, '# leaked: ' + aws),
            (properties, 'jwt.key=-----BEGIN RSA PRIVATE KEY-----'),
            (yaml, 'jwt:\n  key: |\n    -----BEGIN PRIVATE KEY-----\n    MIIEvQIBADANBg\n    -----END PRIVATE KEY-----'),
            (yaml, 'spring:\n  datasource:\n    password:\n      hunter2'),
            (yaml, 'password:\n  # comment\n  "quoted-secret"'),
            (self.root / 'docker-compose.yml', 'services:\n  db:\n    environment:\n      POSTGRES_PASSWORD: hunter2'),
            (self.root / 'docker-compose.prod.yaml', 'services:\n  db:\n    environment:\n      - POSTGRES_PASSWORD=hunter2'),
            (self.root / 'compose.yaml', 'services:\n  db:\n    environment:\n      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-hunter2}'),
            (self.root / 'compose.override.yml', 'services:\n  app:\n    environment:\n      SPRING_DATASOURCE_URL: jdbc:mysql://root:hunter2@db/app'),
        )
        for path, text in denied:
            with self.subTest(text=text):
                self.deny(self.invoke(path, text))
        compose = (ROOT / 'skills/spring-boot-docker/SKILL.md').read_text().split('## docker-compose.yml')[1].split('```yaml\n')[1].split('```')[0]
        allowed = (
            (properties, 'spring.datasource.url=jdbc:postgresql://${DB_USER}:${DB_PASSWORD}@db:5432/app'),
            (properties, 'spring.datasource.url=jdbc:postgresql://${DB_USER:app}:${DB_PASSWORD}@db/app'),
            (properties, 'spring.datasource.url=jdbc:postgresql://db:5432/app'),
            (yaml, 'url: amqp://app:<password>@broker:5672'),
            (yaml, 'url: redis://:changeme@cache:6379'),
            (yaml, 'url: https://user@repo.example.com/path'),
            (yaml, '# jdbc:postgresql://admin:example-pass@db/app'),
            (yaml, 'cloud:\n  aws:\n    access-key-id: ${AWS_ACCESS_KEY_ID}'),
            (yaml, 'cloud:\n  aws:\n    access-key-id: AKIAXXXXXXXXXXXXXXXX'),
            (yaml, 'app:\n  github-token: ${GITHUB_TOKEN}'),
            (yaml, 'jwt:\n  private-key: classpath:jwt.pem'),
            (yaml, 'spring:\n  datasource:\n    password:\n      ${DB_PASSWORD}'),
            (yaml, 'spring:\n  datasource:\n    password:\n    username: app'),
            (yaml, 'spring:\n  security:\n    password:\n      encoder: bcrypt'),
            (yaml, 'password:\n  - first'),
            (self.root / 'docker-compose.yml', compose),
            (self.root / 'docker-compose.yml', 'services:\n  db:\n    environment:\n      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}'),
            (self.root / 'compose.yaml', 'services:\n  db:\n    environment:\n      - POSTGRES_PASSWORD=$POSTGRES_PASSWORD\n      - REDIS_PASSWORD=${REDIS_PASSWORD:?set it}\n      - API_TOKEN=${API_TOKEN:-}'),
            (self.root / 'composer.yml', 'password: hunter2'),
        )
        for path, text in allowed:
            with self.subTest(text=text[:60]):
                self.assertIsNone(self.invoke(path, text))
        self.assertIn('POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}', compose)  # the skill snippet was found
        # Only literals the edit introduces are flagged, for the new patterns as well.
        properties.write_text('spring.datasource.url=jdbc:postgresql://admin:S3cr3tPass@db:5432/app\nserver.port=8080\n')
        self.assertIsNone(self.invoke(properties, tool_input={'old_string': '8080', 'new_string': '8081'}))
        self.deny(self.invoke(properties, tool_input={'old_string': 'S3cr3tPass', 'new_string': 'other-pass'}))
        self.deny(self.invoke(self.root / 'src/test/resources/application.yml', 'url: jdbc:h2://sa:test-pass@mem/db'), decision='ask')

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
        for text in ('\n' * 200000 + 'x', ' ' + 'password' * 20000, 'password: x ' * 20000, 'ddl-auto: none ' * 20000,
                     'a://b:' * 40000, 'x://' + '${' * 100000 + ':p@', 'AKIA' * 50000):
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

    def calls(self):
        calls = self.root / 'target/calls'
        return calls.read_text().splitlines() if calls.exists() else []

    def start_session(self, session='session-1', source='startup', **extra):
        return self.invoke(action='session-baseline', session_id=session, source=source, hook_event_name='SessionStart', **extra)

    def test_stop_skips_when_unchanged_since_session_start(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        self.wrapper()
        source = self.root / 'src/main/java/App.java'
        source.parent.mkdir(parents=True)
        source.write_text('class App {}')
        # SessionStart-only variables must not make the baseline differ from Stop.
        self.assertIsNone(self.start_session(env={'CLAUDE_ENV_FILE': str(self.root / 'session.env')}))
        self.assertIsNone(self.invoke(session_id='session-1'))
        self.assertEqual(self.calls(), [])
        # Another session without a baseline keeps the previous behavior.
        self.assertIsNone(self.invoke(session_id='other-session'))
        self.assertEqual(self.calls(), ['-B -ntp verify'])
        source.write_text('class App { int changed; }')
        self.assertIsNone(self.invoke(session_id='session-1'))
        self.assertEqual(len(self.calls()), 2)
        # Reverting to the session start contents skips again.
        source.write_text('class App {}')
        self.assertIsNone(self.invoke(session_id='session-1'))
        self.assertEqual(len(self.calls()), 2)

    def test_session_baseline_is_kept_and_only_recorded_for_new_conversations(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        self.wrapper()
        source = self.root / 'src/main/java/App.java'
        source.parent.mkdir(parents=True)
        source.write_text('class App {}')
        self.assertIsNone(self.start_session())
        source.write_text('class App { int edited; }')
        # Compaction or a repeated start of the same session never moves the baseline to unverified edits.
        self.assertIsNone(self.start_session(source='compact'))
        self.assertIsNone(self.start_session(source='startup'))
        self.assertIsNone(self.start_session(source='clear'))
        self.assertIsNone(self.invoke(session_id='session-1'))
        self.assertEqual(len(self.calls()), 1)
        # Resume, fork and compaction continue a conversation: no baseline, always verify.
        for name in ('resume', 'fork', 'compact'):
            with self.subTest(source=name):
                self.assertIsNone(self.start_session(name, source=name))
                (self.root / 'input.txt').write_text(name)  # defeat reuse
                before = len(self.calls())
                self.assertIsNone(self.invoke(session_id=name))
                self.assertEqual(len(self.calls()), before + 1)
        # A conversation started by /clear gets a fresh baseline like startup.
        self.assertIsNone(self.start_session('cleared', source='clear'))
        before = len(self.calls())
        self.assertIsNone(self.invoke(session_id='cleared'))
        self.assertEqual(len(self.calls()), before)
        # Not a project, or an input that cannot be fingerprinted: silent, no baseline.
        (self.root / 'linked.txt').symlink_to(self.root / 'input.txt')
        self.assertIsNone(self.start_session('symlinked'))
        self.assertIsNone(self.invoke(session_id='symlinked'))
        self.assertEqual(len(self.calls()), before + 1)
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        result = subprocess.run(['bash', str(ROOT / 'hooks/scripts/session-baseline.sh')], capture_output=True, text=True,
                                input=json.dumps({'session_id': 'x', 'source': 'startup'}),
                                env=dict(os.environ, CLAUDE_PROJECT_DIR=empty.name), cwd=empty.name)
        self.assertEqual((result.returncode, result.stdout), (0, ''))

    def test_session_baselines_are_bounded(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        self.wrapper()
        hooks = load_hook_runtime()
        with mock.patch.object(hooks, 'SESSION_BASELINES', 3), mock.patch.dict(os.environ, {'CLAUDE_PROJECT_DIR': str(self.root)}):
            for index in range(6):
                with redirect_stdout(io.StringIO()) as output:
                    hooks.record_baseline({'session_id': f's{index}', 'source': 'startup'})
                self.assertEqual(output.getvalue(), '')
            folder = state_path(self.root) / 'sessions'
            self.assertEqual(len(list(folder.iterdir())), 3)
            self.assertTrue(hooks.baseline_file(str(self.root), 's5').exists())
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)

    def test_stop_verification_modes(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        self.wrapper()
        self.assertIsNone(self.invoke(env={'CLAUDE_PLUGIN_OPTION_STOP_VERIFICATION': 'off'}))
        self.assertEqual(self.calls(), [])
        # compile mode runs the compile task and never satisfies the full verification gate.
        self.assertIsNone(self.invoke(env={'CLAUDE_PLUGIN_OPTION_STOP_VERIFICATION': 'compile'}))
        self.assertEqual(self.calls(), ['-B -ntp compile'])
        self.assertIsNone(self.invoke(env={'CLAUDE_PLUGIN_OPTION_STOP_VERIFICATION': 'compile'}))
        self.assertIsNone(self.invoke(env={'CLAUDE_PLUGIN_OPTION_STOP_VERIFICATION': ''}))
        self.assertEqual(self.calls(), ['-B -ntp compile', '-B -ntp compile', '-B -ntp verify'])
        # An invalid value falls back to verify with a notice in the same JSON object.
        (self.root / 'input.txt').write_text('defeat reuse')
        output = self.invoke(env={'CLAUDE_PLUGIN_OPTION_STOP_VERIFICATION': 'fast', 'CLAUDE_PLUGIN_OPTION_STOP_TIMEOUT_SECONDS': '5'})
        self.assertIn("stop_verification 'fast'", output['systemMessage'])
        self.assertIn("stop_timeout_seconds '5'", output['systemMessage'])
        self.assertEqual(self.calls()[-1], '-B -ntp verify')
        (self.root / 'pom.xml').unlink()
        (self.root / 'build.gradle.kts').write_text('')
        gradle = self.root / 'gradlew'
        gradle.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> target/calls\nexit 3\n')
        gradle.chmod(0o755)
        output = self.invoke(env={'CLAUDE_PLUGIN_OPTION_STOP_VERIFICATION': 'compile'})
        self.assertEqual(self.calls()[-1], '--console=plain --no-daemon classes')
        self.assertEqual(output['decision'], 'block')
        self.assertIn('classes (exit 3)', output['reason'])
        # Invalid configuration plus a failure still produces one JSON object with both.
        output = self.invoke(env={'CLAUDE_PLUGIN_OPTION_STOP_VERIFICATION': 'bogus'})
        self.assertEqual(output['decision'], 'block')
        self.assertIn('check (exit 3)', output['reason'])
        self.assertIn('bogus', output['systemMessage'])

    def stop_with_stubbed_runtime(self, result, options=None):
        hooks = load_hook_runtime()
        (self.root / 'pom.xml').write_text('')
        requests = []
        def run_build(request, own_process_group=False):
            requests.append(request)
            return dict({'ok': True, 'exitCode': 0, 'stdout': '', 'stderr': '', 'tasks': [request['task']],
                         'timedOut': False, 'outputLimitExceeded': False, 'inputsChanged': False}, **result)
        env = {key: value for key, value in os.environ.items() if not key.startswith('CLAUDE_PLUGIN_OPTION_')}
        env.update(options or {}, CLAUDE_PROJECT_DIR=str(self.root))
        with mock.patch.object(hooks, 'run_build', run_build), mock.patch.dict(os.environ, env, clear=True), \
                redirect_stdout(io.StringIO()) as output:
            hooks.build('Stop', {})
        text = output.getvalue()
        return requests, json.loads(text) if text else None

    def test_stop_timeout_reaches_runtime(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        for value, expected in ((None, 300000), ('', 300000), ('450', 450000), ('600', 600000), ('30.5', 30500),
                                ('29', 300000), ('601', 300000), ('nan', 300000), ('soon', 300000)):
            with self.subTest(value=value):
                options = {} if value is None else {'CLAUDE_PLUGIN_OPTION_STOP_TIMEOUT_SECONDS': value}
                requests, output = self.stop_with_stubbed_runtime({}, options)
                self.assertEqual(requests, [{'projectRoot': str(self.root), 'task': 'verify', 'timeoutMs': expected}])
                if expected == 300000 and value:
                    self.assertIn('stop_timeout_seconds', output['systemMessage'])
                else:
                    self.assertIsNone(output)
        config = json.loads((ROOT / 'hooks/hooks.json').read_text())['hooks']
        stop = config['Stop'][0]['hooks'][0]
        self.assertGreaterEqual(stop['timeout'], 630)  # outlives the 600 s maximum build
        start = config['SessionStart'][0]
        self.assertEqual(start['matcher'], 'startup|clear')
        self.assertNotIn('user_config', json.dumps(config))  # shell-form hooks reject ${user_config.*}

    def test_stop_names_changed_inputs(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        _, output = self.stop_with_stubbed_runtime({'inputsChanged': True, 'changedInputs': ['logs/app.log', 'db/h2.mv.db'],
                                                    'changedInputsTruncated': True})
        self.assertEqual(output['decision'], 'block')
        self.assertIn('Build inputs changed during verification', output['reason'])
        self.assertIn('Changed: logs/app.log, db/h2.mv.db (and more)', output['reason'])
        self.assertIn('If these are build outputs, add them to .gitignore', output['reason'])
        _, output = self.stop_with_stubbed_runtime({'inputsChanged': True, 'changedInputs': ['bad\nname'], 'changedInputsTruncated': False})
        self.assertIn('Changed: bad?name.', output['reason'])
        self.assertNotIn('(and more)', output['reason'])
        # Without the runtime fields (older runtime) the previous message is kept.
        _, output = self.stop_with_stubbed_runtime({'inputsChanged': True})
        self.assertEqual(output['reason'], 'Build inputs changed during verification. Run verification again on the final source contents.')
        _, output = self.stop_with_stubbed_runtime({'inputsChanged': True, 'changedInputs': 'logs/app.log'})
        self.assertNotIn('Changed:', output['reason'])

    def test_stop_build_writing_inputs_blocks_with_message(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        wrapper = self.wrapper()
        wrapper.write_text('#!/bin/sh\nmkdir -p logs\necho line >> logs/app.log\necho "$*" >> target/calls\n')
        output = self.invoke()
        self.assertEqual(output['decision'], 'block')
        self.assertIn('Build inputs changed during verification', output['reason'])

    def controlled_wrapper(self):
        """Mock build controlled from outside the project, so switching outcomes keeps the fingerprint."""
        control = tempfile.TemporaryDirectory()
        self.addCleanup(control.cleanup)
        wrapper = self.wrapper()
        wrapper.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> target/calls\n'
                           f'[ -f {control.name}/log ] && {{ mkdir -p logs; echo line >> logs/app.log; }}\n'
                           f'[ -f {control.name}/fail ] && exit 1\n'
                           f'case "$*" in *verify*) [ -f {control.name}/fail-verify ] && exit 1;; esac\nexit 0\n')
        return Path(control.name)

    def test_session_baseline_never_hides_a_known_failure(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        control = self.controlled_wrapper()
        record = state_path(self.root) / 'stop-unverified.json'
        (control / 'fail').touch()
        source = self.root / 'src/main/java/App.java'
        source.parent.mkdir(parents=True)
        source.write_text('class App { broken }')
        self.assertEqual(self.invoke(session_id='first')['decision'], 'block')
        self.assertEqual(len(self.calls()), 1)
        # New conversations on the same failing tree record it as their baseline,
        # yet Stop still runs and blocks again.
        for session, source_name in (('second', 'startup'), ('third', 'clear')):
            with self.subTest(source=source_name):
                self.assertIsNone(self.start_session(session, source=source_name))
                output = self.invoke(session_id=session)
                self.assertEqual(output['decision'], 'block')
                self.assertIn('exit 1', output['reason'])
        self.assertEqual(len(self.calls()), 3)
        # A non-blocking report after a Stop continuation is still a failure.
        self.assertIn('exit 1', self.invoke(session_id='third', stop_hook_active=True)['systemMessage'])
        self.assertEqual(len(self.calls()), 4)
        # A passing compile check does not clear a failed full verification.
        (control / 'fail').unlink()
        (control / 'fail-verify').touch()
        self.assertIsNone(self.invoke(session_id='third', env={'CLAUDE_PLUGIN_OPTION_STOP_VERIFICATION': 'compile'}))
        self.assertTrue(record.exists())
        self.assertEqual(self.invoke(session_id='third')['decision'], 'block')
        self.assertEqual(len(self.calls()), 6)
        # A passing verification clears the record; unchanged queries then skip again.
        (control / 'fail-verify').unlink()
        self.assertIsNone(self.invoke(session_id='third'))
        self.assertFalse(record.exists())
        self.assertIsNone(self.start_session('fourth'))
        self.assertIsNone(self.invoke(session_id='fourth'))
        self.assertIsNone(self.invoke(session_id='third'))
        self.assertEqual(len(self.calls()), 7)

    def test_session_baseline_never_hides_a_blocked_build(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        control = self.controlled_wrapper()
        (control / 'log').touch()
        self.assertEqual(self.invoke(session_id='first')['decision'], 'block')
        # The new session starts from the tree the blocked build left behind.
        (control / 'log').unlink()
        self.assertIsNone(self.start_session('second'))
        self.assertIsNone(self.invoke(session_id='second'))
        self.assertEqual(len(self.calls()), 2)
        self.assertIsNone(self.invoke(session_id='second'))
        self.assertEqual(len(self.calls()), 2)
        # Unusable records never allow a skip; a valid record for another tree does.
        hooks = load_hook_runtime()
        record = state_path(self.root) / 'stop-unverified.json'
        skip = lambda: hooks.unchanged_since_session_start({'session_id': 'second'}, str(self.root))
        self.assertTrue(skip())
        for content in ('not json', '[]', '{"fingerprint": "short"}', '{"a": "' + 'x' * 5000 + '"}'):
            with self.subTest(record=content[:20]):
                record.write_text(content)
                self.assertFalse(skip())
        record.write_text(json.dumps({'fingerprint': 'd' * 64, 'mode': 'verify'}))
        self.assertTrue(skip())
        record.unlink()
        os.mkfifo(record)
        self.assertFalse(skip())  # O_NONBLOCK: a FIFO neither blocks nor counts as a record
        record.unlink()
        record.mkdir()
        self.assertFalse(skip())


    def test_session_baseline_publish_is_atomic_without_replacement(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        self.wrapper()
        hooks = load_hook_runtime()
        first, second = 'a' * 64, 'b' * 64
        path = hooks.baseline_file(str(self.root), 'race')
        self.assertTrue(hooks.publish_baseline(path, 'race', first))
        self.assertFalse(hooks.publish_baseline(path, 'race', second))
        self.assertEqual(json.loads(path.read_text())['fingerprint'], first)
        # Concurrent SessionStarts both pass the existence check; exactly one publishes.
        barrier, values, results = threading.Barrier(4), iter('0123'), []
        publish = hooks.publish_baseline
        def fingerprint(root):
            value = next(values) * 64
            barrier.wait(timeout=10)
            return value
        def published(path, session, value):
            results.append((publish(path, session, value), value))
            return results[-1][0]
        with mock.patch.object(hooks, 'session_fingerprint', fingerprint), mock.patch.object(hooks, 'publish_baseline', published), \
                mock.patch.dict(os.environ, {'CLAUDE_PROJECT_DIR': str(self.root)}):
            threads = [threading.Thread(target=hooks.record_baseline, args=({'session_id': 'concurrent', 'source': 'startup'},))
                       for _ in range(4)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=10)
        winners = [value for ok, value in results if ok]
        self.assertEqual((len(results), len(winners)), (4, 1))
        stored = json.loads(hooks.baseline_file(str(self.root), 'concurrent').read_text())
        self.assertEqual(stored, {'session': 'concurrent', 'fingerprint': winners[0]})
        self.assertEqual([p.name for p in path.parent.iterdir() if p.name.startswith('baseline-')], [])

    def test_session_baseline_invalid_entries(self):
        if ACTION != 'verify-on-stop': self.skipTest('other hook')
        self.wrapper()
        hooks = load_hook_runtime()
        folder = hooks.baseline_file(str(self.root), 'x').parent
        cases = {'fifo': lambda p: os.mkfifo(p), 'directory': lambda p: p.mkdir(),
                 'garbage': lambda p: p.write_text('{not json'), 'oversized': lambda p: p.write_text('{"a": "' + 'x' * 5000 + '"}'),
                 'other-session': lambda p: p.write_text(json.dumps({'session': 'someone-else', 'fingerprint': 'c' * 64})),
                 'link': lambda p: p.symlink_to(folder / 'elsewhere.json')}
        for session, create in cases.items():
            with self.subTest(entry=session):
                create(hooks.baseline_file(str(self.root), session))
                (self.root / 'input.txt').write_text(session)  # defeat reuse
                # Never replaced and never blocking; a replaced entry would match and skip below.
                self.assertIsNone(self.start_session(session))
                before = len(self.calls())
                self.assertIsNone(self.invoke(session_id=session))
                self.assertEqual(len(self.calls()), before + 1)
        # Pruning touches only regular baseline files and stale baseline temporaries.
        stale, fresh, unrelated = folder / 'baseline-stale', folder / 'baseline-fresh', folder / 'notes.txt'
        for path in (stale, fresh, unrelated):
            path.write_text('{}')
        os.utime(stale, (time.time() - 7200, time.time() - 7200))
        with mock.patch.object(hooks, 'SESSION_BASELINES', 1), mock.patch.dict(os.environ, {'CLAUDE_PROJECT_DIR': str(self.root)}), \
                redirect_stdout(io.StringIO()):
            hooks.record_baseline({'session_id': 'newest', 'source': 'startup'})
        names = {path.name for path in folder.iterdir()}
        self.assertNotIn('baseline-stale', names)
        self.assertTrue({'baseline-fresh', 'notes.txt', hooks.baseline_file(str(self.root), 'newest').name,
                         hooks.baseline_file(str(self.root), 'fifo').name, hooks.baseline_file(str(self.root), 'directory').name,
                         hooks.baseline_file(str(self.root), 'link').name} <= names)
        self.assertEqual(len([n for n in names if hooks.BASELINE_NAME.fullmatch(n) and (folder / n).is_file() and not (folder / n).is_symlink()]), 1)

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
                          'test_test_paths_are_relative_to_the_project', 'test_secret_local_env_files_ask',
                          'test_secret_urls_tokens_and_compose', 'test_secret_quotes_blocks_and_references'],
        'guard-ddl': ['test_ddl', 'test_ddl_documents_keys_and_values', 'test_ddl_test_configuration_and_unrelated_edits',
                      'test_guard_input_cost_is_linear', 'test_test_paths_are_relative_to_the_project'],
        'verify-on-stop': ['test_stop_batches_edits_without_pending_markers', 'test_verify', 'test_verify_maven', 'test_mcp_verification_reused_by_stop', 'test_source_packages_named_build_target_docs', 'test_stop_sigterm_kills_child_session',
                           'test_stop_reports_skipped_discovery', 'test_stop_never_runs_untrusted_ancestor_wrapper', 'test_stop_hangup_and_kill_never_orphan_build',
                           'test_stop_skips_when_unchanged_since_session_start', 'test_session_baseline_is_kept_and_only_recorded_for_new_conversations',
                           'test_session_baselines_are_bounded', 'test_stop_verification_modes', 'test_stop_timeout_reaches_runtime',
                           'test_stop_names_changed_inputs', 'test_stop_build_writing_inputs_blocks_with_message',
                           'test_session_baseline_never_hides_a_known_failure', 'test_session_baseline_never_hides_a_blocked_build',
                           'test_session_baseline_publish_is_atomic_without_replacement', 'test_session_baseline_invalid_entries'],
        'lint-migration': ['test_lint', 'test_sql_policy_corpus'],
    }
    result = unittest.TextTestRunner().run(unittest.TestSuite(HookTests(name) for name in ['test_diagnostics', *methods[ACTION]]))
    sys.exit(not result.wasSuccessful())
