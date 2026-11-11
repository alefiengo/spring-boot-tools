const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const repo = path.resolve(__dirname, '../..');
const entry = path.join(repo, 'mcp-server/index.js');
const { removeBuildState, stateDir } = require('./build-state.cjs');

async function temporary(t) {
  const base = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'sbt-boundary-')));
  t.after(() => { removeBuildState(base); return fs.rm(base, { recursive: true, force: true }); });
  return base;
}
async function write(root, files) {
  for (const [name, content] of Object.entries(files)) {
    await fs.mkdir(path.dirname(path.join(root, name)), { recursive: true });
    await fs.writeFile(path.join(root, name), content, { mode: name === 'mvnw' ? 0o755 : 0o644 });
  }
}
// Real stdio client; `script` selects the server entry path (for symlink checks).
async function server(t, root, { script = entry, env = {} } = {}) {
  const child = spawn(process.execPath, [script], { cwd: root, env: { ...process.env, PROJECT_ROOT: root, ...env }, stdio: ['pipe', 'pipe', 'pipe'] });
  let serial = 0, stderr = '';
  const pending = new Map();
  child.stderr.on('data', data => { stderr += data; });
  readline.createInterface({ input: child.stdout }).on('line', line => {
    const message = JSON.parse(line);
    pending.get(message.id)?.(message);
  });
  const exited = new Promise(resolve => child.on('exit', (code, signal) => {
    for (const done of pending.values()) done({ error: { message: `server exited (${code ?? signal}): ${stderr}` } });
    resolve({ code, signal });
  }));
  t.after(async () => { if (child.exitCode === null && child.signalCode === null) { child.kill('SIGKILL'); await exited; } });
  const request = (method, params, timeout = 15000) => new Promise((resolve, reject) => {
    const id = ++serial;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} timed out: ${stderr}`)); }, timeout);
    pending.set(id, message => { clearTimeout(timer); pending.delete(id); message.error ? reject(new Error(message.error.message)) : resolve(message.result); });
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
  });
  await request('initialize', { protocolVersion: '2025-11-25', capabilities: {}, clientInfo: { name: 'boundary-regression', version: '1' } });
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
  return { child, exited, request, call: (name, args = {}, timeout) => request('tools/call', { name, arguments: args }, timeout) };
}
const value = result => JSON.parse(result.content[0].text);
const pom = '<project><groupId>app</groupId><artifactId>demo</artifactId><version>1</version></project>';
async function httpServer(t, handler) {
  const listener = http.createServer(handler);
  await new Promise((resolve, reject) => { listener.on('error', reject); listener.listen(0, '127.0.0.1', resolve); });
  t.after(() => listener.close());
  return `http://127.0.0.1:${listener.address().port}`;
}

test('an ancestor directory named src does not disable output and dependency exclusions', async t => {
  const root = path.join(await temporary(t), 'src', 'app');
  await write(root, {
    'pom.xml': pom,
    'src/main/java/Api.java': '@RestController class Api { @GetMapping("/real") void get() {} }',
    'src/main/java/com/example/target/Kept.java': '@RestController class Kept { @GetMapping("/kept") void get() {} }',
    'target/checkout/src/main/java/Api.java': '@RestController class Api { @GetMapping("/release-checkout") void get() {} }',
    'target/checkout/src/main/resources/db/migration/V1__copy.sql': 'DROP TABLE users;',
    'node_modules/pkg/src/main/java/X.java': '@RestController class X { @GetMapping("/dependency") void get() {} }',
  });
  const client = await server(t, root);
  assert.deepEqual(value(await client.call('openapi_endpoints')).endpoints.map(e => e.path).sort(), ['/kept', '/real']);
  const migrations = value(await client.call('check_migrations'));
  assert.deepEqual(migrations.migrations, []);
  assert.deepEqual(migrations.risks, []);
});

test('POM coordinates and versions remain declared strings', async t => {
  const root = await temporary(t);
  await write(root, { 'pom.xml': `<project><groupId>007</groupId><artifactId>demo</artifactId><version>1.10</version>
<parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId><version>4.0</version></parent>
<properties><kotlin.version>2.0</kotlin.version><lib.version>0x10</lib.version></properties>
<dependencies><dependency><groupId>g</groupId><artifactId>1e3</artifactId><version>3.10</version></dependency><dependency><groupId>g</groupId><artifactId>zero</artifactId><version>0</version></dependency></dependencies></project>` });
  const client = await server(t, root);
  const response = await client.call('analyze_project');
  assert.notEqual(response.isError, true, response.content[0].text);
  const data = value(response);
  assert.deepEqual(data.dependencies.map(d => [d.group, d.artifact, d.version]), [['g', '1e3', '3.10'], ['g', 'zero', '0']]);
  const versions = Object.fromEntries(data.versions.map(v => [v.component, v.version]));
  assert.equal(versions['007:demo'], '1.10');
  assert.equal(versions['org.springframework.boot:spring-boot-starter-parent'], '4.0');
  assert.equal(versions['kotlin.version'], '2.0');
  assert.equal(versions['lib.version'], '0x10');
});

test('JUnit CDATA may contain a DOCTYPE; declared entities are never expanded anywhere', async t => {
  const root = await temporary(t);
  await write(root, { 'pom.xml': pom, mvnw: `#!/bin/sh
mkdir -p target/surefire-reports
cat > target/surefire-reports/TEST-WebTest.xml <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="Web &amp; MVC" tests="1" failures="0" errors="0" skipped="0" time="0.1"><testcase classname="WebTest" name="renders"/><system-out><![CDATA[Body = <!DOCTYPE html><html><body>ok</body></html>]]></system-out></testsuite>
XML
` });
  const client = await server(t, root);
  const response = await client.call('run_tests');
  const data = value(response);
  assert.notEqual(response.isError, true, response.content[0].text);
  assert.deepEqual(data.reportErrors, []);
  assert.deepEqual(data.suites.map(s => [s.suite, s.tests]), [['Web & MVC', 1]]);
  // fast-xml-parser accepts a DOCTYPE inside an element; its entity must stay literal.
  await fs.writeFile(path.join(root, 'pom.xml'), '<project><a x="<!--"/><!DOCTYPE q [<!ENTITY e "INJECTED">]><artifactId b="-->">&e;</artifactId></project>');
  assert.doesNotMatch((await client.call('analyze_project')).content[0].text, /INJECTED/);
});

test('a plugin root reached through a symlink still starts the server', async t => {
  const base = await temporary(t);
  await fs.symlink(repo, path.join(base, 'linked-plugin'), 'dir');
  await write(base, { 'app/pom.xml': pom });
  const client = await server(t, path.join(base, 'app'), { script: path.join(base, 'linked-plugin/mcp-server/index.js') });
  assert.equal((await client.request('tools/list', {})).tools.length, 7);
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  test(`${signal} shutdown terminates a running build instead of orphaning it`, async t => {
    const root = await temporary(t);
    await write(root, { 'pom.xml': pom, mvnw: '#!/bin/sh\nmkdir -p target\ni=0\nwhile [ $i -lt 40 ]; do echo $i >> target/ticks; i=$((i+1)); sleep 0.1; done\ntouch target/completed\n' });
    const client = await server(t, root);
    const ticks = async () => (await fs.readFile(path.join(root, 'target/ticks'), 'utf8').catch(() => '')).split('\n').filter(Boolean).length;
    client.call('run_build', { task: 'compile' }).catch(() => {});
    for (let i = 0; i < 100 && await ticks() < 3; i++) await new Promise(resolve => setTimeout(resolve, 50));
    assert.ok(await ticks() >= 3, 'build started');
    client.child.kill(signal);
    const exit = await client.exited;
    assert.equal(exit.code, 128 + os.constants.signals[signal]);
    const afterExit = await ticks();
    await new Promise(resolve => setTimeout(resolve, 800));
    assert.ok(await ticks() <= afterExit + 1, 'build stopped with the server');
    assert.equal(await fs.stat(path.join(root, 'target/completed')).catch(() => null), null);
  });
}

test('blank-line-heavy sources stay linear; CRLF properties keep their port', async t => {
  const root = await temporary(t);
  const blank = '\n'.repeat(200_000);
  await write(root, {
    'pom.xml': pom,
    // Blank runs followed by non-matching text are the quadratic case for `^\s*`.
    'src/main/java/Api.java': `@RestController class Api {\n  // @GetMapping("/commented")\n@GetMapping("/live") void get() {}${blank}}`,
    'src/main/resources/application.properties': `server.port=9093\r\n${blank}spring.application.name=demo\r\n`,
  });
  const client = await server(t, root);
  const start = Date.now();
  const project = value(await client.call('analyze_project'));
  const endpoints = value(await client.call('openapi_endpoints'));
  assert.ok(Date.now() - start < 5000, `took ${Date.now() - start} ms`);
  assert.deepEqual(project.ports, [9093]);
  assert.deepEqual(endpoints.endpoints.map(e => e.path), ['/live']);
});

test('HTTP inspection targets only the local application and reports fetch causes', async t => {
  const baseUrl = await httpServer(t, (_, res) => { res.writeHead(302, { Location: 'http://169.254.169.254/' }); res.end(); });
  const root = await temporary(t);
  await write(root, { 'pom.xml': pom });
  const client = await server(t, root);
  for (const remote of ['http://192.0.2.1:8080', 'http://10.0.0.1', 'http://example.com', 'http://[::ffff:7f00:1]:8080']) {
    const start = Date.now();
    for (const [tool, args] of [['actuator_health', { baseUrl: remote }], ['openapi_endpoints', { mode: 'runtime', baseUrl: remote }]]) {
      const response = await client.call(tool, args);
      assert.equal(response.isError, true, remote);
      assert.match(response.content[0].text, /local application/, remote);
    }
    assert.ok(Date.now() - start < 1000, 'rejected before any network attempt');
  }
  const redirect = value(await client.call('actuator_health', { baseUrl })).results[0];
  assert.match(redirect.error, /unexpected redirect/);
  assert.match(redirect.diagnosis, /redirects are not followed/);
  // Accepted loopback forms reach fetch: [::1] and 127.0.0.0/8 report the real cause.
  assert.match(value(await client.call('actuator_health', { baseUrl: 'http://[::1]:10080' })).results[0].error, /bad port/);
  const closed = http.createServer();
  await new Promise(resolve => closed.listen(0, '127.0.0.1', resolve));
  const closedPort = closed.address().port;
  await new Promise(resolve => closed.close(resolve));
  const refused = value(await client.call('actuator_health', { baseUrl: `http://127.0.0.1:${closedPort}` })).results[0].error;
  assert.match(refused, /ECONNREFUSED/);
});

test('server errors disclose only health documents', async t => {
  let body = '{"status":"DOWN","components":{"db":{"status":"DOWN"}}}';
  const baseUrl = await httpServer(t, (_, res) => { res.writeHead(503, { 'Content-Type': 'application/json' }); res.end(body); });
  const root = await temporary(t);
  await write(root, { 'pom.xml': pom });
  const client = await server(t, root);
  assert.equal(value(await client.call('actuator_health', { baseUrl })).results[0].health.components.db.status, 'DOWN');
  for (body of ['{"message":"jdbc:postgresql://db?password=secret-value","trace":"at Secret"}', '["secret-value"]', '"secret-value"']) {
    const response = await client.call('actuator_health', { baseUrl });
    assert.equal(response.isError, true);
    assert.doesNotMatch(response.content[0].text, /secret-value|Secret/);
    assert.equal(value(response).results[0].health, undefined);
    assert.match(value(response).results[0].error, /Non-health response body omitted/);
  }
});

test('failure text decodes references while CDATA stays literal', async t => {
  const root = await temporary(t);
  await write(root, { 'pom.xml': pom, mvnw: `#!/bin/sh
mkdir -p target/surefire-reports
cat > target/surefire-reports/TEST-Text.xml <<'XML'
<testsuite name="Text" tests="3" failures="3" errors="0">
<testcase classname="T" name="cdata"><failure type="A"><![CDATA[expected &amp; literal <tag>]]></failure></testcase>
<testcase classname="T" name="attribute"><failure message="a &lt; b &#233; &#0;"/></testcase>
<testcase classname="T" name="text"><failure type="B">x &amp;&amp; y</failure></testcase>
</testsuite>
XML
exit 1
` });
  const client = await server(t, root);
  const data = value(await client.call('run_tests'));
  assert.deepEqual(data.reportErrors, []);
  assert.deepEqual(Object.fromEntries(data.failures.map(f => [f.test, f.message])), {
    'T.cdata': 'expected &amp; literal <tag>',
    'T.attribute': 'a < b é &#0;',
    'T.text': 'x && y',
  });
});

test('fixture cleanup removes the runtime build state it created', async t => {
  const root = await temporary(t);
  await write(root, { 'pom.xml': pom, mvnw: '#!/bin/sh\nexit 0\n' });
  const client = await server(t, root);
  assert.equal(value(await client.call('run_build', { task: 'compile' })).exitCode, 0);
  // The helper mirrors runtime state_dir(); a drifted formula would leave this absent.
  assert.ok((await fs.stat(stateDir(root))).isDirectory());
  removeBuildState(root);
  assert.equal(await fs.stat(stateDir(root)).catch(() => null), null);
});
