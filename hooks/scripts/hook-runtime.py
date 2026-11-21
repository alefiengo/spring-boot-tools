#!/usr/bin/env python3
"""Shared hook runtime. Python 3 standard library only; never executes input as shell."""
import collections
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile


def emit(event, message, decision='deny', system=None):
    if event == 'PreToolUse':
        result = {'hookSpecificOutput': {'hookEventName': event, 'permissionDecision': decision,
                                       'permissionDecisionReason': message}}
    elif event == 'Stop':
        result = {'decision': 'block', 'reason': message}
    else:
        result = {'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': message}}
    if system:
        result['systemMessage'] = system
    print(json.dumps(result, ensure_ascii=False, separators=(',', ':')))


def notice(message):
    print(json.dumps({'systemMessage': message}, ensure_ascii=False, separators=(',', ':')))


def read_input():
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('expected an object')
        return value
    except (ValueError, TypeError) as error:
        notice(f'Spring Boot hook skipped: invalid JSON input ({error}).')
        return None


def contents(data, path):
    """Return (current file text, proposed file text) for Write or Edit."""
    args = data.get('tool_input', {})
    try:
        current = path.read_text()
    except (OSError, UnicodeDecodeError):
        current = None  # unreadable: every finding in the proposal counts as new
    if 'content' in args:
        return current or '', args['content']
    if 'new_string' in args:
        old, new = args.get('old_string', ''), args['new_string']
        if current is None:
            return '', new  # at least inspect the replacement
        if not old:
            return current, new if not current else current + '\n' + new
        if old not in current:
            return current, current + '\n' + new
        return current, current.replace(old, new, -1 if args.get('replace_all') else 1)
    return current or '', ''


def introduced(before, after):
    """First finding of the proposal that the current file does not already contain.

    Findings are (line, detail) pairs compared by line text with multiplicity, so
    an unrelated edit does not trip on an existing value, while a duplicate does.
    """
    existing = collections.Counter(line for line, _ in before)
    for line, detail in after:
        if existing[line]:
            existing[line] -= 1
        else:
            return detail
    return None


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'runtime'))
from project_runtime import discover, run_build, BuildCancelled, fingerprint, private_dir, state_dir
from sql_lint import lint_hook


SAFE_VALUES = ('', 'changeme', 'change_me', 'example', 'dummy')
SECRET_KEYWORDS = r'password|passwd|secret|api[_-]?key|token|private[_-]?key|signing[_-]?key|credentials'
SECRET_SUFFIXES = ('password', 'passwd', 'secret', 'apikey', 'token', 'privatekey',
                   'secretkey', 'secretaccesskey', 'signingkey', 'credentials')
# Bounded key repetitions keep backtracking linear on long lines.
SECRET_KEY = re.compile(r"(?i)(?:^|[\s:{,])[\"']?([\w.-]{0,256}(?:" + SECRET_KEYWORDS + r")[\w.-]{0,256})[\"']?\s*[:=]\s*")
DDL_KEY = r'(?:ddl[-_]?auto|hbm2ddl\.auto|schema-generation\.database\.action)'
DDL_ASSIGNMENT = re.compile(r"(?i)\b" + DDL_KEY + r"[\"']?\s*[:=]\s*")
DDL_DOTTED = re.compile(r'(?i)(?:^|\.)' + DDL_KEY + '$')
DDL_SPACED_KEY = re.compile(r'(?i)\b' + DDL_KEY + '$')
DDL_UNSAFE = re.compile(r"(?i)[\"']?(create-drop|create-only|drop-and-create|create|update|drop|truncate)[\"']?(?=$|[\s,}])")
# java.util.Properties also separates a key from its value with whitespace alone.
PROPERTIES_SPACE = re.compile(r'^[ \t]*([\w.-]{1,512})[ \t]+(?=[^\s:=])')
MAX_VALUE = 4096
MAX_PLACEHOLDER_DEPTH = 16
COMPOSE_FILE = re.compile(r'(?:docker-)?compose(?:[.-][^/]*)?\.ya?ml')
TEMPLATE_SUFFIXES = ('.example', '.sample', '.template')
# Bounded scheme and userinfo keep scanning linear on long lines.
URL_USERINFO = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{0,31}://([^\s/@'\"]{1,512})@")
TOKENS = (
    (re.compile(r'\b(?:AKIA|ASIA)([0-9A-Z]{16})\b'), 'AWS access key id'),
    (re.compile(r'\b(?:gh[pousr]_([A-Za-z0-9]{36,255})|github_pat_([A-Za-z0-9_]{22,255}))'), 'GitHub token'),
    (re.compile(r'\bxox[abprs]-([A-Za-z0-9-]{10,255})'), 'Slack token'),
    (re.compile(r'-----BEGIN (?:[A-Z0-9]+ ){0,4}PRIVATE KEY-----'), 'private key block'),
)
# A next-line YAML value that starts a sequence, mapping, tag, anchor, alias or
# collection is nested structure, not a plain or quoted scalar.
YAML_NESTED = re.compile(r"(?:-(?:\s|$)|[\w.-]{1,512}\s*:(?:\s|$)|[\"'][^\"']{0,512}[\"']\s*:(?:\s|$)|[?&*|>!{\[%@`])")


