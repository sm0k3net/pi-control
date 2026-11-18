"""Recover verified backup components into a new local staging directory."""
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

import usb_backup as usb

ROOT = Path('/var/lib/pi-control-maintenance/restores')
MAX_ENTRIES = 250_000
KEY = re.compile(r'AGE-SECRET-KEY-1[0-9A-Z]{30,120}')


def find_set(disk, name):
    if not isinstance(name, str) or not usb.SET_RE.fullmatch(name):
        raise ValueError('Выберите резервную копию.')
    folder = usb.secure_directory(disk, usb.BACKUP_DIR) / name
    if not folder.is_dir() or folder.is_symlink():
        raise ValueError('Комплект резервной копии недоступен.')
    return folder


def scan(identifier):
    target = usb.pick(identifier)
    with usb.mounted(target) as disk:
        directory = usb.secure_directory(disk, usb.BACKUP_DIR)
        items = sorted((p for p in directory.iterdir() if usb.SET_RE.fullmatch(p.name)),
                       reverse=True)[:100]
        return [{'name': p.name} for p in items if p.is_dir() and not p.is_symlink()]


def archive_kind(name, home):
    path = PurePosixPath(name)
    if path.is_absolute() or any(part == '..' for part in path.parts):
        raise ValueError('Небезопасный путь в архиве.')
    if not path.parts:
        return None, None
    if path.as_posix() == 'postgresql.sql':
        return 'postgres', path
    home_path = PurePosixPath(home.lstrip('/'))
    if home_path.parts[:1] != ('home',) or len(home_path.parts) != 2:
        raise ValueError('Некорректный домашний каталог в манифесте.')
    if path == home_path or home_path in path.parents:
        return 'home', path
    return 'services', path


def extract(archive, identity, output, selections, home, limits):
    proc = subprocess.Popen(['age', '--decrypt', '-i', str(identity), str(archive)],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        with tarfile.open(fileobj=proc.stdout, mode='r|gz') as tar:
            for item in tar:
                limits['entries'] += 1
                if limits['entries'] > MAX_ENTRIES:
                    raise ValueError('В архиве слишком много элементов.')
                kind, relative = archive_kind(item.name, home)
                if kind not in selections:
                    continue
                if not (item.isfile() or item.isdir()):
                    limits['skipped'] += 1
                    continue
                dest = output.joinpath(*relative.parts)
                if item.isdir():
                    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                limits['remaining'] -= item.size
                if limits['remaining'] < 0:
                    raise ValueError('Недостаточно места для распаковки.')
                dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = tar.extractfile(item)
                with source, dest.open('xb') as fd:
                    shutil.copyfileobj(source, fd, 1024**2)
                limits['files'] += 1
        # Continue reading until age has authenticated the final ciphertext chunk.
        while proc.stdout.read(1024**2):
            pass
        if proc.wait(timeout=30):
            raise ValueError('Ключ age не подошёл или зашифрованный файл повреждён.')
    except (tarfile.TarError, EOFError):
        raise ValueError('Архив не читается. Проверьте закрытый ключ age.') from None
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def restore(data):
    key = data.get('key', '')
    parts = data.get('parts', [])
    if not isinstance(key, str) or not KEY.fullmatch(key.strip()):
        raise ValueError('Введите строку закрытого ключа age.')
    if not isinstance(parts, list) or not parts or any(x not in ('home','postgres','services') for x in parts):
        raise ValueError('Выберите, что нужно восстановить.')
    target = usb.pick(data.get('uuid'))
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with usb.mounted(target) as disk:
        folder = find_set(disk, data.get('backup'))
        manifest = usb.verify_set(folder)
        if manifest.get('filesystem_uuid') != target['uuid']:
            raise ValueError('Манифест относится к другому USB-накопителю.')
        if 'postgres' in parts and manifest.get('postgresql') != 'logical':
            raise ValueError('В этой копии нет логического дампа. Для холодного тома потребуется совместимая версия PostgreSQL.')
        limits = {'entries': 0, 'skipped': 0, 'files': 0,
                  'remaining': max(0, shutil.disk_usage(ROOT).free - 256 * 1024**2)}
        with tempfile.NamedTemporaryFile(mode='w', dir='/run', prefix='pi-age-') as identity, \
             tempfile.TemporaryDirectory(dir=ROOT, prefix='.partial-') as working:
            identity.write(key.strip() + '\n')
            identity.flush()
            source = Path(working)
            if 'home' in parts:
                extract(folder / usb.FILES[0], Path(identity.name), source,
                        {'home'}, manifest['home'], limits)
            if {'postgres', 'services'} & set(parts):
                extract(folder / usb.FILES[1], Path(identity.name), source,
                        set(parts) & {'postgres', 'services'}, manifest['home'], limits)
            if 'postgres' in parts and not (source / 'postgresql.sql').is_file():
                raise ValueError('В комплекте не найден ожидаемый дамп PostgreSQL.')
            if not limits['files']:
                raise ValueError('В выбранной части нет файлов для восстановления.')
            name = time.strftime('restore-%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
            destination = ROOT / name
            source.rename(destination)
    return {'message': 'Выбранные данные извлечены в отдельный каталог.',
            'name': name, 'path': str(destination), 'files': limits['files'],
            'skipped': limits['skipped'], 'version': manifest['app_version']}
