"""Saved Tailscale desktop targets, launched in the Pi's noVNC session."""
import ipaddress
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from maintenance_updates import atomic

STORE = Path(__file__).resolve().parent / 'remote-desktops.json'
TAILNET = ipaddress.ip_network('100.64.0.0/10')
LOCK = threading.Lock()
USER = os.environ.get('PI_NOVNC_USER', 'sm0k3')
MACHINE = ['--user', '--machine=' + USER + '@.host']
STACK = ['novnc-xvnc.service', 'novnc-desktop.service', 'novnc-websockify.service']


def status():
    try:
        targets = json.loads(STORE.read_text())
    except FileNotFoundError:
        targets = []
    return {'targets': targets, 'viewers': {key: bool(viewer(key)) for key in ('vnc', 'rdp')}}


def viewer(protocol):
    names = {'vnc': ('xtigervncviewer', 'tigervncviewer'), 'rdp': ('xfreerdp3', 'xfreerdp')}
    return next((path for name in names[protocol] if (path := shutil.which(name))), None)


def execute(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True).stdout


def launch(target):
    binary = viewer(target['protocol'])
    if not binary:
        raise ValueError('Клиент удалённого рабочего стола ещё не установлен.')
    ip = target['address']
    routes = json.loads(execute(['ip', '-j', 'route', 'get', ip]))
    if not routes or routes[0].get('dev') != 'tailscale0':
        raise ValueError('К устройству нет маршрута через tailscale0.')
    address = ipaddress.ip_address(execute(['tailscale', 'ip', '-4']).strip())
    if address not in TAILNET:
        raise ValueError('У Raspberry Pi нет IPv4 Tailscale.')
    execute(['systemctl', *MACHINE, 'start', *STACK], timeout=35)
    name = 'pi-remote-desktop-' + target['id']
    # Clearing a stale service also makes reconnect deterministic.
    subprocess.run(['systemctl', *MACHINE, 'stop', name + '.service'],
                   capture_output=True, timeout=10)
    if target['protocol'] == 'vnc':
        args = [binary, ip + '::5900']
    else:
        args = [binary, '/v:' + ip, '/dynamic-resolution']
        if target['username']:
            args.append('/u:' + target['username'])
    execute(['systemd-run', *MACHINE, '--collect', '--unit=' + name,
             '--setenv=DISPLAY=:5', *args])
    return {'address': str(address), 'port': 9595}


def action(data):
    op = data.get('op')
    with LOCK:
        targets = status()['targets']
        if op == 'add':
            label = str(data.get('name', '')).strip()
            ip = ipaddress.ip_address(str(data.get('address', '')).strip())
            protocol = data.get('protocol')
            username = str(data.get('username', '')).strip()
            if not label or len(label) > 80 or ip not in TAILNET or protocol not in ('vnc', 'rdp'):
                raise ValueError('Укажите название, протокол и IPv4 Tailscale.')
            if len(username) > 128 or re.search(r'[\x00-\x1f]', username):
                raise ValueError('Некорректное имя пользователя.')
            if len(targets) >= 30:
                raise ValueError('Можно сохранить до 30 рабочих столов.')
            targets.append({'id': uuid.uuid4().hex, 'name': label, 'address': str(ip),
                            'protocol': protocol, 'username': username if protocol == 'rdp' else ''})
        else:
            target = next((item for item in targets if item['id'] == data.get('id')), None)
            if not target:
                raise ValueError('Рабочий стол не найден.')
            if op == 'open':
                return launch(target)
            if op != 'remove':
                raise ValueError('Неизвестное действие.')
            targets.remove(target)
        atomic(STORE, json.dumps(targets, ensure_ascii=False).encode())
        return status()
