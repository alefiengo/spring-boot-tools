const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const readline = require('node:readline');
const http = require('node:http');
const entry = path.resolve(__dirname, '../../mcp-server/index.js');

async function fixture(t, files, env = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'mcp-regression-'));
  for (const [name, content] of Object.entries(files)) {
    const file = path.join(root, name);
    await fs.mkdir(path.dirname(file), { recursive: true });
    await fs.writeFile(file, content, { mode: (name.endsWith('w') || name.startsWith('bin/')) ? 0o755 : 0o644 });
  }
  const child = spawn(process.execPath, [entry], { env: { ...process.env, PROJECT_ROOT: root, ...Object.fromEntries(Object.entries(env).map(([key, value]) => [key, value.replaceAll("{{root}}", root)])) }, stdio: ['pipe', 'pipe', 'pipe'] });
  let serial = 0, stderr = '';
  const pending = new Map();
  child.stderr.on('data', d => stderr += d);
  readline.createInterface({ input: child.stdout }).on('line', line => {
    const data = JSON.parse(line);
    if (pending.has(data.id)) { pending.get(data.id)(data); pending.delete(data.id); }
  });
  child.on('exit', () => { for (const resolve of pending.values()) resolve({ error: { message: stderr || 'Server exited' } }); });
  const request = (method, params) => new Promise((resolve, reject) => {
    const id = ++serial;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} timed out: ${stderr}`)); }, 10000);
    pending.set(id, data => { clearTimeout(timer); data.error ? reject(new Error(data.error.message)) : resolve(data.result); });
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
  });
  t.after(async () => { child.kill(); await fs.rm(root, { recursive: true, force: true }); });
  const initialized = await request('initialize', { protocolVersion: '2025-11-25', capabilities: {}, clientInfo: { name: 'regression', version: '1' } });
  assert.equal(initialized.protocolVersion, '2025-11-25');
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
  const cancel = id => {
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/cancelled', params: { requestId: id, reason: 'test' } }) + '\n');
    // The SDK intentionally suppresses responses to cancelled requests.
    pending.get(id)?.({ result: {} });
    pending.delete(id);
  };
  return { root, request, cancel, call: (name, args = {}) => request('tools/call', { name, arguments: args }), child };
}
const value = result => JSON.parse(result.content[0].text);
const pom = '<project><modelVersion>4.0.0</modelVersion><groupId>app</groupId><artifactId>demo</artifactId><version>1</version></project>';

test('real stdio initializes and registers exactly six tools', async t => {
  const client = await fixture(t, { 'pom.xml': pom });
  assert.deepEqual((await client.request('tools/list', {})).tools.map(t => t.name).sort(), ['actuator_health','analyze_project','check_migrations','openapi_endpoints','run_build','run_tests']);
});

test('Maven and test filters reject shell payloads; valid wrapper args remain distinct', async t => {
  const client = await fixture(t, { 'pom.xml': pom, mvnw: '#!/bin/sh\nprintf "%s\\n" "$@"\n' });
  assert.equal((await client.call('run_build', { task: 'compile; touch injected' })).isError, true);
  assert.equal((await client.call('run_tests', { testClass: "X'; touch injected; #" })).isError, true);
  assert.equal((await client.call('run_tests', { testClass: '--scan' })).isError, true);
  assert.equal(await fs.stat(path.join(client.root, 'injected')).catch(() => null), null);
  assert.equal(value(await client.call('run_build', { task: 'compile' })).stdout, '-B\n-ntp\ncompile\n');
  assert.equal(value(await client.call('run_tests', { testClass: 'com.app.AppTest' })).stdout, '-B\n-ntp\ntest\n-Dtest=com.app.AppTest\n');
});

test('failed build keeps stdout diagnostics and excludes stale reports', async t => {
  const client = await fixture(t, { 'pom.xml': pom, mvnw: '#!/bin/sh\necho compiler-failed\necho stderr-diagnostic >&2\nexit 7\n', 'target/surefire-reports/old.xml': '<testsuite tests="9" failures="0" name="Old"/>' });
  const result = await client.call('run_tests');
  const data = value(result);
  assert.equal(result.isError, true);
  assert.equal(data.exitCode, 7);
  assert.match(data.stdout, /compiler-failed/);
  assert.match(data.stderr, /stderr-diagnostic/);
  assert.deepEqual(data.suites, []);
  assert.equal(data.staleReportsIgnored, 1);
});

test('queued test invocation excludes reports produced by the preceding locked build', async t => {
  const control = await fs.mkdtemp(path.join(os.tmpdir(), 'mcp-report-lock-'));
  t.after(() => fs.rm(control, { recursive: true, force: true }));
  const client = await fixture(t, { 'pom.xml': pom, mvnw: `#!/bin/sh
case " $* " in
  *" verify "*)
    touch '${control}/ready'
    while [ ! -f '${control}/release' ]; do sleep 0.05; done
    mkdir -p target/surefire-reports
    echo '<testsuite tests="9" failures="0" name="OtherInvocation"/>' > target/surefire-reports/other.xml
    ;;
  *" test "*) echo compiler-failed; exit 7;;
