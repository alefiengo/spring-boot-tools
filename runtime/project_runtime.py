#!/usr/bin/env python3
"""Project discovery and coordinated builds, shared by hooks and MCP (stdlib only).

CLI accepts a JSON object on stdin and returns JSON on stdout. Builds under MCP
inherit its dedicated CLI process group so host cancellation reaches the whole group.
When a direct CLI invocation shares a shell session, the build starts a separate
session so cleanup can identify reparented children without signalling the shell.
Send TERM, INT or HUP to the runtime for coordinated cancellation. A group KILL
cannot be forwarded to that separate session. Linux also sends TERM to the build
leader on parent death; macOS has no equivalent.
"""
import ctypes
import errno
import fcntl
import functools
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
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
import xml.parsers.expat

class BuildCancelled(RuntimeError):
    pass


BUILD_FILES = ('pom.xml', 'build.gradle.kts', 'build.gradle')
# Host metadata differs between MCP, hooks and agent sessions. Explicit ${VAR}
# references in project files still capture these values as build inputs.
INVOCATION_PREFIXES = ('CLAUDE_', 'CODEX_', 'HERDR_')
INVOCATION_ENV = {
    'MCP_SERVER_NAME', 'PROJECT_ROOT', 'PWD', 'OLDPWD', 'SHLVL', '_',
    # Terminal/window identifiers, emulator version and multiplexer endpoints.
    'TERM_SESSION_ID', 'WINDOWID', 'WT_SESSION', 'ITERM_SESSION_ID', 'KITTY_WINDOW_ID',
    'TERM_PROGRAM_VERSION', 'TMUX', 'TMUX_PANE', 'STY',
    # Login-session addresses, agent sockets and the interactive signing terminal.
    'SSH_CLIENT', 'SSH_CONNECTION', 'SSH_TTY', 'SSH_AUTH_SOCK', 'GPG_TTY',
    # VS Code's per-session IPC sockets.
    'VSCODE_IPC_HOOK', 'VSCODE_IPC_HOOK_CLI', 'VSCODE_GIT_IPC_HANDLE',
    # Agent identity and its noninteractive Git editor and Corepack auto-pin policy.
    'CLAUDECODE', 'AI_AGENT', 'GIT_EDITOR', 'COREPACK_ENABLE_AUTO_PIN',
    # WSL interop socket and Warp's terminal-session identifier.
    'WSL_INTEROP', 'WARP_TERMINAL_SESSION_UUID',
}
IGNORED = {'.git', '.gradle', 'node_modules', 'target', 'build', 'dist', '__pycache__', '.idea', '.vscode'}
MAVEN_TASKS = {'clean', 'compile', 'test-compile', 'test', 'verify', 'package', 'install', 'dependency:tree', 'flyway:info'}
GRADLE_TASKS = {'clean', 'classes', 'testClasses', 'test', 'check', 'build', 'assemble', 'dependencies'}
ALIASES = {'maven': {'check': 'verify', 'dependencies': 'dependency:tree', 'migration-status': 'flyway:info'},
           'gradle': {'compile': 'classes', 'test-compile': 'testClasses', 'verify': 'check', 'package': 'assemble'}}
SETTINGS_FILES = ('settings.gradle.kts', 'settings.gradle')
WRAPPERS = ('mvnw', 'gradlew')
BUILD_DESCRIPTORS = frozenset((*BUILD_FILES, *SETTINGS_FILES))
BUILD_INPUT_NAMES = frozenset(('pom.xml', 'gradle.properties', *WRAPPERS, 'mvnw.cmd', 'gradlew.bat'))
PROTECTED_TREES = frozenset(('src', '.mvn', 'gradle'))
MAX_WALK_ENTRIES = 100000
MAX_REPORT_BYTES = 8 * 1024 * 1024


def build_file(folder):
    return next((name for name in BUILD_FILES if (folder / name).is_file()), None)


