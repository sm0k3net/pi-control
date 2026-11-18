"""Encrypted PostgreSQL snapshots in Supabase Storage and isolated local import."""
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import platform_events

CONTAINER = 'pi-stack-postgres-1'
BIN = Path('/usr/lib/postgresql/17/bin')
PUBLIC_KEY = Path('/root/pi-backup.pub')
NAME = re.compile(r'[a-z][a-z0-9_]{0,45}')
OBJECT = re.compile(r'pi-postgres-[a-z0-9_-]{1,95}\.dump\.age')
GUARD = threading.Lock()
STATE = {'phase': 'idle', 'message': ''}
MAX_TRANSFER = 10 * 1024**3


def checked(args, *, env=None, source=None, output=None, timeout=900, stage='Операция'):
    proc = subprocess.run(args, env=env, input=source, stdout=output or subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    if proc.returncode:
        raise ValueError(f'{stage}: ошибка (код {proc.returncode}). Проверьте реквизиты, доступ и версию PostgreSQL.')
    return proc.stdout


def admin(sql):
    return checked(['docker', 'exec', '-i', '-u', 'postgres', CONTAINER, 'psql', '-X', '-U',
                    'postgres', '-d', 'postgres', '-At', '-v', 'ON_ERROR_STOP=1'],
                   source=sql.encode(), timeout=25, stage='Локальная PostgreSQL').decode().strip()


def status():
    with GUARD:
        state = STATE.copy()
    return {**state, 'client_ready': (BIN / 'pg_dump').is_file(),
            'storage_ready': bool(shutil.which('rclone')), 'age_ready': PUBLIC_KEY.is_file()}


def storage(data):
    project = str(data.get('project', '')).strip()
    bucket = str(data.get('bucket', '')).strip()
    region = str(data.get('region', '')).strip()
    access = str(data.get('access', '')).strip()
    secret = str(data.get('storage_secret', '')).strip()
    if not re.fullmatch(r'[a-z0-9]{20}', project) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,61}', bucket):
        raise ValueError('Укажите Project Ref и существующий приватный bucket.')
    if not re.fullmatch(r'[a-z0-9-]{3,30}', region) or not access or not secret or max(len(secret), len(access)) > 512:
        raise ValueError('Укажите регион, Access Key ID и Secret Access Key из настроек S3.')
    env = dict(os.environ, RCLONE_CONFIG_PICLOUD_TYPE='s3', RCLONE_CONFIG_PICLOUD_PROVIDER='Other',
               RCLONE_CONFIG_PICLOUD_ENDPOINT=f'https://{project}.storage.supabase.co/storage/v1/s3',
               RCLONE_CONFIG_PICLOUD_REGION=region, RCLONE_CONFIG_PICLOUD_ACCESS_KEY_ID=access,
               RCLONE_CONFIG_PICLOUD_SECRET_ACCESS_KEY=secret, RCLONE_CONFIG_PICLOUD_FORCE_PATH_STYLE='true')
    return f'picloud:{bucket}/pi-control-postgres', env


def rclone(args, env, stage):
    return checked(['rclone', '--config', '/dev/null', '--s3-no-check-bucket',
                    '--retries', '2', '--low-level-retries', '2', *args], env=env, stage=stage)


def cloud_connection(data):
    host, user = (str(data.get(key, '')).strip() for key in ('host', 'user'))
    password = str(data.get('db_password', ''))
    schema = str(data.get('schema', 'public')).strip()
    direct = re.fullmatch(r'db\.[a-z0-9]{20}\.supabase\.co', host)
    pooler = re.fullmatch(r'aws-[0-9]+-[a-z0-9-]+\.pooler\.supabase\.com', host)
    if not (direct or pooler) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,120}', user) or not password or len(password) > 512:
        raise ValueError('Укажите хост Supabase Direct или Session pooler, пользователя и пароль БД.')
    if not NAME.fullmatch(schema) or schema in ('auth', 'storage', 'realtime', 'extensions', 'vault') or schema.startswith(('pg_', 'supabase_')):
        raise ValueError('Укажите одну прикладную схему, например public.')
    env = {k: v for k, v in os.environ.items() if not k.startswith('PG')}
    env.update(PGHOST=host, PGPORT='5432', PGDATABASE='postgres', PGUSER=user, PGPASSWORD=password,
               PGSSLMODE='verify-full', PGSSLROOTCERT='system', PGCONNECT_TIMEOUT='10')
    return env, schema