esac
` });
  const previous = spawn('python3', [path.resolve(__dirname, '../../runtime/project_runtime.py')], { stdio: ['pipe', 'pipe', 'pipe'] });
  t.after(() => previous.kill());
  let previousOutput = '';
  previous.stdout.on('data', chunk => { previousOutput += chunk; });
  previous.stderr.on('data', () => {});
  const previousDone = new Promise(resolve => previous.on('close', resolve));
  previous.stdin.end(JSON.stringify({ action: 'run', projectRoot: client.root, task: 'verify', reuse: false }));
  const waitFor = async predicate => {
    for (let attempt = 0; attempt < 200; attempt++) {
      if (await predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    throw new Error('Timed out waiting for lock coordination');
  };
  await waitFor(() => fs.stat(path.join(control, 'ready')).then(() => true, () => false));
  const pending = client.call('run_tests');
  // The Node server has dispatched the runtime subprocess. With the old
  // implementation its unprotected initial snapshot has already completed.
  await waitFor(() => spawnSync('ps', ['-eo', 'ppid=,args='], { encoding: 'utf8' }).stdout.split('\n')
    .some(line => new RegExp(`^\\s*${client.child.pid}\\s+`).test(line) && line.includes('project_runtime.py')));
  await fs.writeFile(path.join(control, 'release'), '');
  assert.equal(await previousDone, 0, previousOutput);
  const response = await pending;
  const data = value(response);
  assert.equal(response.isError, true);
  assert.equal(data.exitCode, 7);
  assert.deepEqual(data.suites, []);
  assert.equal(data.staleReportsIgnored, 1);
  assert.match(data.diagnostic, /No fresh test reports/);
});

test('fresh nested JUnit reports decode XML and accept reordered attributes', async t => {
  const client = await fixture(t, { 'pom.xml': pom, mvnw: `#!/bin/sh
mkdir -p target/surefire-reports
cat > target/surefire-reports/fresh.xml <<'XML'
<testsuites><testsuite failures="1" time="0.5" tests="2" name="New &amp; fresh"><testcase classname="App" name="fails"><failure type="AssertionError" message="bad &lt;value&gt;"/></testcase></testsuite></testsuites>
XML
exit 1
` });
  const data = value(await client.call('run_tests'));
  assert.equal(data.suites[0].suite, 'New & fresh');
  assert.equal(data.failures[0].test, 'App.fails');
  assert.equal(data.failures[0].message, 'bad <value>');
});

test('POM excludes parent/project/plugin coordinates and handles namespaced dependencies', async t => {
  const client = await fixture(t, { 'pom.xml': '<project xmlns="http://maven.apache.org/POM/4.0.0"><parent><groupId>parent</groupId><artifactId>base</artifactId></parent><groupId>app</groupId><artifactId>demo</artifactId><dependencies><dependency><artifactId>web</artifactId><groupId>org.example</groupId></dependency></dependencies><build><plugins><plugin><groupId>plugin</groupId><artifactId>tool</artifactId></plugin></plugins></build></project>' });
  assert.deepEqual(value(await client.call('analyze_project')).dependencies.map(d => `${d.group}:${d.artifact}`), ['org.example:web']);
});

test('Gradle Kotlin literal dependencies and wrapper work', async t => {
  const client = await fixture(t, { 'build.gradle.kts': 'dependencies { implementation("org.example:core:1"); testImplementation(platform("org.junit:junit-bom:5")) }', gradlew: '#!/bin/sh\nprintf "%s\\n" "$@"\n' });
  assert.deepEqual(value(await client.call('analyze_project')).dependencies.map(d => `${d.group}:${d.artifact}:${d.version}`), ['org.example:core:1','org.junit:junit-bom:5']);
  assert.match(value(await client.call('run_tests', { testClass: 'AppTest' })).stdout, /test\n(?:--rerun-tasks\n)?--tests\nAppTest/);
  assert.equal((await client.call('check_migrations', { mode:'status' })).isError, true);
});

test('recursive packages, YAML/ properties ports and controller annotation variants', async t => {
  const client = await fixture(t, { 'pom.xml': pom, 'src/main/resources/application.yaml': 'server:\n  port: 9091\n', 'src/main/resources/application-test.properties': 'server.port=9092\n', 'src/main/java/com/example/app/api/Api.java': `@RestController
