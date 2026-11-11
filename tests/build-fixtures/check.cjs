#!/usr/bin/env node
// Opt-in real build integration. Never imported by a default test-basic suite.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const repo = path.resolve(__dirname, '../..');
const tools = process.argv.slice(2);
if (tools.some(tool => !['maven', 'gradle'].includes(tool))) throw new Error('Usage: check.sh [maven] [gradle]');
if (!tools.length) tools.push('maven', 'gradle');

function execute(command, args, { cwd = repo, env = process.env, input, timeout = 390000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd, env, detached: true, stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    const kill = () => { try { process.kill(-child.pid, 'SIGKILL'); } catch {} };
    const timer = setTimeout(() => { kill(); reject(new Error(`${command} timed out`)); }, timeout);
    child.stdout.on('data', chunk => { stdout += chunk; });
    child.stderr.on('data', chunk => { stderr += chunk; });
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('close', code => { clearTimeout(timer); resolve({ code, stdout, stderr }); });
    child.stdin.on('error', () => {});
    child.stdin.end(input);
  });
}

async function mcp(root, env) {
  const child = spawn(process.execPath, [path.join(repo, 'mcp-server/index.js')], { cwd: root, env, stdio: ['pipe', 'pipe', 'pipe'] });
  let id = 0, stderr = '';
  const pending = new Map();
  child.stderr.on('data', data => { stderr += data; });
  readline.createInterface({ input: child.stdout }).on('line', line => {
    const message = JSON.parse(line);
    pending.get(message.id)?.(message);
  });
  child.on('exit', () => { for (const done of pending.values()) done({ error: { message: stderr || 'MCP server exited' } }); });
  const request = (method, params) => new Promise((resolve, reject) => {
    const requestId = ++id;
    const timer = setTimeout(() => { pending.delete(requestId); reject(new Error(`${method} timed out: ${stderr}`)); }, 390000);
    pending.set(requestId, message => {
      clearTimeout(timer); pending.delete(requestId);
      message.error ? reject(new Error(message.error.message)) : resolve(message.result);
    });
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: requestId, method, params }) + '\n');
  });
  const close = async () => {
    if (child.exitCode !== null) return;
    const exited = new Promise(resolve => child.once('close', resolve));
    child.kill();
    await exited;
  };
  try {
    await request('initialize', { protocolVersion: '2025-11-25', capabilities: {}, clientInfo: { name: 'real-build-fixtures', version: '1' } });
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
  } catch (error) { await close(); throw error; }
  return { close, call: (name, args = {}) => request('tools/call', { name, arguments: args }) };
}
const value = response => JSON.parse(response.content[0].text);
const successful = response => { const data = value(response); assert.equal(response.isError, undefined, JSON.stringify(data)); assert.equal(data.ok, true, JSON.stringify(data)); return data; };
const testCount = data => data.suites.reduce((sum, suite) => sum + suite.tests, 0);

