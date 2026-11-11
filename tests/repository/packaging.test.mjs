import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../../', import.meta.url));
const require = createRequire(path.join(root, 'package.json'));
const { parse } = require('yaml');
const read = file => readFileSync(path.join(root, file), 'utf8');
const json = file => JSON.parse(read(file));
const frontmatter = file => parse(read(file).match(/^---\n([\s\S]*?)\n---/)[1]);

test('plugin, marketplace and MCP package versions agree', () => {
  const plugin = json('.claude-plugin/plugin.json');
  const entry = json('.claude-plugin/marketplace.json').plugins.find(p => p.name === plugin.name);
  assert.equal(entry.version, plugin.version);
  assert.match(plugin.version, /^\d+\.\d+\.\d+$/);
  assert.equal(json('package.json').version, plugin.version);
  assert.equal(json('.codex-plugin/plugin.json').version, plugin.version);
  assert.equal(json('package-lock.json').version, plugin.version);
  assert.equal(json('package-lock.json').packages[''].version, plugin.version);
  assert.ok(read('mcp-server/index.js').includes(`version: "${plugin.version}"`));
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
  // Claude Code resolves ${...} only in stdio command, args and env; it drops
  // other fields such as cwd, so the project root travels through env.
  assert.deepEqual(Object.keys(server).sort(), ['args', 'command', 'env']);
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
  const pkg = json('package.json');
  const lock = json('package-lock.json');
  assert.equal(lock.version, pkg.version);
  assert.deepEqual(lock.packages[''].dependencies, pkg.dependencies);
  assert.deepEqual(lock.packages[''].engines, pkg.engines);
  assert.ok(pkg.engines.node);
});

test('plugin-root dependencies satisfy Claude Code automatic installation', () => {
  // Claude Code installs only from a package.json plus npm lockfile v2/3 at the
  // plugin root, without overrides, using exact https registry packages.
  const pkg = json('package.json');
  const lock = json('package-lock.json');
  assert.ok([2, 3].includes(lock.lockfileVersion));
  assert.equal(pkg.overrides, undefined);
  assert.equal(pkg.workspaces, undefined);
  assert.equal(pkg.type, undefined, 'root CommonJS keeps tests/**/*.js valid');
  for (const [location, entry] of Object.entries(lock.packages)) {
    if (!location) continue;
    assert.ok(location.startsWith('node_modules/'), location);
    assert.match(entry.version, /^\d+\.\d+\.\d+$/, location);
    assert.ok(entry.resolved.startsWith('https://registry.npmjs.org/'), location);
    assert.match(entry.integrity, /^sha512-/, location);
    assert.equal(entry.link, undefined, location);
  }
  // The server directory keeps only its ESM scope marker; no nested dependency set.
  assert.deepEqual(json('mcp-server/package.json'), { type: 'module' });
  assert.ok(!existsSync(path.join(root, 'mcp-server/package-lock.json')));
  // GHSA-474x-hrwr-fgx4 is fixed in fast-xml-parser 5.12.0.
  const [major, minor] = lock.packages['node_modules/fast-xml-parser'].version.split('.').map(Number);
  assert.ok(major > 5 || (major === 5 && minor >= 12));
});


