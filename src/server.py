#!/usr/bin/env python3
import os,json,time,ssl,hmac,secrets,threading,subprocess,urllib.request,urllib.error,shutil,socket,hashlib
import extras
import features
import control3
import vault
import inventory4
import discovery5
import cluster5
import files6
import devices7
import ssh_nodes7
import observability8
import terminal9
import provision11
import nodeops10
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from http.cookies import SimpleCookie

ROOT=Path('/opt/pi-dashboard'); STATE=ROOT/'telegram.json'; SECRET=ROOT/'dashboard.env'
def read_env(path):
 d={}
 try:
  for line in path.read_text().splitlines():
   if '=' in line and not line.startswith('#'):
    k,v=line.split('=',1); d[k]=v.strip().strip('\"\'')
 except OSError: pass
 return d
cfg=read_env(SECRET); PASSWORD=cfg.get('DASH_PASSWORD',''); assert len(PASSWORD)>=12
sessions={}; failed={}; lock=threading.Lock(); task={'running':False,'label':'','result':'','time':0}; cpu_prev=None
SERVICES={
 'agent':{'name':'Agent Zero','container':'pi-stack-agent-zero-1','port':50080,'scheme':'http','desc':'ИИ-агент и инструменты','group':'core'},
 'portainer':{'name':'Portainer','container':'pi-stack-portainer-1','port':9443,'scheme':'https','desc':'Управление контейнерами','group':'core'},
 'postgres':{'name':'PostgreSQL','container':'pi-stack-postgres-1','port':None,'scheme':'','desc':'База данных приложений','group':'core'},
 'ollama':{'name':'Ollama','container':'pi-stack-ollama-1','port':None,'scheme':'','desc':'Локальные языковые модели','group':'ai'},
 'nextcloud':{'name':'Nextcloud','container':'pi-extra-nextcloud-1','port':8080,'scheme':'http','desc':'Файлы и совместная работа','group':'docs'},
 'office':{'name':'ONLYOFFICE','container':'pi-extra-onlyoffice-1','port':8081,'scheme':'http','desc':'Редактор документов','group':'docs'},
 'n8n':{'name':'n8n','container':'pi-extra-n8n-1','port':5678,'scheme':'http','desc':'Сценарии автоматизации','group':'automation'},
 'zap':{'name':'OWASP ZAP','container':'pi-extra-zap-1','port':8095,'scheme':'https','desc':'Анализ безопасности веб-приложений','group':'security','path':'/zap/'},
 'zap-api':{'name':'ZAP API','container':'pi-extra-zap-api-1','port':None,'scheme':'','desc':'API для автоматизации проверок','group':'security'},
 'pihole':{'name':'Pi-hole','container':'pi-pihole','port':8088,'scheme':'http','path':'/admin/','desc':'DNS-фильтрация по требованию','group':'network'},
 'vpn':{'name':'Gluetun','container':'pi-extra-gluetun-1','port':None,'scheme':'','desc':'VPN для выбранных контейнеров','group':'network'},
}
COMMANDS={
 'pihole:start':['/usr/bin/python3','/opt/pi-dashboard/control3.py','start'], 'pihole:stop':['/usr/bin/python3','/opt/pi-dashboard/control3.py','stop'],
 'portainer:start':['docker','start','pi-stack-portainer-1'], 'portainer:stop':['docker','stop','-t','30','pi-stack-portainer-1'],
 'postgres:start':['docker','start','pi-stack-postgres-1'], 'postgres:stop':['docker','stop','-t','30','pi-stack-postgres-1'],
 'agent:start':['docker','start','pi-stack-agent-zero-1'], 'agent:stop':['docker','stop','-t','30','pi-stack-agent-zero-1'],
 'ollama:start':['/usr/local/bin/pi-mode','llm'], 'ollama:stop':['/usr/local/bin/pi-mode','stop-llm'],
 'nextcloud:start':['/usr/local/bin/pi-app','docs'], 'nextcloud:stop':['/usr/local/bin/pi-app','stop','docs'],
 'office:start':['/usr/local/bin/pi-app','office'], 'office:stop':['/usr/local/bin/pi-app','stop','office'],
 'n8n:start':['/usr/local/bin/pi-app','n8n'], 'n8n:stop':['/usr/local/bin/pi-app','stop','n8n'],
 'zap:start':['/usr/local/bin/pi-app','zap'], 'zap:stop':['/usr/local/bin/pi-app','stop','zap'],
 'zap-api:start':['/usr/local/bin/pi-app','zap-api'], 'zap-api:stop':['/usr/local/bin/pi-app','stop','zap-api'],
 'mode-docs:start':['/usr/local/bin/pi-app','docs'], 'vpn:start':['/usr/local/bin/pi-app','vpn'], 'vpn:stop':['/usr/local/bin/pi-app','stop','vpn'],
 'desktop:start':['/usr/local/bin/pi-desktop','on'], 'desktop:stop':['/usr/local/bin/pi-desktop','off'],
 'vnc:start':['runuser','-u','sm0k3','--','/usr/local/bin/pi-vnc','on'], 'vnc:stop':['runuser','-u','sm0k3','--','/usr/local/bin/pi-vnc','off'],
 'office-lan:set':['/usr/local/bin/pi-app','office-lan'], 'office-tail:set':['/usr/local/bin/pi-app','office-tail'],
}
def execsafe(args,timeout=8):
 try:
  p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=timeout,check=False); return p.stdout.strip() if p.returncode==0 else ''
 except (OSError,subprocess.TimeoutExpired): return ''
