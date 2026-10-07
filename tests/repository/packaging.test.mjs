import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../../', import.meta.url));
const require = createRequire(path.join(root, 'mcp-server/package.json'));
const { parse } = require('yaml');
const read = file => readFileSync(path.join(root, file), 'utf8');
const json = file => JSON.parse(read(file));
const frontmatter = file => parse(read(file).match(/^---\n([\s\S]*?)\n---/)[1]);

test('plugin, marketplace and MCP package versions agree', () => {
  const plugin = json('.claude-plugin/plugin.json');
  const entry = json('.claude-plugin/marketplace.json').plugins.find(p => p.name === plugin.name);
  assert.equal(entry.version, plugin.version);
  assert.match(plugin.version, /^\d+\.\d+\.\d+$/);
  assert.equal(json('mcp-server/package.json').version, plugin.version);
});

test('the standalone LSP map registers a Java server with the required fields', () => {
  const servers = json('.lsp.json');
  for (const server of Object.values(servers)) {
    assert.equal(typeof server.command, 'string');
    assert.ok(Object.keys(server.extensionToLanguage).length);
  }
  assert.equal(servers.java.extensionToLanguage['.java'], 'java');
});

test('all declared options reach a component and references resolve', () => {
  const options = json('.claude-plugin/plugin.json').userConfig;
  const server = json('.mcp.json').mcpServers['spring-boot'];
  const definition = JSON.stringify(server);
  for (const name of Object.keys(options)) assert.ok(definition.includes(`\${user_config.${name}}`));
  for (const match of definition.matchAll(/\$\{user_config\.(\w+)\}/g)) assert.ok(options[match[1]]);
  assert.equal(server.env.PROJECT_ROOT, '${CLAUDE_PROJECT_DIR}');
  assert.equal(server.env.SPRING_BOOT_APP_PORT, '${user_config.app_port}');
});

test('registered hook launchers and their shared runtime are shipped and executable', () => {
  const config = json('hooks/hooks.json');
  for (const entries of Object.values(config.hooks)) {
    for (const entry of entries) for (const hook of entry.hooks) {
      const relative = hook.command.match(/hooks\/scripts\/[\w.-]+/)[0];
      assert.ok(statSync(path.join(root, relative)).mode & 0o111);
    }
  }
  assert.ok(statSync(path.join(root, 'hooks/scripts/hook-runtime.py')).isFile());
});

test('skills and agents have unique matching names and reviewers cannot mutate files or run commands', () => {
  const skills = readdirSync(path.join(root, 'skills'));
  const names = new Set();
  for (const directory of skills) {
    const data = frontmatter(`skills/${directory}/SKILL.md`);
    assert.equal(data.name, directory);
    assert.equal(typeof data.description, 'string');
    assert.ok(!names.has(data.name)); names.add(data.name);
  }
  for (const file of readdirSync(path.join(root, 'agents')).filter(f => f.endsWith('.md'))) {
    const data = frontmatter(`agents/${file}`);
    assert.equal(data.name, file.slice(0, -3));
    const tools = data.tools.split(',').map(t => t.trim());
    if (/reviewer|detector/.test(data.name)) assert.ok(tools.every(t => ['Read', 'Glob', 'Grep'].includes(t)));
  }
});

test('GitLab template declares stages and PostgreSQL 18 compose persists its data directory', () => {
  const fences = file => [...read(file).matchAll(/```yaml\n([\s\S]*?)\n```/g)].map(m => parse(m[1]));
  const gitlab = fences('skills/spring-boot-ci/SKILL.md').find(doc => doc['image-build']);
  assert.ok(gitlab);
  for (const [name, job] of Object.entries(gitlab)) if (job?.stage) {
    assert.ok(gitlab.stages.includes(job.stage), `${name} uses undefined stage ${job.stage}`);
  }
  const compose = fences('skills/spring-boot-docker/SKILL.md').find(doc => doc.services);
  assert.ok(compose.services.db.image.startsWith('postgres:18'));
  assert.ok(compose.services.db.volumes.some(volume => volume.split(':')[1] === '/var/lib/postgresql'));
});

test('MCP lockfile matches package metadata and declared direct dependencies', () => {
  const pkg = json('mcp-server/package.json');
  const lock = json('mcp-server/package-lock.json');
  assert.equal(lock.version, pkg.version);
  assert.deepEqual(lock.packages[''].dependencies, pkg.dependencies);
  assert.deepEqual(lock.packages[''].engines, pkg.engines);
  assert.ok(pkg.engines.node);
});


test('registered components and shared runtime are consistent', () => {
  const agents = readdirSync(path.join(root, 'agents')).filter(f => f.endsWith('.md')).sort();
  assert.deepEqual(agents, ['sb-api-designer.md', 'sb-architect.md', 'sb-code-reviewer.md', 'sb-docker-reviewer.md', 'sb-migration-reviewer.md', 'sb-runtime-reviewer.md', 'sb-test-writer.md']);
  assert.equal(readdirSync(path.join(root, 'skills')).length, 15);
  assert.ok(!json('.claude-plugin/plugin.json').experimental?.monitors);
  const hooks = json('hooks/hooks.json').hooks;
  assert.equal(Object.values(hooks).flatMap(entries => entries.flatMap(entry => entry.hooks)).length, 5);
  assert.ok(existsSync(path.join(root, 'runtime/project_runtime.py')));
  assert.match(read('hooks/scripts/hook-runtime.py'), /project_runtime/);
  assert.match(read('mcp-server/index.js'), /project_runtime/);
  const review = read('skills/sb-pr-review/SKILL.md');
  for (const [name] of review.matchAll(/\bsb-[a-z-]+\b/g)) {
    assert.ok(existsSync(path.join(root, `agents/${name}.md`)) || existsSync(path.join(root, `skills/${name}/SKILL.md`)), `Unknown review component: ${name}`);
  }
  assert.match(review, /documentation-only/);
  assert.match(review, /executed\/reused/);
});