@RequestMapping(path = {"/api/v2", "/alternate"})
class Api {
@GetMapping() public String list() {return "";}
@GetMapping(path = "/{id}") public String get() {return "";}
@PostMapping(value = {"/one", "/two"}) public void create() {}
@RequestMapping(path="/head", method = RequestMethod.HEAD) public void head() {}
}` });
  const project = value(await client.call('analyze_project'));
  assert.deepEqual(project.layers, ['com/example/app/api']);
  assert.deepEqual(project.ports.sort(), [9091, 9092]);
  const endpoints = value(await client.call('openapi_endpoints'));
  assert.equal(endpoints.count, 10);
  assert.ok(endpoints.endpoints.some(e => e.method === 'HEAD' && e.path === '/api/v2/head'));
  assert.ok(endpoints.endpoints.some(e => e.method === 'GET' && e.path === '/api/v2'));
});

test('long builds do not block read-only tools; cancellation terminates command', async t => {
  const client = await fixture(t, { 'pom.xml': pom, mvnw: '#!/bin/sh\nmkdir -p target\ntrap \"touch target/cancelled.marker; exit 1\" TERM\necho started\nsleep 8\ntouch target/completed.marker\n' });
  const build = client.call('run_build', { task: 'compile' });
  await new Promise(resolve => setTimeout(resolve, 100));
  const start = Date.now();
  await client.call('analyze_project');
  assert.ok(Date.now() - start < 2000);
  assert.equal((await client.call("run_tests")).isError, true);
  assert.equal((await client.call("run_build", { task: "clean" })).isError, true);
  // Initialize was ID 1; the build request is ID 2.
  client.cancel(2);
  await build;
  for (let i = 0; i < 20 && !(await fs.stat(path.join(client.root, 'target/cancelled.marker')).catch(() => null)); i++) await new Promise(resolve => setTimeout(resolve, 50));
  assert.ok(await fs.stat(path.join(client.root, 'target/cancelled.marker')).catch(() => null));
  assert.equal(await fs.stat(path.join(client.root, 'target/completed.marker')).catch(() => null), null);
});

test('actuator respects configured app port and reports failed HTTP status', async t => {
  const server = http.createServer((req, res) => { assert.equal(req.url, '/actuator/health'); res.writeHead(503); res.end('{"status":"DOWN"}'); });
  t.after(() => server.close());
  await new Promise((resolve, reject) => { server.on("error", reject); server.listen(0, "127.0.0.1", resolve); });
  t.after(() => server.close());
  const client = await fixture(t, { 'pom.xml': pom }, { SPRING_BOOT_APP_PORT: String(server.address().port) });
  const result = await client.call('actuator_health');
  assert.equal(result.isError, true);
  assert.equal(value(result).results[0].status, 503);
});

test('missing projects and malformed XML error; non-executable wrappers work through sh', async t => {
  const client = await fixture(t, {});
  assert.equal((await client.call('run_build')).isError, true);
  await fs.writeFile(path.join(client.root, 'pom.xml'), '<project><dependencies></project>');
  assert.equal((await client.call('analyze_project')).isError, true);
  await fs.writeFile(path.join(client.root, 'mvnw'), '#!/bin/sh\nexit 0\n', { mode: 0o644 });
  assert.equal(value(await client.call('run_build')).exitCode, 0);
});

test('runner bounds timeout and output capture', async () => {
  const { run } = await import(entry);
  const timed = await run(process.execPath, ['-e', 'setTimeout(()=>{},10000)'], { timeout: 50 });
  assert.equal(timed.error, 'Timed out');
  const large = await run(process.execPath, ['-e', 'setInterval(()=>process.stdout.write("x".repeat(100000)),1)']);
  assert.equal(large.error, 'Output limit exceeded');
  assert.ok(Buffer.byteLength(large.stdout) <= 4 * 1024 * 1024);
});

test('DTD XML is rejected; explicit migration status preserves diagnostics', async t => {
  const client = await fixture(t, { 'pom.xml': '<!DOCTYPE project [<!ENTITY x "secret">]><project><artifactId>&x;</artifactId></project>', mvnw: '#!/bin/sh\necho diagnostic\necho warning >&2\n' });
  const deps = await client.call('analyze_project');
  assert.equal(deps.isError, true);
  assert.match(deps.content[0].text, /(?:DTD|Unsafe)/);
  await fs.writeFile(path.join(client.root, 'pom.xml'), pom);
  const status = value(await client.call('check_migrations', {mode:'status'}));
  assert.equal(status.databaseAccess, true);
  assert.match(status.stdout, /diagnostic/);
  assert.match(status.stderr, /warning/);
});

test('migration lint accepts dotted/repeatable versions and non-idempotent forward SQL', async t => {
  const client = await fixture(t, { 'pom.xml': pom, 'src/main/resources/db/migration/V1.2__create table-valid.sql': 'CREATE TABLE thing (id bigint);', 'src/main/resources/db/migration/R__refresh_view.sql': 'CREATE OR REPLACE VIEW things AS SELECT * FROM thing;' });
  assert.deepEqual(value(await client.call('check_migrations')).errors, []);
  await fs.writeFile(path.join(client.root, 'src/main/resources/db/migration/V2__empty.sql'), '   \n');
  assert.equal((await client.call('check_migrations')).isError, true);
});

test('entity mapping accepts reordered names and returns errors for missing entities', async t => {
  const client = await fixture(t, { 'pom.xml': pom, 'src/main/java/com/example/User.java': '@Entity\n@Table(schema="app", name="users")\nclass User { @Column(nullable=false, name="email") private String email; @ManyToOne(fetch=FetchType.LAZY) private Team team; }' });
  const data = value(await client.call('analyze_project')).persistence[0];
  assert.equal(data.table, 'users');
  assert.equal(data.columns[0].name, 'email');
  assert.equal(data.relations[0].type, 'ManyToOne');
});

test('destructive migration lint ignores comments and string literals', async t => {
  const client = await fixture(t, { 'pom.xml': pom, 'src/main/resources/db/migration/U1_2__rollback.sql': '-- DROP TABLE harmless;\nSELECT \'DROP TABLE quoted\';', 'src/main/resources/db/migration/V2__delete.sql': 'DELETE FROM users;', 'src/main/resources/db/migration/V3__drop-column.sql': 'ALTER TABLE users DROP COLUMN email; DROP INDEX users_email_idx;' });
  const result = await client.call('check_migrations');
  assert.equal(result.isError, undefined);
  assert.match(result.content[0].text, /DELETE without WHERE/);
  assert.match(result.content[0].text, /drop-column/);
  assert.ok(value(result).risks.every(r => !r.file.includes('rollback')));
});

test('project discovery ascends beyond five package levels', async t => {
  const client = await fixture(t, { 'pom.xml': pom, 'a/b/c/d/e/f/g/marker': '' }, { PROJECT_ROOT: '{{root}}/a/b/c/d/e/f/g' });
  assert.equal(value(await client.call('analyze_project')).root, client.root);
  assert.deepEqual(value(await client.call('analyze_project')).dependencies, []);
});

test('module provenance, versions and persistence come from shared discovery', async t => {
  const client = await fixture(t, {
    'pom.xml': '<project><parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId><version>4.1.1</version></parent><modules><module>users</module><module>orders</module></modules></project>',
    'users/pom.xml': '<project><properties><java.version>25</java.version></properties><dependencies><dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-actuator</artifactId></dependency></dependencies></project>',
    'orders/pom.xml': pom,
    'users/src/main/java/User.java': '@Entity class User {}',
    mvnw: '#!/bin/sh\nprintf "%s\\n" "$@"\n',
  }, { PROJECT_ROOT:'{{root}}/users' });
  const data = value(await client.call('analyze_project'));
  assert.equal(data.root, client.root);
  assert.deepEqual(data.modules.map(m => m.path), ['orders','users']);
  assert.equal(data.dependencies[0].provenance.file, 'users/pom.xml');
  assert.equal(data.versions.find(v => v.component === 'java.version').version, '25');
  assert.equal(data.capabilities.actuator, 'declared');
  assert.equal(data.persistence[0].file, 'users/src/main/java/User.java');
  assert.match(value(await client.call('run_build', {task:'compile', module:'users', profiles:['dev']})).stdout, /-Pdev\n-pl\nusers\n-am/);
  assert.equal((await client.call('run_build', {module:'../outside'})).isError, true);
  assert.equal((await client.call('run_build', {profiles:['x;touch injected']})).isError, true);
});

test('migration lint normalizes duplicates, sorts numerically and isolates module locations', async t => {
  const client = await fixture(t, { 'pom.xml':pom,
    'src/main/resources/db/migration/V1.02__a.sql':'CREATE TABLE a(id int);',
    'src/main/resources/db/migration/V1_2_0__b.sql':'CREATE TABLE b(id int);',
    'src/main/resources/db/migration/V10__later.sql':'SELECT 1;',
    'src/main/resources/db/migration/V2__earlier.sql':'SELECT 1;',
    'other/src/main/resources/db/migration/V1_2__separate.sql':'DROP TABLE old;',
  });
  const result = await client.call('check_migrations');
  const data = value(result);
  assert.equal(result.isError, true);
  assert.equal(data.databaseAccess, false);
  assert.equal(data.errors.filter(e => e.code === 'duplicate-version').length, 1);
  assert.equal(data.risks.length, 1);
  assert.deepEqual(data.migrations.filter(m => m.location.startsWith('src/')).map(m => m.version), ['1.2','1.2','2','10']);
});

async function httpFixture(t, handler) {
  const server = http.createServer(handler);
  await new Promise((resolve, reject) => { server.on('error', reject); server.listen(0, '127.0.0.1', resolve); });
  t.after(() => server.close());
  return `http://127.0.0.1:${server.address().port}`;
}