def sysinfo():
 global cpu_prev
 mem={}
 for line in Path('/proc/meminfo').read_text().splitlines():
  p=line.split(); mem[p[0].rstrip(':')]=int(p[1])*1024
 total=mem['MemTotal']; avail=mem.get('MemAvailable',mem.get('MemFree',0)); disk=shutil.disk_usage('/')
 try:
  v=[int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]; totalc=sum(v); idle=v[3]+v[4]; cpu=round(100*(1-(idle-cpu_prev[1])/(totalc-cpu_prev[0])),1) if cpu_prev and totalc>cpu_prev[0] else None; cpu_prev=(totalc,idle)
 except Exception: cpu=None
 try: temp=round(int(Path('/sys/class/thermal/thermal_zone0/temp').read_text())/1000,1)
 except Exception: temp=None
 ip=execsafe(['tailscale','ip','-4']).splitlines(); lan=''
 try:
  rows=json.loads(execsafe(['ip','-j','-4','addr','show','dev','wlan0']) or '[]'); lan=next((a['local'] for r in rows for a in r.get('addr_info',[]) if a.get('scope')=='global'),'')
 except Exception: pass
 wifi=execsafe(['nmcli','-g','GENERAL.CONNECTION','device','show','wlan0'])
 throttle=execsafe(['vcgencmd','get_throttled'])
 try: raw=int(throttle.split('=')[1],16)
 except Exception: raw=0
 return {'cpu':cpu,'temperature':temp,'memory_total':total,'memory_available':avail,'memory_used':total-avail,'disk_total':disk.total,'disk_used':disk.used,'disk_free':disk.free,'load':os.getloadavg()[0],'uptime':int(float(Path('/proc/uptime').read_text().split()[0])),'lan_ip':lan,'tailscale_ip':ip[0] if ip else '', 'wifi':wifi.removeprefix('pi-') if wifi and wifi!='--' else 'Нет подключения','undervoltage_now':bool(raw&1),'undervoltage_previous':bool(raw&0x10000),'throttled_now':bool(raw&4)}