def has_settings(folder):
    return any((folder / name).is_file() for name in SETTINGS_FILES)


@functools.lru_cache(maxsize=None)
def private_group(gid):
    """A user-private group (Linux umask 002 convention), never a shared group.

    macOS gives every user the shared primary group staff, so group write is
    never accepted there. Elsewhere the group must be named after the user and
    list no other members.
    """
    if sys.platform == 'darwin' or gid != os.getgid():
        return False
    try:
        user = pwd.getpwuid(os.getuid()).pw_name
        group = grp.getgrgid(gid)
    except KeyError:
        return False
    return group.gr_name == user and set(group.gr_mem) <= {user}


def untrusted_reason(path):
    """None when the current user owns the path and no other user can write it."""
    try:
        info = os.stat(path)
    except OSError as error:
        return f'{path} cannot be inspected ({error.strerror})'
    if info.st_uid != os.getuid():
        return f'{path} is owned by uid {info.st_uid}, not the current user'
    if info.st_mode & 0o002:
        return f'{path} is writable by all users (mode {stat.S_IMODE(info.st_mode):04o})'
    if info.st_mode & 0o020 and not private_group(info.st_gid):
        return f'{path} is writable by a shared group (mode {stat.S_IMODE(info.st_mode):04o})'
    return None


def trusted(path):
    return untrusted_reason(path) is None


def build_inputs(folder):
    """Files and directories whose content selects or runs build code before any plugin.

    Build/settings files, wrappers, gradle.properties, .mvn/** (extensions.xml,
    jvm.config, maven.config, wrapper properties/jar) and gradle/wrapper/*.
    Their directories count too: a writable directory lets another user replace them.
    """
    paths = [folder / name for name in (*BUILD_FILES, *SETTINGS_FILES, *WRAPPERS, 'gradle.properties')]
    for directory, recursive in ((folder / '.mvn', True), (folder / 'gradle', False), (folder / 'gradle/wrapper', False)):
        if not directory.is_dir() or directory.is_symlink():
            paths.append(directory)
            continue
        entries = [directory, *(directory.rglob('*') if recursive else directory.iterdir())]
        paths += entries[:500]
    return [path for path in paths if os.path.lexists(path)]


def untrusted_build_dir(folder):
    """Reason another user could control the build run from folder, or None."""
    return untrusted_reason(folder) or next(filter(None, map(untrusted_reason, build_inputs(folder))), None)


def trusted_build_dir(folder):
    return untrusted_build_dir(folder) is None


def drvfs(path, mounts=None):
    """True on a WSL Windows drive (drvfs/9p) mounted without Unix metadata."""
    try:
        mounts = (Path('/proc/self/mounts').read_text() if mounts is None else mounts).splitlines()
    except OSError:
        return False
    best = None
    for line in mounts:
        fields = line.split()
        if len(fields) < 4:
            continue
        point = fields[1].replace('\\040', ' ')
        if (str(path) == point or str(path).startswith(point.rstrip('/') + '/')) and (best is None or len(point) > len(best[0])):
            best = (point, fields[2], fields[3])
    # 9p carries drvfs options inside aname, separated by ';'.
    return bool(best) and (best[1] == 'drvfs' or 'aname=drvfs' in best[2]) and 'metadata' not in re.split('[,;]', best[2])


def trust_advice(folder, reason):
    advice = (f'{reason}. Its build and wrapper are not run. Restrict it (chmod go-w) if you own it, '
              'or move the project to a directory only you can write.')
    if drvfs(folder):
        advice += (' This is a WSL Windows drive mounted without Unix permissions, where every file appears as '
                   'mode 0777: enable them with the drvfs "metadata" option (/etc/wsl.conf: [automount] '
                   'options = "metadata,umask=022", then wsl --shutdown) or work under the Linux home directory.')
    return advice


