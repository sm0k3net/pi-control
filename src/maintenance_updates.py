"""Verified, reversible replacement of an explicit set of dashboard files."""
import ast
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

import maintenance_bundle as bundle

APP = Path(__file__).resolve().parent
DATA = Path('/var/lib/pi-control-maintenance')
STATE = DATA / 'installation.json'
UNDO = DATA / 'previous.json'
JOURNAL = DATA / 'transaction.json'
LIMIT = 8 * 1024**2
API = 'https://api.github.com/repos/sm0k3net/pi-control/releases/latest'
ASSETS = 'https://github.com/sm0k3net/pi-control/releases/download/'
UNITS = ('pi-dashboard.service', 'pi-usb-mount.service')


def version(tag):
    if not isinstance(tag, str) or not re.fullmatch(r'v?[0-9]{1,6}(\.[0-9]{1,6}){2}', tag):
        raise ValueError('Некорректная версия релиза.')
    return tuple(map(int, tag.removeprefix('v').split('.')))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data, mode=0o600):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        tmp = Path(output.name)
        try:
            output.write(data)
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
            os.replace(tmp, path)
            directory = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            tmp.unlink(missing_ok=True)


def write_json(path, payload):
    atomic(path, json.dumps(payload, ensure_ascii=False).encode())


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Pi-Control-maintenance'})
    with urllib.request.urlopen(request, timeout=12) as response:
        payload = response.read(LIMIT + 1)
    if len(payload) > LIMIT:
        raise ValueError('Пакет превышает допустимый размер.')
    return payload


def check_release():
    installed = read_json(STATE, {}) or {}
    latest = json.loads(fetch(API))
    tag = latest.get('tag_name')
    remote = version(tag)
    asset = next((item for item in latest.get('assets', []) if item.get('name') == bundle.ASSET), {})
    digest = asset.get('digest') or ''
    compatible = bool(re.fullmatch(r'sha256:[a-f0-9]{64}', digest))
    ui = next((item for item in latest.get('assets', []) if item.get('name') == 'pi-control-ui.zip'), {})
    ui_digest = ui.get('digest') or ''
    compatible = compatible and bool(re.fullmatch(r'sha256:[a-f0-9]{64}', ui_digest))
    current = installed.get('version', '1.0.2')
    return {'tag': tag, 'current': current, 'available': compatible and remote > version(current),
            'digest': digest[7:] if compatible else '',
            'notes': str(latest.get('body') or '')[:16000], 'rollback': UNDO.exists()}


def unpack(raw, tag):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        items = archive.infolist()
        expected = set(bundle.FILES) | {'manifest.json'}
        actual = {item.filename for item in items}
        if not expected <= actual <= expected | set(bundle.ADDITIONS) or len(items) != len(actual):
            raise ValueError('Неверный состав серверного пакета.')
        if sum(item.file_size for item in items) > LIMIT:
            raise ValueError('Распакованный пакет превышает допустимый размер.')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('format') != 1 or version(manifest.get('version')) != version(tag):
            raise ValueError('Версия или формат пакета не совпадают.')
        result = {}
        for name in sorted(actual - {'manifest.json'}):
            data = archive.read(name)
            if sha(data) != manifest['files'].get(name):
                raise ValueError('Не прошла проверка SHA-256 файла ' + name)
            if name.endswith('.py'):
                ast.parse(data, filename=name)
            else:
                data.decode()
            result[name] = data
        return result


def installed_files():
    result = {}
    for name in (*bundle.FILES, *bundle.ADDITIONS):
        path = APP / name
        if name in bundle.ADDITIONS and not path.exists() and not path.is_symlink():
            continue
        if not path.is_file() or path.is_symlink():
            raise ValueError('Серверный файл отсутствует или не является обычным файлом: ' + name)
        result[name] = path.read_bytes()
    return result


def baseline(tag):
    payload = installed_files()
    write_json(STATE, {'version': tag, 'files': {name: sha(data) for name, data in payload.items()}})


def ui_version():
    active = read_json(APP / 'product-release.json')
    return active['version'] if active else ''


