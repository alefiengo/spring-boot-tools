#!/usr/bin/env python3
"""Offline installation diagnostics. No host, build, install or config mutation."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time


LIMIT = 65536
TIMEOUT = 5
# Supported codex/mcp.json loader, compared as data and never evaluated here.
CODEX_LOADER = (
    "const root = process.env.SBT_PLUGIN_ROOT; if (!root || !require('node:path').isAbsolute(root)) { "
    "console.error('Launch Codex with node /absolute/plugin/path/codex/run.mjs (SBT_PLUGIN_ROOT is required).'); "
    "process.exit(1); } import(require('node:url').pathToFileURL(require('node:path').join(root, 'codex', "
    "'launch.mjs')).href).catch(error => { console.error(error.message); process.exitCode = 1; });"
)


def probe(argv, cwd, input_data=None):
    """Bound time/output and kill descendants, including on a stuck pipe."""
    env = {k: v for k, v in os.environ.items() if k not in {
        'NODE_OPTIONS', 'NODE_PATH', 'PYTHONPATH', 'PYTHONHOME',
        'PYTHONSTARTUP', 'PYTHONINSPECT',
        'NODE_V8_COVERAGE', 'NODE_COMPILE_CACHE', 'NODE_COMPILE_CACHE_PORTABLE',
        'NODE_REDIRECT_WARNINGS', 'NODE_REPL_HISTORY',
        'PYTHONPYCACHEPREFIX', 'PYTHONHISTFILE',
    }}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    with subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True,
                          stdin=subprocess.PIPE if input_data else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as child:
        try:
            if input_data:
                child.stdin.write(input_data.encode())
                child.stdin.close()
            output = bytearray()
            deadline = time.monotonic() + TIMEOUT
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError('probe exceeded 5 second limit')
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            output.extend(chunk)
                            if len(output) > LIMIT:
                                raise ValueError('probe exceeded 64 KiB output limit')
                child.wait(timeout=max(0.001, deadline - time.monotonic()))
            text = output.decode('utf-8', errors='replace').strip()
            if child.returncode:
                raise ValueError(f'probe exited {child.returncode}: {text[:1000]}')
            return text
        finally:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


def read_json(path):
    with path.open('rb') as source:
        raw = source.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('JSON exceeds 1 MiB limit')
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError('expected JSON object')
    return result


def diagnose(args):
    root = Path(args.plugin_root).resolve()
    workspace = Path(args.workspace).resolve()
    checks = []

    def add(identifier, status, message, **evidence):
        checks.append(dict(id=identifier, status=status, message=message, **evidence))

    def check(identifier, action):
        try:
            evidence = action()
            add(identifier, 'ok', 'Check passed', **(evidence or {}))
            return evidence
        except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError,
                subprocess.SubprocessError) as error:
            add(identifier, 'error', str(error))
            return None

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    check('os', lambda: require(platform.system() in ('Linux', 'Darwin'),
                               'Shared runtime supports Linux/macOS; native Windows is unsupported'))
    if platform.system() not in ('Linux', 'Darwin'):
        # POSIX process-group and selector probes are not portable to Windows.
        return report(root, workspace, checks)

    def executable(name, minimum):
        selected = shutil.which(name)
        require(selected, f'{name} missing from PATH (or selected executable unavailable)')
        selected = str(Path(selected).resolve())
        output = probe([selected, '--version'], root if root.is_dir() else Path(__file__).parent)
        match = re.fullmatch(r'(?:v|Python )?(\d+)\.(\d+)(?:\.(\d+))?', output)
        require(match, f'Unrecognized version: {output[:200]}')
        version = tuple(int(v or 0) for v in match.groups())
        require(version >= minimum, f'{name} {output} is too old; need >= {".".join(map(str, minimum))}')
        return {'executable': selected, 'version': output}

    node = check('node', lambda: executable(args.node, (20, 3, 0)))
    python = check('python', lambda: executable(args.python, (3, 11, 0)))

    def bundle_file(relative, directory=False):
        require(isinstance(relative, str), 'registration path must be a string')
        path = (root / relative).resolve()
        require(not Path(relative).is_absolute() and path.is_relative_to(root),
                f'Path escapes plugin bundle: {relative}')
        require(path.is_dir() if directory else path.is_file(), f'Missing bundle path: {relative}')
        return path

    documents = {}
    for relative in ('.claude-plugin/plugin.json', '.claude-plugin/marketplace.json',
                     '.codex-plugin/plugin.json', '.mcp.json', 'codex/mcp.json',
                     'package.json', 'package-lock.json'):
        def load(relative=relative):
            documents[relative] = read_json(bundle_file(relative))
        check('json:' + relative, load)

    def registrations():
        claude = documents['.claude-plugin/plugin.json']
        codex = documents['.codex-plugin/plugin.json']
        require(claude['name'] == codex['name'] == 'spring-boot-tools', 'Plugin names disagree')
        bundle_file(codex['skills'], directory=True)
        require(bundle_file(codex['mcpServers']) == root / 'codex/mcp.json',
                'Codex MCP registration must reference codex/mcp.json')
        require(codex['hooks'] == {'hooks': {}}, 'Codex must explicitly disable Claude hooks')
        require(codex['requires_local_executor'] is True, 'Codex requires a local executor')
        server = documents['codex/mcp.json']['mcpServers']['spring-boot']
        require(server['command'] == 'node', 'Codex MCP command must be node')
        require(server['args'] == ['-e', CODEX_LOADER],
                'Codex launcher registration must match the supported codex/launch.mjs loader template')
        require(isinstance(server['env_vars'], list)
                and all(isinstance(value, str) for value in server['env_vars'])
                and 'SBT_PLUGIN_ROOT' in server['env_vars'],
                'Codex env_vars must be a string array forwarding SBT_PLUGIN_ROOT')
        legacy = documents['.mcp.json']['mcpServers']['spring-boot']
        require(legacy['command'] == 'node'
                and legacy['args'] == ['${CLAUDE_PLUGIN_ROOT}/mcp-server/index.js']
                and legacy.get('cwd', '${CLAUDE_PROJECT_DIR}') == '${CLAUDE_PROJECT_DIR}'
                and legacy['env']['PROJECT_ROOT'] == '${CLAUDE_PROJECT_DIR}',
                'Malformed Claude MCP registration')
        for path in ('codex/run.mjs', 'codex/launch.mjs', 'mcp-server/index.js',
                     'runtime/project_runtime.py', 'runtime/sql_lint.py',
                     'runtime/project_initializer.py'):
            bundle_file(path)
    check('registrations', registrations)

    def versions():
        market = documents['.claude-plugin/marketplace.json']['plugins']
        require(isinstance(market, list) and all(isinstance(item, dict) for item in market),
                'Marketplace plugins must be an array of objects')
        entries = [item for item in market if item['name'] == 'spring-boot-tools']
        require(len(entries) == 1 and entries[0]['source'] == './', 'Malformed marketplace registration')
        lock = documents['package-lock.json']
        versions = {name: documents[name]['version'] for name in (
            '.claude-plugin/plugin.json', '.codex-plugin/plugin.json', 'package.json')}
        versions.update(marketplace=entries[0]['version'], lock=lock['version'],
                        lock_package=lock['packages']['']['version'])
        source = bundle_file('mcp-server/index.js').read_text()
        match = re.search(r'version:\s*[\"\']([^\"\']+)[\"\']', source)
        require(match, 'Missing MCP server version literal')
        versions['server'] = match.group(1)
        require(all(isinstance(v, str) and re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][\w.-]+)?', v)
                    for v in versions.values()), 'Malformed version')
        require(len(set(versions.values())) == 1, f'Bundle versions disagree: {versions}')
        package = documents['package.json']
        require(package['dependencies'] == lock['packages']['']['dependencies'],
                'Package and lock dependency declarations disagree')
        return {'version': versions['server']}
    check('versions', versions)

    def dependencies():
        require(node, 'Dependency resolution requires a supported Node executable')
        declarations = documents['package.json']['dependencies']
        require(isinstance(declarations, dict) and bool(declarations)
                and all(isinstance(name, str) and isinstance(value, str)
                        for name, value in declarations.items()),
                'Package dependencies must be a nonempty object of version strings')
        names = [*declarations, '@modelcontextprotocol/server/stdio']
        # Dependencies are installed at the plugin root: resolve from <root>/package.json
        # so a leftover mcp-server/node_modules cannot satisfy the check. Node 20.3
        # requires the flag both for import.meta.resolve and its parent URL argument.
        # Resolve ESM import conditions, then stat: newer Node may return file URLs
        # even when their target is missing. Only builtins load.
        code = """import {statSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const parent=pathToFileURL(process.argv[1]).href;
const names=JSON.parse(process.argv[2]);
const missing=names.filter(n=>{try{
  const target=new URL(import.meta.resolve(n,parent));
  return target.protocol!=='file:' || !statSync(target).isFile();
}catch{return true}});
console.log(JSON.stringify({missing}));"""
        result = json.loads(probe([node['executable'], '--no-warnings',
                                  '--experimental-import-meta-resolve', '--input-type=module', '-e', code,
                                  str(root / 'package.json'), json.dumps(names)], root))
        require(not result['missing'], f'Missing MCP dependencies: {result["missing"]}; install locked dependencies separately')
        if (root / 'mcp-server/node_modules').exists():
            add('stale_dependencies', 'warning',
                'mcp-server/node_modules shadows the plugin-root dependencies for the MCP server; remove it')
        return {'dependencies': names, 'scope': 'ESM import target resolution and file existence only; no dependency imports'}
    check('dependencies', dependencies)

    def environment_root():
        value = os.environ.get('SBT_PLUGIN_ROOT')
        if value is None:
            return {'scope': 'unset; codex/run.mjs supplies this in its child process'}
        require(Path(value).is_absolute(), 'SBT_PLUGIN_ROOT must be absolute')
        require(Path(value).resolve() == root, 'SBT_PLUGIN_ROOT points to a different bundle; use the matching wrapper')
        return {'path': value}
    check('sbt_plugin_root', environment_root)
    check('workspace', lambda: require(workspace.is_dir() and workspace != root
                                       and not workspace.is_relative_to(root),
                                       'Workspace must be an existing application directory outside the plugin bundle'))
    if os.environ.get('PROJECT_ROOT') and Path(os.environ['PROJECT_ROOT']).resolve() != workspace:
        add('inherited_project_root', 'warning', 'Inherited PROJECT_ROOT differs from workspace; Codex launcher overrides it')

    if args.project:
        def discover():
            require(python, 'Project discovery requires supported Python')
            intended = Path(args.project).resolve()
            require(intended.is_dir(), 'Intended project must be an existing directory')
            def run(location):
                data = json.loads(probe([python['executable'], '-I', '-B',
                                         str(bundle_file('runtime/project_runtime.py'))], root,
                                        json.dumps({'action': 'discover', 'projectRoot': str(location)})))
                require('root' in data, f'Runtime discovery failed: {data}')
                return data
            actual = run(workspace)
            expected = run(intended)
            require(Path(actual['root']).resolve() == Path(expected['root']).resolve(),
                    f'Workspace discovers {actual["root"]}, intended project discovers {expected["root"]}')
            if Path(expected['root']).resolve() != intended:
                # Legitimate for a module of a reactor; otherwise another build would run.
                add('discovered_root', 'warning',
                    f'Intended project {intended} is discovered as part of {expected["root"]}; builds and its wrapper run there')
            return {'discovery': actual, 'intendedProject': str(intended)}
        check('project_discovery', discover)
    else:
        add('project_discovery', 'skipped', 'Use --project PATH to compare workspace and intended project via shared runtime')
    return report(root, workspace, checks)


def report(root, workspace, checks):
    code = 1 if any(check['status'] == 'error' for check in checks) else 0
    return dict(schemaVersion=1, ok=code == 0, exitCode=code, pluginRoot=str(root),
                workspace=str(workspace), checks=checks)


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog='Exit codes: 0 checks passed; 1 diagnostic failures; 2 invalid CLI arguments. Probes: 5 seconds / 64 KiB each. No protocol check is run.')
    parser.add_argument('--json', action='store_true', help='emit a schemaVersion=1 JSON report')
    parser.add_argument('--plugin-root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--workspace', default=os.getcwd(), help='actual/intended MCP process cwd (default: current directory)')
    parser.add_argument('--project', help='optional intended project; compare shared-runtime discovery roots')
    parser.add_argument('--node', default='node', help='trusted Node executable, no shell arguments')
    parser.add_argument('--python', default='python3', help='trusted Python executable, no shell arguments')
    args = parser.parse_args()
    result = diagnose(args)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f'Install doctor: {"PASS" if result["ok"] else "FAIL"} (offline)')
        print(f'Plugin: {result["pluginRoot"]}\nWorkspace: {result["workspace"]}')
        for check in result['checks']:
            print(f'{check["status"].upper()} {check["id"]}: {check["message"]}')
    return result['exitCode']


if __name__ == '__main__':
    sys.exit(main())