def reject_doctype(raw):
    """Refuse DTDs in any declared encoding (UTF-16 bypasses a byte-pattern check)."""
    def refuse(*_):
        raise ValueError('Unsafe or oversized POM')
    parser = xml.parsers.expat.ParserCreate()
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.Parse(raw, True)


def maven_modules(folder):
    pom = folder / 'pom.xml'
    try:
        if pom.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('Unsafe or oversized POM')
        raw = pom.read_bytes()
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError('Unsafe or oversized POM')
        reject_doctype(raw)
        document = ET.fromstring(raw)
        # Membership, not activation: profile modules must share the reactor's
        # lock even when invoked directly. The build still selects active profiles.
        nodes = document.findall('./{*}modules/{*}module') + document.findall('./{*}profiles/{*}profile/{*}modules/{*}module')
        return list(dict.fromkeys(node.text.strip() for node in nodes if node.text and node.text.strip()))
    except (ET.ParseError, xml.parsers.expat.ExpatError):
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
        settings = next((root / name for name in SETTINGS_FILES if (root / name).is_file()), None)
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
    found = next((p for p in (folder, *folder.parents) if build_file(p) or has_settings(p)), None)
    if not found:
        raise ValueError(f'No Maven or Gradle project found from {location}')
    # The requested directory is the caller's choice (see SECURITY.md trust
    # boundaries). Ancestors are not: their build and wrapper would execute.
    reason = untrusted_build_dir(found) if found != folder else None
    if reason:
        raise ValueError(f'Refusing untrusted ancestor build directory {found}: ' + trust_advice(found, reason))
    filename = build_file(found)
    tool = 'maven' if filename == 'pom.xml' else 'gradle'
    root = found
    # An ancestor becomes the root only through verifiable reactor membership
    # in a directory that another user cannot control. Its wrapper would run.
    # A refused enclosing build is reported, never skipped silently.
    warnings = []
    if tool == 'maven':
        for candidate in found.parents:
            if (candidate / 'pom.xml').is_file() and any(Path(m['root']) == root for m in module_entries(candidate, 'maven')):
                reason = untrusted_build_dir(candidate)
                if reason:
                    warnings.append(f'Ignored enclosing Maven reactor {candidate}; using {root} alone: ' + trust_advice(candidate, reason))
                    break
                root = candidate
    elif not has_settings(found):
        own_wrapper = filename is not None and (found / 'gradlew').is_file()
        candidate = next((p for p in found.parents if has_settings(p)), None)
        if candidate is not None and (not own_wrapper or any(Path(m['root']) == found for m in module_entries(candidate, 'gradle'))):
            reason = untrusted_build_dir(candidate)
            if reason:
                warnings.append(f'Ignored enclosing Gradle settings {candidate}; using {found} alone: ' + trust_advice(candidate, reason))
            else:
                root = candidate
    filename = build_file(root)
    wrapper = root / ('mvnw' if tool == 'maven' else 'gradlew')
    return {'root': str(root), 'buildTool': tool, 'buildFile': filename, 'wrapper': str(wrapper) if wrapper.is_file() else None,
            'modules': module_entries(root, tool), 'discoveryScope': 'declared Maven modules or static Gradle includes; dynamic Gradle settings require build inspection',
            **({'discoveryWarnings': warnings} if warnings else {})}