test('runtime OpenAPI comparison returns routes without disclosing document security schemas', async t => {
  const hits = [];
  const baseUrl = await httpFixture(t, (req,res) => {
    hits.push(req.url); res.setHeader('Content-Type','application/json');
    res.end(JSON.stringify({openapi:'3.1.0', paths:{'/api/users':{get:{operationId:'listUsers'}}, '/runtime-only':{post:{}}}, components:{securitySchemes:{secret:'do-not-return'}}}));
  });
  const client = await fixture(t, { 'pom.xml':pom, 'src/main/java/Api.java':'@RestController class Api { @GetMapping("/api/users") void users() {} @DeleteMapping("/static-only") void remove() {} }' });
  const result = await client.call('openapi_endpoints', {mode:'compare',baseUrl});
  const data = value(result);
  assert.equal(data.runtime.count, 2);
  assert.deepEqual(data.comparison.onlyStatic.map(e => e.path), ['/static-only']);
  assert.deepEqual(data.comparison.onlyRuntime.map(e => e.path), ['/runtime-only']);
  assert.deepEqual(hits, ['/v3/api-docs']);
  assert.doesNotMatch(result.content[0].text, /do-not-return/);
});

test('runtime OpenAPI validates YAML, invalid bodies and response size limits', async t => {
  let body = 'openapi: 3.1.0\npaths:\n  /hello:\n    get:\n      operationId: hello\n';
  const baseUrl = await httpFixture(t, (_,res) => res.end(body));
  const client = await fixture(t, {'pom.xml':pom});
  assert.equal(value(await client.call('openapi_endpoints',{mode:'runtime',baseUrl,documentPath:'/v3/api-docs.yaml'})).runtime.endpoints[0].operationId,'hello');
  body = '{"status":"UP"}';
  assert.equal((await client.call('openapi_endpoints',{mode:'runtime',baseUrl})).isError,true);
  body = 'x'.repeat(3*1024*1024);
  assert.match((await client.call('openapi_endpoints',{mode:'runtime',baseUrl})).content[0].text,/2 MiB/);
});

