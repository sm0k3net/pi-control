#!/usr/bin/env python3
"""Small, independently authenticated Pi node panel; no external Python packages."""
import os,sys,time,json,ssl,hmac,hashlib,secrets,re,subprocess,shutil,threading,ipaddress,socket
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
ROOT=Path(os.environ.get('PI_NODE_ROOT','/opt/pi-node'));CFG=json.loads((ROOT/'config.json').read_text());sessions={};attempts=[];guard=threading.Lock()
def command(args,timeout=6):
 try:
  r=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
  return r.stdout.strip() if r.returncode==0 else ''
 except (OSError,subprocess.TimeoutExpired):return ''
def status():
 mem={}
 for line in Path('/proc/meminfo').read_text().splitlines():
  p=line.split()
  if p[0].rstrip(':') in ('MemTotal','MemAvailable'):mem[p[0].rstrip(':')]=int(p[1])*1024
 disk=shutil.disk_usage('/');rows=[]
 try:temperature=round(int(Path('/sys/class/thermal/thermal_zone0/temp').read_text().strip())/1000,1)
 except (OSError,ValueError):temperature=None
 for line in command(['docker','ps','-a','--format','{{json .}}'],12).splitlines():
  try:
   x=json.loads(line);name=x['Names']
   if re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',name):rows.append({'id':name,'name':name,'state':x.get('State','unknown'),'status':x.get('Status','')})
  except (ValueError,KeyError):pass
 return {'system':{'memory_total':mem.get('MemTotal',0),'memory_available':mem.get('MemAvailable',0),'memory_used':mem.get('MemTotal',0)-mem.get('MemAvailable',0),'disk_total':disk.total,'disk_free':disk.free,'disk_used':disk.used,'temperature':temperature,'load':os.getloadavg()[0],'uptime':int(float(Path('/proc/uptime').read_text().split()[0]))},'services':rows[:150]}
def network_status():
 def parsed(args,default):
  try:return json.loads(command(args,8))
  except Exception:return default
 interfaces=[]
 for item in parsed(['ip','-j','address','show'],[]):
  interfaces.append({'name':item.get('ifname',''), 'state':item.get('operstate',''), 'addresses':[a.get('local','')+'/'+str(a.get('prefixlen','')) for a in item.get('addr_info',[]) if a.get('family') in ('inet','inet6')]})
 routes=parsed(['ip','-j','-4','route','show'],[])
 try:
  with open('/proc/sys/net/ipv4/ip_forward') as f:forward=f.read().strip()=='1'
 except OSError:forward=False
 return {'interfaces':interfaces,'routes':[{'dst':x.get('dst',''), 'gateway':x.get('gateway',''), 'dev':x.get('dev','')} for x in routes], 'forwarding':forward}
def tunnel_status():
 try:
  p=json.loads(command(['tailscale','debug','prefs'],8))
  return {'available':True,'advertised_routes':p.get('AdvertiseRoutes') or [],'accept_routes':bool(p.get('RouteAll'))}
 except Exception:return {'available':False,'advertised_routes':[],'accept_routes':False}
def home_path(relative):
 if '\x00' in relative:raise ValueError('Invalid path')
 base=Path('/home').resolve(); target=(base/relative.lstrip('/')).resolve(strict=True)
 if target!=base and base not in target.parents:raise ValueError('Path outside /home')
 return target
def list_home(relative):
 p=home_path(relative)
 if not p.is_dir():raise ValueError('Not a directory')
 entries=[]
 for item in sorted(p.iterdir(),key=lambda x:x.name.casefold())[:1000]:
  try:
   if item.is_symlink():continue
   t=item.stat();entries.append({'name':item.name,'directory':item.is_dir(),'size':t.st_size,'modified':int(t.st_mtime)})
  except OSError:continue
 return {'path':str(p.relative_to(Path('/home'))).replace('.', '', 1) if p==Path('/home') else str(p.relative_to(Path('/home'))),'items':entries}
def authorised(handler):
 try:
  c=SimpleCookie();c.load(handler.headers.get('Cookie',''));token=c['pi_session'].value
  with guard:return sessions.get(token,0)>time.monotonic()
 except Exception:return False