def get_status():
 out=execsafe(['docker','ps','-a','--format','{{json .}}'],12); containers={}
 for ln in out.splitlines():
  try:
   d=json.loads(ln); containers[d.get('Names','')]=d
  except Exception: pass
 stats={}
 try:
  for line in execsafe(['docker','stats','--no-stream','--format','{{json .}}'],20).splitlines():
   row=json.loads(line); stats[row.get('Name')]=row
 except Exception: pass
 limits=control3.inspect(SERVICES);items=[]
 for key,info in SERVICES.items():
  d=containers.get(info['container'],{}); state=d.get('State','missing'); item={'id':key,**info,'state':state,'status':d.get('Status','Не установлен'),'cpu_usage':stats.get(info['container'],{}).get('CPUPerc','—'),'memory_usage':stats.get(info['container'],{}).get('MemUsage','—'),'limits':limits.get(info['container'])}; items.append(item)
 vnc=execsafe(['runuser','-u','sm0k3','--','/usr/local/bin/pi-vnc','status']); vnc_on=any('Xtigervnc' in line and line.lstrip().startswith('2') for line in vnc.splitlines())
 return {'system':sysinfo(),'services':items,'desktop':execsafe(['systemctl','is-active','lightdm'])=='active','vnc':vnc_on,'task':task.copy(),'vpn_configured':Path('/opt/pi-stack/wg0.conf').is_file(),'telegram_configured':bool(read_telegram().get('bot_token') and read_telegram().get('chat_id'))}
