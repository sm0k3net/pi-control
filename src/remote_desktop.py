"""Saved Tailscale desktop targets, launched in the Pi's noVNC session."""
import base64
import hashlib
import http.client
import ipaddress
import json
import os
import re
import secrets
import select
import socket
import struct
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

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
    return {'targets': targets, 'viewers': {'vnc': bool(viewer('vnc')),
                                            'mac': '/pc-novnc/core/rfb.js' in NOVNC_ASSETS,
                                            'rdp': bool(viewer('rdp') and terminal())}}


def viewer(protocol):
    names = {'mac': ('remmina',), 'vnc': ('xtigervncviewer', 'tigervncviewer'), 'rdp': ('xfreerdp3', 'xfreerdp')}
    return next((path for name in names[protocol] if (path := shutil.which(name))), None)


def terminal():
    if program := shutil.which('xfce4-terminal'):
        return [program, '--disable-server', '--hold', '--execute']
    if program := shutil.which('xterm'):
        return [program, '-hold', '-e']
    return []


def wait_no_vnc(address):
    for _ in range(16):
        conn = http.client.HTTPConnection(address, 9595, timeout=1)
        try:
            conn.request('GET', '/vnc.html')
            if conn.getresponse().status == 200:
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            conn.close()
        time.sleep(0.4)
    raise ValueError('Служба noVNC не открыла порт 9595 через Tailscale.')


def execute(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True).stdout


def launch(target):
    if target['protocol'] == 'mac':
        return mac_session(target)
    binary = viewer(target['protocol'])
    if not binary:
        raise ValueError('Клиент удалённого рабочего стола ещё не установлен.')
    rdp_terminal = terminal() if target['protocol'] == 'rdp' else []
    if target['protocol'] == 'rdp' and not rdp_terminal:
        raise ValueError('Для RDP нужен xfce4-terminal или xterm в сеансе noVNC.')
    ip = target['address']
    routes = json.loads(execute(['ip', '-j', 'route', 'get', ip]))
    if not routes or routes[0].get('dev') != 'tailscale0':
        raise ValueError('К устройству нет маршрута через tailscale0.')
    address = ipaddress.ip_address(execute(['tailscale', 'ip', '-4']).strip())
    if address not in TAILNET:
        raise ValueError('У Raspberry Pi нет IPv4 Tailscale.')
    execute(['systemctl', *MACHINE, 'start', *STACK], timeout=35)
    wait_no_vnc(str(address))
    name = 'pi-remote-desktop-' + target['id']
    # Clearing a stale service also makes reconnect deterministic.
    subprocess.run(['systemctl', *MACHINE, 'stop', name + '.service'],
                   capture_output=True, timeout=10)
    username = target.get('username') or ''
    if target['protocol'] == 'vnc':
        args = [binary, ip + '::5900']
    elif username:
        args = rdp_terminal + [binary, '/v:' + ip, '/u:' + username, '/dynamic-resolution']
    else:
        script = ("""printf '%s' 'Windows username (DOMAIN\\user or user@example.com): '; """
                  'IFS= read -r login && [ -n "$login" ] && '
                  'exec "$1" "/v:$2" "/u:$login" /dynamic-resolution')
        args = rdp_terminal + ['/bin/sh', '-c', script, 'rdp-login', binary, ip]
    execute(['systemd-run', *MACHINE, '--collect', '--unit=' + name,
             '--setenv=DISPLAY=:5', *args])
    return {'address': str(address), 'port': 9595}


def action(data):
    op = data.get('op')
    with LOCK:
        targets = status()['targets']
        if op in ('add', 'edit'):
            label = str(data.get('name', '')).strip()
            ip = ipaddress.ip_address(str(data.get('address', '')).strip())
            protocol = data.get('protocol')
            username = str(data.get('username', '')).strip()
            if not label or len(label) > 80 or ip not in TAILNET or protocol not in ('mac', 'vnc', 'rdp'):
                raise ValueError('Укажите название, протокол и IPv4 Tailscale.')
            if len(username) > 128 or re.search(r'[\x00-\x1f]', username):
                raise ValueError('Некорректное имя пользователя.')
            target_id = uuid.uuid4().hex
            if op == 'edit':
                target_id = data.get('id')
                if not any(item['id'] == target_id for item in targets):
                    raise ValueError('Рабочий стол не найден.')
            elif len(targets) >= 30:
                raise ValueError('Можно сохранить до 30 рабочих столов.')
            target = {'id': target_id, 'name': label, 'address': str(ip),
                      'protocol': protocol, 'username': username if protocol != 'vnc' else ''}
            if op == 'edit':
                targets = [target if item['id'] == target_id else item for item in targets]
            else:
                targets.append(target)
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


