"""Read-only task templates and USB browsing for enrolled Pi nodes."""
import hashlib
import hmac
import http.client
import json
import ssl
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


def get_peer(node, password, route):
    host, port = node['host'], node['port']
    if not cluster5.addr_ok(host):
        raise ValueError('Адрес узла недопустим.')
    conn = http.client.HTTPSConnection(host, port, context=ssl._create_unverified_context(), timeout=8)
    headers = {'Origin': f'https://{host}:{port}', 'Content-Type': 'application/json'}
    try:
        conn.connect()
        fingerprint = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest().upper()
        if not hmac.compare_digest(fingerprint, node['fingerprint'].upper()):
            raise ValueError('Отпечаток сертификата не совпадает.')
        conn.request('POST', '/api/login', body=json.dumps({'password': password}), headers=headers)
        login = conn.getresponse()
        cookie = login.getheader('Set-Cookie', '').split(';', 1)[0]
        login.read(8192)
        if login.status != 200 or not cookie.startswith('pi_session='):
            raise ValueError('Узел отклонил вход.')
        headers['Cookie'] = cookie
        conn.request('GET', route, headers=headers)
        response = conn.getresponse()
        payload = response.read(MAX + 1)
        if response.status == 404:
            raise ValueError('Обновите лёгкую панель этого узла.')
        if response.status != 200 or len(payload) > MAX:
            raise ValueError('Узел не смог выполнить запрос.')
        return json.loads(payload)
    finally:
        if headers.get('Cookie'):
            try:
                conn.request('POST', '/api/logout', body='{}', headers=headers)
                conn.getresponse().read(8192)
            except (OSError, http.client.HTTPException):
                pass
        conn.close()


def action(data):
    op = data.get('op')
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
