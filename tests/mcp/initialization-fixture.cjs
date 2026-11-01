const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const readline = require('node:readline');
const repo = path.resolve(__dirname, '../..');

exports.fixture = async (t, files = {}, transport = false, nodeArgs = []) => {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'sbt-init-mcp-')));
  for (const [name, content] of Object.entries(files)) {
    await fs.mkdir(path.dirname(path.join(root, name)), { recursive: true });
    await fs.writeFile(path.join(root, name), content);
  }
  const env = { ...process.env, PROJECT_ROOT: root };
  if (transport) {
    const python = spawnSync('python3', ['-c', 'import sys; print(sys.executable)'], { encoding: 'utf8' }).stdout.trim();
    await fs.mkdir(path.join(root, 'bin'));
    // Accept a controlled runtime response script, or exercise the real CLI
    // and initializer with only HTTP transport replaced.
    await fs.writeFile(path.join(root, 'bin/python3'), typeof transport === 'string' ? `#!${python}\n${transport}` : `#!${python}
import io, json, runpy, sys, zipfile
sys.path.insert(0, ${JSON.stringify(path.join(repo, 'runtime'))})
import project_initializer as init
metadata = open(${JSON.stringify(path.join(repo, 'tests/initialization/metadata.json'))}, 'rb').read()
stream = io.BytesIO()
with zipfile.ZipFile(stream, 'w') as archive:
    archive.writestr('pom.xml', '<project/>')
    archive.writestr('mvnw', chr(10).join(['#!/bin/sh', 'touch BUILD_EXECUTED', '']))
init.download = lambda url, *args: metadata if url.endswith('/metadata/client') else stream.getvalue()
runpy.run_path(sys.argv[1], run_name='__main__')
`, { mode: 0o755 });
    env.PATH = `${path.join(root, 'bin')}${path.delimiter}${env.PATH}`;
  }
  const child = spawn(process.execPath, [...nodeArgs, path.join(repo, 'mcp-server/index.js')], { env, stdio: ['pipe', 'pipe', 'pipe'] });
  let id = 0, stderr = '';
  const pending = new Map();
  child.stderr.on('data', data => { stderr += data; });
  readline.createInterface({ input: child.stdout }).on('line', line => {
    const message = JSON.parse(line);
    pending.get(message.id)?.(message);
  });
  child.on('exit', () => { for (const done of pending.values()) done({ error: { message: stderr || 'Server exited' } }); });
  const request = (method, params) => new Promise((resolve, reject) => {
    const requestId = ++id;
    const timer = setTimeout(() => { pending.delete(requestId); reject(new Error(`${method} timed out: ${stderr}`)); }, 10000);
    pending.set(requestId, message => {
      clearTimeout(timer);
      pending.delete(requestId);
      message.error ? reject(new Error(message.error.message)) : resolve(message.result);
    });
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: requestId, method, params }) + '\n');
  });
  t.after(async () => {
    const exited = new Promise(resolve => child.once('close', resolve));
    child.kill();
    if (child.exitCode === null) await exited;
    await fs.rm(root, { recursive: true, force: true });
  });
  const initialized = await request('initialize', { protocolVersion: '2025-11-25', capabilities: {}, clientInfo: { name: 'initialization-regression', version: '1' } });
  assert.equal(initialized.protocolVersion, '2025-11-25');
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
  return { root, request, call: (name, args = {}) => request('tools/call', { name, arguments: args }) };
};
exports.value = response => JSON.parse(response.content[0].text);
