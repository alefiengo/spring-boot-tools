"""Bounded Spring Initializr client. Generates files only; never runs a build.

Uses metadata defaults/choices from start.spring.io (Initializr v2.3 API).
Targets must be absent with an existing parent. Publication requires an atomic
exclusive directory rename on Linux/macOS; unsupported platforms fail closed.
"""
import ctypes
import errno
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys
import time
import urllib.parse
import urllib.request
import zipfile

from project_runtime import discover

ORIGIN = 'https://start.spring.io'
MAX_METADATA = 2 * 1024 * 1024
MAX_DOWNLOAD = 16 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024
MAX_ENTRIES = 2048
DOWNLOAD_SECONDS = 30


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Initializr redirects are refused')


def download(url, accept, limit, deadline):
    """Bound both bytes and elapsed time; never follow redirects to other hosts."""
    if not url.startswith(ORIGIN + '/'):
        raise ValueError('Only the official Spring Initializr service is allowed')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Initializr download timed out')
    request = urllib.request.Request(url, headers={'Accept': accept, 'User-Agent': 'spring-boot-tools'})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=min(5, remaining)) as response:
        if response.status != 200:
            raise ValueError('Initializr returned an unsuccessful response')
        size = response.headers.get('Content-Length')
        if size is not None and (not size.isdigit() or int(size) > limit):
            raise ValueError('Initializr response exceeds download limit')
        output = bytearray()
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError('Initializr download timed out')
            block = response.read1(min(65536, limit + 1 - len(output)))
            if not block:
                break
            output.extend(block)
            if len(output) > limit:
                raise ValueError('Initializr response exceeds download limit')
        if size is not None and len(output) != int(size):
            raise ValueError('Truncated Initializr response')
        return bytes(output)


def parameters(request, metadata):
    params = {}
    for key in ('type', 'language', 'bootVersion', 'javaVersion', 'packaging'):
        capability = metadata[key]
        value = request.get(key, capability.get('default'))
        choices = {item['id']: item for item in capability['values']}
        if not isinstance(value, str) or value not in choices:
            raise ValueError(f'{key} must match a current Initializr metadata choice')
        if key == 'type' and (choices[value].get('action') != '/starter.zip'
                              or choices[value].get('tags', {}).get('format') != 'project'):
            raise ValueError('type must generate a complete ZIP project')
        params[key] = value
    for key in ('groupId', 'artifactId', 'name', 'description', 'packageName', 'version'):
        # Initializr derives the package from the chosen coordinates. Sending
        # metadata's example package would override that for custom projects.
        if key == 'packageName' and key not in request:
            continue
        value = request.get(key, metadata.get(key, {}).get('default'))
        if value is None:
            continue
        if not isinstance(value, str) or not 1 <= len(value) <= 200 or any(ord(c) < 32 for c in value):
            raise ValueError(f'Invalid {key}')
        pattern = {'groupId': r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*',
                   'packageName': r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*',
                   'artifactId': r'[A-Za-z0-9][A-Za-z0-9_-]*',
                   'version': r'[A-Za-z0-9][A-Za-z0-9_.-]*'}.get(key)
        if pattern and not re.fullmatch(pattern, value):
            raise ValueError(f'Invalid {key}')
        params[key] = value
    dependencies = request.get('dependencies', [])
    allowed = {item['id'] for group in metadata['dependencies']['values'] for item in group['values']}
    if (not isinstance(dependencies, list) or len(dependencies) > 50
            or any(not isinstance(item, str) or item not in allowed for item in dependencies)):
        raise ValueError('dependencies must match current Initializr metadata identifiers')
    params['dependencies'] = ','.join(dict.fromkeys(dependencies))
    # An empty baseDir requests a flat archive, independent of the artifact name.
    params['baseDir'] = ''
    return params


def refuse_project(location):
    try:
        project = discover(location)
    except ValueError as error:
        if str(error).startswith('No Maven or Gradle project found from '):
            return
        raise
    raise ValueError(f'Existing project context at {project["root"]}; initialization refused')


def open_directory(folder):
    """Open each path component without following symlinks, anchoring all writes."""
    handle = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in folder.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=handle)
            os.close(handle)
            handle = child
        return handle
    except BaseException:
        os.close(handle)
        raise


def check_destination(parent, name):
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ValueError('Target already exists; choose an absent directory (no overwrites)')


