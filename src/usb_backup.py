"""Encrypted USB backups with manual, scheduled and insertion-driven modes."""
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

import platform_events

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'usb-backup-settings.json'
STATUS = ROOT / 'usb-backup-status.json'
RUNTIME = Path('/run/pi-usb-backup')
REQUEST = RUNTIME / 'request.json'
PRESENCE = RUNTIME / 'presence.json'
RECIPIENT = Path('/root/pi-backup.pub')
SYSTEM_UNIT = 'pi-usb-backup.service'
MOUNT = RUNTIME / 'disk'
FILES_MOUNT = Path('/run/pi-usb-files')
FILES_STATE = Path('/run/pi-usb-files-state')
FILES_RESCAN = FILES_STATE / 'retry'
BACKUP_DIR = 'pi-control-backups'
SCHEMA = 1
UUID_RE = re.compile(r'^[a-zA-Z0-9-]{4,80}$')
SET_RE = re.compile(r'^backup-[0-9T-]+Z-[a-f0-9]{8}$')
FORMATS = {'ext4', 'exfat', 'vfat', 'ntfs'}
FILES = ('home.tar.gz.age', 'services.tar.gz.age')


def load(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(data, ensure_ascii=False).encode())
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def run(args, timeout=20):
    proc = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if proc.returncode:
        raise ValueError(f'{args[0]} не выполнил операцию (код {proc.returncode}).')
    return proc.stdout.strip()


@contextlib.contextmanager
def lock(path, blocking=False):
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise ValueError('Резервное копирование уже выполняется.') from exc
        yield


def devices():
    result = []
    tree = json.loads(run(['lsblk', '--json', '--tree', '--bytes',
                           '-o', 'PATH,TYPE,TRAN,RM,UUID,FSTYPE,SIZE,LABEL,MOUNTPOINTS,RO']))
    def visit(item, inherited=False):
        usb = item.get('tran') == 'usb' or inherited
        if not usb and item.get('rm') and item.get('path'):
            sysfs = (Path('/sys/class/block') / Path(item['path']).name).resolve()
            usb = any(re.fullmatch(r'usb[0-9]+', part) for part in sysfs.parts)
        mounts = [x for x in item.get('mountpoints') or [] if x]
        key = item.get('uuid') or ''
        if (usb and item.get('type') in ('part', 'disk') and UUID_RE.fullmatch(key)
                and not item.get('ro') and not any(m in ('/', '/boot', '/boot/firmware') for m in mounts)):
            result.append({'uuid': key, 'label': item.get('label') or '',
                           'path': item['path'], 'format': item.get('fstype'),
                           'size': item.get('size'), 'mounts': mounts,
                           'supported': item.get('fstype') in FORMATS})
        for child in item.get('children', []):
            visit(child, usb)
    for item in tree['blockdevices']:
        visit(item)
    counts = {item['uuid']: sum(other['uuid'] == item['uuid'] for other in result) for item in result}
    for item in result:
        item['supported'] = item['supported'] and counts[item['uuid']] == 1
    return result


def settings():
    return load(CONFIG, {}) or {}


def status():
    last = load(STATUS, {}) or {}
    if last.get('state') == 'running' and subprocess.run(
            ['systemctl', 'is-active', '--quiet', SYSTEM_UNIT]).returncode != 0:
        last = {**last, 'state': 'failed', 'message': 'Последнее копирование было прервано.'}
    return {'devices': devices(), 'settings': {k: v for k, v in settings().items() if k != 'identity'},
            'last': last, 'recipient_ready': RECIPIENT.is_file()}


def pick(key):
    target = next((d for d in devices() if d['uuid'] == key and d['supported']), None)
    if not target:
        raise ValueError('Подключите поддерживаемый USB-накопитель и обновите список.')
    return target


