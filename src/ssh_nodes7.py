import base64,hashlib,hmac,ipaddress,json,re,socket,subprocess,threading,uuid,io
from pathlib import Path
import paramiko,extras,control3,vault
FILE=Path('/opt/pi-dashboard/ssh-nodes.json');lock=threading.Lock()
def hostok(h):
 try:
  x=ipaddress.ip_address(h)
  return x.version==4 and any(x in ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10'))
 except ValueError:return False
def endpoint(d):
 host=str(d.get('host','')).strip()
 try:port=int(d.get('port',22))
 except Exception:raise ValueError('Укажите SSH-порт.')
 if not hostok(host) or not 1<=port<=65535:raise ValueError('Разрешены только частные и Tailscale IPv4 и корректный порт.')
 return host,port
def fingerprint(d):
 host,port=endpoint(d);sock=None;transport=None
 try:
  sock=socket.create_connection((host,port),timeout=6);transport=paramiko.Transport(sock);transport.start_client(timeout=8);key=transport.get_remote_server_key();fp='SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=');return {'host':host,'port':port,'fingerprint':fp,'algorithm':key.get_name(),'message':'Проверьте отпечаток SSH-ключа по доверенному каналу перед подключением.'}
 except Exception:raise ValueError('SSH недоступен или ключ узла не получен.')
 finally:
  if transport:transport.close()
  elif sock:sock.close()
def list_nodes():
 try:return json.loads(FILE.read_text())
 except Exception:return []
def connect(d):
 host,port=endpoint(d);fp=str(d.get('fingerprint',''));user=str(d.get('username','')).strip();entry=str(d.get('entry',''));master=str(d.get('master',''));name=str(d.get('name','')).strip()
 if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_.-]{0,31}',user) or not name or len(name)>80 or not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}',fp):raise ValueError('Укажите имя, пользователя и проверенный SSH-отпечаток.')
 manual=str(d.get('secret',''))
 if entry:
  try:uuid.UUID(entry)
  except Exception:raise ValueError('Выберите запись KDBX.')
  secret=vault.dispatch('reveal',{'master':master,'id':entry}).get('password','')
 elif manual:secret=manual
 else:raise ValueError('Введите SSH-пароль или выберите запись KDBX.')
 class Pin(paramiko.MissingHostKeyPolicy):
  def missing_host_key(self,client,hostname,key):
   actual='SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
   if not hmac.compare_digest(actual,fp):raise paramiko.SSHException('Host key mismatch')
 auth=str(d.get('auth','password'))
 if auth not in ('password','key'):raise ValueError('Неизвестный тип аутентификации.')
 opts={}
 if auth=='password':opts['password']=secret
 else:
  for cls in (paramiko.Ed25519Key,paramiko.RSAKey,paramiko.ECDSAKey):
   try:opts['pkey']=cls.from_private_key(io.StringIO(secret));break
   except (paramiko.SSHException,ValueError):continue
  if 'pkey' not in opts:raise ValueError('Не удалось прочитать приватный SSH-ключ без парольной фразы из KDBX.')
 client=paramiko.SSHClient();client.set_missing_host_key_policy(Pin())
 try:
  client.connect(host,port=port,username=user,look_for_keys=False,allow_agent=False,timeout=8,banner_timeout=8,auth_timeout=8,**opts)
  _,out,_=client.exec_command("tr '\\000' ' ' </proc/device-tree/model 2>/dev/null; printf '\\n'; uname -m",timeout=8)
  result=out.read(1024).decode('utf-8','replace').splitlines();model=result[0].strip() if result else ''
  if 'raspberry pi' not in model.lower():raise ValueError('Удалённое устройство не подтвердило модель Raspberry Pi.')
 finally:client.close()
 with lock:
  a=list_nodes()
  if any(x['host']==host and x['port']==port for x in a):raise ValueError('SSH-узел уже добавлен.')
  a.append({'id':uuid.uuid4().hex,'name':name,'host':host,'port':port,'username':user,'fingerprint':fp,'entry':entry,'auth':auth,'model':model});extras.atomic(FILE,a)
 return {'message':'SSH-узел проверен и добавлен. Установка панели на нём не выполнялась.','model':model}
def remove(d):
 with lock:
  a=list_nodes();b=[x for x in a if x['id']!=str(d.get('id',''))]
  if len(a)==len(b):raise ValueError('Узел не найден.')
  extras.atomic(FILE,b)
 return {'message':'SSH-узел удалён из списка.'}
