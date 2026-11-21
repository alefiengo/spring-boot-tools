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

test('project inventory skips invalid YAML documents and preserves valid ports', async t => {
  const client = await fixture(t, {
    'pom.xml': '<project><artifactId>demo</artifactId><version>1</version></project>',
    'src/main/resources/application.yml': [
      'server: {port: 8081}',
      '---',
      'server: {port: 8082}',
      'version: @project.version@',
      '---',
      'server.port: 8083',
    ].join('\n'),
    'src/main/resources/application-local.properties': 'server.port=8084\n',
  });
  const response = await client.call('analyze_project');
  assert.equal(response.isError, undefined, response.content[0].text);
  const project = value(response);
  assert.equal(project.versions[0].component, ':demo');
  assert.deepEqual(project.configurations, [
    { file: 'src/main/resources/application-local.properties', ports: [8084] },
    { file: 'src/main/resources/application.yml', ports: [8081, 8083] },
  ]);
  assert.deepEqual(project.ports, [8084, 8081, 8083]);
  assert.equal(project.warnings.length, 1);
  assert.match(project.warnings[0], /src\/main\/resources\/application\.yml: .*reserved character/);
  assert.doesNotMatch(project.warnings[0], /@project\.version@/);
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

test('migration status is advertised as Maven-only and stops after Gradle discovery', async t => {
  const client = await fixture(t, {}, `import json, sys
request = json.load(sys.stdin)
if request['action'] != 'discover':
    sys.exit('Unexpected runtime action: ' + request['action'])
print(json.dumps({'root': request['projectRoot'], 'buildTool': 'gradle', 'modules': []}))
`);
  const response = await client.call('check_migrations', { mode: 'status' });
  assert.equal(response.isError, true);
  assert.match(value(response).error, /status.*Maven-only.*Flyway Maven plugin/);
  const tool = (await client.request('tools/list', {})).tools.find(tool => tool.name === 'check_migrations');
  assert.match(tool.description, /status.*Maven-only.*Flyway Maven plugin/);
});