def run_action(key,verb):
 cmd=COMMANDS.get(f'{key}:{verb}')
 if not cmd: return False,'Недоступная операция'
 with lock:
  if task['running']: return False,'Другая операция уже выполняется'
  task.update(running=True,label=f'{key}:{verb}',result='',time=time.time())
 def work():
  try:
   p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=180,check=False)
   # avoid surfacing docker compose interpolation, environment or credentials
   
   if p.returncode==0 and key in ('agent','portainer','postgres'):
    extras.set_desired(key,verb=='start')
   if p.returncode==0 and key=='mode-docs' and verb=='start':
    p=subprocess.run(['/usr/local/bin/pi-app','office'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=180,check=False)
   msg='Готово' if p.returncode==0 else 'Операция не выполнена. Проверьте состояние сервиса.'
  except subprocess.TimeoutExpired: msg='Превышено время ожидания; проверьте состояние сервиса.'
  except OSError: msg='Не удалось запустить команду управления.'
  with lock: task.update(running=False,result=msg,time=time.time())
 threading.Thread(target=work,daemon=True).start(); return True,'Операция запущена'
def read_telegram():
 try: return json.loads(STATE.read_text())
 except Exception:return {}
def save_telegram(d):
 temp=ROOT/'telegram.tmp'; fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 with os.fdopen(fd,'w') as f: json.dump(d,f)
 os.chmod(temp,0o600);os.replace(temp,STATE)
def send_telegram(message):
 data=read_telegram(); token=data.get('bot_token',''); chat=str(data.get('chat_id',''))
 if not token or not chat:return False,'Сначала настройте бота и chat ID'
 try:
  req=urllib.request.Request('https://api.telegram.org/bot'+token+'/sendMessage',data=json.dumps({'chat_id':chat,'text':message}).encode(),headers={'Content-Type':'application/json'},method='POST')
  with urllib.request.urlopen(req,timeout=12) as resp: payload=json.load(resp)
  return (bool(payload.get('ok')), 'Уведомление отправлено' if payload.get('ok') else 'Telegram отклонил запрос')
 except Exception:return False,'Не удалось отправить уведомление. Проверьте токен, chat ID и сеть.'
def monitor():
 last={}
 while True:
  time.sleep(60)
  try:
   c=read_telegram()
   if not c.get('enabled') or not c.get('bot_token') or not c.get('chat_id'):continue
   x=get_status(); s=x['system']; alerts={}
   if s['temperature'] is not None and s['temperature']>=80:alerts['temp']=f"Температура {s['temperature']} °C"
   if s['disk_used']/s['disk_total']>=.85:alerts['disk']=f"Диск заполнен на {round(100*s['disk_used']/s['disk_total'])}%"
   if s['memory_available']/s['memory_total']<.10:alerts['memory']='Доступно менее 10% оперативной памяти'
   if s['undervoltage_now']:alerts['power']='Обнаружено пониженное напряжение питания'
   for item in x['services']:
    if item['id'] in ('agent','portainer','postgres') and extras.desired_enabled(item['id']) and item['state']!='running':alerts['service_'+item['id']]=item['name']+': сервис не работает'
   now=time.time()
   for key,msg in alerts.items():
    if now-last.get(key,0)>=1800:
     ok,_=send_telegram('🍓 sm0k3-pi\n'+msg)
     if ok:last[key]=now
   for key in list(last):
    if key not in alerts: del last[key]
  except Exception: pass
class Handler(BaseHTTPRequestHandler):
 server_version='PiHub'; sys_version=''
 def log_message(self,*args): pass
 def respond(self,code,data,content='application/json; charset=utf-8',headers=None):
  raw=json.dumps(data,ensure_ascii=False).encode() if content.startswith('application/json') else data
  self.send_response(code);self.send_header('Content-Type',content);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
  for k,v in (headers or {}).items():self.send_header(k,v)
  self.end_headers();self.wfile.write(raw)
 def authenticated(self):
  try:
   c=SimpleCookie(); c.load(self.headers.get('Cookie','')); token=c['pi_session'].value; expiry=sessions.get(token,0)
   if expiry>time.time():return True
  except Exception: pass
  return False
 def body(self):
  try:
   n=int(self.headers.get('Content-Length','0'))
   if n<1 or n>(3000000 if self.path=='/api/vault/import' else 1500000 if self.path=='/api/feature/node-upload' else 65536):return None
   d=json.loads(self.rfile.read(n));return d if isinstance(d,dict) else None
  except Exception:return None
 def do_GET(self):
  if self.path in ('/','/index.html','/app.js','/style.css','/logo.svg','/logo.png','/filemanager.js','/xterm.js','/xterm.css','/addon-fit.js'):
   name={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css','/logo.svg':'logo.svg','/logo.png':'logo.png','/filemanager.js':'filemanager.js','/xterm.js':'xterm.js','/xterm.css':'xterm.css','/addon-fit.js':'addon-fit.js'}[self.path]; typ={'html':'text/html; charset=utf-8','js':'application/javascript; charset=utf-8','css':'text/css; charset=utf-8','svg':'image/svg+xml','png':'image/png'}[name.rsplit('.',1)[1]]
   try:self.respond(200,(ROOT/name).read_bytes(),typ)
   except OSError:self.respond(404,{'error':'Не найдено'})
   return
  if self.path=='/api/session':return self.respond(200,{'authenticated':self.authenticated()})
  if not self.authenticated():return self.respond(401,{'error':'Требуется вход'})
  if self.path.startswith('/api/fm/preview?'):
   try:
    from urllib.parse import urlsplit,parse_qs
    q=parse_qs(urlsplit(self.path).query,keep_blank_values=True);p=files6.resolve(q.get('root',[''])[0],q.get('path',[''])[0]);types={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp'};mime=types.get(p.suffix.lower())
    if not mime or not p.is_file() or p.stat().st_size>10*1024*1024:raise ValueError('Предпросмотр доступен для изображений до 10 МиБ.')
    return self.respond(200,p.read_bytes(),mime)
   except ValueError as e:return self.respond(400,{'error':str(e)})
   except Exception:return self.respond(503,{'error':'Не удалось открыть изображение.'})
  if self.path.startswith('/api/fm/download?'):
   try:
    from urllib.parse import urlsplit,parse_qs,quote
    q=parse_qs(urlsplit(self.path).query,keep_blank_values=True);p=files6.resolve(q.get('root',[''])[0],q.get('path',[''])[0])
    if not p.is_file():raise ValueError('Выберите файл.')
    self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Length',str(p.stat().st_size));self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(p.name));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
    with p.open('rb') as f:shutil.copyfileobj(f,self.wfile,1024*1024)
   except ValueError as e:return self.respond(400,{'error':str(e)})
   except (BrokenPipeError,ConnectionResetError):return
   except Exception:return
   return
  if self.path=='/api/status':
   try:return self.respond(200,get_status())
   except Exception:return self.respond(503,{'error':'Не удалось получить состояние'})
  if self.path=='/api/cluster':return self.respond(200,{'nodes':cluster5.get(),'fingerprint':cluster5.cert_fingerprint(),'ssh_nodes':ssh_nodes7.list_nodes()})
  if self.path=='/api/vault/info':return self.respond(200,vault.info())
  if self.path=='/api/network-history':return self.respond(200,inventory4.list_reports())
  if self.path=='/api/network-inventory':return self.respond(200,{'local':control3.network_info(),'scan':control3.inventory(),'running':control3.running.copy(),'auto':control3.auto_config()})
  if self.path=='/api/backup':return self.respond(200,features.backup_status())
  if self.path=='/api/wifi-observations':return self.respond(200,observability8.wifi_info())
  if self.path=='/api/wifi-journal':return self.respond(200,observability8.wifi_journal())
  if self.path=='/api/disk-usage':return self.respond(200,observability8.disk_status())
  if self.path=='/api/wifi':
   try:return self.respond(200,features.profiles())
   except Exception:return self.respond(503,{'error':'Не удалось получить Wi-Fi подключения'})
  if self.path=='/api/chats':return self.respond(200,features.get_chats())
  if self.path=='/api/interfaces':return self.respond(200,devices7.interfaces())
  if self.path=='/api/bluetooth-inventory':return self.respond(200,devices7.bluetooth_inventory())
  if self.path=='/api/bluetooth-history':return self.respond(200,devices7.bluetooth_history())
  if self.path=='/api/ai-config':
   cfg=features.get_config();return self.respond(200,{'groq':bool(cfg.get('groq')),'openrouter':bool(cfg.get('openrouter'))})
  if self.path=='/api/extras':return self.respond(200,extras.get_info(SERVICES))
  if self.path=='/api/knowledge':return self.respond(200,extras.list_notes())
  if self.path=='/api/telegram':
   t=read_telegram();return self.respond(200,{'configured':bool(t.get('bot_token') and t.get('chat_id')),'enabled':bool(t.get('enabled')),'chat_id':str(t.get('chat_id',''))})
  return self.respond(404,{'error':'Не найдено'})
 def do_POST(self):
  global PASSWORD
  host=self.headers.get('Host',''); origin=self.headers.get('Origin','')
  if not origin or origin!='https://'+host:return self.respond(403,{'error':'Недопустимый источник запроса'})
  if self.path.startswith('/api/fm/chunk/'):
   if not self.authenticated():return self.respond(401,{'error':'Требуется вход'})
   try:
    n=int(self.headers.get('Content-Length','0'))
    if n<1 or n>4194304:raise ValueError('Некорректный размер блока.')
    token=self.path.rsplit('/',1)[-1];return self.respond(200,files6.upload_chunk(token,self.rfile.read(n)))
   except ValueError as e:return self.respond(400,{'error':str(e)})
   except Exception:return self.respond(503,{'error':'Ошибка загрузки.'})
  data=self.body()
  if data is None:return self.respond(400,{'error':'Некорректный запрос'})
  if self.path=='/api/login':
   now=time.time(); recent=[t for t in failed.get('login',[]) if now-t<60]
   if len(recent)>=8:return self.respond(429,{'error':'Слишком много попыток. Повторите через минуту.'})
   if not hmac.compare_digest(str(data.get('password','')).encode(),PASSWORD.encode()):failed['login']=recent+[now];return self.respond(401,{'error':'Неверный пароль'})
   failed['login']=[];token=secrets.token_urlsafe(32);sessions[token]=now+43200
   return self.respond(200,{'ok':True},headers={'Set-Cookie':'pi_session='+token+'; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=43200'})
  if not self.authenticated():return self.respond(401,{'error':'Требуется вход'})
  if self.path.startswith('/api/fm/'):
   try:return self.respond(200,files6.dispatch(self.path.rsplit('/',1)[-1],data))
   except ValueError as e:return self.respond(400,{'error':str(e)})
   except Exception:return self.respond(503,{'error':'Не удалось выполнить файловую операцию.'})
  if self.path.startswith('/api/vault/'):
   try:
    op=self.path.rsplit('/',1)[-1]
    result=vault.create(data) if op=='create' else vault.dispatch(op,data)
    return self.respond(200,result)
   except ValueError as e:return self.respond(400,{'error':str(e)})
   except Exception:return self.respond(503,{'error':'Ошибка операции с хранилищем.'})
  if self.path=='/api/report-html':
   try:return self.respond(200,inventory4.report_html(str(data.get('id',''))),'text/html; charset=utf-8')
   except ValueError as e:return self.respond(400,{'error':str(e)})
  if self.path=='/api/password':
   try:
    PASSWORD=control3.pwd_change(str(data.get('current','')),str(data.get('password','')),PASSWORD);sessions.clear();return self.respond(200,{'message':'Пароль изменён. Войдите снова.'})
   except ValueError as e:return self.respond(400,{'error':str(e)})
   except Exception:return self.respond(503,{'error':'Не удалось изменить пароль.'})
  if self.path=='/api/logout':
   try:c=SimpleCookie();c.load(self.headers.get('Cookie',''));sessions.pop(c['pi_session'].value,None)
   except Exception:pass
   return self.respond(200,{'ok':True},headers={'Set-Cookie':'pi_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0'})
  if self.path.startswith('/api/feature/'):
   try:
    if self.path=='/api/feature/terminal-start':result=terminal9.remote_start(data) if data.get('node_id') else terminal9.start()
    elif self.path in ('/api/feature/terminal-read','/api/feature/terminal-write','/api/feature/terminal-stop','/api/feature/terminal-resize'):result=terminal9.session(data,self.path.rsplit('-',1)[-1])
    elif self.path=='/api/feature/disk-usage':result=observability8.disk_start()
    elif self.path=='/api/feature/wifi-external':result=observability8.wifi_sample(check_external=True)
    elif self.path=='/api/feature/ssh-fingerprint':result=ssh_nodes7.fingerprint(data)
    elif self.path=='/api/feature/ssh-discover':result=ssh_nodes7.discover(data)
    elif self.path=='/api/feature/ssh-add':result=ssh_nodes7.connect(data)
    elif self.path=='/api/feature/ssh-remove':result=ssh_nodes7.remove(data)
    elif self.path=='/api/feature/ssh-status':result=ssh_nodes7.status(data)
    elif self.path=='/api/feature/node-provision':result=provision11.create(data)
    elif self.path=='/api/feature/node-provision-status':result=provision11.snapshot(str(data.get('job','')))
    elif self.path=='/api/feature/node-containers':result=nodeops10.containers(data)
    elif self.path=='/api/feature/node-container-action':result=nodeops10.container_action(data)
    elif self.path=='/api/feature/node-files':result=nodeops10.list_files(data)
    elif self.path=='/api/feature/node-download':result=nodeops10.download(data)
    elif self.path=='/api/feature/node-upload':result=nodeops10.upload(data)
    elif self.path=='/api/feature/cluster-add':result=cluster5.create(data)
    elif self.path=='/api/feature/cluster-remove':result=cluster5.remove(str(data.get('id','')))
    elif self.path=='/api/feature/cluster-action':result=cluster5.action(data)
    elif self.path=='/api/feature/wifi-scan':result=discovery5.wifi_scan(data)
    elif self.path=='/api/feature/bluetooth-scan':result=discovery5.bluetooth_scan()
    elif self.path=='/api/feature/bluetooth-info':result=devices7.bluetooth_info(data)
    elif self.path=='/api/feature/bluetooth-record':result=devices7.bluetooth_record(data)
    elif self.path=='/api/feature/bluetooth-audit':result=devices7.bluetooth_audit(data)
    elif self.path=='/api/feature/bluetooth-action':result=devices7.bluetooth_action(data)
    elif self.path=='/api/feature/bluetooth-forget':result=devices7.bluetooth_forget(data)
    elif self.path=='/api/feature/interface-prefer':result=devices7.prefer(data)
    elif self.path=='/api/feature/chat-manage':result=features.chat_manage(data)
    elif self.path=='/api/feature/network-save':result=inventory4.update_network(data)
    elif self.path=='/api/feature/network-knowledge':result=inventory4.knowledge(str(data.get('id','')))
    elif self.path=='/api/feature/printer-add':result=inventory4.add_printer(data)
    elif self.path=='/api/feature/limits':result=control3.limits_set(data,SERVICES)
    elif self.path=='/api/feature/network-scan':result=control3.scan_start(data)
    elif self.path=='/api/feature/network-auto':result=control3.set_auto(data)
    elif self.path=='/api/feature/backup-config':result=features.backup_config(data)
    elif self.path=='/api/feature/backup-start':result=features.backup_start()
    elif self.path=='/api/feature/wifi':result=features.wifi(data)
    elif self.path=='/api/feature/model':result=features.ollama_op(data)
    elif self.path=='/api/feature/ai-config':result=features.save_config(data)
    elif self.path=='/api/feature/chat':
     secret=None
     if data.get('vault_entry'):
      secret=vault.dispatch('reveal',{'master':str(data.get('vault_master','')),'id':str(data['vault_entry'])}).get('password')
     result=features.ai_chat(data,api_override=secret)
    elif self.path=='/api/feature/chat-history':result=features.get_chat(data.get('chat_id'))
    else:return self.respond(404,{'error':'Не найдено'})
    return self.respond(200,result)
   except ValueError as e:return self.respond(400,{'error':str(e)})
   except Exception:return self.respond(503,{'error':'Не удалось выполнить операцию. Проверьте состояние сервиса.'})
  if self.path.startswith('/api/extra/'):
   try:
    result=extras.dispatch(self.path,data,SERVICES,sysinfo());return self.respond(200,result)
   except ValueError as e:return self.respond(400,{'error':str(e)})
   except Exception:return self.respond(503,{'error':'Не удалось выполнить операцию. Проверьте сервис и сеть.'})
  if self.path=='/api/action':
   key=str(data.get('service',''));verb=str(data.get('action',''));ok,msg=run_action(key,verb);return self.respond(202 if ok else 409,{'ok':ok,'message':msg})
  if self.path=='/api/telegram':
   old=read_telegram();token=str(data.get('bot_token','')).strip();chat=str(data.get('chat_id','')).strip();enabled=bool(data.get('enabled',True))
   if not token:token=old.get('bot_token','')
   if not token or len(token)>200 or ':' not in token or not chat.lstrip('-').isdigit() or len(chat)>24:return self.respond(400,{'error':'Укажите токен бота и числовой chat ID'})
   save_telegram({'bot_token':token,'chat_id':chat,'enabled':enabled});return self.respond(200,{'ok':True,'message':'Настройки сохранены'})
  if self.path=='/api/telegram/test':
   ok,msg=send_telegram('🍓 sm0k3-pi: тестовое уведомление. Панель управления подключена.');return self.respond(200 if ok else 400,{'ok':ok,'message':msg})
  return self.respond(404,{'error':'Не найдено'})
if __name__=='__main__':
 threading.Thread(target=terminal9.reap,daemon=True).start();threading.Thread(target=observability8.wifi_background,daemon=True).start();threading.Thread(target=monitor,daemon=True).start();threading.Thread(target=control3.background,args=(SERVICES,),daemon=True).start();server=ThreadingHTTPServer(('127.0.0.1',18443),Handler); ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.minimum_version=ssl.TLSVersion.TLSv1_2;ctx.load_cert_chain(str(ROOT/'cert.pem'),str(ROOT/'key.pem'));server.socket=ctx.wrap_socket(server.socket,server_side=True);server.serve_forever()