def discover(d):
 if d.get('authorized') is not True:raise ValueError('Подтвердите разрешение на сканирование сети.')
 local=control3.network_info();net=ipaddress.ip_network(local.get('network',''),strict=False)
 if not net.is_private or net.prefixlen<24:raise ValueError('Поиск доступен в текущей частной Wi-Fi подсети не больше /24.')
 try:p=subprocess.run(['nmap','-n','-Pn','-p','22','--open','--max-retries','1','--host-timeout','4s','-T4','-oG','-',str(net)],capture_output=True,text=True,timeout=90)
 except subprocess.TimeoutExpired:raise ValueError('Поиск занял слишком много времени.')
 if p.returncode:raise ValueError('Не удалось выполнить поиск SSH.')
 matches=re.findall(r'^Host: (\d+\.\d+\.\d+\.\d+).*Ports: 22/open/tcp',p.stdout,re.M)
 return {'hosts':[x for x in matches if x!=local.get('ip')],'network':str(net),'message':'Найденные SSH-узлы могут не являться Raspberry Pi. Модель проверяется при подключении.'}

def status(d):
 n=next((x for x in list_nodes() if x['id']==str(d.get('id',''))),None)
 if not n:raise ValueError('SSH-узел не найден.')
 secret=str(d.get('secret','')) or (vault.dispatch('reveal',{'master':str(d.get('master','')),'id':n['entry']}).get('password','') if n.get('entry') else '')
 if not secret:raise ValueError('Введите SSH-секрет для узла или откройте KDBX.')
 class Pin(paramiko.MissingHostKeyPolicy):
  def missing_host_key(self,client,hostname,key):
   actual='SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
   if not hmac.compare_digest(actual,n['fingerprint']):raise paramiko.SSHException('Host key mismatch')
 options={}
 if n.get('auth','password')=='password':options['password']=secret
 else:
  for cls in (paramiko.Ed25519Key,paramiko.RSAKey,paramiko.ECDSAKey):
   try:options['pkey']=cls.from_private_key(io.StringIO(secret));break
   except (paramiko.SSHException,ValueError):continue
  if 'pkey' not in options:raise ValueError('Не удалось открыть приватный ключ.')
 client=paramiko.SSHClient();client.set_missing_host_key_policy(Pin())
 try:
  client.connect(n['host'],port=n['port'],username=n['username'],look_for_keys=False,allow_agent=False,timeout=8,banner_timeout=8,auth_timeout=8,**options)
  command="printf 'HOST='; hostname; printf 'UPTIME='; cut -d' ' -f1 /proc/uptime; printf 'ARCH='; uname -m; printf 'MODEL='; tr '\\000' ' ' </proc/device-tree/model 2>/dev/null; printf '\\n'; printf 'MEM='; awk '/MemTotal|MemAvailable/{printf \"%s:%s \", $1,$2}' /proc/meminfo; printf '\\n'; printf 'DOCKER='; if command -v docker >/dev/null 2>&1; then docker ps --format '{{.Names}}:{{.State}}' 2>/dev/null | head -20 | tr '\\n' ','; else printf unavailable; fi; printf '\\n'"
  command += " ; printf 'OS='; . /etc/os-release; printf '%s %s\\n' \"$ID\" \"$VERSION_ID\"; printf 'DISK='; df -Pk / | tail -1 | awk '{print $2 \" KiB total / \" $4 \" KiB free\"}'; printf 'ROOT='; if [ \"$(id -u)\" = 0 ] || sudo -n true 2>/dev/null; then printf 'yes'; else printf 'no'; fi; printf '\\n'; printf 'PANEL='; if [ -e /opt/pi-dashboard ] || [ -e /opt/pi-node ]; then printf 'present'; else printf 'absent'; fi; printf '\\n'"
  _,out,_=client.exec_command(command,timeout=12);raw=out.read(8192).decode('utf-8','replace')
  fields={}
  for line in raw.splitlines():
   if '=' in line:
    k,v=line.split('=',1)
    if k in ('HOST','UPTIME','ARCH','MODEL','MEM','DOCKER','OS','DISK','ROOT','PANEL'):fields[k]=v[:2000]
  return {'name':n['name'],'fields':fields}
 except Exception:raise ValueError('Не удалось получить сведения. Проверьте доступ SSH и отпечаток ключа.')
 finally:client.close()