NOVNC = Path(__file__).resolve().parent / 'novnc-client'
NOVNC_ASSETS = {
    '/pc-novnc/' + path.relative_to(NOVNC).as_posix(): path
    for group in ('core', 'vendor')
    for path in (NOVNC / group).rglob('*.js')
    if path.is_file() and not path.is_symlink()
}
MAC_PAGE = '/mac-desktop.html'
MAC_CODE = '/mac-desktop.js'
MAC_SOCKET = '/api/platform/mac-rfb'
MAC_GET = {MAC_PAGE, MAC_CODE, MAC_SOCKET, *NOVNC_ASSETS}
MAC_TICKETS = {}
MAC_LOCK = threading.Lock()
MAC_SLOTS = threading.BoundedSemaphore(3)
FRAME_LIMIT = 4 * 1024 * 1024


def mac_connection(address):
    if ipaddress.IPv4Address(address) not in TAILNET:
        raise ValueError('Для Mac нужен адрес Tailscale.')
    routes = json.loads(execute(['ip', '-j', 'route', 'get', address]))
    if not routes or routes[0].get('dev') != 'tailscale0':
        raise ValueError('К Mac нет маршрута через Tailscale.')
    return socket.create_connection((address, 5900), timeout=6)


def mac_session(target):
    if '/pc-novnc/core/rfb.js' not in NOVNC_ASSETS:
        raise ValueError('Модуль noVNC ещё не установлен.')
    address = target['address']
    try:
        with mac_connection(address) as remote:
            with remote.makefile('rb') as stream:
                banner = stream.read(12)
    except OSError:
        raise ValueError('Mac не отвечает на TCP 5900. Проверьте «Общий экран» и доступ по Tailscale.') from None
    if not banner.startswith(b'RFB '):
        raise ValueError('Mac не вернул приветствие VNC на порту 5900.')
    now = time.monotonic()
    ticket = secrets.token_urlsafe(32)
    with MAC_LOCK:
        for key, (expiry, _) in list(MAC_TICKETS.items()):
            if expiry <= now:
                del MAC_TICKETS[key]
        if len(MAC_TICKETS) >= 20:
            raise ValueError('Слишком много ожидающих сеансов. Повторите через минуту.')
        MAC_TICKETS[ticket] = (now + 60, address)
    return {'url': MAC_PAGE + '#' + urlencode({'ticket':ticket, 'username':target.get('username','')})}


