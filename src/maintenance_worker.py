"""Local service for a fixed set of privileged maintenance operations."""
import json
import os
import socket
import socketserver
import struct
import threading
import time
from pathlib import Path

import maintenance_restore as restore
import maintenance_rpc as rpc
import maintenance_updates as updates
import platform_events
import usb_backup as usb

LOCK = threading.Lock()
RESULT = updates.DATA / 'job.json'
BUSY = False


def job(operation, data):
    global BUSY
    try:
        if operation in ('update', 'rollback'):
            usb.RUNTIME.mkdir(mode=0o700, exist_ok=True)
            with usb.lock(usb.RUNTIME / 'worker.lock'):
                value = updates.apply(data.get('tag')) if operation == 'update' else updates.rollback()
            result = {'message': value}
        else:
            usb.RUNTIME.mkdir(mode=0o700, exist_ok=True)
            with usb.lock(usb.RUNTIME / 'worker.lock'):
                result = restore.restore(data)
        phase = 'complete'
    except Exception as error:
        phase = 'error'
        result = {'message': str(error) if isinstance(error, ValueError) else 'Операция прервана. Проверьте журнал службы обслуживания.'}
    try:
        updates.write_json(RESULT, {'phase': phase, **result, 'time': time.time()})
        platform_events.push('maintenance', 'info' if phase == 'complete' else 'error', result['message'])
    finally:
        with LOCK:
            BUSY = False


def dispatch(operation, data):
    global BUSY
    if operation == 'status':
        return {'job': updates.read_json(RESULT, {}) or {},
                'server_version': (updates.read_json(updates.STATE, {}) or {}).get('version'),
                'rollback': updates.UNDO.exists(), 'ui_version': updates.ui_version() or '1.0.0'}
    if operation == 'check':
        return updates.check_release()
    if operation == 'scan':
        with LOCK:
            if BUSY:
                raise ValueError('Дождитесь окончания текущего обслуживания.')
            return restore.scan(data.get('uuid'))
    if operation not in ('update', 'rollback', 'restore'):
        raise ValueError('Неизвестная операция обслуживания.')
    with LOCK:
        if BUSY:
            raise ValueError('Другая операция обслуживания уже выполняется.')
        updates.write_json(RESULT, {'phase': 'running', 'message': 'Операция выполняется…', 'time': time.time()})
        BUSY = True
        threading.Thread(target=job, args=(operation, data), daemon=True).start()
    return {'accepted': True}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(18)
        identity = self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        _, uid, _ = struct.unpack('3i', identity)
        if uid != 0 and uid != int(os.environ.get('PI_MAINTENANCE_WEB_UID', '-1')):
            return
        try:
            raw = self.rfile.readline(rpc.LIMIT)
            if not raw or len(raw) >= rpc.LIMIT:
                return
            request = json.loads(raw)
            if not isinstance(request.get('data'), dict):
                raise ValueError('Неверные параметры.')
            reply = {'result': dispatch(request['operation'], request['data'])}
        except Exception as error:
            reply = {'error': str(error) if isinstance(error, ValueError) else 'Обслуживание недоступно.'}
        self.wfile.write(json.dumps(reply, ensure_ascii=False).encode() + b'\n')


def main():
    os.umask(0o077)
    updates.DATA.mkdir(mode=0o700, exist_ok=True)
    updates.recover_interrupted()
    current = updates.read_json(RESULT, {}) or {}
    if current.get('phase') == 'running':
        updates.write_json(RESULT, {'phase':'error', 'message':'Операция прервана перезапуском. Проверьте состояние перед повтором.'})
    Path(rpc.PATH).unlink(missing_ok=True)
    with socketserver.ThreadingUnixStreamServer(rpc.PATH, Handler) as server:
        os.chmod(rpc.PATH, 0o660)
        server.serve_forever()


if __name__ == '__main__':
    main()