def build_plan(request):
    info = discover(request.get('projectRoot') or os.getcwd())
    tool = info['buildTool']
    tasks = request.get('tasks') or [request.get('task', 'compile')]
    if not isinstance(tasks, list) or not tasks or len(tasks) > 8 or not all(isinstance(t, str) for t in tasks):
        raise ValueError('tasks must be a nonempty list of at most eight task names')
    if tool == 'gradle' and 'migration-status' in tasks:
        raise ValueError("Flyway migration status via the build tool is supported only for Maven (flyway:info). "
                         "For Gradle, run the application's configured flywayInfo task manually.")
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
    if test_class and tool == 'gradle' and info['modules'] and not module:
        raise ValueError('For testClass in a multi-project Gradle build, specify the module containing the test. '
                         'Gradle applies --tests to each selected test task and can fail in an unrelated project.')
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
            command += ['-Dtest=' + test_class, '-Dsurefire.failIfNoSpecifiedTests=false']
    else:
        command += ['--console=plain', '--no-daemon', *[(':' + module.replace('/', ':') + ':' if module else '') + task for task in tasks]]
        if 'test' in tasks:
            command.append('--rerun-tasks')  # fresh reports are required for run_tests
        if test_class:
            if '#' in test_class:
                test_class = test_class.replace('#', '.')
            command += ['--tests', test_class]
    return dict(info, command=command, tasks=tasks, profiles=profiles, module=module, testClass=request.get('testClass'))


def ignored_directory(path, module_roots):
    if path.name in ('target', 'build', 'dist'):
        # A module may itself be named build or target.
        return path.parent in module_roots and path not in module_roots and not build_file(path) and not has_settings(path)
    return path.name in IGNORED


def spring_config_directory(folder, module_roots):
    return (folder in module_roots or (folder.name == 'config' and folder.parent in module_roots)
            or (folder.parent.name == 'config' and folder.parent.parent in module_roots))


def protected_input_name(name, spring_config):
    return (name in BUILD_INPUT_NAMES or name in PROTECTED_TREES or name.startswith(('build.gradle', 'settings.gradle'))
            or (spring_config and name.startswith('application') and name.endswith(('.yml', '.yaml', '.properties'))))


def git_ignored_paths(root):
    # An enclosing repository may ignore a whole untracked project. Its ignore
    # rules apply only when its index recognizes the root build files and wrappers.
    # Inspect the repository containing root even when launched by a different repo's hook.
    git_env = os.environ.copy()
    for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR'):
        git_env.pop(name, None)
    anchors = [name for name in (*BUILD_FILES, *SETTINGS_FILES, *WRAPPERS) if (root / name).is_file()]
    if not anchors:
        return set()
    ls_files = ['git', '-c', 'core.fsmonitor=false', 'ls-files']
    try:
        subprocess.run([*ls_files, '--error-unmatch', '--', *anchors], cwd=root, env=git_env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=2)
        with tempfile.TemporaryFile() as listing:
            subprocess.run([*ls_files, '-z', '--others', '--ignored',
                            '--exclude-standard', '--directory', '--', '.'], cwd=root, env=git_env, stdout=listing,
                           stderr=subprocess.DEVNULL, check=True, timeout=2)
            if listing.tell() > 8 * 1024 * 1024:
                return set()
            listing.seek(0)
            paths = listing.read()
    except (OSError, subprocess.SubprocessError):
        return set()
    if paths.count(b'\0') > 50000:
        return set()
    ignored = {root / os.fsdecode(path) for path in paths.split(b'\0') if path}
    return set() if root in ignored else ignored


