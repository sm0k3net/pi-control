"""A static same-site handoff for DSH's Strict cookie, bound only to Tailscale IPv4.

The token stays in the URL fragment: browsers do not send fragments to this server.
The server has no state, authentication endpoints, filesystem access or proxying.
"""
import ipaddress
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTML='''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="referrer" content="no-referrer"><title>DeepSeek Harness</title><script defer src="/bridge.js"></script></head><body><p id="status">Открываем DeepSeek Harness...</p></body></html>'''.encode('utf-8')
JS='''"use strict";const token=location.hash.slice(1);if(!/^[A-Za-z0-9_-]{20,256}$/.test(token)){document.getElementById("status").textContent="Ссылка недействительна. Вернитесь в панель и откройте Harness снова."}else{location.replace("http://"+location.hostname+":3080/?token="+encodeURIComponent(token))}'''.encode('utf-8')

class Bridge(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  if self.path not in ('/','/bridge.js'):
   self.send_error(404);return
  body=HTML if self.path=='/' else JS
  self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8' if self.path=='/' else 'application/javascript; charset=utf-8')
  self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer')
  self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'")
  self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)

def tailscale_ipv4():
 try:
  p=subprocess.run(['tailscale','ip','-4'],capture_output=True,text=True,timeout=5,check=True)
  addr=ipaddress.ip_address(p.stdout.splitlines()[0].strip())
  if addr.version==4 and addr in ipaddress.ip_network('100.64.0.0/10'):return str(addr)
 except (OSError,ValueError,IndexError,subprocess.SubprocessError):pass
 return ''

def serve():
 server=None;current=''
 while True:
  addr=tailscale_ipv4()
  if addr!=current:
   if server:server.shutdown();server.server_close();server=None
   current=''
   if addr:
    try:
     server=ThreadingHTTPServer((addr,3081),Bridge);threading.Thread(target=server.serve_forever,daemon=True).start();current=addr
    except OSError:pass
  time.sleep(10)

def start():
 threading.Thread(target=serve,daemon=True,name='harness-bridge').start()