test('health probes use an explicit allowlist and diagnose access without response disclosure', async t => {
  const hits = [];
  const baseUrl = await httpFixture(t, (req,res) => {
    hits.push(req.url);
    if (req.url.endsWith('readiness')) {res.writeHead(401);res.end('private credentials');}
    else if (req.url.endsWith('liveness')) {res.writeHead(404);res.end('missing');}
    else res.end('{"status":"UP"}');
  });
  const client = await fixture(t, {'pom.xml':pom});
  const result = await client.call('actuator_health',{baseUrl,probes:['health','readiness','liveness']});
  assert.equal(result.isError,true);
  const data = value(result);
  assert.equal(data.results[0].health.status,'UP');
  assert.match(data.results[1].diagnosis,/Access denied/);
  assert.match(data.results[2].diagnosis,/Endpoint absent/);
  assert.doesNotMatch(result.content[0].text,/private credentials/);
  assert.deepEqual(hits,['/actuator/health','/actuator/health/readiness','/actuator/health/liveness']);
  assert.equal((await client.call('actuator_health',{baseUrl,probes:['env']})).isError,true);
  assert.equal((await client.call('actuator_health',{baseUrl:'http://user:pass@localhost:8080'})).isError,true);
  assert.equal((await client.call('openapi_endpoints',{mode:'runtime',baseUrl,documentPath:'/actuator/env'})).isError,true);
});

