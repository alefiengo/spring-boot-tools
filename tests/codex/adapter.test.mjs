import assert from 'node:assert/strict';
import { test } from 'node:test';
import { cp, mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const json = async file => JSON.parse(await readFile(path.join(root, file), 'utf8'));
const tools = ['actuator_health', 'analyze_project', 'check_migrations', 'initialize_project', 'openapi_endpoints', 'run_build', 'run_tests'];
const pom = '<project><modelVersion>4.0.0</modelVersion><groupId>app</groupId><artifactId>demo</artifactId><version>1</version></project>';

async function temporary(t) {
  const base = await mkdtemp(path.join(tmpdir(), 'sbt-codex-'));
  t.after(() => rm(base, { recursive: true, force: true }));
  return base;
}

async function client(t, { relocated = false, nested = false, project = true } = {}) {
  const base = await temporary(t);
  const app = path.join(base, 'application with spaces');
  const cwd = nested ? path.join(app, 'src/main/java') : app;
  await mkdir(cwd, { recursive: true });
  if (project) {
    await writeFile(path.join(app, 'pom.xml'), pom);
    await writeFile(path.join(app, 'mvnw'), '#!/bin/sh\nmkdir -p target\nprintf "run\\n" >> target/builds\nprintf "%s\\n" "$@"\n', { mode: 0o755 });
  }
  let plugin = root;
  if (relocated) {
    // Exercise real relocation, URL escaping and argument boundaries. Keep the
    // installed dependency tree shared; no npm/network/model calls in tests.
    plugin = path.join(base, "plugin ' $() with spaces");
    await mkdir(plugin);
    for (const directory of ['codex', 'runtime']) await cp(path.join(root, directory), path.join(plugin, directory), { recursive: true });
    await mkdir(path.join(plugin, 'mcp-server'));
    for (const file of ['index.js', 'package.json']) await cp(path.join(root, 'mcp-server', file), path.join(plugin, 'mcp-server', file));
    await symlink(path.join(root, 'mcp-server/node_modules'), path.join(plugin, 'mcp-server/node_modules'), 'dir');
  }
  const config = await json('codex/mcp.json');
  const server = config.mcpServers['spring-boot'];
  const child = spawn(server.command, server.args, {
    cwd,
    env: { ...process.env, SBT_PLUGIN_ROOT: plugin, PROJECT_ROOT: '/stale-project', CLAUDE_PROJECT_DIR: '/stale-claude-project', SPRING_BOOT_APP_PORT: '8181' },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let serial = 0, stderr = '';
  const pending = new Map();
  child.stderr.on('data', chunk => { stderr += chunk; });
  const lines = createInterface({ input: child.stdout });
  lines.on('line', line => {
    const data = JSON.parse(line);
    pending.get(data.id)?.(data);
  });
  child.on('exit', () => { for (const resolve of pending.values()) resolve({ error: { message: stderr || 'MCP exited' } }); });
  t.after(async () => {
    lines.close();
    if (child.exitCode === null && child.signalCode === null) {
      const exited = once(child, 'exit');
      child.kill('SIGKILL');
      await exited;
    }
  });
  const request = (method, params) => new Promise((resolve, reject) => {
    const id = ++serial;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} timed out: ${stderr}`)); }, 10000);
    pending.set(id, data => {
      clearTimeout(timer); pending.delete(id);
      data.error ? reject(new Error(data.error.message)) : resolve(data.result);
    });
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
  });
  const initialized = await request('initialize', { protocolVersion: '2025-11-25', capabilities: {}, clientInfo: { name: 'codex-adapter-test', version: '1' } });
  assert.equal(initialized.serverInfo.name, 'mcp-spring-boot');
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
  return { base, app, cwd, request, call: (name, args = {}) => request('tools/call', { name, arguments: args }) };
}
const value = result => JSON.parse(result.content[0].text);

test('Codex packages skills and its own MCP config, explicitly suppressing default hooks', async () => {
  const manifest = await json('.codex-plugin/plugin.json');
  assert.equal(manifest.name, 'spring-boot-tools');
  assert.equal(manifest.version, (await json('.claude-plugin/plugin.json')).version);
  assert.deepEqual(await readdir(path.join(root, '.codex-plugin')), ['plugin.json']);
  for (const key of ['skills', 'mcpServers']) {
    assert.ok(manifest[key].startsWith('./'));
    assert.ok(!manifest[key].split('/').includes('..'));
    await readdir(path.dirname(path.join(root, manifest[key])));
  }
  assert.deepEqual(manifest.hooks, { hooks: {} });
  assert.equal(manifest.requires_local_executor, true);
  assert.ok(!('agents' in manifest));
  assert.ok(!('lspServers' in manifest));
  assert.equal(manifest.skills, './skills/');
  assert.equal(manifest.mcpServers, './codex/mcp.json');
  const config = await json('codex/mcp.json');
  assert.doesNotMatch(JSON.stringify(config), /CLAUDE_|\$\{|user_config/);
  assert.equal(config.mcpServers['spring-boot'].cwd, undefined);
  assert.deepEqual(config.mcpServers['spring-boot'].env_vars, ['SBT_PLUGIN_ROOT', 'SPRING_BOOT_APP_PORT']);
});

test('the actual config and launcher complete MCP initialize/list-tools after relocation', async t => {
  const c = await client(t, { relocated: true, nested: true });
  assert.deepEqual((await c.request('tools/list', {})).tools.map(tool => tool.name).sort(), tools);
  const analyzed = await c.call('analyze_project');
  assert.ok(!analyzed.isError, analyzed.content[0].text);
  assert.equal(value(analyzed).root, c.app);
});

test('the launcher executes and reuses the shared full gate in the application cwd', async t => {
  const c = await client(t);
  const first = await c.call('run_build', { task: 'verify', force: true });
  assert.ok(!first.isError, first.content[0].text);
  assert.equal(value(first).exitCode, 0);
  assert.equal(value(first).stdout, '-B\n-ntp\nverify\n');
  const second = await c.call('run_build', { task: 'verify' });
  assert.ok(!second.isError, second.content[0].text);
  assert.equal(value(second).reused, true);
  assert.equal(await readFile(path.join(c.app, 'target/builds'), 'utf8'), 'run\n');
});

test('scaffolding refuses implicit intent and existing ancestor project contexts before networking', async t => {
  const c = await client(t, { nested: true });
  const implicit = await c.call('initialize_project', { targetDirectory: 'new-app' });
  assert.equal(implicit.isError, true);
  const existing = await c.call('initialize_project', { userRequested: true, targetDirectory: 'new-app' });
  assert.equal(existing.isError, true);
  assert.match(existing.content[0].text, /[Ee]xisting project/);
  assert.ok(!(await readdir(c.cwd)).includes('new-app'));
});

test('scaffolding preserves existing empty and hidden-file targets without an application', async t => {
  const c = await client(t, { project: false });
  for (const name of ['empty', 'nonempty']) await mkdir(path.join(c.cwd, name));
  await writeFile(path.join(c.cwd, 'nonempty/.keep'), 'preserve');
  for (const name of ['empty', 'nonempty']) {
    const result = await c.call('initialize_project', { userRequested: true, targetDirectory: name });
    assert.equal(result.isError, true);
  }
  assert.deepEqual(await readdir(path.join(c.cwd, 'empty')), []);
  assert.deepEqual(await readdir(path.join(c.cwd, 'nonempty')), ['.keep']);
  assert.equal(await readFile(path.join(c.cwd, 'nonempty/.keep'), 'utf8'), 'preserve');
});

test('missing/relative plugin roots fail visibly without selecting the application as a plugin', async () => {
  const server = (await json('codex/mcp.json')).mcpServers['spring-boot'];
  for (const rootValue of ['', 'relative-plugin']) {
    const result = spawnSync(server.command, server.args, { env: { ...process.env, SBT_PLUGIN_ROOT: rootValue }, encoding: 'utf8', timeout: 5000 });
    assert.ifError(result.error);
    assert.equal(result.status, 1);
    assert.equal(result.stdout, '');
    assert.match(result.stderr, /SBT_PLUGIN_ROOT is required/);
  }
});

test('the Codex wrapper resolves its own root, keeps cwd/arguments and propagates failure', async t => {
  const base = await temporary(t);
  const bin = path.join(base, 'bin');
  await mkdir(bin);
  await writeFile(path.join(bin, 'codex'), `#!${process.execPath}\nconsole.log(JSON.stringify({cwd:process.cwd(),args:process.argv.slice(2),root:process.env.SBT_PLUGIN_ROOT})); process.exitCode=7;\n`, { mode: 0o755 });
  const args = ['-C', '/application with spaces', 'a prompt with $() and "quotes"'];
  const result = spawnSync(process.execPath, [path.join(root, 'codex/run.mjs'), ...args], {
    cwd: base, env: { ...process.env, PATH: bin + path.delimiter + process.env.PATH, SBT_PLUGIN_ROOT: '/stale-plugin' }, encoding: 'utf8', timeout: 5000,
  });
  assert.ifError(result.error);
  assert.equal(result.status, 7, result.stderr);
  const record = JSON.parse(result.stdout);
  assert.equal(record.cwd, base);
  assert.deepEqual(record.args, args);
  assert.equal(path.resolve(record.root), path.resolve(root));
});
