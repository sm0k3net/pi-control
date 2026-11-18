"""Read-only task templates and USB browsing for enrolled Pi nodes."""
import hashlib
import hmac
import http.client
import json
import ssl
import threading
import uuid
from contextlib import contextmanager
import time
from urllib.parse import urlencode

import cluster5

TASKS = {'host', 'links', 'observed', 'bluetooth', 'usb'}
MAX = 12 * 1024 * 1024


def status():
    return {'nodes': [{k: node[k] for k in ('id', 'name', 'host')} for node in cluster5.get()]}


def secret(node, data):
    password = data.get('password')
    if password and data.get('id') == node['id']:
        return str(password)
    entry = node.get('entry')
    if not entry:
        return ''
    try:
        import service_secrets
    except ImportError:
        if not data.get('master') or data.get('id') != node['id']:
            return ''
        import vault
        return vault.dispatch('reveal', {'id': entry, 'master': data['master']})['password']
    return service_secrets.resolve({'id': entry, 'master': data.get('master', '')}, 'nodes')['password']


class PinnedConnection(http.client.HTTPSConnection):
    def __init__(self, node, timeout):
        self.fingerprint = node['fingerprint'].upper()
        if not cluster5.addr_ok(node['host']):
            raise ValueError('Адрес узла недопустим.')
        super().__init__(node['host'], node['port'], context=ssl._create_unverified_context(),
                         timeout=timeout)

    def connect(self):
        super().connect()
        fingerprint = hashlib.sha256(self.sock.getpeercert(binary_form=True)).hexdigest().upper()
        if not hmac.compare_digest(fingerprint, self.fingerprint):
            self.close()
            raise ValueError('Отпечаток сертификата не совпадает.')


@contextmanager
def peer_session(node, password, timeout=8):
    conn = PinnedConnection(node, timeout)
    headers = {'Origin': f"https://{node['host']}:{node['port']}", 'Content-Type': 'application/json'}
    try:
        conn.request('POST', '/api/login', body=json.dumps({'password': password}), headers=headers)
        login = conn.getresponse()
        cookie = login.getheader('Set-Cookie', '').split(';', 1)[0]
        login.read(8192)
        if login.status != 200 or not cookie.startswith('pi_session='):
            raise ValueError('Узел отклонил вход.')
        headers['Cookie'] = cookie
        yield conn, headers
    finally:
        conn.close()
        if headers.get('Cookie'):
            try:
                conn.request('POST', '/api/logout', body='{}', headers=headers)
                conn.getresponse().read(8192)
            except (OSError, ValueError, http.client.HTTPException):
                pass
            finally:
                conn.close()


def get_peer(node, password, route):
    with peer_session(node, password) as (conn, headers):
        conn.request('GET', route, headers=headers)
        response = conn.getresponse()
        payload = response.read(MAX + 1)
        if response.status == 404:
            raise ValueError('Обновите лёгкую панель этого узла.')
        if response.status != 200 or len(payload) > MAX:
            raise ValueError('Узел не смог выполнить запрос.')
        return json.loads(payload)


def mcp_snapshot(node_id, task, token):
    if task not in {'host', 'links', 'observed', 'bluetooth'}:
        raise ValueError('Unsupported diagnostic task.')
    node = next((item for item in cluster5.get() if item['id'] == node_id), None)
    if not node:
        raise ValueError('Enrolled node unavailable.')
    conn = PinnedConnection(node, 16)
    try:
        path = '/api/mcp-diagnostics?' + urlencode({'task':task})
        conn.request('GET', path, headers={'Authorization':'Bearer '+token})
        reply = conn.getresponse()
        if reply.status != 200:
            raise ValueError('The remote node rejected its diagnostic credential.')
        data = reply.read(MAX + 1)
        if len(data) > MAX:
            raise ValueError('Diagnostic response is too large.')
        return {'node':node['name'], 'id':node_id, 'time':int(time.time()), 'result':json.loads(data)}
    finally:
        conn.close()


TRANSFERS = {}
TRANSFER_GUARD = threading.Lock()
TRANSFER_LIMIT = 128 * 1024**3


