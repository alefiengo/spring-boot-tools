const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { fixture, value } = require('./initialization-fixture.cjs');

test('cancelled runner kills a SIGTERM-resistant descendant after its leader and pipes exit', async t => {
  const { run } = await import('../../mcp-server/index.js');
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'sbt-cancellation-'));
  let descendant;
  t.after(async () => {
    if (descendant) { try { process.kill(descendant, 'SIGKILL'); } catch { /* already dead */ } }
    await fs.rm(root, { recursive: true, force: true });
  });
  const childCode = `import os, signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
os.close(1)
os.close(2)
Path(sys.argv[1], 'ready').write_text(str(os.getpid()))
time.sleep(2)
Path(sys.argv[1], 'survived').write_text('SIGTERM-resistant child survived cancellation')
time.sleep(30)
`;
  const leaderCode = `import subprocess, sys, time
subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]])
time.sleep(30)
`;
  const controller = new AbortController();
  const pending = run('python3', ['-c', leaderCode, childCode, root], { signal: controller.signal, timeout: 10000 });
  t.after(() => controller.abort());
  for (let attempt = 0; attempt < 100; attempt++) {
    const pid = await fs.readFile(path.join(root, 'ready'), 'utf8').catch(() => null);
    if (pid) { descendant = Number(pid); break; }
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  assert.ok(descendant, 'descendant installed SIGTERM handler and closed output pipes');
  const start = Date.now();
  controller.abort();
  const response = await pending;
  assert.match(response.error, /Cancelled/);
  assert.ok(Date.now() - start >= 900, 'request waits for group SIGKILL escalation after the leader closes');
  await new Promise(resolve => setTimeout(resolve, 1200));
  assert.equal(await fs.stat(path.join(root, 'survived')).catch(() => null), null);
});

test('health HTTP 200 requires an object with a nonempty status; unfamiliar statuses remain observations', async t => {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'sbt-health-fixture-')));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const body = path.join(root, 'response.json');
  const preload = path.join(root, 'fetch-fixture.mjs');
  await fs.writeFile(preload, `import { readFile } from 'node:fs/promises';
globalThis.fetch = async () => new Response(await readFile(${JSON.stringify(body)}, 'utf8'), { status: 200, headers: { 'Content-Type': 'application/json' } });
`);
  const client = await fixture(t, {}, false, ['--import', preload]);
  const baseUrl = 'http://127.0.0.1:8080';
  for (const invalid of ['null', '[]', '{}', '{"status":4}', '{"status":""}', '{"status":"   "}', 'invalid JSON']) {
    await fs.writeFile(body, invalid);
    const response = await client.call('actuator_health', { baseUrl });
    assert.equal(response.isError, true, invalid);
    assert.equal(value(response).results[0].ok, false, invalid);
    assert.match(value(response).results[0].error, /Health endpoint/, invalid);
  }
  await fs.writeFile(body, '{"status":"CUSTOM_STATUS"}');
  const custom = await client.call('actuator_health', { baseUrl });
  assert.notEqual(custom.isError, true);
  assert.equal(value(custom).results[0].health.status, 'CUSTOM_STATUS');
  await fs.writeFile(body, '{"status":"DOWN"}');
  assert.equal((await client.call('actuator_health', { baseUrl })).isError, true);
});
