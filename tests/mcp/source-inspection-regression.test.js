const { test } = require('node:test');
const assert = require('node:assert/strict');
const { fixture, value } = require('./initialization-fixture.cjs');

test('source inventories retain build and target package directories', async t => {
  const client = await fixture(t, {
    'pom.xml': '<project/>',
    'src/main/java/com/example/build/Thing.java': '@Entity @Table(name="things") class Thing {}',
    'src/main/java/com/example/target/Api.java': '@RestController class Api { @GetMapping("/retained") public String get() {} }',
    'src/main/resources/build/application.properties': 'server.port=9191\n',
    'target/Discarded.java': '@Entity class Discarded {}',
  });
  const project = value(await client.call('analyze_project'));
  assert.deepEqual(project.persistence.map(entity => entity.name), ['Thing']);
  assert.deepEqual(project.ports, [9191]);
  const endpoints = value(await client.call('openapi_endpoints'));
  assert.equal(endpoints.count, 1);
  assert.equal(endpoints.endpoints[0].path, '/retained');
});

test('migration lint follows Gradle projectDir root for logical module selection', async t => {
  const client = await fixture(t, {
    'settings.gradle.kts': 'include(":logical")\nproject(":logical").projectDir = file("services/actual")\n',
    'build.gradle.kts': '',
    'services/actual/build.gradle.kts': '',
    'services/actual/src/main/resources/db/migration/V1__actual.sql': 'CREATE TABLE actual(id int);',
    'logical/src/main/resources/db/migration/V2__wrong.sql': 'DROP TABLE wrong;',
  });
  const data = value(await client.call('check_migrations', { module: 'logical' }));
  assert.deepEqual(data.migrations.map(migration => migration.file), ['services/actual/src/main/resources/db/migration/V1__actual.sql']);
  assert.deepEqual(data.risks, []);
});
