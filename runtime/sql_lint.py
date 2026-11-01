"""Pure migration heuristics shared by the hook and MCP adapters (stdlib only).

Policies deliberately differ: the edit hook warns on all DELETE statements and
transaction loops, while MCP reports empty/duplicate migrations as errors and
only unbounded DELETE statements as risks. Preserve each adapter's established
comment/string handling; this is heuristic lint, not a SQL parser.
"""
import re


NAME = r'(?:([VU])(\d+(?:[._]\d+)*)|R)__([^/]+)\.sql'
JS_WHITESPACE = ' \t\n\r\v\f\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
JS_SPACE_TRANSLATION = str.maketrans({char: ' ' for char in JS_WHITESPACE})
HOOK_DESTRUCTIVE = ('Destructive SQL: IF EXISTS does not protect data. Review backups, dependencies and rollback before applying. '
                    'Never edit an already applied migration; add a new migration.')


def migration_name(name, *, ascii_digits=False):
    match = re.fullmatch(NAME, name, flags=re.ASCII if ascii_digits else 0)
    version = None
    if match and match[2]:
        parts = [part.lstrip('0') or '0' for part in re.split(r'[._]', match[2])]
        while len(parts) > 1 and parts[-1] == '0':
            parts.pop()
        version = '.'.join(parts)
    return {'type': (match[1] or 'R') if match else None, 'version': version}


def lint_hook(name, content):
    messages = []
    if migration_name(name)['type'] is None:
        messages.append('naming: expected V<version>__<description>.sql, U<version>__<description>.sql or R__<description>.sql')
    sql = re.sub(r'/\*.*?\*/|--[^\n]*', '', content, flags=re.S)
    if re.search(r'\bBEGIN\b', sql, re.I) and re.search(r'\bCOMMIT\b', sql, re.I) and re.search(r'\bFOR\b[^;]*\bLOOP\b', sql, re.I):
        messages.append('BEGIN/COMMIT with LOOP: verify transaction handling is intentional')
    if re.search(r'\b(?:DROP\s+(?:TABLE|COLUMN)|TRUNCATE\s+(?:TABLE\s+)?|DELETE\s+FROM)\b', sql, re.I):
        messages.append(HOOK_DESTRUCTIVE)
    return messages


def lint_migrations(records):
    """Lint adapter-selected records without filesystem access or build execution.

    Input order determines which equivalent version is reported as the duplicate;
    presentation ordering remains the MCP adapter's locale-aware sort.
    """
    if not isinstance(records, list) or any(not isinstance(record, dict) or
            any(not isinstance(record.get(key), str) for key in ('file', 'location', 'name', 'content'))
            for record in records):
        raise ValueError('migrations must be a list of file/location/name/content strings')
    migrations, errors, risks, groups = [], [], [], {}
    for record in records:
        file, location, content = record['file'], record['location'], record['content']
        metadata = migration_name(record['name'], ascii_digits=True)
        if metadata['type'] is None:
            errors.append({'file': file, 'code': 'naming', 'message': 'Expected V/U<version>__description.sql or R__description.sql.'})
        # JavaScript String.trim includes the BOM; retain that adapter behavior.
        if not content.strip(JS_WHITESPACE):
            errors.append({'file': file, 'code': 'empty', 'message': 'Empty migration.'})
        migrations.append({'file': file, 'location': location, **metadata})
        version = metadata['version']
        if version:
            key = (location, metadata['type'], version)
            if key in groups:
                errors.append({'file': file, 'code': 'duplicate-version', 'message': f'Equivalent version to {groups[key]}', 'version': version})
            else:
                groups[key] = file
        sql = re.sub(r"/\*[\s\S]*?\*/|--[^\n]*|'(?:''|[^'])*'", ' ', content)
        # JS uses ASCII word boundaries but Unicode whitespace. Normalize only
        # after removing comments/strings so line comments retain their extent.
        sql = sql.translate(JS_SPACE_TRANSLATION)
        flags = re.I | re.ASCII
        if re.search(r'\b(?:DROP\s+(?:TABLE|SCHEMA|DATABASE|INDEX|COLUMN)|TRUNCATE(?:\s+TABLE)?)\b', sql, flags):
            risks.append({'file': file, 'code': 'destructive-sql', 'message': 'DROP/TRUNCATE requires data-loss and lock review.'})
        for statement in sql.split(';'):
            if re.search(r'\bDELETE\s+FROM\b', statement, flags) and not re.search(r'\bWHERE\b', statement, flags):
                risks.append({'file': file, 'code': 'unbounded-delete', 'message': 'DELETE without WHERE requires explicit data-loss review.'})
    return {'migrations': migrations, 'errors': errors, 'risks': risks}