def switch_ui(target):
    path = APP / 'product-release.json'
    if target:
        version(target)
        write_json(path, {'version': target})
    else:
        path.unlink(missing_ok=True)


def snapshot(payload, version_tag):
    folder = Path(tempfile.mkdtemp(prefix='snapshot-', dir=DATA))
    for name, data in payload.items():
        atomic(folder / name, data)
    return {'name': folder.name, 'version': version_tag, 'ui': ui_version(),
            'files': {name: sha(data) for name, data in payload.items()}}


def from_snapshot(meta):
    name = meta.get('name', '')
    if not re.fullmatch(r'snapshot-[a-z0-9_]+', name):
        raise ValueError('Снимок отката повреждён.')
    files = set(meta['files'])
    if not set(bundle.FILES) <= files <= set(bundle.FILES) | set(bundle.ADDITIONS):
        raise ValueError('Неверный состав снимка отката.')
    data = {item: (DATA / name / item).read_bytes() for item in files}
    if any(sha(payload) != meta['files'].get(item) for item, payload in data.items()):
        raise ValueError('Снимок отката не прошёл проверку.')
    return data


def activate(payload):
    for name in (*bundle.FILES, *bundle.ADDITIONS):
        if name in payload:
            atomic(APP / name, payload[name], 0o644)
        else:
            (APP / name).unlink(missing_ok=True)


def health():
    for unit in UNITS:
        subprocess.run(['systemctl', 'restart', unit], check=True, timeout=45, capture_output=True)
    import ssl
    context = ssl._create_unverified_context()
    for _ in range(12):
        try:
            with urllib.request.urlopen('https://127.0.0.1:18443/api/session', context=context, timeout=2) as response:
                if isinstance(json.load(response).get('authenticated'), bool):
                    for unit in UNITS:
                        subprocess.run(['systemctl', 'is-active', '--quiet', unit],
                                       check=True, timeout=10, capture_output=True)
                    return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise ValueError('Панель не прошла проверку после перезапуска.')


def recover_interrupted():
    pending = read_json(JOURNAL)
    if pending:
        activate(from_snapshot(pending))
        if 'ui' in pending:
            switch_ui(pending['ui'])
        baseline(pending['version'])
        health()
        JOURNAL.unlink()


def transaction(payload, next_version, *, ui_install=None):
    current = read_json(STATE)
    if not current:
        raise ValueError('Установите исходную версию управляемых файлов.')
    existing = installed_files()
    if {name: sha(data) for name, data in existing.items()} != current['files']:
        raise ValueError('Обнаружены локальные изменения серверных файлов. Обновление приостановлено.')
    previous = snapshot(existing, current['version'])
    write_json(JOURNAL, previous)
    try:
        activate(payload)
        if ui_install:
            ui_install()
        health()
        baseline(next_version)
        write_json(UNDO, previous)
    except Exception:
        recover_interrupted()
        raise ValueError('Обновление не прошло проверку. Предыдущие файлы восстановлены.') from None
    JOURNAL.unlink()


def apply(tag):
    release = check_release()
    if tag != release['tag'] or not release['available']:
        raise ValueError('Выбранный серверный релиз уже недоступен.')
    raw = fetch(ASSETS + tag + '/' + bundle.ASSET)
    if sha(raw) != release['digest']:
        raise ValueError('Контрольная сумма GitHub не совпадает.')
    def install_ui():
        import product
        if version(product.current_version()) < version(tag):
            product.install_release({'tag': tag})

    transaction(unpack(raw, tag), tag.removeprefix('v'), ui_install=install_ui)
    return 'Серверное обновление установлено, панель прошла проверку.'


def rollback():
    previous = read_json(UNDO)
    if not previous:
        raise ValueError('Снимок предыдущей версии отсутствует.')
    restore_ui = (lambda: switch_ui(previous['ui'])) if 'ui' in previous else None
    transaction(from_snapshot(previous), previous['version'], ui_install=restore_ui)
    return 'Предыдущая серверная версия восстановлена.'
