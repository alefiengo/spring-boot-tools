#!/usr/bin/env python3
"""Optional local Codex protocol check; isolated configuration, no model turns.

Requires Codex CLI, Node and the already installed mcp-server dependencies.
Not part of test-basic.sh: CI need not install/authenticate a Codex CLI.
"""
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import tempfile
import threading


ROOT = Path(__file__).resolve().parents[2]
TOOLS = {'actuator_health', 'analyze_project', 'check_migrations',
         'initialize_project', 'openapi_endpoints', 'run_build', 'run_tests'}


def main():
    version = subprocess.run(['codex', '--version'], capture_output=True,
                             text=True, check=True).stdout.strip()
    with tempfile.TemporaryDirectory(prefix='sbt-codex-cli-') as temporary:
        base = Path(temporary)
        home = base / 'codex-home'
        home.mkdir()
        market = base / 'market'
        catalog = market / '.agents/plugins/marketplace.json'
        catalog.parent.mkdir(parents=True)
        (market / 'spring-boot-tools').symlink_to(ROOT, target_is_directory=True)
        catalog.write_text(json.dumps({
            'name': 'sbt-isolated',
            'interface': {'displayName': 'SBT isolated test'},
            'plugins': [{
                'name': 'spring-boot-tools',
                'source': {'source': 'local', 'path': './spring-boot-tools'},
                'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_USE'},
            }],
        }))
        app = base / 'application with spaces'
        app.mkdir()
        (app / 'pom.xml').write_text(
            '<project><modelVersion>4.0.0</modelVersion><groupId>test</groupId>'
            '<artifactId>app</artifactId><version>1</version></project>')
        # No credentials copied and no global installation/config changes.
        environment = {**os.environ, 'CODEX_HOME': str(home),
                       'PROJECT_ROOT': '/stale-project-context'}
        with (base / 'stderr.log').open('w+') as errors:
            child = subprocess.Popen(
                ['node', str(ROOT / 'codex/run.mjs'), 'app-server', '--stdio'],
                cwd=app, env=environment, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=errors, text=True,
                start_new_session=True,
            )
            messages = queue.Queue()

            def read():
                try:
                    for line in child.stdout:
                        messages.put(json.loads(line))
                except Exception as error:
                    messages.put({'readerError': str(error)})

            threading.Thread(target=read, daemon=True).start()
            serial = 0

            def request(method, params):
                nonlocal serial
                # This allowlist prevents accidental addition of a model turn.
                assert method in {
                    'initialize', 'plugin/read', 'plugin/install', 'thread/start',
                    'mcpServerStatus/list', 'mcpServer/tool/call',
                }, method
                serial += 1
                identifier = serial
                child.stdin.write(json.dumps({'id': identifier, 'method': method,
                                              'params': params}) + '\n')
                child.stdin.flush()
                while True:
                    try:
                        data = messages.get(timeout=30)
                    except queue.Empty:
                        errors.flush()
                        errors.seek(0)
                        raise AssertionError(f'{method} timed out:\n{errors.read()}')
                    assert 'readerError' not in data, data
                    if data.get('id') != identifier:
                        continue
                    assert 'error' not in data, data
                    return data['result']

            try:
                request('initialize', {
                    'clientInfo': {'name': 'sbt-codex-protocol-check', 'version': '1'},
                    'capabilities': {'experimentalApi': True},
                })
                selector = {'marketplacePath': str(catalog), 'pluginName': 'spring-boot-tools'}
                plugin = request('plugin/read', selector)['plugin']
                assert plugin['hooks'] == [], plugin['hooks']
                assert plugin['mcpServers'] == ['spring-boot'], plugin['mcpServers']
                assert len(plugin['skills']) == 16, plugin['skills']
                assert any(skill['name'].endswith(':spring-boot-initializr')
                           for skill in plugin['skills'])
                request('plugin/install', selector)
                # Starting an empty thread initializes local capabilities. There
                # is no turn/start, prompt, model response or behavioral eval.
                thread = request('thread/start', {
                    'cwd': str(app), 'ephemeral': True,
                    'approvalPolicy': 'never', 'sandbox': 'read-only',
                })['thread']['id']
                status = request('mcpServerStatus/list', {'threadId': thread})
                server = next(item for item in status['data'] if item['name'] == 'spring-boot')
                assert not server['toolsError'], server['toolsError']
                assert set(server['tools']) == TOOLS, server['tools']
                payload = request('mcpServer/tool/call', {
                    'threadId': thread, 'server': 'spring-boot',
                    'tool': 'analyze_project', 'arguments': {},
                })
                assert not payload.get('isError'), payload
                analyzed = json.loads(payload['content'][0]['text'])
                assert Path(analyzed['root']).resolve() == app.resolve(), analyzed
                print(f'PASS {version}: 16 skills, no hooks, 7 MCP tools, actual application cwd; no model turns')
            finally:
                # Kill only this isolated process group, including wrapper/MCP.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=10)


if __name__ == '__main__':
    main()
