# Codex installation and behavior

This plugin includes a supported `.codex-plugin/plugin.json` compatibility
manifest, the shared `skills/` directory and a separate `codex/mcp.json`.
The root `.mcp.json` is exclusively for Claude Code. Do not load it in Codex:
its project and user-option placeholders are Claude-specific.

## Install from a local checkout

Use a local executor with Node.js meeting `mcp-server/package.json`'s engine
requirement, Python 3.11+, and the application's JDK/build prerequisites. The
shared runtime currently supports Linux/macOS, not native Windows. Install
the locked MCP dependencies before installing the plugin:

```bash
npm ci --prefix /absolute/path/to/spring-boot-tools/mcp-server
```

Create a local marketplace outside the application and plugin directories.
Replace the illustrative absolute paths below with your own. Keep the
checkout available while using the plugin:

```bash
mkdir -p /absolute/path/to/local-marketplace/.agents/plugins
ln -s /absolute/path/to/spring-boot-tools /absolute/path/to/local-marketplace/spring-boot-tools
```

Save this catalog as
`/absolute/path/to/local-marketplace/.agents/plugins/marketplace.json`:

```json
{
  "name": "spring-boot-local",
  "interface": { "displayName": "Spring Boot local" },
  "plugins": [
    {
      "name": "spring-boot-tools",
      "source": { "source": "local", "path": "./spring-boot-tools" },
      "policy": { "installation": "AVAILABLE", "authentication": "ON_USE" }
    }
  ]
}
```

Register and install with the CLI. These are user-run installation commands;
the adapter itself never installs a plugin or writes Codex configuration:

```bash
codex plugin marketplace add /absolute/path/to/local-marketplace
codex plugin add spring-boot-tools@spring-boot-local
```

Use the supplied wrapper to launch Codex in the **application** workspace:

```bash
node /absolute/path/to/spring-boot-tools/codex/run.mjs -C /absolute/path/to/application
```

The wrapper resolves its own plugin path, sets `SBT_PLUGIN_ROOT` only in its
child process, and forwards every CLI argument. It preserves cwd and does not
write configuration. The MCP config explicitly forwards `SBT_PLUGIN_ROOT`
and optional `SPRING_BOOT_APP_PORT`; the latter defaults to 8080 in the server.
For example:

```bash
SPRING_BOOT_APP_PORT=8181 node /absolute/path/to/spring-boot-tools/codex/run.mjs -C /absolute/path/to/application
```

The server launcher sets `PROJECT_ROOT` to the actual MCP process cwd, ignoring
stale inherited project variables. Shared Python discovery finds the project
and modules; the adapter implements no competing discovery/build logic. Start
with `analyze_project` and confirm its root before invoking a build. Changing
a shell subprocess's directory does not retarget an already running MCP
server: start a new Codex session with the intended `-C` workspace. For a new
scaffold, start outside existing build trees with an absent target whose parent
exists. Start a new session in the generated project for verification.

After changing or updating the plugin, reinstall it and restart the wrapper
from the matching checkout. Installed skills are cached by Codex, while the
wrapper loads MCP from its own bundle; keep those versions aligned.

## Offline installation doctor

The local doctor itself requires Python 3.11+ to start. It uses only the Python
standard library, so it can diagnose missing Node or MCP dependencies before
those dependencies are installed. If no suitable Python is available, obtain
it separately; a Python script cannot diagnose its own missing interpreter.
Run it against a trusted plugin checkout and trusted Node/Python executables:

```bash
python3 /absolute/path/to/spring-boot-tools/scripts/install-doctor.py \
  --workspace /absolute/path/to/application
python3 /absolute/path/to/spring-boot-tools/scripts/install-doctor.py --json \
  --workspace /absolute/path/to/application --project /absolute/path/to/application
```

Quote paths containing spaces. `--workspace` defaults to the current directory;
it represents the MCP process cwd you intend to launch. `--plugin-root` defaults
to the doctor's containing checkout, resolved from the script's own location.
The doctor checks Linux/macOS support, Node >=20.3, Python >=3.11, local MCP
ESM import target resolution and file existence (including the server's `/stdio`
export), registration structure, launcher and runtime support file paths,
bundle version agreement, and any inherited `SBT_PLUGIN_ROOT`. Codex's inline
loader must exactly match the supported `codex/launch.mjs` template; equivalent
custom loader scripts require separate verification. Unset
`SBT_PLUGIN_ROOT` is acceptable when using `codex/run.mjs`; a relative or
different bundle path is an error. An inherited stale `PROJECT_ROOT` produces a
warning because the Codex launcher replaces it with its actual cwd.

By default workspace checking only requires an existing directory outside the
plugin bundle. `--project PATH` additionally sends only `action: discover` to
the shared Python runtime for both paths and compares their canonical project
roots. Nested workspaces in the same discovered reactor are accepted. This
static check does not activate Maven profiles, evaluate dynamic Gradle settings,
execute project wrappers, or verify Java/build prerequisites. The doctor does
not discover the cwd of an already running host or inspect installed skill
caches; provide the intended paths and check `analyze_project` in the session.