class Handler(BaseHTTPRequestHandler):
 server_version='PiNode';sys_version=''
 def log_message(self,*args):pass
 def respond(self,code,data,typ='application/json; charset=utf-8',extra=None):
  raw=json.dumps(data,ensure_ascii=False).encode() if typ.startswith('application/json') else data.encode();self.send_response(code);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'")
  for k,v in (extra or {}).items():self.send_header(k,v)
  self.end_headers();self.wfile.write(raw)
 def do_GET(self):
  if self.path=='/':return self.respond(200,HTML,'text/html; charset=utf-8')
  if self.path=='/app.js':return self.respond(200,JS,'application/javascript; charset=utf-8')
  if self.path=='/style.css':return self.respond(200,CSS,'text/css; charset=utf-8')
  if not authorised(self):return self.respond(401,{'error':'Sign in required'})
  if self.path=='/api/status':return self.respond(200,status())
  if self.path=='/api/network':return self.respond(200,network_status())
  if self.path=='/api/tunnels':return self.respond(200,tunnel_status())
  route=urlsplit(self.path)
  if route.path=='/api/files':
   try:return self.respond(200,list_home(parse_qs(route.query).get('path',[''])[0]))
   except (OSError,ValueError):return self.respond(400,{'error':'Недоступный каталог'})
  if route.path=='/api/file':
   try:
    p=home_path(parse_qs(route.query).get('path',[''])[0]);f=p.open('rb')
    if not p.is_file():f.close();raise ValueError()
    size=os.fstat(f.fileno()).st_size
    self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Disposition','attachment; filename="download"');self.send_header('Content-Length',str(size));self.send_header('Cache-Control','no-store');self.end_headers()
    with f:shutil.copyfileobj(f,self.wfile,128*1024)
    return
   except (OSError,ValueError):return self.respond(400,{'error':'Недоступный файл'})
  return self.respond(404,{'error':'Not found'})
 def do_POST(self):
  origin=self.headers.get('Origin','');host=self.headers.get('Host','')
  if origin!='https://'+host:return self.respond(403,{'error':'Invalid origin'})
  try:
   length=int(self.headers.get('Content-Length','0'))
   if not 0<length<=16384:raise ValueError()
   data=json.loads(self.rfile.read(length));assert isinstance(data,dict)
  except Exception:return self.respond(400,{'error':'Invalid request'})
  if self.path=='/api/login':
   now=time.monotonic()
   with guard:
    attempts[:]=[x for x in attempts if now-x<60]
    if len(attempts)>=8:return self.respond(429,{'error':'Try again later'})
    candidate=hashlib.scrypt(str(data.get('password','')).encode(),salt=bytes.fromhex(CFG['salt']),n=16384,r=8,p=1).hex()
    if not hmac.compare_digest(candidate,CFG['password_hash']):attempts.append(now);return self.respond(401,{'error':'Invalid password'})
    token=secrets.token_urlsafe(32);sessions[token]=now+43200
   return self.respond(200,{'ok':True},extra={'Set-Cookie':'pi_session='+token+'; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=43200'})
  if not authorised(self):return self.respond(401,{'error':'Sign in required'})
  if self.path=='/api/logout':
   try:
    c=SimpleCookie();c.load(self.headers.get('Cookie',''))
    with guard:sessions.pop(c['pi_session'].value,None)
   except Exception:pass
   return self.respond(200,{'ok':True},extra={'Set-Cookie':'pi_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0'})
  if self.path=='/api/action':
   name=str(data.get('service',''));action=str(data.get('action',''))
   if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',name) or action not in ('start','stop','restart'):return self.respond(400,{'message':'Invalid operation'})
   try:p=subprocess.run(['docker',action,name],capture_output=True,text=True,timeout=60)
   except Exception:return self.respond(503,{'message':'Docker unavailable'})
   return self.respond(200 if p.returncode==0 else 409,{'message':'Done' if p.returncode==0 else 'Docker action failed'})
  return self.respond(404,{'error':'Not found'})