def fingerprint_inputs(root):
    ignored_names = {}
    for path in git_ignored_paths(root):
        ignored_names.setdefault(path.parent, set()).add(path.name)
    module_roots = {root}
    if has_settings(root) and build_file(root) != 'pom.xml':
        module_roots.update(Path(m['root']) for m in module_entries(root, 'gradle'))
    pending = [(root, False)]
    walked = 0
    while pending:
        folder, ignored_folder = pending.pop()
        relative = folder.relative_to(root)
        prefix = relative.as_posix() + '/' if relative.parts else ''
        protected_tree = not PROTECTED_TREES.isdisjoint(relative.parts)
        ignored_here = ignored_names.get(folder, ())
        directories, filenames = [], []
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    walked += 1
                    if walked > MAX_WALK_ENTRIES:
                        raise ValueError('Build input walk exceeds safety limit; verification will run without reuse')
                    try:
                        is_dir = entry.is_dir()
                    except OSError:
                        if not ignored_folder and entry.name not in ignored_here:
                            raise
                        is_dir = False  # Still inspect a protected name if its type was unavailable.
                    if is_dir:
                        directories.append(entry.name)
                    else:
                        filenames.append(entry.name)
        except OSError:
            if ignored_folder and not protected_tree and not spring_config_directory(folder, module_roots):
                continue  # An inaccessible ignored volume is not a known input.
            raise
        if not BUILD_DESCRIPTORS.isdisjoint(filenames):
            module_roots.add(folder)
        spring_config = spring_config_directory(folder, module_roots)
        for name in sorted(directories, reverse=True):
            path = folder / name
            child_ignored = ignored_folder or name in ignored_here
            optional = (child_ignored and not protected_tree and not protected_input_name(name, spring_config)
                        and not spring_config_directory(path, module_roots))
            try:
                if not protected_tree and ignored_directory(path, module_roots):
                    continue
                linked = path.is_symlink()
            except OSError:
                if optional:
                    continue
                raise
            if linked:
                if optional:
                    continue
                raise ValueError(f'Cannot fingerprint symlinked build directory: {path}')
            pending.append((path, child_ignored))
        for name in sorted(filenames):
            protected = protected_tree or protected_input_name(name, spring_config)
            if (ignored_folder or name in ignored_here) and not protected:
                continue
            path = folder / name
            if not protected and path.suffix.lower() in ('.md', '.rst', '.adoc'):
                continue
            yield prefix + name, path


def fingerprint_snapshot(root):
    digest = hashlib.sha256(Path(__file__).read_bytes())
    count, total = 0, 0
    environment = {key: value for key, value in os.environ.items()
                   if key not in INVOCATION_ENV and not key.startswith(INVOCATION_PREFIXES)}
    files = {}
    for relative, path in fingerprint_inputs(Path(root).resolve()):
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
        total += len(content)
        count += 1
        if total > 128 * 1024 * 1024 or count > 50000:
            raise ValueError('Build input fingerprint exceeds safety limit; verification will run without reuse')
        for variable in re.findall(rb'\$\{([A-Za-z_][A-Za-z0-9_]*)', content):
            key = variable.decode()
            environment[key] = os.environ.get(key)
        file_digest = hashlib.sha256(content)
        file_digest.update(stat.S_IMODE(metadata.st_mode).to_bytes(2, 'big'))
        files[relative] = file_digest.digest()
        encoded_path = os.fsencode(relative)
        digest.update(len(encoded_path).to_bytes(8, 'big') + encoded_path + files[relative])
    digest.update(json.dumps(environment, sort_keys=True).encode())
    return digest.hexdigest(), files


def fingerprint(root):
    return fingerprint_snapshot(root)[0]


def private_dir(folder):
    try:
        folder.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError(f'Unsafe shared build state directory: {folder}')
    return folder


def state_base():
    """Per-user state root, independent of TMPDIR/HOME/XDG variables.

    Hooks, MCP and Codex may inherit different environments; every process of
    one user must agree on the lock. /tmp names could be pre-created by others.
    A uid without a user database entry (docker -u, OpenShift) or a missing or
    read-only home falls back to a private per-uid directory in the temporary
    directory, still created 0700 and owner-checked: another user can deny it,
    not take it over. An unsafe existing cache is an error, never a fallback.
    """
    try:
        cache = Path(pwd.getpwuid(os.getuid()).pw_dir) / '.cache'
        try:
            cache.mkdir(mode=0o700)
        except FileExistsError:
            pass
        if cache.is_dir():
            if not trusted(cache):
                raise ValueError(f'Unsafe build state cache directory: {cache}')
            return private_dir(cache / 'spring-boot-tools')
    except (KeyError, OSError):
        pass
    return private_dir(Path(tempfile.gettempdir()) / f'spring-boot-tools-{os.getuid()}')


