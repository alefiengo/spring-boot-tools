#!/usr/bin/env python3
"""Shared hook runtime. Python 3 standard library only; never executes input as shell."""
import collections
import json
import os
from pathlib import Path
import re
import sys


def emit(event, message, decision='deny'):
    if event == 'PreToolUse':
        result = {'hookSpecificOutput': {'hookEventName': event, 'permissionDecision': decision,
                                       'permissionDecisionReason': message}}
    elif event == 'Stop':
        result = {'decision': 'block', 'reason': message}
    else:
        result = {'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': message}}
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
from project_runtime import discover, run_build, BuildCancelled
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


def secret_findings(text, path):
    findings = []
    yaml = path.suffix in ('.yaml', '.yml')
    for line in text.splitlines():
        if line.lstrip().startswith(('#', '!')):
            continue
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
            if key.endswith(('privatekey', 'signingkey', 'credentials')) and value.startswith(('classpath:', 'file:')):
                continue  # key material remains in the referenced resource, not this scalar
            if value.startswith('{') or value in ('[]', '{}'):
                continue  # nested configuration is inspected field by field
            if value.lower() in SAFE_VALUES:
                continue
            if re.fullmatch(r'<[^<>]+>', value):
                continue
            if safe_placeholder(value):
                continue
            findings.append((line.strip(), name))
            break  # findings are compared per line
    return findings


def secrets(data, path):
    if not (config_path(path) or path.name.startswith('.env') or path.name.endswith('.env')):
        return
    current, proposal = contents(data, path)
    name = introduced(secret_findings(current, path), secret_findings(proposal, path))
    if name is None:
        return
    if test_path(path):
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


def build(event, data):
    location = Path(os.environ.get('CLAUDE_PROJECT_DIR') or data.get('cwd') or os.getcwd())
    try:
        info = discover(location)
    except ValueError as error:
        if not str(error).startswith('No Maven or Gradle project found'):
            notice(f'Spring Boot verification skipped: {error}')
        return
    try:
        result = run_build({'projectRoot': info['root'], 'task': 'verify'}, own_process_group=True)
    except (OSError, ValueError, KeyError, TimeoutError, BuildCancelled) as error:
        notice(str(error))
        return
    if not result['ok']:
        detail = 'timed out' if result['timedOut'] else ('exceeded output limit' if result['outputLimitExceeded'] else f"exit {result['exitCode']}")
        output = result['stdout'] + result['stderr']
        message = f"Build check failed: {result['tasks'][0]} ({detail}) in {info['root']}.\n" + '\n'.join(output.splitlines()[:40])
    elif result.get('inputsChanged'):
        message = 'Build inputs changed during verification. Run verification again on the final source contents.'
    else:
        message = None
    warnings = info.get('discoveryWarnings') or result.get('discoveryWarnings') or []
    if not message:
        if warnings:
            notice('\n'.join(warnings))
        return
    message = '\n'.join([message, *warnings])
    if data.get('stop_hook_active') is True:
        # Already continuing because of a Stop hook: report without blocking again.
        notice(message)
    else:
        emit(event, message)


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