HTML='''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Pi Control Node</title><link rel="stylesheet" href="/style.css"><script defer src="/app.js"></script></head><body><main><header><span>● PI CONTROL / NODE</span><h1>Узел Raspberry Pi<span class="dot">.</span></h1><p>Лёгкая панель удалённого устройства</p></header><section id="login"><h2>Вход</h2><form id="form"><input id="password" type="password" autocomplete="current-password" placeholder="Пароль панели" required><button>Войти</button></form><p id="error" role="alert"></p></section><div id="view" hidden><nav aria-label="Разделы"><button data-tab="overview">Обзор</button><button data-tab="monitor">Мониторинг</button><button data-tab="network">Сеть</button><button data-tab="tunnels">Туннели</button><button data-tab="files">Файлы</button><button data-tab="settings">Настройки</button></nav><section><div class="line"><h2 id="heading">Обзор</h2><button id="refresh" class="secondary">Обновить</button></div><div id="content"></div></section><button id="logout" class="secondary">Выйти</button></div></main></body></html>'''
CSS='''*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:#f3f7f4;color:#193027;font:15px system-ui}main{max-width:1050px;margin:4vh auto;padding:20px}header span{color:#b81f49;font-size:12px;letter-spacing:.15em;font-weight:700}header .dot{font-size:inherit}h1{font-size:clamp(30px,5vw,46px);margin:12px 0}header p,section p{color:#6e8578}section{background:#fff;border:1px solid #e1ebe4;border-radius:18px;padding:24px;margin:20px 0;box-shadow:0 8px 30px #1c46300c}input,button{font:inherit;border:1px solid #d7e5db;border-radius:9px;padding:10px}input{max-width:100%}button{background:#186441;color:#fff;cursor:pointer}button.secondary,nav button{background:#fff;color:#245841}nav{display:flex;gap:7px;flex-wrap:wrap}nav button.active{background:#186441;color:#fff}.line,.row{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.row{border-top:1px solid #edf3ee;padding:12px 0}.metrics{display:flex;gap:12px;flex-wrap:wrap}.metric{background:#f4f9f5;border-radius:10px;padding:15px;flex:1;min-width:140px}.muted{color:#6e8578;font-size:13px}.name{overflow-wrap:anywhere}.error{color:#ad2445}a{color:#186441}'''
JS='''"use strict";const $=x=>document.querySelector(x);let tab='overview',dir='';const labels={overview:'Обзор',monitor:'Мониторинг',network:'Сеть',tunnels:'Туннели',files:'Файлы',settings:'Настройки'};async function api(url,data){let r=await fetch(url,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined,credentials:'same-origin'});let x=await r.json();if(!r.ok)throw Error(x.error||x.message||'Ошибка');return x}function text(tag,value,cls){let e=document.createElement(tag);e.textContent=value;if(cls)e.className=cls;return e}function row(parent,name,value){let r=text('div','','row');r.append(text('span',name,'name'),text('span',String(value),'muted'));parent.append(r)}function metrics(root,s){let box=text('div','','metrics');for(let [a,b] of [['ОЗУ занято',(s.memory_used/1073741824).toFixed(2)+' ГиБ'],['Диск свободен',(s.disk_free/1073741824).toFixed(2)+' ГиБ'],['Нагрузка CPU',Number(s.load).toFixed(2)],['Время работы',Math.floor(s.uptime/3600)+' ч']])box.append(text('div',a+': '+b,'metric'));root.append(box)}async function load(){try{let d=await api('/api/status');$('#login').hidden=true;$('#view').hidden=false;$('#heading').textContent=labels[tab];document.querySelectorAll('[data-tab]').forEach(e=>e.classList.toggle('active',e.dataset.tab===tab));let root=$('#content');root.replaceChildren();if(tab==='overview'||tab==='monitor'){metrics(root,d.system);if(tab==='monitor'){row(root,'ОЗУ всего',(d.system.memory_total/1073741824).toFixed(2)+' ГиБ');row(root,'Диск всего',(d.system.disk_total/1073741824).toFixed(2)+' ГиБ');row(root,'Диск занято',(d.system.disk_used/1073741824).toFixed(2)+' ГиБ');row(root,'Температура',d.system.temperature===null?'нет данных':d.system.temperature+' °C')}else{root.append(text('h3','Контейнеры'));for(let c of d.services){let r=text('div','','row');r.append(text('span',c.name+' · '+c.state,'name'));let b=text('button',c.state==='running'?'Остановить':'Запустить');b.onclick=async()=>{try{await api('/api/action',{service:c.id,action:c.state==='running'?'stop':'start'});load()}catch(e){alert(e.message)}};r.append(b);root.append(r)}if(!d.services.length)root.append(text('p','Контейнеров нет.'))}}else if(tab==='network'){let n=await api('/api/network');root.append(text('h3','Интерфейсы'));for(let i of n.interfaces)row(root,i.name+' · '+i.state,i.addresses.join(', ')||'нет адреса');root.append(text('h3','Маршруты IPv4'));for(let r of n.routes)row(root,r.dst||'default',[r.gateway,r.dev].filter(Boolean).join(' · '));row(root,'Пересылка IPv4',n.forwarding?'включена':'выключена')}else if(tab==='tunnels'){let t=await api('/api/tunnels');root.append(text('p','Состояние Tailscale и объявленных маршрутов. Изменение маршрутов выполняется администратором.'));row(root,'Tailscale',t.available?'доступен':'недоступен');row(root,'Приём маршрутов',t.accept_routes?'включён':'выключен');for(let r of t.advertised_routes)row(root,'Объявленный маршрут',r);if(!t.advertised_routes.length)root.append(text('p','Объявленных маршрутов нет.'))}else if(tab==='files'){let f=await api('/api/files?path='+encodeURIComponent(dir));root.append(text('p','Просмотр и скачивание файлов из /home. Управление и загрузка доступны через основную панель.'));root.append(text('p','/home/'+f.path,'muted'));if(dir){let up=text('button','↑ Выше','secondary');up.onclick=()=>{dir=dir.split('/').slice(0,-1).join('/');load()};root.append(up)}for(let i of f.items){let r=text('div','','row'),name=text('span',(i.directory?'📁 ':'📄 ')+i.name,'name');r.append(name);let path=[dir,i.name].filter(Boolean).join('/');if(i.directory){let b=text('button','Открыть','secondary');b.onclick=()=>{dir=path;load()};r.append(b)}else{let a=text('a','Скачать');a.href='/api/file?path='+encodeURIComponent(path);a.download=i.name;r.append(a)}root.append(r)}}else if(tab==='settings'){root.append(text('p','Облегчённая панель работает с отдельным паролем и HTTPS-сертификатом. Доступны прямые подключения по локальной сети и Tailscale.'));row(root,'Режим','Управляемый узел');row(root,'Порт панели',location.port||'443');row(root,'Безопасность','HTTPS · отдельная сессия');root.append(text('p','Пароль и сетевые параметры панели в этом разделе пока изменить нельзя.','muted'))}}catch(e){if(/Sign in/.test(e.message)){$('#login').hidden=false;$('#view').hidden=true}else{$('#content').replaceChildren(text('p',e.message,'error'))}}}document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;load()});$('#form').onsubmit=async e=>{e.preventDefault();try{await api('/api/login',{password:$('#password').value});$('#password').value='';$('#error').textContent='';load()}catch(e){$('#error').textContent=e.message}};$('#refresh').onclick=load;$('#logout').onclick=async()=>{await api('/api/logout',{});location.reload()};load();'''
def private_addresses():
 out=[]
 try:rows=json.loads(command(['ip','-j','-4','addr','show']))
 except Exception:return []
 for r in rows:
  name=r.get('ifname','')
  if not (name.startswith(('eth','en','wl')) or name=='tailscale0'):continue
  for x in r.get('addr_info',[]):
   try:
    addr=ipaddress.ip_address(x['local'])
    if addr.version==4 and (addr.is_private or addr in ipaddress.ip_network('100.64.0.0/10')) and not addr.is_loopback:out.append(str(addr))
   except Exception:pass
 return sorted(set(out))
if __name__=='__main__':
 ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.minimum_version=ssl.TLSVersion.TLSv1_2;ctx.load_cert_chain(str(ROOT/'cert.pem'),str(ROOT/'key.pem'))
 # Reconcile private and Tailscale addresses as interfaces move between networks.
 port=int(CFG.get('port',8484));servers={}
 while True:
  addresses=set(private_addresses())
  for addr in addresses-set(servers):
   try:
    srv=ThreadingHTTPServer((addr,port),Handler);srv.socket=ctx.wrap_socket(srv.socket,server_side=True);servers[addr]=srv;threading.Thread(target=srv.serve_forever,daemon=True).start()
   except OSError:continue
  for addr in set(servers)-addresses:
   srv=servers.pop(addr);srv.shutdown();srv.server_close()
  time.sleep(20)
