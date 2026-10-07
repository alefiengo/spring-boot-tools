#!/usr/bin/env python3
"""Shared hook runtime. Python 3 standard library only; never executes input as shell."""
import json
import os
from pathlib import Path
import re
import sys


def emit(event, message):
    if event == 'PreToolUse':
        result = {'hookSpecificOutput': {'hookEventName': event, 'permissionDecision': 'deny',
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


def proposed(data, path):
    args = data.get('tool_input', {})
    if 'content' in args:
        return args['content']
    if 'new_string' in args:
        old, new = args.get('old_string', ''), args['new_string']
        try:
            text = path.read_text()
        except OSError:
            # At least inspect the replacement if the old file cannot be read.
            return new
        if not old:
            return new if not text else text + '\n' + new
        if old not in text:
            return text + '\n' + new
        return text.replace(old, new, -1 if args.get('replace_all') else 1)
    return ''


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'runtime'))
from project_runtime import discover, mark_pending, run_build, relevant, BuildCancelled



def unquote(value):
    value = re.split(r'\s+#', value, maxsplit=1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def config_path(path):
    return bool(re.fullmatch(r'application[^/]*\.(?:yml|yaml|properties)', path.name))


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
    match = re.fullmatch(r'\$\{([A-Za-z_][\w.-]*)(?::(.*))?\}', value)
    if not match:
        return False
    default = match.group(2)
    return default is None or default.lower() in ('', 'changeme', 'change_me', 'example', 'dummy') or safe_placeholder(default)


def secrets(data, path):
    if not (config_path(path) or path.name.startswith('.env') or path.name.endswith('.env')):
        return
    # Match each scalar independently: a placeholder elsewhere never exempts a literal.
    pattern = re.compile(r"(?i)(?:^|[\s:{,])[\"']?([\w.-]*(?:password|passwd|secret|api[_-]?key|token|private[_-]?key)[\w.-]*)[\"']?\s*[:=]\s*")
    for line in proposed(data, path).splitlines():
        if line.lstrip().startswith(('#', '!')):
            continue
        flow = path.suffix in ('.yaml', '.yml') and bool(re.search(r'(?:^|:)\s*\{', line))
        for match in pattern.finditer(line):
            depth = 0
            for token in re.findall(r'\$\{|}', line[:match.start(1)]):
                depth = depth + 1 if token == '${' else max(0, depth - 1)
            if depth:
                continue  # a nested placeholder default is a value, not a new key
            raw = line[match.end():]
            value = unquote(flow_scalar(raw) if flow else raw)
            key = re.sub(r'[^a-z0-9]', '', match.group(1).lower())
            if not key.endswith(('password', 'passwd', 'secret', 'apikey', 'token', 'privatekey')):
                continue  # policy flags, credential names and token URLs are not credentials
            if key.endswith('privatekey') and value.startswith(('classpath:', 'file:')):
                continue  # key material remains in the referenced resource, not this scalar
            if value.startswith('{') or value in ('[]', '{}'):
                continue  # nested configuration is inspected field by field
            if not value or value.lower() in ('changeme', 'change_me', 'example', 'dummy'):
                continue
            if re.fullmatch(r'<[^<>]+>', value):
                continue
            if safe_placeholder(value):
                continue
            emit('PreToolUse', f'Possible hard-coded credential ({match.group(1)}) in {path}. Use an environment variable such as ${{DB_PASSWORD}}.')
            return


def scalar_assignments(text):
    """Conservative dotted paths for properties and ordinary YAML mappings.

    We exempt a development document only when its activation path can be read
    unambiguously. Unsupported YAML constructs keep the guard enabled.
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
            yield full, unquote(value)
        elif not value:
            parents.append((depth, key))


def ddl(data, path):
    if not config_path(path):
        return
    text = proposed(data, path)
    if re.fullmatch(r'application-(?:dev|local)\.(?:yml|yaml|properties)', path.name):
        return
    # YAML and Spring properties support profile-scoped multi-document config.
    # An unrelated on-profile field, active=dev, !prod or dev|prod never exempts it.
    for document in re.split(r'(?m)^\s*(?:---|#---|!---)\s*$', text):
        activation = [value for key, value in scalar_assignments(document) if key == 'spring.config.activate.on-profile']
        if len(activation) == 1 and activation[0] in ('dev', 'local'):
            continue
        for line in document.splitlines():
            if line.lstrip().startswith(('#', '!')):
                continue
            for match in re.finditer(r"(?i)\bddl-auto[\"']?\s*[:=]\s*", line):
                raw = line[match.end():]
                flow = path.suffix in ('.yaml', '.yml') and bool(re.search(r'(?:^|:)\s*\{', line))
                value = unquote(flow_scalar(raw) if flow else raw)
                default = re.fullmatch(r'\$\{[^{}:]+:([^{}]*)\}', value)
                effective = default.group(1) if default else value
                unsafe = re.match(r"(?i)[\"']?(create-drop|create|update)[\"']?(?=$|[\s,}])", effective)
                if unsafe:
                    emit('PreToolUse', f'Unsafe ddl-auto={unsafe.group(1)} in {path}. Use migrations and ddl-auto=none or validate; development DDL requires a dev/local profile file or a document activated only for dev/local.')
                    return


def build(event, data, path):
    if event == 'Stop' and data.get('stop_hook_active'):
        return
    location = path if event == 'PostToolUse' else Path(os.environ.get('CLAUDE_PROJECT_DIR') or data.get('cwd') or os.getcwd())
    try:
        info = discover(location)
    except ValueError:
        return
    if event == 'PostToolUse':
        # Batch edits: record pending work, never launch a build for each keystroke.
        # Stop hashes current sources/configuration and checks edits from any source.
        if relevant(path.resolve(), Path(info['root'])):
            mark_pending(info['root'])
        return
    try:
        result = run_build({'projectRoot': info['root'], 'task': 'verify'}, own_process_group=True)
    except (OSError, ValueError, TimeoutError, BuildCancelled) as error:
        notice(str(error))
        return
    if not result['ok']:
        detail = 'timed out' if result['timedOut'] else ('exceeded output limit' if result['outputLimitExceeded'] else f"exit {result['exitCode']}")
        output = result['stdout'] + result['stderr']
        emit(event, f"Build check failed: {result['tasks'][0]} ({detail}) in {info['root']}.\n" + '\n'.join(output.splitlines()[:40]))
    elif result.get('inputsChanged'):
        emit(event, 'Build inputs changed during verification. Run verification again on the final source contents.')


def lint(data, path):
    if path.suffix != '.sql' or not any(path.parts[i:i+2] == ('db', 'migration') for i in range(len(path.parts)-1)) or not path.is_file():
        return
    messages = []
    # Flyway supports dotted/underscored versions, repeatable scripts and undo scripts.
    if not re.fullmatch(r'(?:[VU]\d+(?:[._]\d+)*|R)__[^/]+\.sql', path.name):
        messages.append('naming: expected V<version>__<description>.sql, U<version>__<description>.sql or R__<description>.sql')
    text = path.read_text()
    sql = re.sub(r'/\*.*?\*/|--[^\n]*', '', text, flags=re.S)
    if re.search(r'\bBEGIN\b', sql, re.I) and re.search(r'\bCOMMIT\b', sql, re.I) and re.search(r'\bFOR\b[^;]*\bLOOP\b', sql, re.I):
        messages.append('BEGIN/COMMIT with LOOP: verify transaction handling is intentional')
    if re.search(r'\b(?:DROP\s+(?:TABLE|COLUMN)|TRUNCATE\s+(?:TABLE\s+)?|DELETE\s+FROM)\b', sql, re.I):
        messages.append('Destructive SQL: IF EXISTS does not protect data. Review backups, dependencies and rollback before applying. Never edit an already applied migration; add a new migration.')
    if messages:
        emit('PostToolUse', f'{path.name}: ' + '\n'.join(messages))



def main():
    action = sys.argv[1]
    data = read_input()
    if data is None:
        return
    args = data.get('tool_input', {})
    path = Path(args.get('file_path', ''))
    if not path.is_absolute() and args.get('file_path'):
        path = Path(data.get('cwd') or os.environ.get('CLAUDE_PROJECT_DIR') or os.getcwd()) / path
    if action == 'verify-on-stop':
        build('Stop', data, path)
    elif args.get('file_path'):
        if action == 'guard-secrets':
            secrets(data, path)
        elif action == 'guard-ddl':
            ddl(data, path)
        elif action == 'post-edit-compile':
            build('PostToolUse', data, path)
        elif action == 'lint-migration':
            lint(data, path)


if __name__ == '__main__':
    try:
        main()
    except (OSError, TypeError, ValueError, AttributeError) as error:
        notice(f'Spring Boot hook skipped: {error}')
