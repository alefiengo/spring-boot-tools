#!/usr/bin/env python3
"""Execute extracted Pact/Gatling docs against an independent local provider."""
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

project, cache = sys.argv[1:]
state = {'broken': False, 'requests': 0}


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        state['requests'] += 1
        user = {'id': 42, 'email': 'user@example.com', 'extra': 'additive field'}
        if state['broken']:
            user.pop('email')
        if self.path.startswith('/api/v1/users?'):
            payload = {'data': [user]}
        elif self.path == '/api/v1/users/42':
            payload = user
        else:
            self.send_error(404)
            return
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base = ['mvn', '-B', '-ntp', '-Dpact_do_not_track=true', f'-Dmaven.repo.local={cache}']


def run(*args, expect_failure=False):
    result = subprocess.run(base + list(args), cwd=project, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
    if expect_failure:
        if result.returncode == 0 or 'email' not in result.stdout or 'Actual map is missing the following keys: email' not in result.stdout:
            print(result.stdout)
            raise SystemExit('Expected a verified email contract mismatch, not an unrelated failure')
        print('PASS: breaking provider response rejected by Pact verification')
    elif result.returncode:
        print(result.stdout)
        raise SystemExit(result.returncode)
    else:
        print(result.stdout)


try:
    run('test', '-Dtest=UserClientPactTest', '-Dpact.rootDir=target/pacts')
    pacts = list((Path(project) / 'target/pacts').glob('*.json'))
    if len(pacts) != 1 or not json.loads(pacts[0].read_text()).get('interactions'):
        raise SystemExit('Consumer must generate a non-empty pact')
    provider_args = ('test', '-Dtest=UserServiceProviderTest', f'-Dprovider.port={server.server_port}')
    run(*provider_args)
    print('PASS: additive provider field accepted; generated consumer pact verified')
    state['broken'] = True
    run(*provider_args, expect_failure=True)
    state['broken'] = False
    before = state['requests']
    run('gatling:test', '-Dgatling.simulationClass=com.example.fixture.UsersLoadSimulation',
        f'-DbaseUrl=http://127.0.0.1:{server.server_port}', '-DusersPerSecond=2',
        '-DrampSeconds=1', '-DsteadySeconds=2', '-DthinkSeconds=0')
    if state['requests'] - before < 4:
        raise SystemExit('Gatling must perform list and detail requests; a zero-request run cannot pass')
    print(f"PASS: Gatling executed {state['requests'] - before} local requests with response-time/error assertions")
finally:
    server.shutdown()
    server.server_close()
