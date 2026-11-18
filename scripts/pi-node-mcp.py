#!/usr/bin/env python3
"""Scoped MCP gateway for enrolled Pi Control nodes."""
import argparse
import hmac
import http.client
import ipaddress
import json
import os
import re
import ssl
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore

VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05', '2026-07-28')
TASKS = {'node_health': 'host', 'node_links': 'links', 'node_inventory': 'observed',
         'node_bluetooth': 'bluetooth'}
TOOLS = {
    'node_list': 'List nodes granted to this MCP instance.',
    'node_health': 'Read system health and container status of an enrolled Pi.',
    'node_links': 'Read network interfaces, routes and cached Wi-Fi of an enrolled Pi.',
    'node_inventory': 'Read known neighbors and TCP/UDP listeners of the Pi itself.',
    'node_bluetooth': 'Read existing Bluetooth device records from an enrolled Pi.',
}
MAX = 65536
ARGS = {'type':'object', 'properties':{'node_id':{'type':'string'}}, 'required':['node_id'],
        'additionalProperties':False}


def error(request_id, code, message):
    return {'jsonrpc':'2.0','id':request_id,'error':{'code':code,'message':message}}


class Gateway:
    def __init__(self, backend, allowed, peer_credentials=None):
        self.backend = backend
        self.allowed = frozenset(allowed)
        self.peer_credentials = peer_credentials or {}

    def describe(self):
        return {'serverInfo':{'name':'pi-control-nodes','version':'1.0.0'},
                'capabilities':{'tools':{}}}

    def call(self, name, arguments):
        if name not in TOOLS or not isinstance(arguments, dict):
            raise ValueError('Unknown tool or invalid arguments.')
        if name == 'node_list':
            if arguments:
                raise ValueError('node_list takes no arguments.')
            return {'nodes': [node for node in self.backend.status()['nodes']
                              if node['id'] in self.allowed]}
        node_id = arguments.get('node_id')
        if set(arguments) != {'node_id'} or not isinstance(node_id, str) or node_id not in self.allowed:
            raise ValueError('This node is outside the MCP allowlist.')
        if node_id in self.peer_credentials:
            return self.backend.mcp_snapshot(node_id, TASKS[name], self.peer_credentials[node_id])
        return self.backend.action({'op':'task','task':TASKS[name],'id':node_id})

    def handle(self, request):
        if not isinstance(request, dict) or request.get('jsonrpc') != '2.0':
            return error(None,-32600,'Invalid JSON-RPC request.')
        request_id = request.get('id')
        if 'id' not in request:
            return None
        method = request.get('method')
        params = request.get('params', {})
        if not isinstance(params, dict):
            return error(request_id,-32602,'Invalid parameters.')
        if method == 'initialize':
            requested = params.get('protocolVersion')
            result = {'protocolVersion':requested if requested in VERSIONS else VERSIONS[0], **self.describe()}
        elif method == 'server/discover':
            result = self.describe()
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            result = {'tools': [{'name':name,'description':description,
                                 'inputSchema':ARGS if name != 'node_list' else
                                 {'type':'object','properties':{},'additionalProperties':False},
                                 'annotations':{'readOnlyHint':True,'destructiveHint':False}}
                                for name,description in TOOLS.items()]}
        elif method == 'tools/call':
            try:
                name = params.get('name')
                if not isinstance(name, str):
                    raise ValueError('Tool name is required.')
                data = self.call(name, params.get('arguments', {}))
                result = {'structuredContent':data,
                          'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}]}
            except (ValueError, OSError, http.client.HTTPException) as exc:
                result = {'isError':True, 'content':[{'type':'text','text':str(exc)}]}
        else:
            return error(request_id,-32601,'Method not found.')
        return {'jsonrpc':'2.0','id':request_id,'result':result}


def response(gateway, raw):
    try:
        return gateway.handle(json.loads(raw))
    except (json.JSONDecodeError, UnicodeError):
        return error(None,-32700,'Invalid JSON.')


def stdio(gateway):
    for line in sys.stdin.buffer:
        reply = response(gateway,line) if len(line) <= MAX else error(None,-32600,'Message too large.')
        if reply is not None:
            print(json.dumps(reply,ensure_ascii=False),flush=True)


def serve(gateway, address, port, certificate, key, token):
    slots = BoundedSemaphore(6)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, payload=None):
            body = b'' if payload is None else json.dumps(payload,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            if body:
                self.send_header('Content-Type','application/json')
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            self.send(405)

        def do_POST(self):
            self.connection.settimeout(15)
            if self.path != '/mcp':
                return self.send(404)
            if self.headers.get('Origin'):
                return self.send(403)
            if not hmac.compare_digest(self.headers.get('Authorization','').encode(),b'Bearer '+token):
                return self.send(401)
            if not slots.acquire(blocking=False):
                return self.send(503)
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= MAX or self.headers.get('Transfer-Encoding'):
                    return self.send(400)
                reply = response(gateway,self.rfile.read(length))
                return self.send(202) if reply is None else self.send(200,reply)
            except (ValueError,OSError):
                return
            finally:
                slots.release()

    with ThreadingHTTPServer((address,port),Handler,bind_and_activate=False) as server:
        server.socket.settimeout(15)
        server.server_bind()
        server.server_activate()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certificate,key)
        server.socket = context.wrap_socket(server.socket,server_side=True)
        server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node',action='append',required=True,help='Allow this enrolled node ID (repeatable).')
    parser.add_argument('--dashboard-root',type=Path,default=Path('/opt/pi-dashboard'))
    parser.add_argument('--listen',action='store_true',help='Serve HTTPS on the local Tailscale IPv4.')
    parser.add_argument('--port',type=int,default=8794)
    parser.add_argument('--certificate',type=Path)
    parser.add_argument('--private-key',type=Path)
    parser.add_argument('--token-file',type=Path)
    parser.add_argument('--peer-credentials',type=Path,help='Private JSON object of node IDs and diagnostic tokens.')
    args = parser.parse_args()
    sys.path.insert(0,str(args.dashboard_root))
    import node_features
    credentials = {}
    if args.peer_credentials:
        info = args.peer_credentials.stat()
        if info.st_mode & 0o077 or info.st_uid != os.geteuid():
            parser.error('Use a credential file owned by this process user with mode 0600.')
        credentials = json.loads(args.peer_credentials.read_text())
        if not isinstance(credentials, dict) or not all(
            key in args.node and isinstance(value, str) and re.fullmatch('[0-9a-f]{64}',value)
            for key,value in credentials.items()
        ):
            parser.error('Provide diagnostic credentials only for individually allowed nodes.')
    gateway = Gateway(node_features,args.node,credentials)
    if not args.listen:
        return stdio(gateway)
    if not all((args.certificate,args.private_key,args.token_file)):
        parser.error('--listen requires --token-file, --certificate and --private-key.')
    address = subprocess.check_output(['tailscale','ip','-4'],text=True,timeout=5).strip()
    if ipaddress.ip_address(address) not in ipaddress.ip_network('100.64.0.0/10'):
        parser.error('A local Tailscale IPv4 address is required.')
    info = args.token_file.stat()
    if info.st_mode & 0o077 or info.st_uid != os.geteuid():
        parser.error('Use a token file owned by this process user with mode 0600.')
    token = args.token_file.read_bytes().strip()
    if len(token) < 32 or any(c <= 32 or c >= 127 for c in token):
        parser.error('Use a random token at least 32 ASCII characters long.')
    serve(gateway,address,args.port,args.certificate,args.private_key,token)


if __name__ == '__main__':
    main()