def action(data):
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    with lock(RUNTIME / 'request.lock', blocking=True):
        op = data.get('op')
        cfg = settings()
        if op in ('prepare', 'start', 'verify'):
            if REQUEST.exists() or subprocess.run(
                    ['systemctl', 'is-active', '--quiet', SYSTEM_UNIT]).returncode == 0:
                raise ValueError('Дождитесь завершения текущего задания.')
            target = pick(data.get('uuid'))
            if op != 'prepare' and cfg.get('uuid') != target['uuid']:
                raise ValueError('Сначала подготовьте этот накопитель.')
            if op != 'verify' and not RECIPIENT.is_file():
                raise ValueError('Настройте публичный ключ age для резервного копирования.')
            save(REQUEST, {'op': op, 'uuid': target['uuid']})
            try:
                run(['systemctl', 'start', '--no-block', SYSTEM_UNIT])
            except Exception:
                REQUEST.unlink(missing_ok=True)
                raise
        elif op == 'settings':
            if not cfg.get('uuid'):
                raise ValueError('Сначала подготовьте USB-накопитель.')
            schedule, clock = data.get('schedule'), data.get('clock')
            hours = data.get('insert_hours')
            if schedule not in ('off', 'daily', 'weekly') or not isinstance(clock, str) or not re.fullmatch(
                    r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', clock):
                raise ValueError('Проверьте расписание.')
            if type(hours) is not int or not 1 <= hours <= 168:
                raise ValueError('Интервал подключения должен быть от 1 до 168 часов.')
            cfg.update(schedule=schedule, clock=clock, on_insert=data.get('on_insert') is True,
                       insert_hours=hours)
            save(CONFIG, cfg)
        else:
            raise ValueError('Неизвестное действие.')
    return {'message': 'Настройки сохранены.' if op == 'settings' else 'Задание принято.'}


def is_device_mount(path, target):
    if not path.is_absolute() or path.is_symlink():
        return False
    try:
        result = run(['findmnt', '--noheadings', '--raw', '--output', 'SOURCE',
                      '--mountpoint', str(path)])
        return Path(result).resolve() == Path(target['path']).resolve()
    except (ValueError, OSError, subprocess.SubprocessError):
        return False


def existing_mount(target):
    paths = [Path(p) for p in target.get('mounts', [])]
    managed = FILES_MOUNT / target['uuid']
    paths.sort(key=lambda path: path != managed)
    return next((path for path in paths if path != MOUNT and is_device_mount(path, target)), None)


def mount_device(target, path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or os.path.ismount(path):
        raise ValueError('Рабочая точка монтирования занята.')
    device = Path(target['path'])
    if not stat.S_ISBLK(device.stat().st_mode):
        raise ValueError('Раздел больше недоступен.')
    run(['mount', '-t', target['format'], '-o', 'nosuid,nodev,noexec',
         '--', str(device), str(path)])
    if not is_device_mount(path, target):
        run(['umount', '--', str(path)], timeout=30)
        raise ValueError('Не удалось подтвердить USB-файловую систему.')


def media_identity(item):
    node = (Path('/sys/class/block') / Path(item['path']).name).resolve()
    for candidate in (node, node.parent):
        try:
            return item['path'] + ':' + (candidate / 'diskseq').read_text().strip()
        except OSError:
            continue
    return item['path']


def automount_files(detected, prior, retry=False):
    FILES_MOUNT.mkdir(mode=0o700, parents=True, exist_ok=True)
    active = {item['uuid']: item for item in detected if item['supported']}
    state = {}
    now = time.time()
    blocked = set()
    for path in FILES_MOUNT.iterdir():
        if not UUID_RE.fullmatch(path.name) or path.is_symlink():
            continue
        item = active.get(path.name)
        old = prior.get(path.name, {})
        changed = item and old.get('identity') not in (None, media_identity(item))
        if os.path.ismount(path) and (not item or changed or not is_device_mount(path, item)):
            try:
                run(['umount', '--', str(path)], timeout=12)
            except (OSError, ValueError, subprocess.SubprocessError):
                blocked.add(path.name)
        if not os.path.ismount(path) and not item:
            try:
                path.rmdir()
            except OSError:
                pass
    for key, item in active.items():
        identity = media_identity(item)
        old = prior.get(key, {})
        if key in blocked:
            state[key] = {'identity': identity, 'error': 'Старая точка монтирования занята. Закройте операции с USB и повторите.'}
            continue
        if existing_mount(item):
            state[key] = {'identity': identity}
            continue
        if not retry and old.get('identity') == identity and old.get('next_try', 0) > now:
            state[key] = old
            continue
        try:
            mount_device(item, FILES_MOUNT / key)
            state[key] = {'identity': identity}
        except (ValueError, OSError, subprocess.SubprocessError):
            state[key] = {'identity': identity, 'next_try': now + 25,
                          'error': 'Не удалось смонтировать USB. Проверьте файловую систему или повторите.'}
    return state


def file_roots():
    state = load(FILES_STATE / 'mounts.json', {}) or {}
    result = []
    for item in devices():
        if not item['supported']:
            continue
        mount = existing_mount(item)
        previous = state.get(item['uuid'], {})
        error = previous.get('error', '') if previous.get('identity') == media_identity(item) else ''
        result.append({'id': 'usb:' + item['uuid'], 'name': 'USB · ' + (item['label'] or item['path']),
                       'ready': mount is not None and not error, 'error': error})
    return result


def request_rescan():
    FILES_STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    FILES_RESCAN.touch(mode=0o600)
    return {'message': 'Повторная проверка USB запрошена.'}


def file_root(identifier):
    if not identifier.startswith('usb:') or not UUID_RE.fullmatch(identifier[4:]):
        return None
    try:
        item = pick(identifier[4:])
        current = (load(FILES_STATE / 'mounts.json', {}) or {}).get(item['uuid'], {})
        if current.get('error') or current.get('identity') not in (None, media_identity(item)):
            return None
        return existing_mount(item)
    except ValueError:
        return None


@contextlib.contextmanager
def mounted(target):
    # Reuse an authenticated local mount; never unmount another application's mount.
    current = existing_mount(target)
    if current is not None:
        yield current
        return
    mount_device(target, MOUNT)
    try:
        yield MOUNT
    finally:
        os.sync()
        run(['umount', '--', str(MOUNT)], timeout=60)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_set(folder):
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('Некорректный каталог копии.')
    file = folder / 'manifest.json'
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 32768:
        raise ValueError('Нет корректного манифеста копии.')
    manifest = load(file, {})
    if not isinstance(manifest, dict) or not all(k in manifest for k in ('created', 'files', 'app_version')):
        raise ValueError('Повреждён манифест копии.')
    if manifest.get('schema') != SCHEMA or not isinstance(manifest['files'], dict) or set(manifest['files']) != set(FILES):
        raise ValueError('Неподдерживаемый формат копии.')
    if not isinstance(manifest['created'], (float, int)) or not 0 < manifest['created'] <= time.time() + 300:
        raise ValueError('Некорректная дата копии.')
    if not isinstance(manifest['app_version'], str) or not manifest['app_version']:
        raise ValueError('В манифесте отсутствует версия.')
    for name, info in manifest['files'].items():
        path = folder / name
        if (path.is_symlink() or not path.is_file() or not isinstance(info, dict)
                or not info.get('size') or path.stat().st_size != info['size']
                or digest(path) != info.get('sha256')):
            raise ValueError('Размер или SHA-256 резервной копии не совпадают.')
    return manifest


def latest(directory):
    valid = sorted((x for x in directory.iterdir() if SET_RE.fullmatch(x.name)), reverse=True)
    return valid[0] if valid else None


def encrypted_tar(output, paths, stage=None, excludes=(), strict=True):
    args = ['tar', '--create', '--gzip', '--file=-', '--one-file-system', '--numeric-owner',
            '--acls', '--xattrs']
    args.extend('--exclude=' + pattern for pattern in excludes)
    if stage:
        args.extend(['-C', str(stage), '.'])
    args.extend(['-C', '/', *paths])
    with tempfile.TemporaryFile() as log:
        archive = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=log)
        try:
            age = subprocess.run(
                ['age', '-R', str(RECIPIENT), '-o', str(output)], stdin=archive.stdout,
                stdout=subprocess.DEVNULL, stderr=log, timeout=21600)
        finally:
            archive.stdout.close()
            if archive.poll() is None:
                try:
                    archive.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    archive.kill()
                    archive.wait()
        if age.returncode or archive.returncode not in ((0,) if strict else (0, 1)):
            raise ValueError('Ошибка записи архива. Проверьте место, USB и ключ age.')
    with output.open('rb') as handle:
        os.fsync(handle.fileno())
    return archive.returncode == 1


def inventory():
    ids = run(['docker', 'ps', '-aq']).split()
    containers = json.loads(run(['docker', 'inspect', *ids])) if ids else []
    services = ('agent', 'n8n', 'nextcloud', 'pihole', 'gluetun', 'postgres',
                'portainer', 'onlyoffice', 'redis')
    containers = [item for item in containers
                  if any(service in item['Name'] for service in services)]
    sources = set()
    paused = []
    postgres = None
    for item in containers:
        name = item['Name'].lstrip('/')
        active = item['State']['Running']
        if 'postgres' in name and active:
            postgres = name
            continue
        for mount in item.get('Mounts', []):
            source = Path(mount.get('Source', '/'))
            if mount.get('Type') == 'volume' and source.is_dir() and source.is_relative_to('/var/lib/docker/volumes'):
                sources.add(str(source).lstrip('/'))
            elif mount.get('Type') == 'bind' and source.is_absolute() and source.exists() and any(
                    source.is_relative_to(prefix) for prefix in ('/opt', '/srv', '/var/lib', '/mnt', '/media')):
                sources.add(str(source).lstrip('/'))
        if active and 'gluetun' not in name:
            paused.append(name)
    return sources, paused, postgres


@contextlib.contextmanager
def quiesce(containers, harness=False):
    stopped = []
    harness_stopped = False
    try:
        if harness and subprocess.run(['systemctl', 'is-active', '--quiet', 'dsh-web.service']).returncode == 0:
            harness_stopped = True
            run(['systemctl', 'stop', 'dsh-web.service'], 90)
        for name in reversed(containers):
            stopped.append(name)
            run(['docker', 'stop', '-t', '30', name], 60)
        yield
    finally:
        failed = []
        for name in reversed(stopped):
            try:
                run(['docker', 'start', name], 60)
            except (ValueError, OSError, subprocess.SubprocessError):
                failed.append(name)
        if harness_stopped:
            try:
                run(['systemctl', 'start', 'dsh-web.service'], 90)
            except (ValueError, OSError, subprocess.SubprocessError):
                failed.append('DeepSeek Harness')
        if failed:
            raise ValueError('Проверьте перезапуск сервисов: ' + ', '.join(failed))


def checkpoint(config, state, message, **fields):
    save(STATUS, {'state': state, 'time': int(time.time()), 'uuid': config.get('uuid', ''),
                  'message': message, **fields})


def build(directory, target, config):
    import product

    username = os.environ.get('PI_BACKUP_USER', 'sm0k3')
    home = Path(pwd.getpwnam(username).pw_dir)
    if not home.is_dir() or not home.is_relative_to('/home') or home == Path('/home'):
        raise ValueError('Рабочий домашний каталог отсутствует.')
    free = shutil.disk_usage(directory).free
    if free < 256 * 1024 * 1024:
        raise ValueError('На USB-накопителе осталось меньше 256 МиБ.')
    name = 'backup-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ') + '-' + uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory(prefix='.partial-', dir=directory) as working:
        folder = Path(working)
        checkpoint(config, 'running', 'Шифрование домашнего каталога…', stage='home')
        with quiesce([], harness=True):
            changed = encrypted_tar(folder / FILES[0], [str(home).lstrip('/')], strict=False)

        checkpoint(config, 'running', 'Согласованное копирование сервисов и PostgreSQL…', stage='services')
        volumes, writers, postgres = inventory()
        configs = {'opt/pi-dashboard', 'opt/pi-stack', 'etc/systemd/system', 'etc/tor',
                   'etc/NetworkManager/system-connections', 'etc/wireguard', 'etc/cloudflared',
                   'etc/cups', 'etc/NetworkManager/dispatcher.d', 'root/pi-backup.pub',
                   'root/pi-passwords.txt'}
        # Keep installed wrappers needed to restore Pi services.
        configs.update(str(p).lstrip('/') for p in Path('/usr/local/bin').glob('pi-*') if p.is_file())
        paths = sorted(p for p in configs | volumes if (Path('/') / p).exists())
        if any((Path('/') / p).stat().st_dev == directory.stat().st_dev for p in paths):
            raise ValueError('USB-накопитель уже используется как источник данных одного из сервисов.')
        with tempfile.TemporaryDirectory(prefix='pi-usb-stage-', dir='/var/tmp') as temp:
            stage = Path(temp)
            with quiesce(writers):
                if postgres:
                    with (stage / 'postgresql.sql').open('wb') as output:
                        result = subprocess.run(
                            ['docker', 'exec', '-u', 'postgres', postgres, 'pg_dumpall', '-U', 'postgres'],
                            stdout=output, stderr=subprocess.DEVNULL, timeout=3600)
                    if result.returncode or not (stage / 'postgresql.sql').stat().st_size:
                        raise ValueError('Не удалось создать дамп PostgreSQL.')
                service_changed = encrypted_tar(folder / FILES[1], paths, stage, excludes=(
                    'opt/pi-dashboard/platform-events.sqlite3*', 'opt/pi-dashboard/metrics-history.sqlite3*',
                    'opt/pi-dashboard/usb-backup-status.json', 'opt/pi-dashboard/__pycache__',
                    'opt/pi-dashboard/capture*', 'opt/pi-dashboard/wifi-audits',
                ), strict=False)

        checkpoint(config, 'running', 'Проверка размера и SHA-256…', stage='verify')
        manifest = {'schema': SCHEMA, 'created': int(time.time()), 'app_version': product.current_version(),
                    'filesystem_uuid': target['uuid'], 'home': str(home),
                    'home_changed': changed, 'service_files_changed': service_changed, 'sources': paths,
                    'postgresql': 'logical' if postgres else 'cold-volume-if-present',
                    'files': {part: {'size': (folder / part).stat().st_size, 'sha256': digest(folder / part)}
                              for part in FILES}}
        save(folder / 'manifest.json', manifest)
        verify_set(folder)
        published = directory / name
        folder.rename(published)
    os.sync()
    return name, manifest


