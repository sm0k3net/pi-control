"""Small bounded JSON transport for maintenance operations."""
import json
import socket

PATH = '/run/pi-control-maintenance/control.sock'
LIMIT = 65536


def exchange(operation, data=None):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as session:
            session.settimeout(17)
            session.connect(PATH)
            session.sendall(json.dumps({'operation': operation, 'data': data or {}}).encode() + b'\n')
            with session.makefile('rb') as stream:
                value = json.loads(stream.readline(LIMIT))
    except (OSError, ValueError):
        raise ValueError('Служба обслуживания временно недоступна.') from None
    if value.get('error'):
        raise ValueError(value['error'])
    return value['result']