test('registered components and shared runtime are consistent', () => {
  const agents = readdirSync(path.join(root, 'agents')).filter(f => f.endsWith('.md')).sort();
  assert.deepEqual(agents, ['sb-api-designer.md', 'sb-architect.md', 'sb-code-reviewer.md', 'sb-docker-reviewer.md', 'sb-migration-reviewer.md', 'sb-runtime-reviewer.md', 'sb-test-writer.md']);
  assert.equal(readdirSync(path.join(root, 'skills')).length, 16);
  assert.ok(!json('.claude-plugin/plugin.json').experimental?.monitors);
  const hooks = json('hooks/hooks.json').hooks;
  assert.equal(Object.values(hooks).flatMap(entries => entries.flatMap(entry => entry.hooks)).length, 4);
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

// Documentation and manifests must describe the components that actually ship.
const numbers = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10 };
const count = word => /^\d+$/.test(word) ? Number(word) : numbers[word.toLowerCase()];
const skillNames = () => readdirSync(path.join(root, 'skills')).sort();
const agentNames = () => readdirSync(path.join(root, 'agents')).filter(f => f.endsWith('.md')).map(f => f.slice(0, -3)).sort();
const toolNames = () => [...read('mcp-server/index.js').matchAll(/\bregister\("([a-z_]+)"/g)].map(m => m[1]).sort();
const hookEntries = () => Object.entries(json('hooks/hooks.json').hooks)
  .flatMap(([event, entries]) => entries.flatMap(entry => entry.hooks.map(hook => ({ event, command: hook.command }))));

test('README catalog lists every shipped skill, agent, MCP tool and hook, and no others', () => {
  const readme = read('README.md');
  const rows = heading => {
    const section = readme.split(/^## /m).find(part => part.startsWith(heading));
    assert.ok(section, `README section ${heading}`);
    return [...section.matchAll(/^\| `([\w-]+)` \|/gm)].map(m => m[1]).sort();
  };
  assert.deepEqual(rows('Skills'), skillNames());
  assert.deepEqual(rows('Specialist agents'), agentNames());
  assert.deepEqual(rows('MCP tools'), toolNames());
  const hooks = hookEntries().map(hook => hook.command.match(/hooks\/scripts\/([\w-]+)\.sh/)[1]).sort();
  assert.deepEqual(rows('Automatic hooks'), hooks);
});

test('component counts in README and manifest descriptions match the repository', () => {
  const skills = skillNames().length, agents = agentNames().length, tools = toolNames().length, hooks = hookEntries().length;
  const intro = read('README.md').match(/provides (\w+) shared skills and (\w+) MCP tools\. Claude Code additionally loads (\w+) specialist agents and (\w+) hooks/);
  assert.ok(intro, 'README introduction sentence');
  assert.deepEqual(intro.slice(1).map(count), [skills, tools, agents, hooks]);
  const entry = json('.claude-plugin/marketplace.json').plugins[0].description;
  const counted = pattern => count(entry.match(pattern)[1]);
  assert.equal(counted(/(\w+) skills/), skills);
  assert.equal(counted(/(\w+) MCP tools/), tools);
  assert.equal(counted(/(\w+) Claude agents/), agents);
  assert.equal(counted(/(\w+) Claude hooks/), hooks);
  assert.equal(count(json('.claude-plugin/plugin.json').description.match(/(\w+) diagnostic MCP tools/)[1]), tools);
  assert.equal(count(json('package.json').description.match(/(\w+) Spring-aware MCP tools/)[1]), tools);
  assert.deepEqual(json('docs/component-evidence.json').components.filter(c => c.kind === 'skill').map(c => c.id).sort(), skillNames());
  assert.deepEqual(json('docs/component-evidence.json').components.filter(c => c.kind === 'agent').map(c => c.id).sort(), agentNames());
  assert.deepEqual(json('docs/component-evidence.json').components.filter(c => c.kind === 'mcp-tool').map(c => c.id).sort(), toolNames());
});

test('internal Markdown links resolve to existing files', () => {
  const files = [];
  const walk = directory => {
    for (const entry of readdirSync(path.join(root, directory), { withFileTypes: true })) {
      const relative = path.join(directory, entry.name);
      if (entry.isDirectory()) { if (!['node_modules', '.git', '__pycache__'].includes(entry.name)) walk(relative); }
      else if (entry.name.endsWith('.md')) files.push(relative);
    }
  };
  walk('.');
  assert.ok(files.includes('README.md') && files.includes('docs/codex.md'));
  for (const file of files) {
    const text = read(file).replace(/```[\s\S]*?```/g, '');
    for (const match of text.matchAll(/\]\(([^)\s]+)\)/g)) {
      const target = match[1].split('#')[0];
      if (!target || /^[a-z][a-z0-9+.-]*:/i.test(target)) continue;
      assert.ok(existsSync(path.join(root, path.dirname(file), target)), `${file} links to missing ${target}`);
    }
  }
});

test('launchers, doctor and server entry point are executable', () => {
  for (const file of ['codex/run.mjs', 'codex/launch.mjs', 'scripts/install-doctor.py', 'mcp-server/index.js']) {
    assert.ok(statSync(path.join(root, file)).mode & 0o111, `${file} must be executable`);
  }
});

test('documentation reflects removed CI and the plugin-root dependency install', () => {
  assert.ok(!existsSync(path.join(root, '.github/workflows')), 'GitHub Actions workflows were removed in 0.3.0');
  const ignore = read('.gitignore');
  assert.match(ignore, /^node_modules\/$/m);
  assert.match(ignore, /^evals\/results\/$/m);
  const documents = ['README.md', 'CONTRIBUTING.md', 'SECURITY.md', 'AGENTS.md', 'docs/architecture.md', 'docs/codex.md',
    'docs/capability-matrix.md', 'docs/releases.md', 'docs/audit-2026-10-31.md'];
  for (const file of documents) {
    const text = read(file);
    assert.doesNotMatch(text, /pass the CI matrix|director owns CI|CI invocation/, `${file} refers to CI that no longer exists`);
    assert.doesNotMatch(text, /--prefix [^\s]*mcp-server|\bmcp-server\/package-lock\.json/, `${file} points to the old dependency location`);
  }
  assert.match(read('CONTRIBUTING.md'), /^npm ci$/m);
  assert.match(read('CONTRIBUTING.md'), /^npm audit$/m);
});

test('plugin manifest metadata matches the repository license and location', () => {
  const plugin = json('.claude-plugin/plugin.json');
  assert.equal(plugin.license, 'MIT');
  assert.match(read('LICENSE'), /^MIT License/);
  assert.match(plugin.repository, /^https:\/\/github\.com\/[\w.-]+\/spring-boot-tools$/);
  assert.ok(plugin.homepage.startsWith(plugin.repository));
  assert.ok(Object.keys(plugin).every(key => ['name', 'description', 'version', 'author', 'homepage', 'repository', 'license', 'userConfig'].includes(key)),
    'unexpected plugin.json key; confirm it is documented before shipping it');
});