test('shared build coordinator reuses a full successful verification only for unchanged inputs', async t => {
  const client = await fixture(t, {'pom.xml':pom,mvnw:'#!/bin/sh\nmkdir -p target\necho executed >> target/executions.log\nexit 0\n','src/main/java/App.java':'class App {}'});
  assert.equal(value(await client.call('run_build',{task:'verify'})).reused,false);
  assert.equal(value(await client.call('run_build',{task:'verify'})).reused,true);
  await fs.writeFile(path.join(client.root,'src/main/java/App.java'),'class App { int value; }');
  assert.equal(value(await client.call('run_build',{task:'verify'})).reused,false);
  assert.equal((await fs.readFile(path.join(client.root,'target/executions.log'),'utf8')).trim().split('\n').length,2);
});

test('forced verification bypasses cached success and status failure is an MCP error', async t => {
  const client = await fixture(t, {'pom.xml':pom,mvnw:'#!/bin/sh\ncase "$*" in *flyway:info*) echo failed >&2; exit 9;; esac\nmkdir -p target\necho executed >> target/executions.log\nexit 0\n'});
  await client.call('run_build',{task:'verify'});
  assert.equal(value(await client.call('run_build',{task:'verify',force:true})).reused,false);
  const status = await client.call('check_migrations',{mode:'status'});
  assert.equal(status.isError,true);
  assert.equal(value(status).exitCode,9);
});

test('bridge preserves valid JSON for bounded control-character build output', async t => {
  const client = await fixture(t, {'pom.xml':pom,mvnw:'#!/bin/sh\npython3 -c "import sys; sys.stdout.buffer.write(bytes(1000000))"\n'});
  const result = await client.call('run_build',{task:'compile'});
  assert.equal(result.isError,undefined);
  assert.equal(value(result).stdout.length,1000000);
});

test('Gradle settings-only aggregator supports discovered modules and portable checks', async t => {
  const client = await fixture(t, {'settings.gradle.kts':'include(":app")','app/build.gradle.kts':'plugins { id("java") }',gradlew:'#!/bin/sh\nprintf "%s\\n" "$@"\n'});
  const project = value(await client.call('analyze_project'));
  assert.equal(project.buildTool,'gradle');
  assert.deepEqual(project.modules.map(m => m.path),['app']);
  assert.match(value(await client.call('run_build',{task:'check',module:'app'})).stdout,/:app:check/);
});


test('a real MCP verification satisfies the separate Stop hook and changed inputs rerun', async t => {
  const client = await fixture(t, {'pom.xml':pom,mvnw:'#!/bin/sh\nmkdir -p target\necho executed >> target/executions.log\nexit 0\n','src/main/java/App.java':'class App {}'}, {SPRING_BOOT_APP_PORT:'8080'});
  assert.equal(value(await client.call('run_build',{task:'verify'})).reused,false);
  const stopEnvironment = { ...process.env, CLAUDE_PROJECT_DIR:client.root };
  delete stopEnvironment.SPRING_BOOT_APP_PORT;
  const invokeStop = () => spawnSync('python3', [path.resolve(__dirname,'../../hooks/scripts/hook-runtime.py'),'verify-on-stop'], {
    input: JSON.stringify({cwd:client.root,tool_input:{}}), encoding:'utf8',
    env: stopEnvironment, timeout:10000,
  });
  let stop = invokeStop();
  assert.equal(stop.status,0,stop.stderr);
  assert.equal(stop.stdout,'');
  assert.equal((await fs.readFile(path.join(client.root,'target/executions.log'),'utf8')).trim().split('\n').length,1);
  await fs.writeFile(path.join(client.root,'src/main/java/App.java'),'class App { int changed; }');
  stop = invokeStop();
  assert.equal(stop.status,0,stop.stderr);
  assert.equal(stop.stdout,'');
  assert.equal((await fs.readFile(path.join(client.root,'target/executions.log'),'utf8')).trim().split('\n').length,2);
});