def local_names():
    return admin("SELECT datname FROM pg_database WHERE NOT datistemplate AND datallowconn ORDER BY 1;").splitlines()


def apply_archive(dump, database, env, major):
    options = ['--no-owner', '--no-acl', '--no-tablespaces', '--no-subscriptions']
    if major >= 17:
        checked([str(BIN/'pg_restore'), '--dbname', database, *options,
                 '--single-transaction', '--exit-on-error', str(dump)],
                env=env, stage='Восстановление в новую БД')
        return

    # pg_restore 17 emits a transaction_timeout preamble that PostgreSQL 16
    # cannot parse. Preserve psql restriction and strip only that preamble line.
    key = secrets.token_hex(24)
    sql = dump.with_suffix('.restore.sql')
    checked([str(BIN/'pg_restore'), *options, '--file', str(sql),
             '--restrict-key=' + key, str(dump)], stage='Подготовка восстановления')
    compatible = dump.with_suffix('.compatible.sql')
    with sql.open('rb') as source, compatible.open('wb') as output:
        for _ in range(32):
            line = source.readline(8192)
            if not line:
                break
            if line != b'SET transaction_timeout = 0;\n':
                output.write(line)
            else:
                break
        shutil.copyfileobj(source, output)
    sql.unlink()
    checked([str(BIN/'psql'), '-X', '-w', '--single-transaction', '-v', 'ON_ERROR_STOP=1',
             '--file', str(compatible)], env=env, stage='Восстановление в PostgreSQL 16')
    compatible.unlink()


def restore(dump, database):
    if not NAME.fullmatch(database) or database.startswith('pg_') or database in local_names():
        raise ValueError('Укажите новое имя базы. Существующие базы защищены от перезаписи.')
    address = checked(['docker', 'inspect', '-f', '{{range .NetworkSettings.Networks}}{{println .IPAddress}}{{end}}',
                       CONTAINER], timeout=10, stage='Сеть PostgreSQL').decode().strip().splitlines()
    import ipaddress
    addr = next((str(ipaddress.IPv4Address(ip)) for ip in address if re.fullmatch(r'\d+\.\d+\.\d+\.\d+', ip)), None)
    if not addr:
        raise ValueError('У контейнера PostgreSQL нет локального IPv4.')
    role = 'pi_restore_' + secrets.token_hex(8)
    password = secrets.token_hex(24)
    dbq, roleq = '"' + database + '"', '"' + role + '"'
    created_role = created_db = False
    try:
        admin(f"CREATE ROLE {roleq} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;")
        created_role = True
        admin(f'CREATE DATABASE {dbq} OWNER {roleq} TEMPLATE template0;')
        created_db = True
        admin(f'REVOKE ALL ON DATABASE {dbq} FROM PUBLIC;')
        env = {k: v for k, v in os.environ.items() if not k.startswith('PG')}
        env.update(PGHOST=addr, PGPORT='5432', PGDATABASE=database, PGUSER=role, PGPASSWORD=password,
                   PGSSLMODE='disable', PGCONNECT_TIMEOUT='10')
        major = int(admin("SHOW server_version_num;")) // 10000
        apply_archive(dump, database, env, major)
        admin(f'ALTER ROLE {roleq} NOLOGIN PASSWORD NULL;')
        return f'База {database} восстановлена. Назначьте права нужному локальному пользователю.'
    except Exception:
        if created_db:
            admin(f'DROP DATABASE {dbq} WITH (FORCE);')
        if created_role:
            admin(f'DROP ROLE {roleq};')
        raise