def unquote(value):
    value = re.split(r'\s+#', value, maxsplit=1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def config_path(path):
    return bool(re.fullmatch(r'(?:application|bootstrap)[^/]*\.(?:yml|yaml|properties)', path.name))


def project_parts(path):
    """Path components below the nearest build directory (or the project directory).

    Only the part inside the project is meaningful: a checkout under ~/src/test/
    must not make every file look like a test resource.
    """
    for folder in path.parents:
        if any((folder / name).is_file() for name in ('pom.xml', 'build.gradle', 'build.gradle.kts', 'settings.gradle', 'settings.gradle.kts')):
            return path.relative_to(folder).parts
    project = os.environ.get('CLAUDE_PROJECT_DIR')
    if project and path.is_relative_to(project):
        return path.relative_to(project).parts
    return (path.name,)


def test_path(path):
    """src/test/**, *-test.* and application-test* configuration is test-only."""
    parts = project_parts(path)
    return (any(parts[i:i+2] == ('src', 'test') for i in range(len(parts) - 1))
            or bool(re.search(r'-test\.[^./]+$', path.name)) or bool(re.match(r'application-test[.-]', path.name)))


def flow_scalar(value):
    """Read one flow-map scalar while preserving quotes and ${...} expressions."""
    quote = None
    placeholders = 0
    skip_next = False
    for index, char in enumerate(value):
        if skip_next:
            skip_next = False
            continue
        if quote:
            if char == quote:
                # JSON-style double-quote escapes; YAML single quotes escape by doubling.
                if quote == '"' and (len(value[:index]) - len(value[:index].rstrip('\\'))) % 2:
                    continue
                if quote == "'" and index + 1 < len(value) and value[index+1] == "'":
                    skip_next = True
                    continue
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char == '{' and index and value[index-1] == '$':
            placeholders += 1
        elif char == '}' and placeholders:
            placeholders -= 1
        elif char in ',}' and not placeholders:
            return value[:index].strip()
    return value.strip()


def safe_placeholder(value):
    """${NAME}, or nested defaults ending in a safe value. Bounded depth, iterative."""
    for _ in range(MAX_PLACEHOLDER_DEPTH):
        match = re.fullmatch(r'\$\{([A-Za-z_][\w.-]*)(?::(.*))?\}', value, re.S)
        if not match:
            return False
        default = match.group(2)
        if default is None or default.lower() in SAFE_VALUES:
            return True
        value = default
    return False  # deeper nesting is treated as a literal


def compose_placeholder(value):
    """Compose and .env interpolation: $NAME, ${NAME}, ${NAME:?error} or a safe ${NAME:-default}."""
    for _ in range(MAX_PLACEHOLDER_DEPTH):
        if re.fullmatch(r'\$[A-Za-z_]\w*', value):
            return True
        match = re.fullmatch(r'\$\{([A-Za-z_]\w*)(?:(:?[-?])(.*))?\}', value, re.S)
        if not match:
            return False
        operator, default = match.group(2), match.group(3)
        if operator is None or operator.endswith('?') or default.lower() in SAFE_VALUES:
            return True
        value = default
    return False


def literal(value, compose=False):
    """True unless the value is empty, a known dummy or a placeholder."""
    if value.lower() in SAFE_VALUES or re.fullmatch(r'<[^<>]+>', value) or safe_placeholder(value):
        return False
    return not (compose and compose_placeholder(value))


def url_credential(line, compose):
    """scheme://user:password@host with a literal password; the user part may be a placeholder."""
    for match in URL_USERINFO.finditer(line):
        userinfo, depth = match.group(1), 0
        for index, char in enumerate(userinfo):
            if char == '$' and userinfo[index + 1:index + 2] == '{':
                depth += 1
            elif char == '}' and depth:
                depth -= 1
            elif char == ':' and not depth:
                if literal(userinfo[index + 1:], compose):
                    return True
                break
    return False


def token_finding(line):
    """Well-known token formats; a body of one repeated character is a placeholder."""
    for pattern, name in TOKENS:
        for match in pattern.finditer(line):
            body = next((group for group in match.groups() if group), '')
            if not body or len(set(body)) > 1:
                return name
    return None


def secret_findings(text, path, compose=False):
    findings = []
    yaml = path.suffix in ('.yaml', '.yml')
    pending = None  # (indent, key, line) of a YAML credential key whose value starts on the next line
    for line in text.splitlines():
        stripped = line.strip()
        comment = line.lstrip().startswith(('#', '!'))
        if pending and stripped and not comment:
            indent, name, key_line = pending
            pending = None
            if (len(line) - len(line.lstrip()) > indent and not YAML_NESTED.match(stripped)
                    and literal(unquote(stripped), compose)):
                findings.append((key_line.strip() + '\n' + stripped, name))
                continue
        known_token = token_finding(line)
        if comment:
            # Commented-out values are examples, but a well-known token is leaked either way.
            if known_token:
                findings.append((stripped, known_token))
            continue
        found = None
        flow = yaml and bool(re.search(r'(?:^|:)\s*\{', line))
        # A longer value stays a non-placeholder literal; bounded slices keep long lines linear.
        candidates = [(match.start(1), match.group(1), line[match.end():match.end() + MAX_VALUE])
                      for match in SECRET_KEY.finditer(line)]
        spaced = PROPERTIES_SPACE.match(line) if path.suffix == '.properties' else None
        if spaced and not candidates:
            candidates.append((spaced.start(1), spaced.group(1), line[spaced.end():spaced.end() + MAX_VALUE]))
        depth, position = 0, 0
        for start, name, raw in candidates:
            # Match each scalar independently: a placeholder elsewhere never exempts a literal.
            for token in re.findall(r'\$\{|}', line[position:start]):
                depth = depth + 1 if token == '${' else max(0, depth - 1)
            position = start
            if depth:
                continue  # a nested placeholder default is a value, not a new key
            value = unquote(flow_scalar(raw) if flow else raw)
            key = re.sub(r'[^a-z0-9]', '', name.lower())
            if not key.endswith(SECRET_SUFFIXES):
                continue  # policy flags, credential names and token URLs are not credentials
            if yaml and not flow and not raw.strip():
                pending = (len(line) - len(line.lstrip()), name, line)
                continue
            if key.endswith(('privatekey', 'signingkey', 'credentials')) and value.startswith(('classpath:', 'file:')):
                continue  # key material remains in the referenced resource, not this scalar
            if value.startswith('{') or value in ('[]', '{}'):
                continue  # nested configuration is inspected field by field
            if not literal(value, compose):
                continue
            found = name
            break  # findings are compared per line
        if not found and url_credential(line, compose):
            found = 'credentials in URL'
        found = found or known_token
        if found:
            findings.append((stripped, found))
    return findings


def secret_file(path):
    """Committed configuration, compose files and env templates deny; local env files ask."""
    name = path.name
    if config_path(path):
        return 'config'
    if COMPOSE_FILE.fullmatch(name):
        return 'compose'
    if (name.startswith('.env') or '.env.' in name) and name.endswith(TEMPLATE_SUFFIXES):
        return 'template'
    if name == '.env' or name.startswith('.env.') or name.endswith('.env'):
        return 'local'
    if name.startswith('.env'):
        return 'other'  # e.g. .envrc keeps the previous deny
    return None


def secrets(data, path):
    kind = secret_file(path)
    if kind is None:
        return
    compose = kind != 'config'  # Compose interpolation syntax is not Spring placeholder syntax
    current, proposal = contents(data, path)
    name = introduced(secret_findings(current, path, compose), secret_findings(proposal, path, compose))
    if name is None:
        return
    if kind == 'local':
        emit('PreToolUse', f'Possible hard-coded credential ({name}) in local environment file {path}. '
             'Confirm the file is gitignored and holds only local development values; '
             'commit only placeholders in a template such as .env.example.', decision='ask')
    elif test_path(path):
        # Test fixtures legitimately hold throwaway credentials: let the user confirm.
        emit('PreToolUse', f'Possible hard-coded credential ({name}) in test configuration {path}. '
             'Confirm it is a throwaway test value, or use an environment variable.', decision='ask')
    else:
        emit('PreToolUse', f'Possible hard-coded credential ({name}) in {path}. Use an environment variable such as ${{DB_PASSWORD}}.')


def scalar_assignments(text):
    """Conservative dotted paths for properties and ordinary YAML mappings.

    Yields (path, value, line). Unsupported YAML constructs yield nothing, so
    they never establish a development-only activation.
    """
    parents = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(('#', '!')):
            continue
        match = re.match(r"^(\s*)([\w.\-]+|[\"'][\w.\-]+[\"'])\s*[:=]\s*(.*?)\s*$", line)
        if not match:
            continue
        indent, key, value = match.groups()
        depth = len(indent.expandtabs(8))
        while parents and parents[-1][0] >= depth:
            parents.pop()
        key = unquote(key)
        full = '.'.join([item[1] for item in parents] + [key])
        if value and not value.startswith('{'):
            yield full, unquote(value), line
        elif not value:
            parents.append((depth, key))


def documents(text, path):
    """Split multi-document configuration. Returns (documents, ambiguous).

    YAML: '---' (optionally followed by a comment, tag or content) starts a
    document and '...' ends one. Spring properties: exactly '#---' or '!---'.
    Separator-like lines that are neither make activation-based exemptions unsafe.
    """
    yaml = path.suffix in ('.yaml', '.yml')
    separator = re.compile(r'---(?:[ \t].*)?|\.\.\.[ \t]*' if yaml else r'[#!]---[ \t]*')
    result, current, ambiguous = [], [], False
    for line in text.splitlines():
        if separator.fullmatch(line):
            result.append('\n'.join(current))
            current = []
            continue
        if re.match(r'[ \t]*[#!]?---', line) and not (yaml and line.startswith('#')):
            ambiguous = True
        current.append(line)
    result.append('\n'.join(current))
    return result, ambiguous


def ddl_value(raw, flow):
    value = unquote(flow_scalar(raw) if flow else raw)
    default = re.fullmatch(r'\$\{[^{}:]+:([^{}]*)\}', value)
    unsafe = DDL_UNSAFE.match(default.group(1) if default else value)
    return unsafe.group(1) if unsafe else None


def ddl_findings(text, path):
    if re.fullmatch(r'application-(?:dev|local)\.(?:yml|yaml|properties)', path.name) or test_path(path):
        return []
    findings = []
    yaml = path.suffix in ('.yaml', '.yml')
    parts, ambiguous = documents(text, path)
    # An unrelated on-profile field, active=dev, !prod or dev|prod never exempts a document.
    for document in parts:
        assignments = list(scalar_assignments(document))
        activation = [value for key, value, _ in assignments if key == 'spring.config.activate.on-profile']
        if not ambiguous and len(activation) == 1 and activation[0] in ('dev', 'local'):
            continue
        current = {}  # one finding per line within a document
        for line in document.splitlines():
            if line.lstrip().startswith(('#', '!')):
                continue
            flow = yaml and bool(re.search(r'(?:^|:)\s*\{', line))
            matches = [line[match.end():match.end() + MAX_VALUE] for match in DDL_ASSIGNMENT.finditer(line)]
            spaced = PROPERTIES_SPACE.match(line) if path.suffix == '.properties' else None
            if spaced and not matches and DDL_SPACED_KEY.search(spaced.group(1)):
                matches.append(line[spaced.end():spaced.end() + MAX_VALUE])
            for raw in matches:
                unsafe = ddl_value(raw, flow)
                if unsafe:
                    current.setdefault(line, unsafe)
                    break
        # Nested YAML such as hibernate: / hbm2ddl: / auto: create.
        for key, value, line in assignments:
            if DDL_DOTTED.search(key):
                unsafe = ddl_value(value[:MAX_VALUE], False)
                if unsafe:
                    current.setdefault(line, unsafe)
        findings += [(line.strip(), value) for line, value in current.items()]
    return findings


def ddl(data, path):
    if not config_path(path):
        return
    current, proposal = contents(data, path)
    unsafe = introduced(ddl_findings(current, path), ddl_findings(proposal, path))
    if unsafe:
        emit('PreToolUse', f'Unsafe ddl-auto={unsafe} in {path}. Use migrations and ddl-auto=none or validate; development DDL requires a dev/local profile file, test configuration or a document activated only for dev/local.')


STOP_MODES = ('verify', 'compile', 'off')
STOP_TIMEOUT = (300, 30, 600)  # default, minimum, maximum seconds; hooks.json allows 660
SESSION_BASELINES = 64
# Exported only to some hook events; it must not make SessionStart and Stop fingerprints differ.
EVENT_ONLY_ENV = ('CLAUDE_ENV_FILE',)
MAX_CHANGED_LISTED = 10


def stop_settings():
    """Mode and timeout from userConfig. Claude Code exports CLAUDE_PLUGIN_OPTION_<KEY>
    only for configured options, so an absent or empty value means the default."""
    problems = []
    mode = os.environ.get('CLAUDE_PLUGIN_OPTION_STOP_VERIFICATION', '').strip()
    if not mode:
        mode = 'verify'
    elif mode.lower() in STOP_MODES:
        mode = mode.lower()
    else:
        problems.append(f'Ignoring invalid stop_verification {mode[:40]!r}; expected verify, compile or off. Using verify.')
        mode = 'verify'
    default, low, high = STOP_TIMEOUT
    raw = os.environ.get('CLAUDE_PLUGIN_OPTION_STOP_TIMEOUT_SECONDS', '').strip()
    seconds = default
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = math.nan
        if low <= value <= high:
            seconds = value
        else:
            problems.append(f'Ignoring invalid stop_timeout_seconds {raw[:40]!r}; expected {low} to {high}. Using {default}.')
    return mode, round(seconds * 1000), problems


def project_location(data):
    return Path(os.environ.get('CLAUDE_PROJECT_DIR') or data.get('cwd') or os.getcwd())


def session_fingerprint(root):
    saved = {key: os.environ.pop(key) for key in EVENT_ONLY_ENV if key in os.environ}
    try:
        return fingerprint(root)
    finally:
        os.environ.update(saved)


def baseline_file(root, session):
    folder = private_dir(state_dir(root) / 'sessions')
    return folder / (hashlib.sha256(session.encode()).hexdigest() + '.json')


def session_id(data):
    session = data.get('session_id')
    return session if isinstance(session, str) and 0 < len(session) <= 256 else None


def record_baseline(data):
    """SessionStart: remember the input fingerprint the session started with.

    Cheap and silent: a missing project, an unfingerprintable tree or unusable
    state only leaves no baseline, so Stop keeps verifying as before. Only a new
    conversation (startup or /clear) records one, and an existing baseline is
    never replaced. Resume, fork and compaction continue a conversation that may
    already hold unverified edits, so they never record one.
    """
    session = session_id(data)
    if session is None or data.get('source') not in ('startup', 'clear'):
        return
    try:
        root = discover(project_location(data))['root']
        path = baseline_file(root, session)
        if path.exists() or path.is_symlink():
            return
        value = session_fingerprint(root)
        handle, temporary = tempfile.mkstemp(prefix='baseline-', dir=path.parent)
        try:
            with os.fdopen(handle, 'w') as output:
                json.dump({'session': session, 'fingerprint': value}, output)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        prune_baselines(path)
    except (OSError, ValueError, KeyError):
        return


def prune_baselines(keep):
    entries = []
    for path in keep.parent.glob('*.json'):
        try:
            entries.append((path.lstat().st_mtime_ns, path))
        except OSError:
            continue
    entries.sort(reverse=True)
    for _, path in entries[SESSION_BASELINES:]:
        if path != keep:
            try:
                path.unlink()
            except OSError:
                pass


def unchanged_since_session_start(data, root):
    """True only when this session's baseline exists and equals the current fingerprint."""
    session = session_id(data)
    if session is None:
        return False
    try:
        handle = os.open(baseline_file(root, session), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(handle) as source:
            stored = json.loads(source.read(4096))
        baseline = stored.get('fingerprint') if stored.get('session') == session else None
        return isinstance(baseline, str) and bool(re.fullmatch(r'[0-9a-f]{64}', baseline)) and session_fingerprint(root) == baseline
    except (OSError, ValueError, AttributeError):
        return False


def changed_inputs(result):
    """'Changed: a, b (and more).' from the runtime's optional changedInputs list."""
    changed = result.get('changedInputs')
    if not isinstance(changed, list):
        return ''
    names = [re.sub(r'[\x00-\x1f\x7f]', '?', item)[:200] for item in changed if isinstance(item, str) and item]
    if not names:
        return ''
    more = result.get('changedInputsTruncated') is True or len(names) > MAX_CHANGED_LISTED
    return ' Changed: ' + ', '.join(names[:MAX_CHANGED_LISTED]) + (' (and more)' if more else '') + '.'


def build(event, data):
    mode, timeout, notes = stop_settings()

    def finish(message=None):
        if message and data.get('stop_hook_active') is not True:
            emit(event, message, system='\n'.join(notes))
        elif message or notes:
            # Already continuing because of a Stop hook: report without blocking again.
            notice('\n'.join([*([message] if message else []), *notes]))

    if mode == 'off':
        return finish()
    try:
        info = discover(project_location(data))
    except ValueError as error:
        if not str(error).startswith('No Maven or Gradle project found'):
            notes.insert(0, f'Spring Boot verification skipped: {error}')
        return finish()
    if unchanged_since_session_start(data, info['root']):
        return finish()
    try:
        result = run_build({'projectRoot': info['root'], 'task': mode, 'timeoutMs': timeout}, own_process_group=True)
    except (OSError, ValueError, KeyError, TimeoutError, BuildCancelled) as error:
        notes.insert(0, str(error))
        return finish()
    if not result['ok']:
        detail = 'timed out' if result['timedOut'] else ('exceeded output limit' if result['outputLimitExceeded'] else f"exit {result['exitCode']}")
        output = result['stdout'] + result['stderr']
        message = f"Build check failed: {result['tasks'][0]} ({detail}) in {info['root']}.\n" + '\n'.join(output.splitlines()[:40])
    elif result.get('inputsChanged'):
        changed = changed_inputs(result)
        message = (f"Build inputs changed during {'verification' if mode == 'verify' else 'the compile check'}.{changed} "
                   'Run verification again on the final source contents.'
                   + (' If these are build outputs, add them to .gitignore.' if changed else ''))
    else:
        message = None
    warnings = info.get('discoveryWarnings') or result.get('discoveryWarnings') or []
    if not message:
        notes[:0] = warnings
        return finish()
    finish('\n'.join([message, *warnings]))


def lint(data, path):
    if path.suffix != '.sql' or not any(path.parts[i:i+2] == ('db', 'migration') for i in range(len(path.parts)-1)) or not path.is_file():
        return
    messages = lint_hook(path.name, path.read_text())
    if messages:
        emit('PostToolUse', f'{path.name}: ' + '\n'.join(messages))


def main():
    action = sys.argv[1]
    data = read_input()
    if data is None:
        return
    if action == 'verify-on-stop':
        build('Stop', data)
        return
    if action == 'session-baseline':
        record_baseline(data)
        return
    args = data.get('tool_input', {})
    if not args.get('file_path'):
        return
    path = Path(args['file_path'])
    if not path.is_absolute():
        path = Path(data.get('cwd') or os.environ.get('CLAUDE_PROJECT_DIR') or os.getcwd()) / path
    if action == 'guard-secrets':
        secrets(data, path)
    elif action == 'guard-ddl':
        ddl(data, path)
    elif action == 'lint-migration':
        lint(data, path)


if __name__ == '__main__':
    try:
        main()
    except (OSError, TypeError, ValueError, KeyError, AttributeError, RecursionError) as error:
        notice(f'Spring Boot hook skipped: {error}')
