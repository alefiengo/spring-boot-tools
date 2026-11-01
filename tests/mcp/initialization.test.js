const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { fixture, value } = require('./initialization-fixture.cjs');

test('initialize_project is the seventh tool and requires explicit scaffold intent and target', async t => {
  const client = await fixture(t);
  const tools = (await client.request('tools/list', {})).tools;
  assert.equal(tools.length, 7);
  assert.ok(tools.some(tool => tool.name === 'initialize_project'));
  for (const args of [{}, { targetDirectory: 'app' }, { userRequested: true }, { userRequested: false, targetDirectory: 'app' }]) {
    assert.equal((await client.call('initialize_project', args)).isError, true);
  }
  assert.deepEqual(await fs.readdir(client.root), []);
});

test('initialize_project delegates to Python and returns metadata choices without executing wrapper', async t => {
  const client = await fixture(t, {}, true);
  const response = await client.call('initialize_project', { userRequested: true, targetDirectory: 'app', dependencies: ['web'] });
  assert.notEqual(response.isError, true, response.content[0].text);
  const data = value(response);
  assert.equal(data.parameters.bootVersion, '9.8.7');
  assert.equal(data.buildExecuted, false);
  assert.equal((await fs.stat(path.join(client.root, 'app/mvnw'))).mode & 0o777, 0o755);
  assert.equal(await fs.stat(path.join(client.root, 'app/BUILD_EXECUTED')).catch(() => null), null);
  // Positive control: the exact generated wrapper must create the sentinel
  // when deliberately executed in a separate, disposable directory.
  const proof = await fs.mkdtemp(path.join(os.tmpdir(), 'sbt-wrapper-control-'));
  t.after(() => fs.rm(proof, { recursive: true, force: true }));
  const wrapper = await fs.readFile(path.join(client.root, 'app/mvnw'), 'utf8');
  assert.equal(wrapper, '#!/bin/sh\ntouch BUILD_EXECUTED\n');
  const executable = path.join(proof, 'mvnw');
  await fs.writeFile(executable, wrapper, { mode: 0o755 });
  const executed = spawnSync(executable, [], { cwd: proof, encoding: 'utf8' });
  assert.equal(executed.status, 0, executed.error?.message || executed.stderr);
  assert.ok((await fs.stat(path.join(proof, 'BUILD_EXECUTED'))).isFile());
  assert.equal(await fs.stat(path.join(client.root, 'app/BUILD_EXECUTED')).catch(() => null), null);
  assert.equal((await client.call('initialize_project', { userRequested: true, targetDirectory: 'app' })).isError, true);
});

test('initialization rejects active project, nonempty target and unsafe path without changes', async t => {
  const client = await fixture(t, { 'occupied/keep': 'original' });
  for (const targetDirectory of ['occupied', '../escape']) {
    assert.equal((await client.call('initialize_project', { userRequested: true, targetDirectory })).isError, true);
  }
  await fs.writeFile(path.join(client.root, 'pom.xml'), '<project><parent><groupId>org.springframework.boot</groupId></parent></project>');
  const response = await client.call('initialize_project', { userRequested: true, targetDirectory: 'new' });
  assert.equal(response.isError, true);
  assert.match(value(response).error, /Existing project context/);
  assert.equal(await fs.readFile(path.join(client.root, 'occupied/keep'), 'utf8'), 'original');
  assert.deepEqual((await fs.readdir(client.root)).sort(), ['occupied', 'pom.xml']);
});