def upload(data, root):
    remote, env = storage(data)
    database = str(data.get('database', ''))
    if not NAME.fullmatch(database) or database not in local_names():
        raise ValueError('Выберите существующую локальную базу.')
    if not PUBLIC_KEY.is_file():
        raise ValueError('Публичный ключ age не настроен.')
    dump = root / 'snapshot.dump'
    with dump.open('wb') as output:
        checked(['docker', 'exec', '-u', 'postgres', CONTAINER, 'pg_dump', '-U', 'postgres',
                 '--no-password', '--format=custom', '--dbname', database], output=output, stage='Дамп локальной БД')
    key = f'pi-postgres-{database}-{time.strftime("%Y%m%d-%H%M%S", time.gmtime())}-{secrets.token_hex(4)}.dump.age'
    encrypted = root / key
    checked(['age', '-R', str(PUBLIC_KEY), '-o', str(encrypted), str(dump)], stage='Шифрование age')
    dump.unlink()
    rclone(['copyto', str(encrypted), remote+'/'+key, '--immutable'], env, 'Загрузка в Supabase')
    info = json.loads(rclone(['lsjson', '--stat', remote+'/'+key], env, 'Проверка объекта'))
    if info.get('Size') != encrypted.stat().st_size:
        raise ValueError('Размер облачного объекта не совпадает.')
    return 'Проверенный по размеру зашифрованный архив сохранён: ' + key


def restore_storage(data, root):
    remote, env = storage(data)
    key = str(data.get('object', ''))
    private_key = str(data.get('age_key', '')).strip()
    if not OBJECT.fullmatch(key) or not re.fullmatch(r'AGE-SECRET-KEY-1[0-9A-Z]{30,120}', private_key):
        raise ValueError('Укажите имя архива и строку закрытого ключа age.')
    info = json.loads(rclone(['lsjson', '--stat', remote+'/'+key], env, 'Проверка объекта'))
    size = info.get('Size', 0)
    if not 0 < size <= min(MAX_TRANSFER, shutil.disk_usage(root).free // 2):
        raise ValueError('Для архива недостаточно места или размер превышает 10 GiB.')
    encrypted, identity, dump = root/'snapshot.age', root/'identity', root/'snapshot.dump'
    rclone(['copyto', remote+'/'+key, str(encrypted)], env, 'Скачивание из Supabase')
    identity.write_text(private_key+'\n')
    checked(['age', '-d', '-i', str(identity), '-o', str(dump), str(encrypted)], stage='Расшифровка age')
    return restore(dump, str(data.get('target', '')))


def pull(data, root):
    env, schema = cloud_connection(data)
    dump = root/'supabase.dump'
    checked([str(BIN/'pg_dump'), '-w', '-Fc', '--schema', schema, '--strict-names',
             '--no-acl', '--no-owner', '--no-tablespaces', '--no-subscriptions',
             '-f', str(dump)], env=env, stage='Чтение схемы Supabase через TLS')
    return restore(dump, str(data.get('target', '')))


def job(op, data):
    try:
        with tempfile.TemporaryDirectory(prefix='pi-pg-', dir='/var/tmp') as directory:
            message = {'upload': upload, 'download': restore_storage, 'pull': pull}[op](data, Path(directory))
        phase = 'complete'
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        phase = 'error'
        message = str(exc) if isinstance(exc, ValueError) else 'Не удалось завершить операцию PostgreSQL.'
    with GUARD:
        STATE.update(phase=phase, message=message)
    platform_events.push('backup', 'info' if phase == 'complete' else 'error', message)


def action(data):
    op = data.get('op')
    if op == 'databases':
        return {'databases': local_names()}
    if op in ('list', 'storage-test'):
        remote, env = storage(data)
        if op == 'storage-test':
            rclone(['lsjson', '--dirs-only', '--max-depth', '1', remote.rsplit('/', 1)[0]],
                   env, 'Проверка Supabase Storage')
            return {'message': 'Доступ к bucket подтверждён.'}
        output = rclone(['lsjson', '--files-only', '--max-depth', '1', remote], env, 'Проверка Supabase Storage')
        return {'objects': [item for item in json.loads(output) if OBJECT.fullmatch(item['Name'])]}
    if op == 'db-test':
        env, schema = cloud_connection(data)
        return {'result': checked([str(BIN/'psql'), '-X', '-w', '-Atqc',
                    "SELECT current_database() || ' · PostgreSQL ' || current_setting('server_version')"],
                    env=env, timeout=20, stage='Проверка Supabase PostgreSQL').decode().strip()}
    if op not in ('upload', 'download', 'pull'):
        raise ValueError('Неизвестная операция.')
    with GUARD:
        if STATE['phase']=='running':
            raise ValueError('Другая операция PostgreSQL уже выполняется.')
        STATE.update(phase='running', message='Операция PostgreSQL выполняется…')
        threading.Thread(target=job,args=(op,data.copy()),daemon=True).start()
    return {'message':'Задание принято.'}