def extract_archive(data, stage):
    if len(data) > MAX_DOWNLOAD:
        raise ValueError('Initializr response exceeds download limit')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_ENTRIES:
            raise ValueError('Archive entry limit exceeded or empty archive')
        total, seen, files = 0, set(), set()
        for entry in entries:
            name = entry.filename
            parts = name.rstrip('/').split('/')
            # Reject ambiguous names, including paths unsafe on Windows/macOS.
            if (entry.orig_filename != name or len(name) > 500 or len(parts) > 30
                    or any(not part or part in ('.', '..') or part.endswith((' ', '.'))
                           or any(ord(c) < 32 or c in '\\:' for c in part) for part in parts)
                    or name.startswith('/') or name.casefold().rstrip('/') in seen):
                raise ValueError('Unsafe or duplicate archive path')
            seen.add(name.casefold().rstrip('/'))
            mode = entry.external_attr >> 16
            kind = stat.S_IFMT(mode)
            directory = entry.is_dir()
            if (kind not in (0, stat.S_IFDIR if directory else stat.S_IFREG)
                    or mode & 0o7000 or entry.flag_bits & 1
                    or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                raise ValueError('Unsafe archive mode, encryption or compression')
            total += entry.file_size
            if (entry.file_size > MAX_FILE or total > MAX_EXPANDED
                    or entry.file_size > max(1, entry.compress_size) * 200
                    or directory and entry.file_size):
                raise ValueError('Archive expansion limit exceeded')
            if not directory:
                files.add(name)
        if not files.intersection({'pom.xml', 'build.gradle', 'build.gradle.kts'}):
            raise ValueError('Archive has no root project build file')
        for entry in entries:
            parts = entry.filename.rstrip('/').split('/')
            parent = os.dup(stage)
            try:
                for part in parts if entry.is_dir() else parts[:-1]:
                    try:
                        os.mkdir(part, 0o755, dir_fd=parent)
                    except FileExistsError:
                        pass
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    os.close(parent)
                    parent = child
                if entry.is_dir():
                    continue
                handle = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                with os.fdopen(handle, 'wb') as output, archive.open(entry) as source:
                    count = 0
                    while True:
                        block = source.read(min(65536, MAX_FILE + 1 - count))
                        if not block:
                            break
                        count += len(block)
                        if count > MAX_FILE or count > entry.file_size:
                            raise ValueError('Archive expansion limit exceeded')
                        output.write(block)
                    if count != entry.file_size:
                        raise ValueError('Truncated archive entry')
                    # Strip privileges and arbitrary executable modes. Wrappers
                    # retain executable permission without being executed here.
                    os.fchmod(output.fileno(), 0o755 if entry.filename in ('mvnw', 'gradlew') else 0o644)
            finally:
                os.close(parent)
        return sorted(files)


def publish(parent, stage, name):
    """Atomic no-replace publication: plain rename could clobber a racing target."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith('linux') and hasattr(libc, 'renameat2'):
        rename = libc.renameat2
        flag = 1  # RENAME_NOREPLACE
    elif sys.platform == 'darwin' and hasattr(libc, 'renameatx_np'):
        rename = libc.renameatx_np
        flag = 4  # RENAME_EXCL
    else:
        raise ValueError('Atomic exclusive directory publication is unavailable on this platform')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(stage), parent, os.fsencode(name), flag):
        code = ctypes.get_errno()
        if code in (errno.EEXIST, errno.ENOTEMPTY):
            raise ValueError('Target appeared during initialization; no files overwritten')
        raise OSError(code, os.strerror(code))


def initialize_project(request):
    if request.get('userRequested') is not True:
        raise ValueError('Initialization requires an explicit user scaffold request (userRequested=true)')
    raw = request.get('targetDirectory')
    if (not isinstance(raw, str) or not raw or len(raw) > 4096 or '\\' in raw
            or any(ord(c) < 32 for c in raw) or '..' in raw.split('/')):
        raise ValueError('targetDirectory must be an explicit path without traversal')
    context = Path(request.get('projectRoot') or os.getcwd()).absolute()
    target = Path(raw) if Path(raw).is_absolute() else context / raw
    if target == target.parent:
        raise ValueError('Target must name a new directory')
    refuse_project(context)
    refuse_project(target)
    parent = open_directory(target.parent)
    stage = None
    try:
        check_destination(parent, target.name)
        deadline = time.monotonic() + DOWNLOAD_SECONDS
        metadata = json.loads(download(ORIGIN + '/metadata/client', 'application/vnd.initializr.v2.3+json', MAX_METADATA, deadline))
        try:
            params = parameters(request, metadata)
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError('Malformed Initializr metadata') from error
        archive = download(ORIGIN + '/starter.zip?' + urllib.parse.urlencode(params), 'application/zip', MAX_DOWNLOAD, deadline)
        stage = '.sbt-initializr-' + secrets.token_hex(16)
        os.mkdir(stage, 0o700, dir_fd=parent)
        stage_handle = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            try:
                files = extract_archive(archive, stage_handle)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
                raise ValueError(f'Invalid Initializr archive: {error}') from error
        finally:
            os.close(stage_handle)
        # Repeat discovery and identity checks after network and extraction.
        # The anchored descriptor prevents ancestor symlink swaps redirecting writes.
        refuse_project(context)
        refuse_project(target)
        current_parent = open_directory(target.parent)
        try:
            if (os.fstat(current_parent).st_dev, os.fstat(current_parent).st_ino) != (os.fstat(parent).st_dev, os.fstat(parent).st_ino):
                raise ValueError('Target parent changed during initialization')
        finally:
            os.close(current_parent)
        check_destination(parent, target.name)
        os.chmod(stage, 0o755, dir_fd=parent)
        publish(parent, stage, target.name)
        stage = None
        return {'ok': True, 'targetDirectory': str(target), 'source': ORIGIN,
                'parameters': params, 'files': files, 'buildExecuted': False}
    finally:
        try:
            if stage is not None:
                shutil.rmtree(stage, dir_fd=parent)
        finally:
            os.close(parent)