Both text and `--json` output use exit status 0 for passing checks (warnings and
skipped optional checks are allowed), 1 for diagnostic failures, and 2 for invalid
CLI arguments. JSON reports expose `schemaVersion: 1`, `ok`, `exitCode`,
`pluginRoot`, `workspace`, and ordered `checks` with stable `id`, `status`, and
human-readable `message` fields. Status is `ok`, `error`, `warning`, or `skipped`;
additional evidence fields depend on the check. Messages are not a parsing API.
Argument errors use argparse's stderr usage text, including with `--json`.
The bootstrap interpreter must already work for these exit codes to apply.

Each executable probe is limited to five seconds and 64 KiB of captured output.
`--node` and `--python` select trusted executable paths/names, without shell
arguments. Probes strip inherited Node coverage, compile-cache, warning-output
and REPL-history settings, plus Python cache/history output paths.
Dependency checking resolves ESM import targets without importing dependencies;
it does not prove dependency integrity, installed version compatibility, or
successful server startup. The resolver uses `--experimental-import-meta-resolve`
with an explicit parent module URL, as supported by the
[Node 20.3 API](https://nodejs.org/download/release/v20.3.0/docs/api/esm.html#importmetaresolvespecifier-parent).
The doctor never writes files, installs packages,
changes configuration, uses the network, runs builds, or launches Claude/Codex,
MCP servers, Java or language servers. It is a local executable, not an eighth
MCP tool. No protocol check runs by default. The existing optional
`python3 tests/codex/cli-smoke.py` is separate and creates an isolated temporary
host configuration as described below.

Run only the focused doctor regressions with:

```bash
python3 -B -m unittest discover -s tests/install-doctor -v
```

See the [capability matrix](capability-matrix.md) for host-specific registrations
and the limits of installation/protocol evidence.

## Supported capabilities and limits

- The shared skills and seven stdio MCP tools are available locally. Skill
  selection depends on the host/model; use Codex's skill picker for an explicit
  invocation. Natural-language requests explicitly asking to scaffold a new
  project can select `spring-boot-initializr`; it never infers that intent from
  missing files. Initialization fetches current official Initializr metadata
  and defaults. It refuses existing project contexts and every existing target,
  including an empty directory. Generation alone does not run a build.
- Claude agents in `agents/` are not registered as Codex agents. PR review
  uses their relevant read-only guidance directly when specialists are absent
  and reports that limitation. It does not claim independent reviewers ran.
- Claude hooks are deliberately disabled with the manifest's inline empty
  `hooks` object. This also suppresses Codex's automatic discovery of
  `hooks/hooks.json`. There are no plugin pre-edit guards, post-edit compilation,
  or automatic Stop verification on Codex. Explicitly invoke `run_build` with
  Maven `verify` or Gradle `check` and inspect the result. The shared runtime's
  locking and verification reuse remain available through MCP or its CLI.
- `.lsp.json`, Claude agent tool/model fields, and Claude `userConfig` are not
  Codex registrations. Java language-server integration is not provided here.
- The wrapper is required for this compatibility package. Codex CLI 0.166.0
  did not expand `${PLUGIN_ROOT}` in the referenced compatibility MCP config,
  and supplied no `CODEX_PLUGIN_ROOT`, `PLUGIN_ROOT`, or `CLAUDE_PLUGIN_ROOT`
  environment variable to that MCP process. The config therefore uses an
  explicit `env_vars` path handoff with no host placeholder interpolation.
  A missing or relative `SBT_PLUGIN_ROOT` fails visibly. Launching bare `codex`
  requires setting `SBT_PLUGIN_ROOT` yourself to the matching absolute bundle
  path. Desktop launches without this environment setup are not validated.
- This requires a local executor, its dependencies and the application's
  permissions/network access. Hosted web-only execution and public-directory
  publication are not established by these checks.

## Validation evidence

`bash tests/codex/test-basic.sh` runs deterministic packaging and real MCP
initialize/list-tools tests through the shipped config and launcher. It covers
relocation with spaces/shell metacharacters, nested workspace discovery, stale
project variables, coordinated verification/reuse, scaffold refusals and wrapper
argument/exit propagation. It makes no model calls and fetches no scaffolds.

`python3 tests/codex/cli-smoke.py` is a separate optional local CLI protocol
check. It uses an isolated temporary `CODEX_HOME` and local marketplace, reads
the manifest, installs only in that temporary home, starts an empty thread and
checks MCP inventory and application discovery. It never starts a model turn.
The implementation was checked against Codex CLI 0.166.0; compatibility with
other CLI versions and desktop UI flows needs separate verification. Neither
these protocol checks nor deterministic tests certify model skill selection.

## Official sources

- [Package your plugin](https://developers.openai.com/plugins/build/plugins):
  compatibility manifests, root-relative component paths, separate MCP
  configuration and explicit hooks overriding default hook discovery.
- [Plugins in the Agents API](https://developers.openai.com/api/docs/guides/agents-api/tools/plugins):
  the compatibility manifest's `skills` and `mcpServers` references.
- [Desktop-only plugins](https://developers.openai.com/plugins/deploy/desktop-only):
  local execution requirements and `${PLUGIN_ROOT}` for portable MCP packages.
  That portable format's placeholder behavior must not be assumed for this
  compatibility overlay; the CLI probe above established its actual behavior.
- [Build skills](https://developers.openai.com/plugins/build/skills): packaged
  skill workflows. Local `codex --help`, `codex plugin --help`, and generated
  `codex app-server` schemas were used to check the installed CLI protocol.
