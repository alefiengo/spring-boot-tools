const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { fixture, value } = require('./initialization-fixture.cjs');

test('shared SQL corpus preserves MCP severity, names, versions and risk policies', async t => {
  const corpus = JSON.parse(await fs.readFile(path.join(__dirname, '../project-runtime/sql-corpus.json'), 'utf8'));
  const files = { 'pom.xml': '<project/>' };
  for (const c of corpus) files[`${c.id}/src/main/resources/db/migration/${c.name}`] = c.sql;
  const client = await fixture(t, files);
  const response = await client.call('check_migrations');
  const data = value(response);
  assert.equal(response.isError, true); // naming/empty errors, never risks alone
  assert.equal(data.databaseAccess, false);
  for (const c of corpus) {
    const file = `${c.id}/src/main/resources/db/migration/${c.name}`;
    assert.deepEqual(data.errors.filter(e => e.file === file).map(e => e.code), c.mcp.errors, c.id);
    assert.deepEqual(data.risks.filter(e => e.file === file).map(e => e.code), c.mcp.risks, c.id);
    assert.deepEqual(data.migrations.find(m => m.file === file), {
      file, location: path.posix.dirname(file), type: c.mcp.type, version: c.mcp.version,
    }, c.id);
  }
});

test('shared SQL normalization keeps numeric order, migration type and location scope', async t => {
  const base = 'src/main/resources/db/migration/';
  const client = await fixture(t, {
    'pom.xml': '<project/>',
    [base + 'V01.02__first.sql']: 'SELECT 1;',
    [base + 'V1_2_0__duplicate.sql']: 'SELECT 1;',
    [base + 'U1.2__undo.sql']: 'DROP TABLE users;',
    [base + 'V10__later.sql']: 'SELECT 1;',
    [base + 'V2__earlier.sql']: 'SELECT 1;',
    [base + 'R__last.sql']: 'SELECT 1;',
    ['other/' + base + 'V1_2__isolated.sql']: 'SELECT 1;',
  });
  const data = value(await client.call('check_migrations'));
  assert.deepEqual(data.errors, [{ file: base + 'V1_2_0__duplicate.sql', code: 'duplicate-version',
    message: 'Equivalent version to ' + base + 'V01.02__first.sql', version: '1.2' }]);
  assert.deepEqual(data.migrations.filter(m => m.location === base.slice(0, -1)).map(m => m.version), ['1.2', '1.2', '1.2', '2', '10', null]);
  assert.equal(data.risks.length, 1);
});

test('shared SQL risks remain advisory without naming or empty errors', async t => {
  const client = await fixture(t, { 'pom.xml': '<project/>',
    'src/main/resources/db/migration/V1__risk.sql': 'DROP SCHEMA old; DELETE FROM users;' });
  const response = await client.call('check_migrations');
  assert.equal(response.isError, undefined);
  assert.deepEqual(value(response).risks.map(r => r.code), ['destructive-sql', 'unbounded-delete']);
});