def secure_directory(mount, name, create=False):
    path = mount / name
    if path.is_symlink():
        raise ValueError('Каталог резервных копий не должен быть символической ссылкой.')
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir():
        raise ValueError('Накопитель не подготовлен. Выполните первое копирование вручную.')
    return path


def clock_period(config):
    schedule = config.get('schedule')
    if schedule not in ('daily', 'weekly'):
        return None
    now = datetime.datetime.now(ZoneInfo('Europe/Minsk'))
    if now.strftime('%H:%M') < config.get('clock', '03:00'):
        return None
    if schedule == 'daily':
        return now.date().isoformat()
    monday = now.date() - datetime.timedelta(days=now.weekday())
    if now.weekday() != 0:
        return None
    return monday.isoformat()


def execute(op, target, config, period=None):
    identity = config.get('identity') or uuid.uuid4().hex
    with mounted(target) as mount:
        directory = secure_directory(mount, BACKUP_DIR, create=op == 'prepare')
        marker = directory / '.pi-control-volume.json'
        if marker.is_symlink() or (marker.exists() and (
                not marker.is_file() or marker.stat().st_size > 4096)):
            raise ValueError('Некорректная метка USB-накопителя.')
        expected = {'identity': identity, 'uuid': target['uuid'], 'schema': SCHEMA}
        existing = load(marker)
        if existing != expected and (op != 'prepare' or marker.exists()):
            raise ValueError('USB-накопитель не подтверждён. Выберите его в настройках и подготовьте.')
        previous = latest(directory)
        if op in ('verify', 'insert', 'scheduled'):
            if not previous:
                raise ValueError('Первой копии ещё нет. Запустите подготовку вручную.')
            checkpoint(config, 'running', 'Проверка предыдущей копии…', stage='verify')
            verified = verify_set(previous)
            if verified.get('filesystem_uuid') != target['uuid']:
                raise ValueError('Манифест копии относится к другому накопителю.')
            import product
            same = verified['app_version'] == product.current_version()
            if op == 'verify' or (op == 'insert' and same and
                                 time.time() - verified['created'] < config.get('insert_hours', 24) * 3600):
                checkpoint(config, 'verified', 'Последняя копия проверена.', backup=previous.name,
                           saved=verified['created'], bytes=sum(v['size'] for v in verified['files'].values()),
                           app_version=verified['app_version'])
                if op == 'verify':
                    platform_events.push('backup', 'info', 'Размер и SHA-256 последней USB-копии совпадают.')
                return
        with lock(Path('/run/pi-address-refresh.lock')):
            name, manifest = build(directory, target, config)
        if op == 'prepare':
            if not marker.exists():
                save(marker, expected)
            with lock(RUNTIME / 'request.lock', blocking=True):
                saved = settings()
                config = ({**saved, 'identity': identity} if saved.get('uuid') == target['uuid'] else
                          {'uuid': target['uuid'], 'identity': identity, 'schedule': 'off',
                           'clock': '03:00', 'on_insert': False, 'insert_hours': 24})
                save(CONFIG, config)
        if period:
            with lock(RUNTIME / 'request.lock', blocking=True):
                saved = settings()
                if saved.get('uuid') == target['uuid']:
                    saved['last_period'] = period
                    save(CONFIG, saved)
        size = sum(v['size'] for v in manifest['files'].values())
        message = 'USB-копия зашифрована и проверена.' + (
            ' Некоторые файлы изменились при чтении.' if manifest['home_changed'] or manifest['service_files_changed'] else '')
        checkpoint(config, 'complete', message, backup=name, saved=manifest['created'],
                   bytes=size, app_version=manifest['app_version'])
        platform_events.push('backup', 'info', message)


