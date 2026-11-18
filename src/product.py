"""Local UI preferences and verified Pi Control UI release bundles."""
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREFERENCES = ROOT / 'product-preferences.json'
ACTIVE = ROOT / 'product-release.json'
RELEASES = ROOT / 'product-releases'
REPOSITORY = 'sm0k3net/pi-control'
ASSET = 'pi-control-ui.zip'
MAX_SIZE = 2_000_000
FILES = {'product.js': 'application/javascript', 'product.css': 'text/css',
         'product-en.json': 'application/json'}
VERSION = '1.0.0'
LOCK = threading.RLock()
CACHE = {}


def atomic_write(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        staging = Path(output.name)
        try:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
            os.replace(staging, path)
        finally:
            staging.unlink(missing_ok=True)


def version_parts(value):
    match = re.fullmatch(r'v?([0-9]{1,6})\.([0-9]{1,6})\.([0-9]{1,6})', value)
    if not match:
        raise ValueError('Invalid release version.')
    return tuple(int(part) for part in match.groups())


def current_version():
    try:
        value = json.loads(ACTIVE.read_text())['version']
        version_parts(value)
        return value
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return VERSION


def preferences():
    try:
        language = json.loads(PREFERENCES.read_text()).get('language')
    except (FileNotFoundError, ValueError):
        language = None
    return {'language': language if language in ('ru', 'en') else 'ru'}


def save_preferences(data):
    language = data.get('language')
    if language not in ('ru', 'en'):
        raise ValueError('Choose Russian or English.')
    atomic_write(PREFERENCES, json.dumps({'language': language}).encode())
    return preferences()


def static_asset(name):
    version = current_version()
    installed = RELEASES / version / name
    path = installed if installed.is_file() else ROOT / name
    payload = path.read_bytes()
    return (json.loads(payload) if name.endswith('.json') else payload,
            FILES[name] + '; charset=utf-8')


def fetch(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'Pi-Control'})
    with urllib.request.urlopen(request, timeout=10) as response:
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError('Release payload exceeds the size limit.')
    return content


def release_status(force=False):
    with LOCK:
        if force or not CACHE or time.monotonic() - CACHE['time'] > 900:
            url = 'https://api.github.com/repos/' + REPOSITORY + '/releases/latest'
            try:
                raw = json.loads(fetch(url, MAX_SIZE))
                tag = raw['tag_name']
                parts = version_parts(tag)
                asset = next((a for a in raw['assets'] if a['name'] == ASSET), {})
                digest = str(asset.get('digest') or '').removeprefix('sha256:')
                CACHE.update(time=time.monotonic(), release={
                    'tag': tag, 'latest': '.'.join(map(str, parts)),
                    'changelog': str(raw.get('body') or '')[:18000],
                    'digest': digest if re.fullmatch('[a-f0-9]{64}', digest) else '',
                    'error': '',
                })
            except Exception as exc:
                code = getattr(exc, 'code', None)
                reason = 'No GitHub release is published yet.' if code == 404 else 'GitHub release check failed.'
                CACHE.update(time=time.monotonic(), release={'error': reason})
        release = CACHE['release']
        current = current_version()
        newer = bool(release.get('latest') and version_parts(release['latest']) > version_parts(current))
        return {**release, 'current': current, 'available': newer and bool(release.get('digest')),
                'newer': newer, 'repository': 'https://github.com/' + REPOSITORY + '/releases'}


def install_release(data):
    with LOCK:
        release = release_status(force=True)
        if not release['available'] or release['tag'] != data.get('tag'):
            raise ValueError('The selected release is unavailable. Check for updates again.')
        url = f"https://github.com/{REPOSITORY}/releases/download/{release['tag']}/{ASSET}"
        content = fetch(url, MAX_SIZE)
        if hashlib.sha256(content).hexdigest() != release['digest']:
            raise ValueError('Release checksum did not match. Nothing was installed.')
        version = release['latest']
        RELEASES.mkdir(mode=0o700, exist_ok=True)
        target = RELEASES / version
        with zipfile.ZipFile(io.BytesIO(content)) as bundle:
            members = bundle.infolist()
            if len(members) != len(FILES) or {m.filename for m in members} != FILES.keys():
                raise ValueError('Unrecognized release bundle.')
            if sum(m.file_size for m in members) > MAX_SIZE:
                raise ValueError('Unpacked release is too large.')
            with tempfile.TemporaryDirectory(dir=RELEASES) as work:
                staging = Path(work) / 'release'
                staging.mkdir()
                for name in FILES:
                    payload = bundle.read(name)
                    payload.decode('utf-8')
                    if name.endswith('.json') and not isinstance(json.loads(payload), dict):
                        raise ValueError('Invalid translation catalog.')
                    (staging / name).write_bytes(payload)
                if not target.exists():
                    os.replace(staging, target)
                elif any((target / name).read_bytes() != (staging / name).read_bytes() for name in FILES):
                    raise ValueError('This version already exists with different files.')
                atomic_write(ACTIVE, json.dumps({'version': version}).encode())
        return {'current': version}


def handle_get(handler):
    path = handler.path
    if path.removeprefix('/') in FILES:
        return handler.respond(200, *static_asset(path[1:]))
    if path == '/api/product/preferences':
        return handler.respond(200, preferences())
    if path == '/api/product/update':
        if not handler.authenticated():
            return handler.respond(401, {'error': 'Требуется вход'})
        return handler.respond(200, release_status(
            force=handler.headers.get('X-Pi-Refresh') == '1'))


def handle_post(handler, data):
    try:
        if handler.path == '/api/product/preferences':
            return handler.respond(200, save_preferences(data))
        if handler.path == '/api/product/vault-restore':
            return handler.respond(200, restore_vault(data))
        if handler.path == '/api/product/update':
            return handler.respond(200, install_release(data))
        return handler.respond(404, {'error': 'Not found'})
    except ValueError as exc:
        return handler.respond(400, {'error': str(exc)})
    except (OSError, zipfile.BadZipFile):
        return handler.respond(503, {'error': 'Release could not be installed.'})


def restore_vault(data):
    """Initialize an empty vault with a lossless, verified KDBX backup."""
    import base64
    import binascii
    import vault

    with vault.guard:
        if vault.FILE.exists():
            raise ValueError('A vault already exists. Use the entry import to merge another file.')
        encoded = data.get('data')
        password = data.get('master')
        if not isinstance(encoded, str) or len(encoded) > 2_500_000:
            raise ValueError('The KDBX file is missing or too large.')
        if not isinstance(password, str) or len(password) > 256:
            raise ValueError('Enter the master password for this backup.')
        try:
            blob = base64.b64decode(encoded, validate=True)
        except binascii.Error:
            raise ValueError('Invalid KDBX file.') from None
        with tempfile.NamedTemporaryFile(dir=vault.FILE.parent) as temporary:
            temporary.write(blob)
            temporary.flush()
            try:
                vault.PyKeePass(temporary.name, password=password)
            except Exception:
                raise ValueError('Could not open the backup. Check its master password and format.') from None
        atomic_write(vault.FILE, blob)
    return {'exists': True}