def state_dir(root):
    key = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()
    return private_dir(state_base() / f'build-{key}')


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


def build_descendants(process, group, timeout):
    # A fresh snapshot can identify reparented members by their dedicated group.
    # Keep ps outside that group so a poll cannot count itself as a survivor.
    try:
        listing = subprocess.run(['ps', '-eo', 'pid=,ppid=,pgid=,stat='], capture_output=True, text=True,
                                 timeout=timeout, start_new_session=True, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    rows = [tuple(map(int, (pid, parent, pgid))) for pid, parent, pgid, status in
            (line.split() for line in listing.stdout.splitlines()) if not status.startswith('Z')]
    runtime_pid = os.getpid()
    ids = {pid for pid, parent, pgid in rows if pid != runtime_pid and
           (pgid == group or (process.returncode is None and pid == process.pid and parent == runtime_pid))}
    while True:
        children = {pid for pid, parent, _ in rows if parent in ids}
        if children <= ids:
            return ids
        ids.update(children)


def signal_members(members, sig):
    for pid in sorted(members, reverse=True):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def terminate(process, own_group):
    group = process.pid if own_group else os.getpid()
    try:
        if own_group:
            os.killpg(group, signal.SIGTERM)
        else:
            members = build_descendants(process, group, 2)
            if members is None:
                process.terminate()
            else:
                signal_members(members, signal.SIGTERM)
        deadline = time.monotonic() + .75
        while (remaining := deadline - time.monotonic()) > 0:
            if build_descendants(process, group, remaining) == set():
                return
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
        if own_group:
            os.killpg(group, signal.SIGKILL)
        else:
            # Any first-pass PID might now belong to another tree.
            signal_members(build_descendants(process, group, 2) or (), signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


def read_regular(path, limit):
    """Read a regular file without following links or blocking on special files."""
    try:
        handle = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENXIO, errno.ENOENT):
            return None
        raise
    with os.fdopen(handle, 'rb') as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            return None
        if metadata.st_size > limit:
            raise ValueError(f'Test report exceeds 8 MiB limit: {path}')
        content = source.read(limit + 1)
    if len(content) > limit:
        raise ValueError(f'Test report exceeds 8 MiB limit: {path}')
    return metadata, content


def parent_death_signal():
    """Linux: the build leader receives SIGTERM if the process that started it dies.

    The hook build runs in its own session, outside any group kill aimed at the
    hook. SIGKILL cannot be handled; this keeps the build from outliving it.
    """
    if not sys.platform.startswith('linux'):
        return None
    prctl = ctypes.CDLL(None, use_errno=True).prctl
    parent = os.getpid()
    def apply():
        # Before exec the child still has the runtime's deferred cancellation handler.
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
        if os.getppid() != parent:
            os.kill(os.getpid(), signal.SIGTERM)
    return apply


def test_report_snapshot(root, module_roots=None):
    """Read report contents while the caller owns the project build lock."""
    reports = {}
    total = 0
    root = Path(root).resolve()
    if module_roots is None:
        try:
            module_roots = [Path(m['root']) for m in discover(root)['modules']]
        except ValueError:
            module_roots = []
    module_roots = {Path(p).resolve() for p in module_roots}
    excluded = {'.git', 'node_modules', '.gradle', 'src', '.idea', '.mvn', '.claude'}
    for folder, directories, _ in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in excluded
                                and not (Path(folder) / name).is_symlink())
        for name in ('target', 'build'):
            if name not in directories:
                continue
            output = Path(folder) / name
            if output.resolve() in module_roots or build_file(output) or has_settings(output):
                continue  # a real module named build/target is not an output tree
            directories.remove(name)
            for report_dir in (('surefire-reports', 'failsafe-reports') if name == 'target' else ('test-results',)):
                report_root = output / report_dir
                if report_root.is_symlink() or not report_root.resolve().is_relative_to(root):
                    raise ValueError(f'Unsafe test report directory: {report_root}')
                for report_folder, children, files in os.walk(report_root, followlinks=False):
                    children[:] = sorted(child for child in children if child not in IGNORED
                                         and not (Path(report_folder) / child).is_symlink())
                    for filename in sorted(files):
                        file = Path(report_folder) / filename
                        if file.suffix != '.xml':
                            continue
                        report = read_regular(file, MAX_REPORT_BYTES)
                        if report is None:
                            continue  # symlinks, FIFOs and devices are not reports and may block
                        metadata, content = report
                        total += len(content)
                        if total > 8 * 1024 * 1024 or len(reports) >= 1024:
                            raise ValueError('Test report aggregate exceeds 8 MiB or 1024 files; narrow test/module scope')
                        reports[str(file)] = ((metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_size),
                                              content.decode('utf-8', 'replace'))
    return reports


