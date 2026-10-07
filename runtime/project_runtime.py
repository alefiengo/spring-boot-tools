#!/usr/bin/env python3
"""Project discovery and coordinated builds, shared by hooks and MCP (stdlib only).

CLI accepts a JSON object on stdin and returns JSON on stdout. Build subprocesses
inherit the CLI process group so an MCP cancellation terminates the whole group.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

class BuildCancelled(RuntimeError):
    pass


BUILD_FILES = ('pom.xml', 'build.gradle.kts', 'build.gradle')
INVOCATION_ENV = {'CLAUDE_PROJECT_DIR', 'CLAUDE_PLUGIN_ROOT', 'CLAUDE_SESSION_ID', 'CODEX_THREAD_ID', 'MCP_SERVER_NAME', 'PROJECT_ROOT', 'PWD', 'OLDPWD', 'SHLVL', '_'}
IGNORED = {'.git', '.gradle', 'node_modules', 'target', 'build', 'dist', '__pycache__', '.idea', '.vscode'}
SOURCE_EXTENSIONS = {'.java', '.kt', '.kts', '.groovy', '.scala', '.sql', '.xml', '.yaml', '.yml', '.properties', '.gradle', '.toml', '.json', '.sh', '.proto', '.graphql', '.gql'}
MAVEN_TASKS = {'clean', 'compile', 'test-compile', 'test', 'verify', 'package', 'install', 'dependency:tree', 'flyway:info'}
GRADLE_TASKS = {'clean', 'classes', 'testClasses', 'test', 'check', 'build', 'assemble', 'dependencies'}
ALIASES = {'maven': {'check': 'verify', 'dependencies': 'dependency:tree', 'migration-status': 'flyway:info'},
           'gradle': {'compile': 'classes', 'test-compile': 'testClasses', 'verify': 'check', 'package': 'assemble', 'dependencies': 'dependencies'}}


def build_file(folder):
    return next((name for name in BUILD_FILES if (folder / name).is_file()), None)


def maven_modules(folder):
    pom = folder / 'pom.xml'
    try:
        if pom.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('Unsafe or oversized POM')
        raw = pom.read_bytes()
        if len(raw) > 8 * 1024 * 1024 or re.search(br'<!\s*(?:DOCTYPE|ENTITY)', raw, re.I):
            raise ValueError('Unsafe or oversized POM')
        xml = ET.fromstring(raw)
        return [node.text.strip() for node in xml.findall('./{*}modules/{*}module') if node.text and node.text.strip()]
    except ET.ParseError:
        return []  # discovery works on incomplete edits; the build reports syntax failures


def module_entries(root, tool):
    entries = []
    seen = {root}
    if tool == 'maven':
        queue = [root]
        while queue:
            folder = queue.pop(0)
            for name in maven_modules(folder):
                child = (folder / name).resolve()
                if not child.is_relative_to(root) or child in seen:
                    continue
                seen.add(child)
                filename = build_file(child)
                if filename:
                    entries.append({'path': child.relative_to(root).as_posix(), 'root': str(child), 'buildFile': filename, 'buildTool': 'maven' if filename == 'pom.xml' else 'gradle'})
                    queue.append(child)
    else:
        settings = next((root / name for name in ('settings.gradle.kts', 'settings.gradle') if (root / name).is_file()), None)
        if settings:
            text = settings.read_text()
            # Static includes only; dynamic settings are reported as such by discovery.
            text = re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)
            overrides = {}
            for match in re.finditer(r"project\s*\(\s*[\"'](:?[\w:./-]+)[\"']\s*\)\.projectDir\s*=\s*file\s*\(\s*[\"']([^\"']+)[\"']\s*\)", text):
                overrides[match.group(1).strip(':').replace(':', '/')] = match.group(2)
            for match in re.finditer(r'(?m)^\s*include\b\s*(?:\((.*?)\)|([^\n]+))', text, re.S):
                for name in re.findall(r'[\"\'](:?[\w:./-]+)[\"\']', match.group(1) or match.group(2)):
                    path = name.strip(':').replace(':', '/')
                    child = (root / overrides.get(path, path)).resolve()
                    if child in seen or not child.is_relative_to(root):
                        continue
                    seen.add(child)
                    entries.append({'path': path, 'root': str(child), 'buildFile': build_file(child), 'buildTool': 'gradle'})
    return sorted(entries, key=lambda item: item['path'])


def discover(location):
    location = Path(location).resolve()
    folder = location if location.is_dir() else location.parent
    found = next((p for p in (folder, *folder.parents) if build_file(p) or any((p / name).is_file() for name in ('settings.gradle.kts', 'settings.gradle'))), None)
    if not found:
        raise ValueError(f'No Maven or Gradle project found from {location}')
    filename = build_file(found)
    tool = 'maven' if filename == 'pom.xml' else 'gradle'
    root = found
    if tool == 'maven':
        for candidate in found.parents:
            if (candidate / 'pom.xml').is_file() and any(Path(m['root']) == root for m in module_entries(candidate, 'maven')):
                root = candidate
    else:
        root = next((p for p in (found, *found.parents) if any((p / s).is_file() for s in ('settings.gradle.kts', 'settings.gradle'))), found)
    filename = build_file(root)
    wrapper = root / ('mvnw' if tool == 'maven' else 'gradlew')
    return {'root': str(root), 'buildTool': tool, 'buildFile': filename, 'wrapper': str(wrapper) if wrapper.is_file() else None,
            'modules': module_entries(root, tool), 'discoveryScope': 'declared Maven modules or static Gradle includes; dynamic Gradle settings require build inspection'}


def build_plan(request):
    info = discover(request.get('projectRoot') or os.getcwd())
    tool = info['buildTool']
    tasks = request.get('tasks') or [request.get('task', 'compile')]
    if not isinstance(tasks, list) or not tasks or len(tasks) > 8 or not all(isinstance(t, str) for t in tasks):
        raise ValueError('tasks must be a nonempty list of at most eight task names')
    tasks = [ALIASES[tool].get(task, task) for task in tasks]
    allowed = MAVEN_TASKS if tool == 'maven' else GRADLE_TASKS
    if any(task not in allowed for task in tasks):
        raise ValueError(f'Unsupported {tool} task; allowed: {", ".join(sorted(allowed))}')
    profiles = request.get('profiles', [])
    if not isinstance(profiles, list) or len(profiles) > 20 or any(not isinstance(p, str) or not re.fullmatch(r'[A-Za-z0-9][\w.-]{0,99}', p) for p in profiles):
        raise ValueError('profiles must contain simple Maven profile identifiers')
    if profiles and tool != 'maven':
        raise ValueError('Maven profiles are unavailable for Gradle; use project build configuration')
    module = request.get('module')
    if module is not None:
        if not isinstance(module, str) or module not in {m['path'] for m in info['modules']}:
            raise ValueError('module must match a discovered module path')
    test_class = request.get('testClass')
    if test_class is not None and (not isinstance(test_class, str) or not re.fullmatch(r'[A-Za-z_$][\w.$]*(?:#[A-Za-z_$][\w$]*)?', test_class)):
        raise ValueError('testClass must be a qualified class name, optionally #method')
    if test_class and 'test' not in tasks:
        raise ValueError('testClass requires the test task')
    wrapper = info['wrapper']
    if wrapper:
        command = [wrapper] if os.access(wrapper, os.X_OK) else ['sh', wrapper]
    else:
        executable = shutil.which('mvn' if tool == 'maven' else 'gradle')
        if not executable:
            raise ValueError(f'Build check skipped: {"mvn" if tool == "maven" else "gradle"} and its project wrapper are unavailable in {info["root"]}.')
        command = [executable]
    if tool == 'maven':
        command += ['-B', '-ntp', *tasks]
        if profiles:
            command.append('-P' + ','.join(profiles))
        if module:
            command += ['-pl', module, '-am']
        if test_class:
            command += ['-Dtest=' + test_class]
    else:
        command += ['--console=plain', '--no-daemon', *[(':' + module.replace('/', ':') + ':' if module else '') + task for task in tasks]]
        if 'test' in tasks:
            command.append('--rerun-tasks')  # fresh reports are required for run_tests
        if test_class:
            if '#' in test_class:
                test_class = test_class.replace('#', '.')
            command += ['--tests', test_class]
    return dict(info, command=command, tasks=tasks, profiles=profiles, module=module, testClass=request.get('testClass'))


def ignored_directory(path, root):
    relative = path.relative_to(root)
    # A package may be named build, target, docs or node_modules. None of those
    # names excludes source files when they occur inside a source tree.
    if 'src' in relative.parts or path.name not in IGNORED:
        return False
    if path.name in ('target', 'build', 'dist'):
        if build_file(path) or any((path / settings).is_file() for settings in ('settings.gradle', 'settings.gradle.kts')):
            return False  # a module can legitimately be named build or target
        # Output names outside a module root may be real input directories.
        return path.parent == root or bool(build_file(path.parent)) or any(Path(module['root']) == path.parent for module in module_entries(root, 'gradle'))
    return True


def relevant(path, root):
    relative = path.relative_to(root)
    if 'src' in relative.parts:
        return True
    if any(ignored_directory(root / Path(*relative.parts[:index + 1]), root) for index in range(len(relative.parts) - 1)):
        return False
    # Build scripts can read arbitrary configuration names, binary fixtures,
    # extensionless inputs and data under docs. Exempt only pure prose outside src.
    return path.suffix.lower() not in ('.md', '.rst', '.adoc')


def fingerprint(root):
    root = Path(root)
    digest = hashlib.sha256(Path(__file__).read_bytes())
    count, total = 0, 0
    environment = {key: value for key, value in os.environ.items() if key not in INVOCATION_ENV}
    def walk_error(error):
        raise error
    for folder, directories, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        for name in directories:
            if not ignored_directory(Path(folder) / name, root) and (Path(folder) / name).is_symlink():
                raise ValueError(f'Cannot fingerprint symlinked build directory: {Path(folder) / name}')
        directories[:] = sorted(d for d in directories if not ignored_directory(Path(folder) / d, root) and not (Path(folder) / d).is_symlink())
        for name in sorted(filenames):
            path = Path(folder) / name
            if not relevant(path, root):
                continue
            if path.is_symlink():
                # Avoid treating uninspected external sources as verified.
                raise ValueError(f'Cannot fingerprint symlinked build input: {path}')
            metadata = path.stat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f'Cannot fingerprint nonregular build input: {path}')
            if metadata.st_size + total > 128 * 1024 * 1024:
                raise ValueError('Build input fingerprint exceeds safety limit; verification will run without reuse')
            with path.open('rb') as source:
                content = source.read(128 * 1024 * 1024 - total + 1)
            digest.update((metadata.st_mode & 0o777).to_bytes(8, 'big'))
            total += len(content)
            count += 1
            if total > 128 * 1024 * 1024 or count > 50000:
                raise ValueError('Build input fingerprint exceeds safety limit; verification will run without reuse')
            relative = path.relative_to(root).as_posix().encode()
            for variable in re.findall(rb'\$\{([A-Za-z_][A-Za-z0-9_]*)', content):
                key = variable.decode()
                environment[key] = os.environ.get(key)
            digest.update(len(relative).to_bytes(8, 'big') + relative)
            digest.update(len(content).to_bytes(8, 'big') + content)
    digest.update(json.dumps(environment, sort_keys=True).encode())
    return digest.hexdigest()


def state_dir(root):
    key = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()
    folder = Path(tempfile.gettempdir()) / f'sb-tools-build-{os.getuid()}-{key}'
    try:
        folder.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Unsafe shared build state directory')
    return folder


def state_read(folder):
    path = folder / 'state.json'
    if path.is_symlink():
        raise ValueError('Unsafe build state file')
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def state_write(folder, data):
    handle, temporary = tempfile.mkstemp(prefix='state-', dir=folder)
    try:
        with os.fdopen(handle, 'w') as output:
            json.dump(data, output)
        os.replace(temporary, folder / 'state.json')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def acquire(folder, deadline):
    lock = folder / 'build.lock'
    handle = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    stream = os.fdopen(handle, 'a')
    try:
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return stream
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Timed out waiting for the project build lock')
                time.sleep(.05)
    except BaseException:
        stream.close()
        raise


def terminate(process, own_group):
    if own_group:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        # CLI must remain in its caller's group. Kill its build descendants only.
        try:
            listing = subprocess.run(['ps', '-eo', 'pid=,ppid=,pgid='], capture_output=True, text=True, timeout=2).stdout
            rows = [tuple(map(int, line.split())) for line in listing.splitlines() if len(line.split()) == 3]
            pairs = [(pid, parent) for pid, parent, group in rows]
            ids = {process.pid}
            if os.getpgrp() == os.getpid():
                ids |= {pid for pid, parent, group in rows if group == os.getpgrp() and pid != os.getpid()}
            while True:
                children = {pid for pid, parent in pairs if parent in ids}
                if children <= ids:
                    break
                ids |= children
            for pid in sorted(ids, reverse=True):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            if process.poll() is None:
                process.kill()
    process.wait()


def test_report_snapshot(root):
    """Read report contents while the caller owns the project build lock."""
    reports = {}
    excluded = {'.git', 'node_modules', '.gradle', 'src', '.idea', '.mvn', '.claude'}
    for folder, directories, _ in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in excluded
                                and not (Path(folder) / name).is_symlink())
        for name in ('target', 'build'):
            if name not in directories:
                continue
            directories.remove(name)
            output = Path(folder) / name
            for report_dir in (('surefire-reports', 'failsafe-reports') if name == 'target' else ('test-results',)):
                for report_folder, children, files in os.walk(output / report_dir, followlinks=False):
                    children[:] = sorted(child for child in children if child not in IGNORED
                                         and not (Path(report_folder) / child).is_symlink())
                    for filename in sorted(files):
                        file = Path(report_folder) / filename
                        if file.suffix != '.xml' or file.is_symlink():
                            continue
                        metadata = file.stat()
                        if metadata.st_size > 8 * 1024 * 1024:
                            raise ValueError(f'Test report exceeds 8 MiB limit: {file}')
                        content = file.read_bytes()
                        if len(content) > 8 * 1024 * 1024:
                            raise ValueError(f'Test report exceeds 8 MiB limit: {file}')
                        reports[str(file)] = ((metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_size),
                                              content.decode('utf-8', 'replace'))
    return reports


def run_build(request, own_process_group=False):
    plan = build_plan(request)
    timeout = request.get('timeoutMs', 300000)
    limit = request.get('maxOutputBytes', 4 * 1024 * 1024)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 600000:
        raise ValueError('timeoutMs must be between 1 and 600000')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 16 * 1024 * 1024:
        raise ValueError('maxOutputBytes must be between 1 and 16777216')
    start = time.monotonic()
    deadline = start + timeout / 1000
    folder = state_dir(plan['root'])
    with acquire(folder, deadline):
        collect_reports = request.get('collectTestReports', False)
        if collect_reports and plan['tasks'] != ['test']:
            raise ValueError('collectTestReports requires only the test task')
        reports_before = test_report_snapshot(plan['root']) if collect_reports else {}
        cache_diagnostic = None
        try:
            before = fingerprint(plan['root'])
        except (OSError, ValueError) as error:
            before, cache_diagnostic = None, str(error)
        state = state_read(folder)
        verification = plan['tasks'] in (['verify'], ['check'])
        scope = json.dumps([plan['buildTool'], plan['tasks'], plan['profiles'], plan['module'], plan['testClass']], separators=(',', ':'))
        scopes = state.get('verifiedScopes', {})
        if not isinstance(scopes, dict):
            scopes = {}
        if before is not None and request.get('reuse', True) and verification and scopes.get(scope) == before:
            return dict(plan, ok=True, exitCode=0, stdout='', stderr='', reused=True, fingerprint=before, timedOut=False, outputLimitExceeded=False, inputsChanged=False, cacheable=True, cacheDiagnostic=None, durationMs=round((time.monotonic() - start) * 1000))
        # Any build attempt invalidates previous success; failure/cancellation cannot
        # preserve a stale verification after clean or other mutating build tasks.
        state.pop('verifiedScopes', None)
        state.pop('verifiedFingerprint', None)
        state_write(folder, state)
        process = subprocess.Popen(plan['command'], cwd=plan['root'], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=own_process_group)
        selector = selectors.DefaultSelector()
        outputs = {'stdout': bytearray(), 'stderr': bytearray()}
        for name in outputs:
            selector.register(getattr(process, name), selectors.EVENT_READ, name)
        timed_out, too_large, used = False, False, 0
        previous_handlers = {}
        if own_process_group:
            def interrupted(signum, frame):
                raise BuildCancelled(f'Build cancelled by signal {signum}')
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[signum] = signal.signal(signum, interrupted)
        try:
            while selector.get_map():
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                for key, _ in selector.select(min(.1, max(0, deadline - time.monotonic()))):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    remaining = max(0, limit - used)
                    outputs[key.data].extend(block[:remaining])
                    used += len(block)
                    if used > limit:
                        too_large = True
                        break
                if too_large:
                    break
            if timed_out or too_large:
                terminate(process, own_process_group)
            else:
                try:
                    process.wait(timeout=max(.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    terminate(process, own_process_group)
            try:
                after = fingerprint(plan['root'])
            except (OSError, ValueError) as error:
                after, cache_diagnostic = None, str(error)
            ok = process.returncode == 0 and not timed_out and not too_large
            if ok and verification and before is not None and before == after:
                state['verifiedScopes'] = {scope: after}
                state.pop('pendingFingerprint', None)
                state_write(folder, state)
                try:
                    (folder / 'pending.json').unlink()
                except FileNotFoundError:
                    pass
            report_result = {}
            if collect_reports:
                reports_after = test_report_snapshot(plan['root'])
                fresh = [{'file': file, 'content': value[1]} for file, value in reports_after.items()
                         if reports_before.get(file) != value]
                report_result = {'testReports': fresh, 'staleReportsIgnored': len(reports_after) - len(fresh)}
            return dict(plan, **report_result, ok=ok, exitCode=process.returncode, **{name: bytes(value).decode('utf-8', 'replace') for name, value in outputs.items()},
                        reused=False, durationMs=round((time.monotonic() - start) * 1000), fingerprint=before, inputsChanged=before is not None and after is not None and before != after, cacheable=before is not None and after is not None, cacheDiagnostic=cache_diagnostic, timedOut=timed_out, outputLimitExceeded=too_large)
        finally:
            selector.close()
            for name in outputs:
                getattr(process, name).close()
            try:
                if own_process_group or process.poll() is None:
                    terminate(process, own_process_group)
            finally:
                for signum, handler in previous_handlers.items():
                    signal.signal(signum, handler)


def mark_pending(location):
    info = discover(location)
    folder = state_dir(info['root'])
    # A hint has its own file, so editing never waits for an ongoing build. The
    # actual success gate remains locked and compares all current input contents.
    handle, temporary = tempfile.mkstemp(prefix='pending-', dir=folder)
    try:
        with os.fdopen(handle, 'w') as output:
            json.dump({'pending': True}, output)
        os.replace(temporary, folder / 'pending.json')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return info


def main():
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError('Expected JSON object')
        action = request.get('action', 'discover')
        if action == 'discover':
            result = discover(request.get('projectRoot') or os.getcwd())
        elif action == 'plan':
            result = build_plan(request)
        elif action == 'run':
            result = run_build(request)
        elif action == 'mark':
            result = mark_pending(request.get('projectRoot') or os.getcwd())
        else:
            raise ValueError('Unknown runtime action')
        print(json.dumps(result))
    except (OSError, ValueError, TypeError, TimeoutError, BuildCancelled) as error:
        print(json.dumps({'ok': False, 'error': str(error), 'exitCode': 1}))
        sys.exit(1)


if __name__ == '__main__':
    main()