def mac_get(handler):
    path = handler.path
    if not handler.authenticated():
        return handler.respond(401, {'error':'Войдите в Pi Control перед подключением к Mac.'})
    if path == MAC_PAGE:
        host = handler.headers.get('Host','')
        if not re.fullmatch(r'[A-Za-z0-9.\-\[\]:]{1,255}', host):
            return handler.respond(400, {'error':'Некорректный адрес панели.'})
        body = MAC_HTML.encode()
        handler.send_response(200)
        handler.send_header('Content-Type','text/html; charset=utf-8')
        handler.send_header('Content-Length',str(len(body)))
        handler.send_header('Cache-Control','no-store')
        handler.send_header('X-Content-Type-Options','nosniff')
        handler.send_header('Referrer-Policy','no-referrer')
        handler.send_header('X-Frame-Options','DENY')
        handler.send_header('Content-Security-Policy',
                            "default-src 'self'; script-src 'self'; style-src 'self'; "
                            f"connect-src 'self' wss://{host}; img-src 'self' data:; "
                            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        handler.end_headers()
        handler.wfile.write(body)
        return
    if path == MAC_CODE:
        return handler.respond(200, MAC_SCRIPT.encode(), 'application/javascript; charset=utf-8')
    if path in NOVNC_ASSETS:
        return handler.respond(200, NOVNC_ASSETS[path].read_bytes(), 'application/javascript; charset=utf-8')
    return mac_socket(handler)


def mac_socket(handler):
    headers = handler.headers
    protocols = [item.strip() for item in headers.get('Sec-WebSocket-Protocol','').split(',')]
    tickets = [item[3:] for item in protocols if re.fullmatch(r'pc-[A-Za-z0-9_-]{43}',item)]
    key = headers.get('Sec-WebSocket-Key','')
    if (headers.get('Origin') != 'https://' + headers.get('Host','')
            or headers.get('Upgrade','').lower() != 'websocket'
            or 'upgrade' not in headers.get('Connection','').lower()
            or headers.get('Sec-WebSocket-Version') != '13'
            or not re.fullmatch(r'[0-9A-Za-z+/]{22}==',key)
            or 'binary' not in protocols or len(tickets) != 1):
        return handler.respond(403, {'error':'Некорректный запрос WebSocket.'})
    with MAC_LOCK:
        entry = MAC_TICKETS.pop(tickets[0],None)
    if not entry or entry[0] <= time.monotonic():
        return handler.respond(403, {'error':'Приглашение истекло. Откройте Mac из карточки снова.'})
    if not MAC_SLOTS.acquire(blocking=False):
        return handler.respond(503, {'error':'Открыто слишком много сеансов Mac.'})
    try:
        try:
            remote = mac_connection(entry[1])
        except (OSError, ValueError, subprocess.SubprocessError):
            return handler.respond(502, {'error':'Mac не отвечает на подключение.'})
        accept = base64.b64encode(hashlib.sha1(
            (key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode(),
            usedforsecurity=False).digest()).decode()
        try:
            handler.wfile.write(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n'
                                 'Connection: Upgrade\r\nSec-WebSocket-Protocol: binary\r\n'
                                 f'Sec-WebSocket-Accept: {accept}\r\n\r\n').encode())
            handler.wfile.flush()
            handler.close_connection = True
            handler.connection.settimeout(10)
            pass_rfb(handler.connection,remote)
        except (OSError, ValueError):
            pass
        finally:
            remote.close()
    finally:
        MAC_SLOTS.release()


def websocket_frame(payload, opcode=2):
    size = len(payload)
    header = bytes((0x80 | opcode, size)) if size < 126 else (
        bytes((0x80 | opcode, 126)) + struct.pack('!H',size) if size <= 65535 else
        bytes((0x80 | opcode, 127)) + struct.pack('!Q',size))
    return header + payload


def browser_frames(buffer):
    while len(buffer) >= 2:
        header, length = buffer[:2]
        opcode = header & 15
        if not length & 128 or header & 112 or opcode not in (0,2,8,9,10):
            raise ValueError('Invalid WebSocket frame.')
        length &= 127
        extended = 2 if length == 126 else 8 if length == 127 else 0
        offset = 2 + extended
        if len(buffer) < offset + 4:
            break
        if extended:
            length = int.from_bytes(buffer[2:offset],'big')
        if length > FRAME_LIMIT or (opcode >= 8 and (length > 125 or not header & 128)):
            raise ValueError('Invalid WebSocket payload size.')
        end = offset + 4 + length
        if len(buffer) < end:
            break
        mask = buffer[offset:offset+4]
        payload = bytes(value ^ mask[index % 4] for index,value in enumerate(buffer[offset+4:end]))
        del buffer[:end]
        yield opcode,payload


def pass_rfb(client, remote):
    buffer = bytearray()
    while True:
        if client.pending():
            readable = [client]
        else:
            readable,_,_ = select.select([client,remote],[],[],1800)
        if not readable:
            return
        if remote in readable:
            data = remote.recv(65536)
            if not data:
                client.sendall(websocket_frame(b'\x03\xe8',8))
                return
            client.sendall(websocket_frame(data))
        if client in readable:
            data = client.recv(65536)
            if not data:
                return
            buffer.extend(data)
            for opcode,payload in browser_frames(buffer):
                if opcode == 8:
                    client.sendall(websocket_frame(payload,8))
                    return
                if opcode == 9:
                    client.sendall(websocket_frame(payload,10))
                if opcode in (0,2) and payload:
                    remote.sendall(payload)


MAC_HTML = '''<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mac · Pi Control</title><link rel="stylesheet" href="/maintenance-ui.css"></head>
<body class="mac-desktop"><header><div><a href="/">Pi Control</a><h1>Общий экран Mac</h1></div>
<div class="mac-controls"><label>Режим изображения <select id="mac-mode">
<option value="compat">Совместимость Mac</option><option value="auto">Автоматический</option></select></label>
<button id="mac-refresh" disabled>Обновить экран</button><p id="mac-status" role="status">Подключаемся по Tailscale…</p></div></header>
<main id="mac-screen"></main>
<dialog id="mac-login"><form><h2>Вход на Mac</h2><label id="mac-user-row">Имя пользователя macOS
<input name="username" autocomplete="username"></label><label>Пароль Mac<input name="password" type="password" autocomplete="current-password"></label>
<button type="submit">Подключиться</button><button type="button" id="mac-cancel">Отмена</button></form></dialog>
<script type="module" src="/mac-desktop.js"></script></body></html>'''

MAC_SCRIPT = '''import RFB from '/pc-novnc/core/rfb.js';

// These protocol hooks match the pinned noVNC 1.6.0 browser client.
class MacRFB extends RFB {
  _sendEncodings() {
    if(document.getElementById('mac-mode').value==='auto')return super._sendEncodings();
    // Raw pixels and DesktopSize only. Avoid optional codecs and cursor/resize
    // extensions until the Mac has sent an image that this client can render.
    RFB.messages.clientEncodings(this._sock,[0,-223]);
  }
  refreshFrame() {
    if(this._rfbConnectionState!=='connected'||!this._fbWidth||!this._fbHeight)return;
    this._sendEncodings();
    RFB.messages.fbUpdateRequest(this._sock,false,0,0,this._fbWidth,this._fbHeight);
  }
  _framebufferUpdate() {
    const complete=super._framebufferUpdate();
    if(complete) this.dispatchEvent(new CustomEvent('mac-frame',{
      detail:{width:this._fbWidth,height:this._fbHeight}
    }));
    return complete;
  }
}
const query = new URLSearchParams(location.hash.slice(1));
const ticket = query.get('ticket');
history.replaceState(null,'',location.pathname);
const status=document.getElementById('mac-status');
const dialog=document.getElementById('mac-login');
const form=dialog.querySelector('form');
let rfb, reason='', waitFrame;
const refresh=document.getElementById('mac-refresh');
const mode=document.getElementById('mac-mode');
function requestFrame(){
  if(!rfb)return;
  clearTimeout(waitFrame);
  status.textContent='Запрашиваем полный кадр…';
  rfb.refreshFrame();
  waitFrame=setTimeout(()=>{
    status.textContent='Кадр пока не получен. Проверьте, что Mac не спит, или смените режим изображения.';
  },6000);
}
if (!ticket || !window.isSecureContext || !crypto.subtle) {
  status.textContent=ticket?'Браузеру нужен доверенный HTTPS-сертификат панели.':'Откройте сеанс заново из Pi Control.';
} else {
  const socket=new URL('/api/platform/mac-rfb',location.href);
  socket.protocol='wss:';
  rfb=new MacRFB(document.getElementById('mac-screen'),socket.href,{shared:true,wsProtocols:['binary','pc-'+ticket]});
  rfb.scaleViewport=true;
  form.elements.username.value=query.get('username')||'';
  rfb.addEventListener('credentialsrequired',event=>{
    const needsUser=event.detail.types.includes('username');
    document.getElementById('mac-user-row').hidden=!needsUser;
    form.elements.username.required=needsUser;
    form.elements.password.value='';
    form.elements.password.required=true;
    if(!dialog.open)dialog.showModal();
    (needsUser&&!form.elements.username.value?form.elements.username:form.elements.password).focus();
    status.textContent='Mac запрашивает учётные данные.';
  });
  rfb.addEventListener('connect',()=>{
    refresh.disabled=false;
    status.textContent='Вход принят · ждём изображение';
    waitFrame=setTimeout(requestFrame,2400);
  });
  rfb.addEventListener('mac-frame',event=>{
    clearTimeout(waitFrame);
    status.textContent='Экран Mac · '+event.detail.width+' × '+event.detail.height;
  });
  rfb.addEventListener('securityfailure',event=>{
    reason=event.detail.reason||'Mac отклонил вход.';
    status.textContent=reason;
  });
  rfb.addEventListener('disconnect',()=>{
    clearTimeout(waitFrame);refresh.disabled=true;
    if(dialog.open)dialog.close();
    form.elements.password.value='';
    status.textContent=reason||'Соединение закрыто. Повторите вход из карточки Mac.';
  });
}
refresh.onclick=requestFrame;
mode.onchange=()=>{if(!refresh.disabled)requestFrame();};
form.onsubmit=event=>{
  event.preventDefault();
  rfb.sendCredentials({username:form.elements.username.value,password:form.elements.password.value});
  form.elements.password.value='';
  dialog.close();
  status.textContent='Проверяем учётные данные на Mac…';
};
document.getElementById('mac-cancel').onclick=()=>{dialog.close();rfb?.disconnect();};
dialog.addEventListener('cancel',()=>rfb?.disconnect());'''