def report_has_executed_tests(content):
    """Require positive execution evidence before accepting a filtered build."""
    try:
        reject_doctype(content)
        document = ET.fromstring(content)
        tag = document.tag.rsplit('}', 1)[-1]
        if tag not in ('testsuite', 'testsuites'):
            return False
        suites = [document] if tag == 'testsuite' else document.findall('{*}testsuite')
        while suites:
            suite = suites.pop()
            suites.extend(suite.findall('{*}testsuite'))
            if 'tests' in suite.attrib:
                if 0 <= int(suite.get('skipped', 0)) < int(suite.get('tests')):
                    return True
            elif any(case.find('{*}skipped') is None for case in suite.findall('{*}testcase')):
                return True
    except (ET.ParseError, xml.parsers.expat.ExpatError, ValueError):
        pass  # Malformed reports cannot establish a match; MCP reports parse errors.
    return False


def run_build(request, own_process_group=False, *, handle_cancellation=False):
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
        report_root = next((m['root'] for m in plan['modules'] if m['path'] == plan['module']), plan['root'])
        report_modules = [m['root'] for m in plan['modules']]
        inspect_reports = collect_reports or plan['testClass']
        reports_before = test_report_snapshot(report_root, report_modules) if inspect_reports else {}
        cache_diagnostic = None
        try:
            before, before_files = fingerprint_snapshot(plan['root'])
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
        outputs = {'stdout': bytearray(), 'stderr': bytearray()}
        timed_out, too_large, used = False, False, 0
        previous_handlers, process, selector = {}, None, None
        terminated = False
        cancellation = None
        try:
            if own_process_group or handle_cancellation:
                # If interrupted inside Popen, first obtain the child handle. A
                # second host signal must not interrupt cleanup of the first one.
                def interrupted(signum, frame):
                    nonlocal cancellation
                    if cancellation is None:
                        cancellation = BuildCancelled(f'Build cancelled by signal {signum}')
                        if process is not None:
                            raise cancellation
                for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                    previous_handlers[signum] = signal.signal(signum, interrupted)
            # MCP already supplies a dedicated session. With a shared terminal
            # group, isolate the build so reparented children remain identifiable.
            separate_session = own_process_group or os.getsid(0) != os.getpid()
            process = subprocess.Popen(plan['command'], cwd=plan['root'], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=separate_session,
                                       preexec_fn=parent_death_signal() if separate_session else None)
            if cancellation is not None:
                raise cancellation
            selector = selectors.DefaultSelector()
            for name in outputs:
                selector.register(getattr(process, name), selectors.EVENT_READ, name)
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
            if not timed_out and not too_large:
                try:
                    process.wait(timeout=max(.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
            # A hook owns the group even after the leader exits. Finish cleanup
            # before taking the final fingerprint, including writes by TERM handlers.
            if timed_out or too_large or own_process_group:
                terminate(process, separate_session)
                terminated = True
            try:
                after, after_files = fingerprint_snapshot(plan['root'])
            except (OSError, ValueError) as error:
                after, cache_diagnostic = None, str(error)
            ok = process.returncode == 0 and not timed_out and not too_large
            if ok and verification and before is not None and before == after:
                state['verifiedScopes'] = {scope: after}
                state_write(folder, state)
            report_result = {}
            if inspect_reports:
                reports_after = test_report_snapshot(report_root, report_modules)
                fresh = [{'file': file, 'content': value[1]} for file, value in reports_after.items()
                         if reports_before.get(file) != value]
                if collect_reports:
                    report_result = {'testReports': fresh, 'staleReportsIgnored': len(reports_after) - len(fresh),
                                     'reportScope': report_root}
                if ok and plan['testClass'] and not any(report_has_executed_tests(r['content']) for r in fresh):
                    ok = False
                    report_result['diagnostic'] = f"No tests matched testClass {plan['testClass']} in fresh test reports."
            inputs_changed = before is not None and after is not None and before != after
            if inputs_changed:
                changed = sorted(path for path in before_files.keys() | after_files.keys()
                                 if before_files.get(path) != after_files.get(path))
                report_result.update(changedInputs=changed[:20], changedInputsTruncated=len(changed) > 20)
            return dict(plan, **report_result, ok=ok, exitCode=process.returncode, **{name: bytes(value).decode('utf-8', 'replace') for name, value in outputs.items()},
                        reused=False, durationMs=round((time.monotonic() - start) * 1000), fingerprint=before, inputsChanged=inputs_changed, cacheable=before is not None and after is not None, cacheDiagnostic=cache_diagnostic, timedOut=timed_out, outputLimitExceeded=too_large)
        finally:
            try:
                if selector is not None:
                    selector.close()
                if process is not None:
                    try:
                        if not terminated and (own_process_group or process.returncode is None):
                            terminate(process, separate_session)
                    finally:
                        for name in outputs:
                            getattr(process, name).close()
            finally:
                for signum, handler in previous_handlers.items():
                    signal.signal(signum, handler)


def main():
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError('Expected JSON object')
        action = request.get('action', 'discover')
        if action == 'discover':
            result = discover(request.get('projectRoot') or os.getcwd())
        elif action == 'initialize':
            from project_initializer import initialize_project
            def cancelled(signum, frame):
                raise BuildCancelled(f'Initialization cancelled by signal {signum}')
            previous = {sig: signal.signal(sig, cancelled) for sig in (signal.SIGTERM, signal.SIGINT)}
            try:
                result = initialize_project(request)
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
        elif action == 'plan':
            result = build_plan(request)
        elif action == 'run':
            result = run_build(request, handle_cancellation=True)
        elif action == 'lint-migrations':
            from sql_lint import lint_migrations
            result = lint_migrations(request.get('migrations'))
        elif action == 'mark':
            result = dict(discover(request.get('projectRoot') or os.getcwd()),
                          diagnostic='action=mark is deprecated; Stop verification fingerprints current inputs without pending markers.')
        else:
            raise ValueError('Unknown runtime action')
        encoded = json.dumps(result)
        # Leave headroom under the MCP bridge's 32 MiB transport budget even
        # when control characters cause substantial JSON escaping expansion.
        if len(encoded.encode('utf-8')) > 24 * 1024 * 1024:
            result = {'ok': False, 'exitCode': 1,
                      'error': 'Runtime result exceeds 24 MiB serialized limit; narrow test/module scope',
                      'buildExitCode': result.get('exitCode'),
                      'reportsOmitted': len(result.get('testReports', [])),
                      'outputOmitted': True}
            encoded = json.dumps(result)
        print(encoded)
    except (OSError, ValueError, TypeError, KeyError, TimeoutError, BuildCancelled) as error:
        print(json.dumps({'ok': False, 'error': str(error), 'exitCode': 1}))
        sys.exit(1)


if __name__ == '__main__':
    main()