async function check(tool) {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), `sb-real-${tool}-`)));
  const env = { ...process.env, PROJECT_ROOT: root, CLAUDE_PROJECT_DIR: root };
  // The MCP bridge removes this diagnostic endpoint setting before builds.
  delete env.SPRING_BOOT_APP_PORT;
  const key = crypto.createHash('sha256').update(root).digest('hex');
  // The runtime keeps per-project state under the user database's home, never TMPDIR/HOME.
  const state = path.join(os.userInfo().homedir, '.cache', 'spring-boot-tools', 'build-' + key);
  let client, failed = false;
  const log = text => console.log(`[${tool}] ${text}`);
  const runtime = async args => {
    const result = await execute('python3', [path.join(repo, 'runtime/project_runtime.py')], {
      cwd: root, env, input: JSON.stringify({ action: 'run', projectRoot: root, timeoutMs: 300000, ...args }),
    });
    assert.equal(result.code, 0, result.stderr || result.stdout);
    return JSON.parse(result.stdout);
  };
  const stop = async () => {
    const result = await execute('bash', [path.join(repo, 'hooks/scripts/verify-on-stop.sh')], { cwd: root, env, input: '{}' });
    assert.equal(result.code, 0, result.stderr);
    return result.stdout.trim() ? JSON.parse(result.stdout) : null;
  };
  try {
    await fs.cp(path.join(__dirname, tool), root, { recursive: true });
    client = await mcp(root, env);
    const discovered = await runtime({ action: 'discover' });
    assert.equal(discovered.buildTool, tool);
    assert.deepEqual(discovered.modules.map(m => m.path), ['core', 'extra']);
    log('initial real verification');
    const initial = await runtime({ task: 'verify' });
    assert.equal(initial.ok, true, initial.stdout + initial.stderr);
    assert.equal(initial.reused, false);
    assert.equal(initial.inputsChanged, false);
    assert.equal((await runtime({ task: 'verify' })).reused, true);
    assert.equal(successful(await client.call('run_build', { task: 'verify' })).reused, true);
    const beforeStop = await fs.stat(path.join(state, 'state.json'), { bigint: true });
    assert.equal(await stop(), null);
    assert.equal((await fs.stat(path.join(state, 'state.json'), { bigint: true })).mtimeNs, beforeStop.mtimeNs, 'Stop must reuse the MCP/runtime gate without rewriting state');

    log('compile cannot satisfy verification; exact module scope');
    assert.equal((await runtime({ task: 'compile' })).ok, true);
    assert.equal((await runtime({ task: 'verify' })).reused, false);
    const moduleGate = await runtime({ task: 'verify', module: 'core' });
    assert.equal(moduleGate.ok, true, moduleGate.stdout + moduleGate.stderr);
    assert.equal(moduleGate.reused, false);
    assert.equal((await runtime({ task: 'verify', module: 'core' })).reused, true);
    assert.equal((await runtime({ task: 'verify' })).reused, false);

    log('MCP filtered tests return fresh XML; unchanged XML stays stale');
    const staleDir = path.join(root, 'core', tool === 'maven' ? 'target/failsafe-reports' : 'build/test-results/previous');
    await fs.mkdir(staleDir, { recursive: true });
    await fs.writeFile(path.join(staleDir, 'TEST-stale.xml'), '<testsuite name="stale.sentinel" tests="93" failures="0"/>');
    const selected = { module: 'core', testClass: 'fixture.FixtureTest#adds' };
    for (let i = 0; i < 2; i++) {
      const data = successful(await client.call('run_tests', selected));
      assert.equal(testCount(data), 1, JSON.stringify(data));
      assert.equal(data.reportScope, path.join(root, 'core'));
      assert.ok(data.staleReportsIgnored >= 1, JSON.stringify(data));
      assert.deepEqual(data.reportErrors, []);
      assert.deepEqual(data.failures, []);
    }

    if (tool === 'maven') {
      log('real profile activation and reactor report scope');
      const profiled = successful(await client.call('run_tests', { profiles: ['extra'] }));
      assert.equal(testCount(profiled), 3, JSON.stringify(profiled));
      assert.ok(profiled.suites.some(s => s.suite.includes('ProfileTest')));
      const ordinary = successful(await client.call('run_tests'));
      assert.equal(testCount(ordinary), 2, JSON.stringify(ordinary));
      assert.ok(!ordinary.suites.some(s => s.suite.includes('ProfileTest')));
      const scoped = await runtime({ task: 'verify', profiles: ['extra'] });
      assert.equal(scoped.ok, true, scoped.stdout + scoped.stderr);
      assert.equal((await runtime({ task: 'verify', profiles: ['extra'] })).reused, true);
      assert.equal((await runtime({ task: 'verify' })).reused, false);
    } else {
      const all = successful(await client.call('run_tests'));
      assert.equal(testCount(all), 3, JSON.stringify(all));
      assert.equal((await client.call('run_build', { task: 'verify', profiles: ['extra'] })).isError, true);
    }

    log('real JUnit failure is parsed and invalidates verification');
    const testFile = path.join(root, 'core/src/test/java/fixture/FixtureTest.java');
    const testSource = await fs.readFile(testFile, 'utf8');
    await fs.writeFile(testFile, testSource.replace('assertEquals(2,', 'assertEquals(3,'));
    const failed = await client.call('run_tests', selected), failure = value(failed);
    assert.equal(failed.isError, true, JSON.stringify(failure));
    assert.equal(failure.ok, false);
    assert.ok(failure.failures.some(f => f.test.includes('adds')), JSON.stringify(failure));
    const blocked = await stop();
    assert.equal(blocked?.decision, 'block', JSON.stringify(blocked));
    assert.match(blocked.reason, /Build check failed/);
    await fs.writeFile(testFile, testSource);
    const repaired = await runtime({ task: 'verify' });
    assert.equal(repaired.ok, true, repaired.stdout + repaired.stderr);
    assert.equal(repaired.reused, false);

    log('external invalid Java edit blocks Stop without any edit hook');
    const sourceFile = path.join(root, 'core/src/main/java/fixture/Calculator.java');
    const source = await fs.readFile(sourceFile, 'utf8');
    await fs.writeFile(sourceFile, source + '\nthis is invalid Java;\n');
    assert.equal((await stop())?.decision, 'block');
    await fs.writeFile(sourceFile, source);
    assert.equal(await stop(), null);
    assert.equal((await runtime({ task: 'verify' })).reused, true);
    log('PASS');
  } catch (error) {
    failed = true;
    if (env.SB_REAL_BUILD_KEEP_FAILURE === '1') {
      log(`preserving failed fixture: ${root}`);
      log(`preserving verification records: ${state}`);
    }
    throw error;
  } finally {
    await client?.close();
    if (!failed || env.SB_REAL_BUILD_KEEP_FAILURE !== '1') {
      await fs.rm(root, { recursive: true, force: true });
      await fs.rm(state, { recursive: true, force: true });
    }
  }
}

(async () => {
  const java = await execute('java', ['-version'], { timeout: 30000 });
  assert.equal(java.code, 0, java.stderr);
  assert.match(java.stdout + java.stderr, /version "25(?:[."])/, 'real fixtures require the pinned JDK 25');
  for (const tool of tools) {
    const executable = tool === 'maven' ? 'mvn' : 'gradle';
    const version = await execute(executable, ['--version'], { timeout: 30000 });
    assert.equal(version.code, 0, version.stderr);
    assert.match(version.stdout, tool === 'maven' ? /Apache Maven 3\.10\.0\b/ : /Gradle 9\.8\.1\b/, 'use the pinned installed build tool; this script never downloads distributions');
    await check(tool);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