def transfer_action(data):
    operation = data['op']
    with TRANSFER_GUARD:
        if operation == 'transfer-status':
            item = TRANSFERS.get(data.get('job'))
            if not item:
                raise ValueError('Передача не найдена.')
            return dict(item)
        active = sum(item['phase'] == 'running' for item in TRANSFERS.values())
        if active >= 2:
            raise ValueError('Дождитесь завершения одной из текущих передач.')
        for key, item in list(TRANSFERS.items()):
            if item['phase'] != 'running' and time.time() - item['updated'] > 1800:
                del TRANSFERS[key]
        if len(TRANSFERS) >= 64:
            raise ValueError('Журнал передач заполнен. Повторите позже.')
        nodes = {node['id']: node for node in cluster5.get()}
        source = nodes.get(data.get('source'))
        destination = nodes.get(data.get('destination'))
        if not source or not destination or source['id'] == destination['id']:
            raise ValueError('Выберите два разных подключённых узла.')
        credentials = []
        for node, key in ((source, 'source_password'), (destination, 'destination_password')):
            password = secret(node, {'id':node['id'], 'password':data.get(key), 'master':data.get('master')})
            if not password:
                raise ValueError('Укажите пароль панели или настройте секреты обоих узлов.')
            credentials.append(password)
        source_path = str(data.get('source_path', ''))
        target_path = str(data.get('target_path', ''))
        if not source_path or not target_path or max(len(source_path),len(target_path)) > 2048:
            raise ValueError('Укажите пути в папках обмена обоих узлов.')
        job = uuid.uuid4().hex
        TRANSFERS[job] = {'phase':'running','bytes':0,'total':None,'updated':time.time()}
        threading.Thread(target=copy_file, args=(job, source, destination, credentials,
                                               source_path, target_path), daemon=True).start()
        return {'job':job}


def transfer_progress(job, **changes):
    with TRANSFER_GUARD:
        TRANSFERS[job].update(changes, updated=time.time())


def copy_file(job, source, destination, credentials, source_path, target_path):
    try:
        with peer_session(source, credentials[0], 45) as (reader, auth):
            reader.request('GET','/api/share-download?'+urlencode({'path':source_path}),headers=auth)
            response = reader.getresponse()
            if response.status != 200:
                raise ValueError('Исходный файл недоступен в папке обмена.')
            size = int(response.getheader('Content-Length', '-1'))
            if not 0 <= size <= TRANSFER_LIMIT:
                raise ValueError('Некорректный размер файла.')
            transfer_progress(job, total=size)
            with peer_session(destination, credentials[1], 45) as (writer, auth):
                writer.putrequest('POST','/api/share-upload?'+urlencode({'path':target_path}))
                writer.putheader('Content-Length',str(size))
                for key, value in auth.items():
                    if key != 'Content-Type':
                        writer.putheader(key,value)
                writer.endheaders()
                sent = 0
                while sent < size:
                    chunk = response.read(min(256*1024, size-sent))
                    if not chunk:
                        raise ValueError('Чтение исходного файла прервано.')
                    writer.send(chunk)
                    sent += len(chunk)
                    transfer_progress(job, bytes=sent)
                reply = writer.getresponse()
                if reply.status != 201:
                    raise ValueError('Целевой узел отклонил файл. Проверьте путь, свободное место и имя.')
                reply.read(8192)
        transfer_progress(job, phase='complete')
    except (OSError, ValueError, http.client.HTTPException) as error:
        message = str(error) if isinstance(error, ValueError) else 'Соединение прервалось во время передачи.'
        transfer_progress(job, phase='error', error=message)


def action(data):
    op = data.get('op')
    if op in ('transfer-start', 'transfer-status'):
        return transfer_action(data)
    if op == 'task':
        task = data.get('task')
        if not isinstance(task, str) or task not in TASKS:
            raise ValueError('Неизвестный шаблон.')
        route = '/api/diagnostics?' + urlencode({'task': task})
    elif op in ('usb-list', 'usb-download'):
        route = '/api/' + op + '?' + urlencode({'uuid': str(data.get('uuid', ''))[:80],
                                               'path': str(data.get('path', ''))[:2048]})
    else:
        raise ValueError('Неизвестное действие.')
    selected = data.get('id')
    nodes = cluster5.get()
    candidates = [node for node in nodes if node['id'] == selected]
    if op == 'task' and selected == 'auto':
        candidates = [node for node in nodes if node.get('entry')][:3]
        data = {'id': 'auto'}
    error = 'Добавьте узел и укажите пароль панели или привяжите секрет.'
    for node in candidates:
        try:
            password = secret(node, data)
            if password:
                result = get_peer(node, password, route)
                return {'node': node['name'], 'id': node['id'], 'time': int(time.time()), 'result': result}
        except (KeyError, TypeError, ValueError, OSError, http.client.HTTPException) as exc:
            error = str(exc) if isinstance(exc, ValueError) else 'Связь с узлом недоступна.'
    raise ValueError(error)