def tick():
    os.umask(0o077)
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    with lock(RUNTIME / 'worker.lock'):
        cfg = settings()
        present = devices()
        connected = cfg.get('uuid') in {d['uuid'] for d in present if d['supported']}
        prior = load(PRESENCE, {}) or {}
        save(PRESENCE, {'connected': connected, 'uuid': cfg.get('uuid'), 'attempt': prior.get('attempt')})
        with lock(RUNTIME / 'request.lock', blocking=True):
            request = load(REQUEST)
            REQUEST.unlink(missing_ok=True)
        op, key, period = None, cfg.get('uuid'), None
        if request:
            op, key = request['op'], request['uuid']
        elif connected and cfg.get('on_insert') and (not prior.get('connected') or prior.get('uuid') != key):
            op = 'insert'
        elif connected:
            period = clock_period(cfg)
            attempt = prior.get('attempt') or {}
            if (period and period != cfg.get('last_period')
                    and (attempt.get('period') != period or time.time() - attempt.get('time', 0) > 3600)):
                op = 'scheduled'
                save(PRESENCE, {'connected': True, 'uuid': key,
                                'attempt': {'period': period, 'time': time.time()}})
        if not op:
            return
        cfg = {**cfg, 'uuid': key}
        try:
            execute(op, pick(key), cfg, period if op == 'scheduled' else None)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else f'Ошибка резервирования ({type(exc).__name__}).'
            checkpoint(cfg, 'failed', message)
            platform_events.push('backup', 'error', message)


if __name__ == '__main__':
    def interrupted(signum, frame):
        # Let context managers restore services and unmount before the worker exits.
        raise ValueError('Резервное копирование остановлено.')
    signal.signal(signal.SIGTERM, interrupted)
    tick()
