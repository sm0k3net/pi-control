"""Dashboard adapter: authentication stays in the dashboard, execution in the worker."""
from pathlib import Path

import maintenance_rpc as rpc

ROOT = Path(__file__).resolve().parent
ASSETS = {'/maintenance-ui.js':'application/javascript', '/maintenance-ui.css':'text/css'}
API = '/api/maintenance'
GET = {API, *ASSETS}
POST = {API}


def get(handler):
    if handler.path in ASSETS:
        path = handler.path
        return handler.respond(200, (ROOT / path[1:]).read_bytes(), ASSETS[path] + '; charset=utf-8')
    if not handler.authenticated():
        return handler.respond(401, {'error':'Требуется вход'})
    try:
        return handler.respond(200, rpc.exchange('status'))
    except ValueError as error:
        return handler.respond(503, {'error':str(error)})


def post(handler, data):
    if not handler.authenticated():
        return handler.respond(401, {'error':'Требуется вход'})
    operation = data.get('op')
    fields = {'check':(), 'scan':('uuid',), 'update':('tag',), 'rollback':(),
              'restore':('uuid','backup','key','parts')}
    if not isinstance(operation, str) or operation not in fields:
        return handler.respond(400, {'error':'Неизвестная операция обслуживания.'})
    try:
        return handler.respond(200, rpc.exchange(operation, {key:data[key] for key in fields[operation] if key in data}))
    except ValueError as error:
        return handler.respond(400, {'error':str(error)})
