const { test } = require('node:test');
const assert = require('node:assert/strict');
const { fixture, value } = require('./initialization-fixture.cjs');

test('MCP preserves structured serialized-limit failures from the runtime for builds and tests', async t => {
  const failure = {
    ok: false,
    exitCode: 1,
    error: 'Runtime result exceeds 24 MiB serialized limit; narrow test/module scope',
    buildExitCode: 7,
    reportsOmitted: 3,
    outputOmitted: true,
  };
  const transport = `import json, sys
request = json.load(sys.stdin)
if request.get('action') == 'discover':
    print(json.dumps({'root': request['projectRoot'], 'buildTool': 'maven', 'buildFile': 'pom.xml', 'modules': []}))
else:
    print(json.dumps(json.loads(${JSON.stringify(JSON.stringify(failure))})))
    sys.exit(1)
`;
  const client = await fixture(t, {}, transport);
  for (const name of ['run_build', 'run_tests']) {
    const response = await client.call(name);
    assert.equal(response.isError, true, name);
    assert.deepEqual(value(response), failure, name);
  }
});
